"""Training the learned plan chooser: privileged distillation, then evolution strategies (#85).

    hoi4-harness tactics train-plans --minutes 18

* **Teacher**: the planner with ``observe="full"`` (province-level numbers). **Student**: the
  scorer of learned_plans.py, which sees the aggregated picture only.
* **Cloning**: the teacher plays, and at every offensive re-plan the student's candidates (built
  from the aggregated picture of the same game) are recorded with the one the teacher drew. Softmax
  regression over the candidates. Then DAgger rounds: the student plays and the teacher is asked
  what it would have drawn in the states the student reached, and the new samples are added.
* **ES**: antithetic evolution strategies on win and casualty share, kept only if the result beats
  the clone on validation games the training never used (same rule as train.py).

Pool: the four small scenarios and the Poland map, each with procedural variants (variants.py).
The France front is never loaded here. Seeds: training games 0-299, validation 200-201 on
variants numbered 900 and up; the benchmark uses 3000-3029, nothing else used those.
"""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .evaluate import run_episode
from .learned_plans import (
    COVERAGE,
    FEATURE_NAMES,
    N_FEATURES,
    AxisTeacher,
    LearnedPlanner,
    PlanPolicy,
    RecordingTeacher,
    learned_plans_controller,
)
from .planner import planner_controller
from .plans import PlanController
from .scenario import SCENARIO_DIR, Scenario, list_scenarios
from .train import episode_fitness
from .variants import load_raw, vary

OPPONENTS = ("vanilla", "scripted")
REALMAP_TRAIN = ("germany_poland_1939",)
HELD_OUT_FRONT = "germany_france_1940"
PRIOR_STRENGTHS = (0.05, 0.3)
Job = tuple[Scenario, int, str]   # scenario, game seed, opponent
Sample = tuple[list[list[float]], int]


def training_sources() -> list[str]:
    """Paths of the scenarios the pool is built from: the four small ones and the Poland map."""
    realmap = SCENARIO_DIR.parent / "realmap"
    return [*(str(SCENARIO_DIR / f"{n}.json") for n in list_scenarios()),
            *(str(realmap / f"{n}.json") for n in REALMAP_TRAIN)]


def make_pool(sources: Sequence[str], vseeds: Sequence[int]) -> list[Scenario]:
    return [vary(load_raw(p), v) for p in sources for v in vseeds]


def make_jobs(
    pool: Sequence[Scenario], seeds: Sequence[int], opponents: Sequence[str] = OPPONENTS
) -> list[Job]:
    return [(sc, s, o) for s in seeds for sc in pool for o in opponents]


def controller_fitness(make: Callable[[], Any], jobs: Sequence[Job]) -> float:
    total = 0.0
    for sc, seed, opp in jobs:
        total += episode_fitness(run_episode(sc, seed, make(), opp))
    return total / len(jobs)


def fitness(policy: PlanPolicy, jobs: Sequence[Job]) -> float:
    return controller_fitness(lambda: learned_plans_controller(policy), jobs)


def planner_prior() -> PlanPolicy:
    """Weights that draw what the hand-written aggregated planner draws."""
    w = [0.0] * N_FEATURES
    w[FEATURE_NAMES.index("is_default")] = 5.0
    return PlanPolicy(w, {"stage": "planner prior"})


# ---- cloning -----------------------------------------------------------------------------------


def collect_teacher(jobs: Sequence[Job]) -> list[Sample]:
    """The full-information planner plays side A; the aggregated candidates and its choice are kept."""
    data: list[Sample] = []
    for sc, seed, opp in jobs:
        run_episode(sc, seed, PlanController(RecordingTeacher(data), name="teacher"), opp)
    return data


def collect_dagger(policy: PlanPolicy, jobs: Sequence[Job]) -> list[Sample]:
    """The student plays; the teacher is asked, on each view the student reached, what it would draw."""
    data: list[Sample] = []
    for sc, seed, opp in jobs:
        ctrl = PlanController(LearnedPlanner(policy, teacher=AxisTeacher(), recorder=data), name="student")
        run_episode(sc, seed, ctrl, opp)
    return data


