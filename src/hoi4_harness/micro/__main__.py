"""``python -m hoi4_harness.micro extract|train|eval|report``: the offline loop.

    python -m hoi4_harness.micro extract runs/overnight-ger-1936 -o data/micro.jsonl
    python -m hoi4_harness.micro train data/micro.jsonl -o data/micro-model.json
    python -m hoi4_harness.micro eval data/micro.jsonl --model data/micro-model.json
    python -m hoi4_harness.micro report runs/overnight-ger-1936      # extract + train + eval in one go
    python -m hoi4_harness.micro simulate -o data/sim --episodes 60  # toy-simulator runs to practise on

``train`` fits on the earlier part of the data and ``eval`` scores on the held-out part with the
same split, so a model is never judged on rows it learned from.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import dataset as D
from .boosted import BoostedBandit, MissingExtra
from .evaluate import evaluate, render
from .learn import LinearBandit, load_model, split_episodes


def _rows(paths: list[str], horizon: int) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        path = Path(p)
        rows += D.load_dataset(path) if path.is_file() else D.extract_run(path, horizon)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hoi4_harness.micro", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sm = sub.add_parser("simulate", help="run the toy simulator (sim.py) and write run directories")
    sm.add_argument("-o", "--out", required=True)
    sm.add_argument("--episodes", type=int, default=60)
    sm.add_argument("--turns", type=int, default=12)
    sm.add_argument("--epsilon", type=float, default=0.3, help="exploration; 0 logs the baseline alone")
    ex = sub.add_parser("extract", help="run directories -> a dataset (jsonl)")
    ex.add_argument("runs", nargs="+")
    ex.add_argument("-o", "--out", required=True)
    ex.add_argument("--horizon", type=int, default=D.HORIZON_DAYS)
    for name in ("train", "eval", "report"):
        p = sub.add_parser(name)
        p.add_argument("data", nargs="+", help="dataset files or run directories")
        p.add_argument("--horizon", type=int, default=D.HORIZON_DAYS)
        p.add_argument("--test-fraction", type=float, default=0.3)
        p.add_argument("--l2", type=float, default=1.0)
        if name in ("train", "report"):
            p.add_argument("--model-type", choices=("linear", "boosted"), default="linear",
                           help="boosted needs the ml extra: pip install 'hoi4-agent-harness[ml]'")
            p.add_argument("--mode", choices=("dr", "ipw"), default="dr",
                           help="boosted only: doubly robust targets or importance-weighted regression")
        if name == "train":
            p.add_argument("-o", "--out", required=True)
        if name == "eval":
            p.add_argument("--model", required=True)
        if name in ("eval", "report"):
            p.add_argument("--json", action="store_true", help="print the numbers, not the report")
    args = ap.parse_args(argv)

    if args.cmd == "simulate":
        from . import sim
        from .baseline import baseline_from_features
        from .explore import ExploringController

        base = sim.FeaturePolicyController(baseline_from_features, "baseline")
        for n in range(args.episodes):
            sim.run_episode(ExploringController(base, args.epsilon, seed=n), n, args.turns,
                            run_dir=Path(args.out) / f"sim-{n:03d}")
        print(f"wrote {args.episodes} toy episodes to {args.out}; "
              f"next: python -m hoi4_harness.micro report {args.out}/sim-* --horizon 30")
        return 0
    if args.cmd == "extract":
        rows = D.extract_runs([Path(r) for r in args.runs], args.horizon)
        D.write_jsonl(rows, Path(args.out))
        print(json.dumps(D.summary(rows)))
        return 0
    try:
        return _learn(args)
    except MissingExtra as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _fit(args, train):
    if getattr(args, "model_type", "linear") == "boosted":
        return BoostedBandit(mode=args.mode).fit(train, args.l2)
    return LinearBandit().fit(train, args.l2)


def _learn(args) -> int:
    rows = _rows(args.data, args.horizon)
    train, test = split_episodes(rows, args.test_fraction)
    if args.cmd == "train":
        model = _fit(args, train)
        model.save(Path(args.out))
        print(f"trained on {len(train)} rows ({len(test)} held out); counts {model.counts}")
        return 0
    model = load_model(Path(args.model)) if args.cmd == "eval" else _fit(args, train)
    report = evaluate(model, train, test)
    print(json.dumps(report, indent=1) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
