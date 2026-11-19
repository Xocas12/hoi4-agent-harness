"""Held-out evaluation for docs/tactics.md: every scenario x {vanilla, scripted, learned} as side A
against each opponent on seeds 1000-1029, written to src/hoi4_harness/tactics/data/learned_results.json.

    PYTHONPATH=src python scripts/tactics_heldout.py [--params FILE] [--seeds 30]
"""

import argparse
import json
from pathlib import Path

from hoi4_harness.tactics.evaluate import evaluate, table
from hoi4_harness.tactics.learned import PARAMS_PATH, LearnedController, Policy
from hoi4_harness.tactics.scenario import list_scenarios

FIRST_SEED = 1000
KEEP = ("scenario", "controller", "opponent", "seeds", "wins", "win_rate", "casualty_ratio", "mean_hours",
        "mean_territory_held", "mean_divisions_destroyed", "mean_divisions_lost", "mean_pockets_closed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--params", default=str(PARAMS_PATH))
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--out", default=str(Path(PARAMS_PATH).with_name("learned_results.json")))
    args = ap.parse_args()
    learned = LearnedController(Policy.load(args.params))
    rows = []
    for opp in ("vanilla", "scripted"):
        for sc in list_scenarios():
            for ctrl in ("vanilla", "scripted", learned):
                r = evaluate(sc, ctrl, args.seeds, opp, FIRST_SEED)
                rows.append({k: r[k] for k in KEEP})
        print(f"\nAgainst the {opp} opponent, seeds {FIRST_SEED}-{FIRST_SEED + args.seeds - 1}\n")
        print(table([r for r in rows if r["opponent"] == opp]))
    out = {"first_seed": FIRST_SEED, "seeds": args.seeds, "rows": rows}
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
