"""Where does the learned plan chooser's edge over the planner come from? (#117)

#115 found that the plan chooser (learned_plans, and its prior-only ablation, which always draws the
planner's own pick) reaches 18-20/30 on the held-out France map against the vanilla opponent, which
no planner patience reaches (best 15/30). Its own explanation (hysteresis and candidate set) was a
guess. The prior-only chooser differs from ``Planner(patience=0)`` in ways that this script
isolates one at a time, either added to the planner or removed from the chooser:

* clear: the chooser drops its committed axis when it draws nothing; the planner leaves the old
  commitment in place, and a finished commitment raises the "axis completed" event at every step, so
  an idle planner re-plans every step instead of every cadence (24 h).
* sorted: the chooser returns its axes as a sorted tuple of region ids; the planner returns them
  best-first. The order is the order of the plans (axis1, axis2...), and the first plan is given
  the divisions standing nearest its anchor first.
* reversed: control for sorted. If the order matters only because it is some fixed order, the
  opposite fixed order should do as well; if it matters because of what sorting happens to do on
  this map, it will not.
* default-only: the chooser's candidate set cut to the planner's own pick.
* no-hysteresis: the chooser's switch margin set to 0 (a kept axis set is never preferred to the
  best-scoring one); sticky: the margin set to infinity (a kept set is always preferred while its
  patience lasts), the other end of the same dial.
* shuf1-3: three more fixed orders (hashes of the region ids), to see how far the order alone swings.

Cells run on Poland and France (and, with --small, the tuning set) against both opponents, 30 fresh
seeds (4000-4029) per cell, a fresh controller per game. Paired counts (games won only by the cell /
only by planner@96 on the same seed) go to src/hoi4_harness/tactics/data/chooser_ablation_results.json.
France is held out: nothing here is tuned on it; the cells were fixed before it was read.

    PYTHONPATH=src python scripts/tactics_chooser_ablation.py [--seeds 30] [--jobs 8] [--small]
"""

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from hoi4_harness.tactics import learned_plans as lp
from hoi4_harness.tactics.evaluate import run_episode
from hoi4_harness.tactics.planner import Planner
from hoi4_harness.tactics.plans import PlanController
from hoi4_harness.tactics.scenario import SCENARIO_DIR, list_scenarios
from hoi4_harness.tactics.variants import load_raw, vary

FIRST_SEED = 4000
REALMAP = SCENARIO_DIR.parent / "realmap"
POLAND = str(REALMAP / "germany_poland_1939.json")
FRANCE = str(REALMAP / "germany_france_1940.json")
VARIANT_SEEDS = (7001, 7002, 7003)
OUT = SCENARIO_DIR.parent / "chooser_ablation_results.json"
BASE = "planner@96"


class _Ordered(Planner):
    """The planner handing its axes on in a fixed order other than best-first or sorted."""

    order = "reversed"

    def _choose_axes(self, pic):
        axes = super()._choose_axes(pic)
        if self.order == "reversed":
            return sorted(axes, reverse=True)
        # a fixed pseudo-random order of region ids: the same ids always rank the same way
        return sorted(axes, key=lambda rid: hashlib.md5(f"{self.order}{rid}".encode()).hexdigest())


def _planner(patience, clear=False, order="ranked"):
    if order in ("ranked", "sorted"):
        return PlanController(Planner(patience=patience, reset_idle_commit=clear,
                                      sort_axes=order == "sorted"), name="planner")
    cls = type("Ordered", (_Ordered,), {"order": order})
    return PlanController(cls(patience=patience, reset_idle_commit=clear), name="planner")


def _prior_policy():
    w = lp.PlanPolicy()
    w.weights[lp._IDX["is_default"]] = 5.0
    return w


