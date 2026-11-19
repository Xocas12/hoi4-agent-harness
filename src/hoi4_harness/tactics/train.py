"""Training for the learned controller: behaviour cloning from the scripted one, then evolution
strategies on win rate and casualties. Pure Python, single process, seeded.

    hoi4-harness tactics train --minutes 12

Seeds: training uses 0-299, the held-out evaluation in docs/tactics.md uses 1000-1029.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Sequence
from typing import Any

from .controllers import CONTROLLERS
from .env import TacticalEnv
from .evaluate import run_episode
from .learned import N_FEATURES, Context, LearnedController, Policy, candidates, current_intent
from .scenario import Scenario, list_scenarios, load_scenario

OPPONENTS = ("vanilla", "scripted")
TRAIN_SEEDS = range(0, 300)
HELDOUT_SEEDS = range(1000, 1030)


def episode_fitness(m: dict[str, Any]) -> float:
    """1 for a win, plus up to 0.5 for the share of the casualties the enemy took."""
    lost_e, lost_o = m["strength_lost_enemy"], m["strength_lost_own"]
    share = lost_e / (lost_e + lost_o) if lost_e + lost_o > 0 else 0.5
    return (1.0 if m["outcome"] == "win" else 0.0) + 0.5 * share


Job = tuple["Scenario", int, str]  # scenario, seed, opponent


def fitness(policy: Policy, jobs: Sequence[Job]) -> float:
    ctrl_runs = []
    for sc, seed, opp in jobs:
        ctrl_runs.append(episode_fitness(run_episode(sc, seed, LearnedController(policy), opp)))
    return sum(ctrl_runs) / len(ctrl_runs)


def make_jobs(
    scenarios: Sequence[Scenario], seeds: Sequence[int], opponents: Sequence[str] = OPPONENTS
) -> list[Job]:
    return [(sc, s, o) for s in seeds for sc in scenarios for o in opponents]


# ---- behaviour cloning -----------------------------------------------------------------------


def collect_demonstrations(jobs: Sequence[Job]) -> list[tuple[list[list[float]], int]]:
    """Play the scripted controller; at every step record, per division, the candidate it chose.

    A division the scripted controller gave no order keeps its current one, which is a candidate
    too. Samples whose order is outside the candidate set are dropped."""
    data: list[tuple[list[list[float]], int]] = []
    for sc, seed, opp in jobs:
        env = TacticalEnv(opponent=CONTROLLERS[opp]())
        env.reset(sc, seed=seed)
        teacher = CONTROLLERS["scripted"]()
        done = False
        while not done:
            view = env.view()
            orders = {o.division: o for o in teacher.act(view)}
            ctx = Context(view)
            for d in ctx.own:
                cands = candidates(ctx, d)
                o = orders.get(d.id)
                want = None
                for i, c in enumerate(cands):
                    if o is None and c.is_current:
                        want = i
                    elif o is not None and (c.order.kind, c.order.target) == (o.kind, o.target):
                        want = i
                if o is not None and want is None and current_intent(d) == (o.kind, o.target):
                    want = next((i for i, c in enumerate(cands) if c.is_current), None)
                if want is not None and len(cands) > 1:
                    data.append(([c.feats for c in cands], want))
            done = env.step(list(orders.values())).done
    return data


def behaviour_clone(
    data: list[tuple[list[list[float]], int]], epochs: int = 8, lr: float = 0.15, l2: float = 1e-4,
    seed: int = 0, log: Callable[[str], None] = lambda s: None,
) -> Policy:
    """Softmax regression over each division's candidates, plain per-sample gradient steps."""
    rng = random.Random(seed)
    w = [0.0] * N_FEATURES
    order = list(range(len(data)))
    for epoch in range(epochs):
        rng.shuffle(order)
        loss = 0.0
        hits = 0
        for k in order:
            feats, y = data[k]
            scores = [sum(w[i] * x for i, x in enumerate(f) if x) for f in feats]
            top = max(scores)
            exps = [math.exp(s - top) for s in scores]
            z = sum(exps)
            probs = [e / z for e in exps]
            loss -= math.log(max(probs[y], 1e-12))
            hits += probs[y] == max(probs)
            for j, f in enumerate(feats):
                g = probs[j] - (1.0 if j == y else 0.0)
                if abs(g) < 1e-4:
                    continue
                for i, x in enumerate(f):
                    if x:
                        w[i] -= lr * (g * x + l2 * w[i])
        log(f"  bc epoch {epoch + 1}/{epochs}: loss {loss / len(data):.3f}, top-1 {hits / len(data):.1%}")
        lr *= 0.8
    return Policy(w, {"stage": "behaviour-clone", "samples": len(data), "epochs": epochs})


