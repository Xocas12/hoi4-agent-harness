"""``python -m hoi4_harness.micro extract|train|eval|report``: the offline loop.

    python -m hoi4_harness.micro extract runs/overnight-ger-1936 -o data/micro.jsonl
    python -m hoi4_harness.micro train data/micro.jsonl -o data/micro-model.json
    python -m hoi4_harness.micro eval data/micro.jsonl --model data/micro-model.json
    python -m hoi4_harness.micro report runs/overnight-ger-1936      # extract + train + eval in one go

``train`` fits on the earlier part of the data and ``eval`` scores on the held-out part with the
same split, so a model is never judged on rows it learned from.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import dataset as D
from .evaluate import evaluate, render
from .learn import LinearBandit, split_episodes


def _rows(paths: list[str], horizon: int) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        path = Path(p)
        rows += D.load_dataset(path) if path.is_file() else D.extract_run(path, horizon)
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hoi4_harness.micro", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
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
        if name == "train":
            p.add_argument("-o", "--out", required=True)
        if name == "eval":
            p.add_argument("--model", required=True)
        if name in ("eval", "report"):
            p.add_argument("--json", action="store_true", help="print the numbers, not the report")
    args = ap.parse_args(argv)

    if args.cmd == "extract":
        rows = D.extract_runs([Path(r) for r in args.runs], args.horizon)
        D.write_jsonl(rows, Path(args.out))
        print(json.dumps(D.summary(rows)))
        return 0
    rows = _rows(args.data, args.horizon)
    train, test = split_episodes(rows, args.test_fraction)
    if args.cmd == "train":
        model = LinearBandit().fit(train, args.l2)
        model.save(Path(args.out))
        print(f"trained on {len(train)} rows ({len(test)} held out); counts {model.counts}")
        return 0
    model = (LinearBandit.load(Path(args.model)) if args.cmd == "eval"
             else LinearBandit().fit(train, args.l2))
    report = evaluate(model, train, test)
    print(json.dumps(report, indent=1) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