def clone(
    data: Sequence[Sample], start: PlanPolicy | None = None, epochs: int = 12, lr: float = 0.1,
    l2: float = 1e-3, seed: int = 0, prior: PlanPolicy | None = None,
    log: Callable[[str], None] = lambda s: None,
) -> tuple[PlanPolicy, float]:
    """Softmax regression over each state's candidates; returns the policy and its top-1 agreement.

    With a ``prior`` the weights start there and the L2 term pulls toward it, not toward zero:
    the student stays the prior (the hand-written planner's pick) except where the data says otherwise."""
    rng = random.Random(seed)
    anchor = list(prior.weights) if prior is not None else [0.0] * N_FEATURES
    w = list(start.weights) if start is not None else list(anchor)
    order = list(range(len(data)))
    hits = 0.0
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
            hits += scores[y] >= top
            for j, f in enumerate(feats):
                g = probs[j] - (1.0 if j == y else 0.0)
                if abs(g) < 1e-4:
                    continue
                for i, x in enumerate(f):
                    if x:
                        w[i] -= lr * (g * x + l2 * (w[i] - anchor[i]))
        if epoch in (0, epochs - 1):
            log(f"  clone epoch {epoch + 1}/{epochs}: loss {loss / len(data):.3f}, "
                f"top-1 {hits / len(data):.1%}")
        lr *= 0.85
    acc = hits / max(len(data), 1)
    return PlanPolicy(w, {"stage": "clone", "samples": len(data), "epochs": epochs}), acc


# ---- evolution strategies ----------------------------------------------------------------------


def evolve(
    start: PlanPolicy, jobs_for: Callable[[int], Sequence[Job]], *, seed: int = 0, seconds: float = 600.0,
    pairs: int = 5, sigma: float = 0.1, lr: float = 0.06, max_generations: int = 1000,
    fit: Callable[[PlanPolicy, Sequence[Job]], float] = fitness,
    log: Callable[[str], None] = lambda s: None,
) -> tuple[PlanPolicy, list[dict[str, Any]]]:
    """Antithetic ES with rank-shaped fitness; every perturbed copy plays the same jobs."""
    rng = random.Random(seed)
    theta = list(start.weights)
    t0 = time.perf_counter()
    history: list[dict[str, Any]] = []
    for gen in range(max_generations):
        if time.perf_counter() - t0 > seconds:
            break
        jobs = jobs_for(gen)
        noises = [[rng.gauss(0, 1) for _ in theta] for _ in range(pairs)]
        results = []
        for n in noises:
            for sign in (1, -1):
                results.append(fit(PlanPolicy([t + sign * sigma * e for t, e in zip(theta, n, strict=True)]),
                                   jobs))
        center = fit(PlanPolicy(theta), jobs)
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
        log(f"  es gen {gen}: center {center:.3f}, mean {history[-1]['mean_fitness']:.3f} "
            f"({history[-1]['elapsed']}s)")
    return PlanPolicy(theta, {"stage": "es", "generations": len(history)}), history


def keep_if_better(
    start: PlanPolicy, candidate: PlanPolicy, jobs: Sequence[Job],
    fit: Callable[[PlanPolicy, Sequence[Job]], float] = fitness,
) -> tuple[PlanPolicy, dict[str, Any]]:
    """The honesty rule: the ES result replaces the clone only if it scores strictly higher on the
    validation jobs (which neither stage trained on)."""
    f0, f1 = fit(start, jobs), fit(candidate, jobs)
    kept = f1 > f0
    return (candidate if kept else start), {"clone": round(f0, 4), "es": round(f1, 4), "es_kept": kept}


# ---- the whole run -----------------------------------------------------------------------------