# ---- evolution strategies ----------------------------------------------------------------------


def evolve(
    start: Policy, scenarios: Sequence[Scenario], *, seed: int = 0, generations: int = 1000,
    seconds: float | None = None, pairs: int = 6, sigma: float = 0.15, lr: float = 0.08,
    seeds_per_gen: int = 1, opponents: Sequence[str] = OPPONENTS,
    log: Callable[[str], None] = lambda s: None,
) -> tuple[Policy, list[dict[str, Any]]]:
    """Antithetic evolution strategies with rank-shaped fitness.

    Each generation scores 2*pairs perturbed copies of the parameters on the same fresh seeds
    (common random numbers), then moves along the rank-weighted sum of the perturbations.
    The final parameters are returned; `train` checks them against the start on a validation set."""
    rng = random.Random(seed)
    theta = list(start.weights)
    t0 = time.perf_counter()
    history: list[dict[str, Any]] = []
    for gen in range(generations):
        if seconds is not None and time.perf_counter() - t0 > seconds:
            break
        seeds = [TRAIN_SEEDS[(gen * seeds_per_gen + i) % len(TRAIN_SEEDS)] for i in range(seeds_per_gen)]
        jobs = make_jobs(scenarios, seeds, opponents)
        noises = [[rng.gauss(0, 1) for _ in theta] for _ in range(pairs)]
        results = []  # fitness, in noise order (+, -)
        for n in noises:
            for sign in (1, -1):
                p = Policy([t + sign * sigma * e for t, e in zip(theta, n, strict=True)])
                results.append(fitness(p, jobs))
        center = fitness(Policy(theta), jobs)
        order = sorted(range(len(results)), key=lambda i: results[i])
        rank = [0.0] * len(results)
        for r, i in enumerate(order):
            rank[i] = r / (len(results) - 1) - 0.5
        grad = [0.0] * len(theta)
        for k, n in enumerate(noises):
            adv = rank[2 * k] - rank[2 * k + 1]
            for i, e in enumerate(n):
                grad[i] += adv * e
        theta = [t + lr / pairs * g for t, g in zip(theta, grad, strict=True)]
        history.append({"generation": gen, "center_fitness": round(center, 4),
                        "mean_fitness": round(sum(results) / len(results), 4),
                        "elapsed": round(time.perf_counter() - t0, 1)})
        log(f"  es gen {gen}: center {center:.3f}, population mean {history[-1]['mean_fitness']:.3f}"
            f" ({history[-1]['elapsed']}s)")
    return Policy(theta, {"stage": "es", "generations": len(history)}), history


def train(
    minutes: float = 12.0, seed: int = 0, scenarios: Sequence[str] | None = None,
    log: Callable[[str], None] = print,
) -> Policy:
    """Behaviour-clone the scripted controller, then spend the rest of the budget on ES."""
    t0 = time.perf_counter()
    scs = [load_scenario(n) for n in (scenarios or list_scenarios())]
    log("collecting demonstrations from the scripted controller")
    demo = collect_demonstrations(make_jobs(scs, range(0, 10)))
    log(f"  {len(demo)} samples ({time.perf_counter() - t0:.0f}s)")
    policy = behaviour_clone(demo, seed=seed, log=log)
    left = minutes * 60 - (time.perf_counter() - t0)
    log(f"evolution strategies for {left / 60:.1f} more minutes")
    start = policy
    policy, history = evolve(start, scs, seed=seed, seconds=left, log=log)
    val = make_jobs(scs, range(200, 204))
    f0, f1 = fitness(start, val), fitness(policy, val)
    log(f"validation fitness (seeds 200-203): behaviour clone {f0:.3f}, after ES {f1:.3f}")
    if f1 < f0:
        log("ES did not beat the behaviour clone on validation; keeping the clone")
        policy = start
    policy.meta.update({
        "stage": "bc+es", "seed": seed, "scenarios": [s.name for s in scs], "train_seeds": "0-299",
        "wall_seconds": round(time.perf_counter() - t0), "es_generations": len(history),
    })
    return policy