#: label -> (builder of a fresh controller, chooser settings or None). Settings are
#: (switch margin or None, candidate set cut to the planner's own pick).
LABELS = {
    "planner@96": (lambda: _planner(96), None),
    "planner@0": (lambda: _planner(0), None),
    "planner@0+clear": (lambda: _planner(0, clear=True), None),
    "planner@0+sorted": (lambda: _planner(0, order="sorted"), None),
    "planner@0+reversed": (lambda: _planner(0, order="reversed"), None),
    "planner@0+clear+sorted": (lambda: _planner(0, clear=True, order="sorted"), None),
    "planner@0+clear+reversed": (lambda: _planner(0, clear=True, order="reversed"), None),
    "planner@0+clear+shuf1": (lambda: _planner(0, clear=True, order="shuf1"), None),
    "planner@0+clear+shuf2": (lambda: _planner(0, clear=True, order="shuf2"), None),
    "planner@0+clear+shuf3": (lambda: _planner(0, clear=True, order="shuf3"), None),
    "planner@96+clear+sorted": (lambda: _planner(96, clear=True, order="sorted"), None),
    "prior_only": (lambda: lp.learned_plans_controller(_prior_policy()), (None, False)),
    "prior_only-no-hysteresis": (lambda: lp.learned_plans_controller(_prior_policy()), (0.0, False)),
    "prior_only-sticky": (lambda: lp.learned_plans_controller(_prior_policy()), (1e9, False)),
    "prior_only-default-only": (lambda: lp.learned_plans_controller(_prior_policy()), (None, True)),
    "learned_plans": (lambda: lp.learned_plans_controller(), (None, False)),
    "learned_plans-no-hysteresis": (lambda: lp.learned_plans_controller(), (0.0, False)),
    "learned_plans-sticky": (lambda: lp.learned_plans_controller(), (1e9, False)),
}


def scenarios(small: bool) -> list[tuple[str, str]]:
    out = [(POLAND, "tuning"), (FRANCE, "heldout")]
    if small:
        out = [(s, "tuning") for s in list_scenarios()] + out
        out += [(f"{POLAND}#{v}", "tuning") for v in VARIANT_SEEDS]
    return out


def run_cell(args: tuple[str, str, str, int]) -> dict:
    key, opp, label, seeds = args
    if "#" in key:
        path, v = key.split("#")
        scenario = vary(load_raw(path), int(v))
        name = scenario.name
    else:
        scenario = key
        name = Path(key).stem
    build, settings = LABELS[label]
    saved = (lp.SWITCH_MARGIN, lp.generate)
    try:
        if settings is not None:
            margin, default_only = settings
            if margin is not None:
                lp.SWITCH_MARGIN = margin
            if default_only:
                lp.generate = lambda pic: [tuple(sorted(lp.default_axes(pic)))]
        runs = [run_episode(scenario, FIRST_SEED + i, build(), opp) for i in range(seeds)]
    finally:
        lp.SWITCH_MARGIN, lp.generate = saved
    won = sum(x["strength_lost_enemy"] for x in runs)
    lost = sum(x["strength_lost_own"] for x in runs)
    return {"scenario": name, "key": key, "controller": label, "opponent": opp, "seeds": seeds,
            "wins": sum(x["outcome"] == "win" for x in runs),
            "casualty_ratio": round(won / max(lost, 1.0), 3),
            "outcomes": "".join("W" if x["outcome"] == "win" else "." for x in runs)}


def paired(rows: list[dict], row: dict) -> tuple[int, int]:
    base = next(r for r in rows if r["key"] == row["key"] and r["opponent"] == row["opponent"]
                and r["controller"] == BASE)
    only = sum(a == "W" and b != "W" for a, b in zip(row["outcomes"], base["outcomes"], strict=True))
    lost = sum(b == "W" and a != "W" for a, b in zip(row["outcomes"], base["outcomes"], strict=True))
    return only, lost


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--small", action="store_true", help="also the small maps and Poland variants")
    ap.add_argument("--opponents", nargs="*", default=["vanilla", "scripted"])
    args = ap.parse_args()
    scen = scenarios(args.small)
    groups = dict(scen)
    work = [(k, o, lab, args.seeds) for o in args.opponents for k, _ in scen for lab in LABELS]
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(run_cell, work))
    for r in rows:
        r["group"] = groups[r["key"]]
        r["vs_96_only_cell"], r["vs_96_only_96"] = paired(rows, r)
    for o in args.opponents:
        print(f"\nAgainst the {o} opponent, seeds {FIRST_SEED}-{FIRST_SEED + args.seeds - 1}"
              " (wins; paired +cell/-96h)\n")
        names = [next(r for r in rows if r["key"] == k)["scenario"] for k, _ in scen]
        print("| cell | " + " | ".join(names) + " |")
        print("|---|" + "---|" * len(scen))
        for lab in LABELS:
            cs = [next(r for r in rows if r["key"] == k and r["opponent"] == o and r["controller"] == lab)
                  for k, _ in scen]
            print(f"| {lab} | " + " | ".join(
                f"{c['wins']}" + ("" if lab == BASE else f" (+{c['vs_96_only_cell']}/-{c['vs_96_only_96']})")
                for c in cs) + " |")
    for r in rows:   # no machine paths in the committed file
        r["key"] = r["key"].replace(str(REALMAP), "realmap").replace("\\", "/")
    Path(args.out).write_text(json.dumps({"first_seed": FIRST_SEED, "seeds": args.seeds, "rows": rows},
                                         indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
