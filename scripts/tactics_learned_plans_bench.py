"""The learned plan chooser's benchmark for docs/tactics.md: the four small scenarios, the Poland map
(a training front) and the France map (never seen in training) x {vanilla plans, planner, planner
with full information, learned plans, scripted, learned} as side A against each opponent, on seeds
3000-3029 (no earlier development, training or benchmark used them). Written to
src/hoi4_harness/tactics/data/learned_plans_results.json.

    PYTHONPATH=src python scripts/tactics_learned_plans_bench.py [--seeds 30] [--scenarios NAME ...]
"""

import argparse
import json
from pathlib import Path

from hoi4_harness.tactics.evaluate import evaluate, table
from hoi4_harness.tactics.learned import PARAMS_PATH, LearnedController, Policy
from hoi4_harness.tactics.scenario import SCENARIO_DIR, list_scenarios

FIRST_SEED = 3000
REALMAP = SCENARIO_DIR.parent / "realmap"
FRONTS = ("germany_poland_1939", "germany_france_1940")
OUT = SCENARIO_DIR.parent / "learned_plans_results.json"
KEEP = ("scenario", "controller", "opponent", "seeds", "wins", "win_rate", "casualty_ratio", "mean_hours",
        "mean_territory_held", "mean_divisions_destroyed", "mean_divisions_lost", "mean_pockets_closed")
CONTROLLERS = ("vanilla_plans", "planner", "planner_full", "learned_plans", "scripted", "learned")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--opponents", nargs="*", default=["vanilla", "scripted"])
    ap.add_argument("--scenarios", nargs="*")
    args = ap.parse_args()
    old = LearnedController(Policy.load(PARAMS_PATH))
    scenarios = args.scenarios or [*list_scenarios(), *(str(REALMAP / f"{n}.json") for n in FRONTS)]
    rows = []
    for opp in args.opponents:
        for sc in scenarios:
            for ctrl in CONTROLLERS:
                r = evaluate(sc, old if ctrl == "learned" else ctrl, args.seeds, opp, FIRST_SEED)
                r["controller"] = ctrl
                row = {k: r[k] for k in KEEP}
                row["outcomes"] = "".join("W" if x["outcome"] == "win" else "." for x in r["runs"])
                rows.append(row)
                print({k: v for k, v in row.items() if k != "outcomes"}, flush=True)
        print(f"\nAgainst the {opp} opponent, seeds {FIRST_SEED}-{FIRST_SEED + args.seeds - 1}\n")
        print(table([r for r in rows if r["opponent"] == opp]), flush=True)
    out = {"first_seed": FIRST_SEED, "seeds": args.seeds, "rows": rows}
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
