"""The plan-level benchmark for docs/tactics.md: every scenario (the four, plus the real-map one) x
{vanilla plans, planner, planner (full information), scripted, learned} as side A against each
opponent on seeds 2000-2029, written to src/hoi4_harness/tactics/data/plans_results.json.

    PYTHONPATH=src python scripts/tactics_plans_bench.py [--seeds 30] [--only NAME ...]
"""

import argparse
import json
from pathlib import Path

from hoi4_harness.tactics.evaluate import evaluate, table
from hoi4_harness.tactics.learned import PARAMS_PATH, LearnedController, Policy
from hoi4_harness.tactics.scenario import SCENARIO_DIR, list_scenarios

FIRST_SEED = 2000
REALMAP = SCENARIO_DIR.parent / "realmap" / "germany_poland_1939.json"
OUT = SCENARIO_DIR.parent / "plans_results.json"
KEEP = ("scenario", "controller", "opponent", "seeds", "wins", "win_rate", "casualty_ratio", "mean_hours",
        "mean_territory_held", "mean_divisions_destroyed", "mean_divisions_lost", "mean_pockets_closed")
CONTROLLERS = ("vanilla", "vanilla_plans", "planner", "planner_full", "scripted", "learned")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--opponents", nargs="*", default=["vanilla", "scripted"])
    ap.add_argument("--scenarios", nargs="*")
    args = ap.parse_args()
    learned = LearnedController(Policy.load(PARAMS_PATH))
    scenarios = args.scenarios or [*list_scenarios(), str(REALMAP)]
    rows = []
    for opp in args.opponents:
        for sc in scenarios:
            for ctrl in CONTROLLERS:
                r = evaluate(sc, learned if ctrl == "learned" else ctrl, args.seeds, opp, FIRST_SEED)
                r["controller"] = ctrl
                rows.append({k: r[k] for k in KEEP})
                print(rows[-1], flush=True)
        print(f"\nAgainst the {opp} opponent, seeds {FIRST_SEED}-{FIRST_SEED + args.seeds - 1}\n")
        print(table([r for r in rows if r["opponent"] == opp]), flush=True)
    out = {"first_seed": FIRST_SEED, "seeds": args.seeds, "rows": rows}
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