def train(minutes: float = 18.0, seed: int = 0, log: Callable[[str], None] = print) -> PlanPolicy:
    t0 = time.perf_counter()
    sources = training_sources()
    log("round 0: the full-information teacher plays")
    pool0 = make_pool(sources, range(0, 12))
    data = collect_teacher(make_jobs(pool0, [0, 1]))
    log(f"  {len(data)} samples ({time.perf_counter() - t0:.0f}s); the teacher's choice was among the "
        f"student's candidates in {COVERAGE['covered']}/{COVERAGE['decisions']} decisions")
    policy, acc = clone(data, seed=seed, log=log)
    rounds = [{"round": 0, "samples": len(data), "top1": round(acc, 3)}]
    for rnd in (1, 2):
        log(f"dagger round {rnd}: the student plays, the teacher labels")
        pool = make_pool(sources, range(12 * rnd, 12 * rnd + 8))
        new = collect_dagger(policy, make_jobs(pool, [2 * rnd]))
        data += new
        policy, acc = clone(data, start=policy, epochs=8, seed=seed + rnd, log=log)
        rounds.append({"round": rnd, "samples": len(data), "top1": round(acc, 3)})
        log(f"  +{len(new)} samples, {len(data)} total ({time.perf_counter() - t0:.0f}s)")
    # three ways to fit the collected samples; the best on the selection games starts the ES
    val = make_jobs(make_pool(sources, [900, 901]), range(200, 202))
    prior = planner_prior()
    fits = {"clone": policy}
    for strength in PRIOR_STRENGTHS:
        fits[f"clone toward the planner (l2 {strength})"] = clone(
            data, epochs=12, l2=strength, prior=prior, seed=seed)[0]
    scores = {name: fitness(p, val) for name, p in fits.items()}
    pick = max(scores, key=lambda n: scores[n])
    start = fits[pick]
    log("selection games (seeds 200-201, variants 900-901): "
        + "; ".join(f"{n} {v:.3f}" for n, v in scores.items()) + f" -> {pick}")
    val = make_jobs(make_pool(sources, [902, 903]), range(202, 204))   # the keep-only-if-better games
    planner_f = controller_fitness(planner_controller, val)
    full_f = controller_fitness(lambda: planner_controller("full"), val)
    left = minutes * 60 - (time.perf_counter() - t0) - 0.15 * minutes * 60   # keep time for validation
    log(f"evolution strategies for {left / 60:.1f} minutes")
    pools: dict[int, list[Scenario]] = {}

    def jobs_for(gen: int) -> Sequence[Job]:
        v = 100 + gen % 40
        if v not in pools:
            pools[v] = make_pool(sources, [v])
        return make_jobs(pools[v], [gen % 300])

    cand, history = evolve(start, jobs_for, seed=seed, seconds=left, log=log)
    policy, verdict = keep_if_better(start, cand, val)
    log(f"validation fitness: clone {verdict['clone']}, after ES {verdict['es']}, "
        f"hand-written aggregated planner {planner_f:.4f}, full-information planner {full_f:.4f}; "
        f"ES kept: {verdict['es_kept']}")
    policy.meta.update({
        "stage": "distil+es" if verdict["es_kept"] else "distil", "seed": seed, "rounds": rounds,
        "train_scenarios": [Path(p).stem for p in sources],
        "held_out_front": HELD_OUT_FRONT, "validation": {**verdict, "planner_aggregated": round(planner_f, 4),
                       "planner_full": round(full_f, 4)},
        "teacher_choice_covered": f"{COVERAGE['covered']}/{COVERAGE['decisions']}",
        "clone_selection": {"scores": {n: round(v, 4) for n, v in scores.items()}, "picked": pick},
        "es_generations": len(history), "wall_seconds": round(time.perf_counter() - t0),
        "train_seeds": "0-299", "selection_seeds": "200-201 on variants 900-901",
        "validation_seeds": "202-203 on variants 902-903",
    })
    return policy
