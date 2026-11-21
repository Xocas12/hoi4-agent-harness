"""Does a principled attack-axis order beat best-first? (#123)

#117 found that the plan chooser's France edge is the order it tries attack directions in, and that
region-id order is the best of six arbitrary orders on one map. This runs every order of
``Planner(axis_order=...)`` (best-first, the default; region id; nearest, weakest, supply, flanks) on
the tuning set (the four small maps, Poland and three procedural Poland variants) against both
opponents, seeds 4000-4029, a fresh controller per game (#116), with paired counts against
best-first (games won only by the cell / only by best-first on the same seed).

France (germany_france_1940) is held out: choose on the tuning set first, then read France once:

    PYTHONPATH=src python scripts/tactics_axis_order_bench.py --group tuning
    PYTHONPATH=src python scripts/tactics_axis_order_bench.py --group heldout

``--group tuning`` writes src/hoi4_harness/tactics/data/axis_order_results.json by default, ``--group
heldout`` writes axis_order_heldout.json next to it.
"""

import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from hoi4_harness.tactics.evaluate import run_episode
from hoi4_harness.tactics.planner import AXIS_ORDERS, planner_controller
from hoi4_harness.tactics.scenario import SCENARIO_DIR, list_scenarios
from hoi4_harness.tactics.variants import load_raw, vary

FIRST_SEED = 4000
DATA = SCENARIO_DIR.parent
REALMAP = DATA / "realmap"
POLAND = str(REALMAP / "germany_poland_1939.json")
FRANCE = str(REALMAP / "germany_france_1940.json")
VARIANT_SEEDS = (7001, 7002, 7003)
BASE = "best"


def scenarios(group: str) -> list[str]:
    if group == "heldout":
        return [FRANCE]
    return [*list_scenarios(), POLAND, *(f"{POLAND}#{v}" for v in VARIANT_SEEDS)]


def _low_priority() -> None:
    try:
        if os.name == "nt":
            import ctypes
            kernel = ctypes.windll.kernel32
            kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x4000)   # BELOW_NORMAL
        else:
            os.nice(10)
    except Exception:  # priority is a courtesy, never a failure
        pass


def run_cell(args: tuple[str, str, str, int]) -> dict:
    key, opp, order, seeds = args
    if "#" in key:
        path, v = key.split("#")
        scenario = vary(load_raw(path), int(v))
        name = scenario.name
    else:
        scenario = key
        name = Path(key).stem
    # a fresh controller per game: a planner keeps its commitment between calls (#116)
    runs = [run_episode(scenario, FIRST_SEED + i, planner_controller(axis_order=order), opp)
            for i in range(seeds)]
    won = sum(x["strength_lost_enemy"] for x in runs)
    lost = sum(x["strength_lost_own"] for x in runs)
    return {"scenario": name, "key": key, "order": order, "opponent": opp, "seeds": seeds,
            "wins": sum(x["outcome"] == "win" for x in runs),
            "casualty_ratio": round(won / max(lost, 1.0), 3),
            "outcomes": "".join("W" if x["outcome"] == "win" else "." for x in runs)}


def paired(rows: list[dict], row: dict) -> tuple[int, int]:
    base = next(r for r in rows if r["key"] == row["key"] and r["opponent"] == row["opponent"]
                and r["order"] == BASE)
    pairs = list(zip(row["outcomes"], base["outcomes"], strict=True))
    return sum(a == "W" and b != "W" for a, b in pairs), sum(b == "W" and a != "W" for a, b in pairs)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--group", choices=["tuning", "heldout"], default="tuning")
    ap.add_argument("--out")
    ap.add_argument("--opponents", nargs="*", default=["vanilla", "scripted"])
    args = ap.parse_args()
    out = args.out or str(DATA / ("axis_order_heldout.json" if args.group == "heldout"
                                  else "axis_order_results.json"))
    keys = scenarios(args.group)
    work = [(k, o, order, args.seeds) for o in args.opponents for k in keys for order in AXIS_ORDERS]
    with ProcessPoolExecutor(args.jobs, initializer=_low_priority) as ex:
        rows = list(ex.map(run_cell, work))
    for r in rows:
        r["group"] = args.group
        r["vs_best_only_cell"], r["vs_best_only_best"] = paired(rows, r)
    for o in args.opponents:
        print(f"\nAgainst the {o} opponent, seeds {FIRST_SEED}-{FIRST_SEED + args.seeds - 1}"
              " (wins; paired +cell/-best-first)\n")
        names = [next(r for r in rows if r["key"] == k)["scenario"] for k in keys]
        print("| order | " + " | ".join(names) + " | total |")
        print("|---|" + "---|" * (len(keys) + 1))
        for order in AXIS_ORDERS:
            cs = [next(r for r in rows if r["key"] == k and r["opponent"] == o and r["order"] == order)
                  for k in keys]
            tot = sum(c["wins"] for c in cs)
            plus = sum(c["vs_best_only_cell"] for c in cs)
            minus = sum(c["vs_best_only_best"] for c in cs)
            cells = [f"{c['wins']}" + ("" if order == BASE
                                       else f" (+{c['vs_best_only_cell']}/-{c['vs_best_only_best']})")
                     for c in cs]
            print(f"| {order} | " + " | ".join(cells)
                  + f" | {tot}" + ("" if order == BASE else f" (+{plus}/-{minus})") + " |")
    for r in rows:   # no machine paths in the committed file
        r["key"] = r["key"].replace(str(REALMAP), "realmap").replace("\\", "/")
    Path(out).write_text(json.dumps({"first_seed": FIRST_SEED, "seeds": args.seeds, "rows": rows},
                                    indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
