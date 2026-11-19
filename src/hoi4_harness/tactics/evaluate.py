"""Run controllers over scenarios and seeds, and summarise."""

from __future__ import annotations

import time
from statistics import mean
from typing import Any

from .controllers import CONTROLLERS
from .env import TacticalEnv
from .scenario import Scenario, list_scenarios, load_scenario


def run_episode(
    scenario: str | Scenario, seed: int, controller: Any = "scripted", opponent: str = "vanilla"
) -> dict[str, Any]:
    env = TacticalEnv(opponent=CONTROLLERS[opponent]())
    env.reset(scenario, seed=seed)
    ctrl = CONTROLLERS[controller]() if isinstance(controller, str) else controller
    done = False
    steps = 0
    while not done:
        result = env.step(ctrl.act(env.view()))
        done = result.done
        steps += 1
    m = env.metrics()
    m["steps"] = steps
    return m


def evaluate(
    scenario: str | Scenario, controller: Any, seeds: int, opponent: str = "vanilla", first_seed: int = 0
) -> dict[str, Any]:
    sc = load_scenario(scenario) if isinstance(scenario, str) else scenario
    t0 = time.perf_counter()
    runs = [run_episode(sc, first_seed + i, controller, opponent) for i in range(seeds)]
    label = controller if isinstance(controller, str) else getattr(controller, "name", "custom")
    elapsed = time.perf_counter() - t0
    wins = sum(r["outcome"] == "win" for r in runs)
    steps = sum(r["steps"] for r in runs)
    enemy_lost = sum(r["strength_lost_enemy"] for r in runs)
    own_lost = sum(r["strength_lost_own"] for r in runs)

    def avg(key: str, digits: int = 2) -> float:
        return round(mean(r[key] for r in runs), digits)

    return {
        "scenario": sc.name,
        "controller": label,
        "opponent": opponent,
        "seeds": seeds,
        "wins": wins,
        "win_rate": wins / seeds,
        "casualty_ratio": round(enemy_lost / max(own_lost, 1.0), 3),
        "mean_hours": avg("hours", 1),
        "mean_territory_held": avg("territory_held"),
        "mean_divisions_destroyed": avg("divisions_destroyed"),
        "mean_divisions_lost": avg("divisions_lost"),
        "mean_pockets_closed": avg("pockets_closed"),
        "steps_per_second": round(steps / elapsed, 1) if elapsed else 0.0,
        "runs": runs,
    }


TABLE_HEAD = (
    "scenario", "controller", "win rate", "casualty ratio", "hours", "territory", "destroyed", "lost",
    "pockets",
)


def table(rows: list[dict[str, Any]]) -> str:
    """A markdown table; casualty ratio is enemy strength lost over own (above 1 is good)."""
    lines = ["| " + " | ".join(TABLE_HEAD) + " |", "|" + "---|" * len(TABLE_HEAD)]
    for r in rows:
        cells = (
            r["scenario"], r["controller"], f"{r['wins']}/{r['seeds']} ({r['win_rate']:.0%})",
            r["casualty_ratio"], r["mean_hours"], r["mean_territory_held"], r["mean_divisions_destroyed"],
            r["mean_divisions_lost"], r["mean_pockets_closed"],
        )
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines)


def run_all(
    seeds: int, controllers: tuple[str, ...] = ("vanilla", "scripted"), opponent: str = "vanilla"
) -> list[dict]:
    return [evaluate(s, c, seeds, opponent) for s in list_scenarios() for c in controllers]
