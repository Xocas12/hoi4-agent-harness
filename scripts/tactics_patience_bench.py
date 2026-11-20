"""Planner axis patience benchmark (#112): how long an attack axis is kept, across map sizes.

Cells: patience in {0, 24, 48, 96, scaled} for the aggregated planner, plus the #111 ablation
(learned_plans with the planner prior only, and with the committed weights), over the four small
scenarios, the Poland map, three procedural variants of Poland (the tuning set, with the small
maps) and the France map (held out: never used to choose the default), against both opponents.
Games are seeds 4000-4029 (fresh: no earlier stage used them). Wins, and paired counts against the
96 h planner (games won only by the cell / only by 96 h on the same seed), are written to
src/hoi4_harness/tactics/data/patience_results.json.

    PYTHONPATH=src python scripts/tactics_patience_bench.py [--seeds 30] [--jobs 4] [--out FILE]
"""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from hoi4_harness.tactics.evaluate import run_episode
from hoi4_harness.tactics.learned_plans import PlanPolicy, learned_plans_controller
from hoi4_harness.tactics.planner import planner_controller
from hoi4_harness.tactics.scenario import SCENARIO_DIR, list_scenarios
from hoi4_harness.tactics.variants import load_raw, vary

FIRST_SEED = 4000
REALMAP = SCENARIO_DIR.parent / "realmap"
POLAND = str(REALMAP / "germany_poland_1939.json")
FRANCE = str(REALMAP / "germany_france_1940.json")
VARIANT_SEEDS = (7001, 7002, 7003)
OUT = SCENARIO_DIR.parent / "patience_results.json"
PATIENCES = (0, 24, 48, 96, "scaled")
#: (label, group). Tuning = small maps + Poland + its variants; held out = France.
GROUPS = {"tuning": "small + Poland + variants", "heldout": "France"}


def planner_label(p) -> str:
    return f"planner@{p}"


def make(label: str):
    if label.startswith("planner@"):
        p = label.split("@", 1)[1]
        return planner_controller(patience=p if p == "scaled" else int(p))
    if label == "prior_only":
        from hoi4_harness.tactics import learned_plans as lp
        w = PlanPolicy()
        w.weights[lp._IDX["is_default"]] = 5.0
        return learned_plans_controller(w)
    if label == "learned_plans":
        return learned_plans_controller()
    raise KeyError(label)


def cells() -> list[tuple[str, str, str]]:
    """(scenario key, group, label) cells; a scenario key is a path/name or 'path#vseed'."""
    scen = [(s, "tuning") for s in list_scenarios()] + [(POLAND, "tuning")]
    scen += [(f"{POLAND}#{v}", "tuning") for v in VARIANT_SEEDS] + [(FRANCE, "heldout")]
    labels = [planner_label(p) for p in PATIENCES] + ["prior_only", "learned_plans"]
    return [(s, g, lab) for s, g in scen for lab in labels]


def run_cell(args: tuple[str, str, str, int]) -> dict:
    key, opp, label, seeds = args
    if "#" in key:
        path, v = key.split("#")
        scenario = vary(load_raw(path), int(v))
    else:
        scenario = key
    if not isinstance(scenario, str):
        name = scenario.name
    else:
        name = Path(scenario).stem
    # a planner keeps its drawn axes between calls: every game gets a fresh controller (reusing one
    # across seeds leaks the commitment from the last game into the next one)
    runs = [run_episode(scenario, FIRST_SEED + i, make(label), opp) for i in range(seeds)]
    won = sum(x["strength_lost_enemy"] for x in runs)
    lost = sum(x["strength_lost_own"] for x in runs)
    return {"scenario": name, "key": key, "controller": label, "opponent": opp, "seeds": seeds,
            "wins": sum(x["outcome"] == "win" for x in runs), 
            "casualty_ratio": round(won / max(lost, 1.0), 3),
            "outcomes": "".join("W" if x["outcome"] == "win" else "." for x in runs)}


def paired(rows: list[dict], row: dict) -> tuple[int, int]:
    base = next(r for r in rows if r["key"] == row["key"] and r["opponent"] == row["opponent"]
                and r["controller"] == planner_label(96))
    only = sum(a == "W" and b != "W" for a, b in zip(row["outcomes"], base["outcomes"], strict=True))
    lost = sum(b == "W" and a != "W" for a, b in zip(row["outcomes"], base["outcomes"], strict=True))
    return only, lost


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--opponents", nargs="*", default=["vanilla", "scripted"])
    ap.add_argument("--group", choices=sorted(GROUPS), help="only this group (tune, then held out)")
    args = ap.parse_args()
    groups = {k: g for k, g, _ in cells()}
    work = [(k, o, lab, args.seeds) for o in args.opponents for k, g, lab in cells()
            if args.group in (None, g)]
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(run_cell, work))
    for r in rows:
        r["group"] = groups[r["key"]]
        r["vs_96_only_cell"], r["vs_96_only_96"] = paired(rows, r)
    for o in args.opponents:
        print(f"\nAgainst the {o} opponent, seeds {FIRST_SEED}-{FIRST_SEED + args.seeds - 1}"
              f" (wins; paired +cell/-cell vs planner@96)\n")
        labs = [planner_label(p) for p in PATIENCES] + ["prior_only", "learned_plans"]
        print("| scenario | " + " | ".join(labs) + " |")
        print("|---|" + "---|" * len(labs))
        for k in dict.fromkeys(k for k, g, _ in cells() if args.group in (None, g)):
            cs = [next(r for r in rows if r["key"] == k and r["opponent"] == o and r["controller"] == lab)
                  for lab in labs]
            print(f"| {cs[0]['scenario']} ({groups[k]}) | "
                  + " | ".join(f"{c['wins']} (+{c['vs_96_only_cell']}/-{c['vs_96_only_96']})"
                              for c in cs) + " |")
    Path(args.out).write_text(json.dumps({"first_seed": FIRST_SEED, "seeds": args.seeds, "rows": rows},
                                         indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
