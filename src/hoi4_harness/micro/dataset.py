"""Data capture: (state features, action, outcome over the next N days) rows from run directories.

Three things in a run directory carry the material, and a row says which one it came from:

* ``micro_decisions.jsonl`` (written by a controller running under the LLM, runtime.py): the real
  per-sector features and the order given. These are the rows worth training on.
* ``transcript.jsonl``: every ``set_front_order`` the LLM gave. The per-sector forces were not
  logged, so the features are the theatre-wide "Front picture" line of that turn's brief
  (own against foe divisions in the visible states), plus the movement from ``front_history.json``
  when the run kept one. The state the order names is recorded but its sector is not observed.
* ``campaign_progress.jsonl``: the outcome series (states controlled, divisions) one row per turn.

The outcome is the change in controlled states over the horizon, less a small charge for
divisions lost. It is a theatre-wide outcome, not the outcome of the one order: with the LLM
giving several orders a turn, an order and a result are confounded. The extractor says so in each
row (``source``) rather than hiding it; docs/micro-controller.md has the consequences.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..frontreport import HISTORY_FILE, days_between, ordinal
from .features import feature_dict
from .interface import ORDERS

DECISIONS_FILE = "micro_decisions.jsonl"
HORIZON_DAYS = 60
DIVISION_LOSS_CHARGE = 0.02
_PICTURE = re.compile(r"Front picture \([^)]*\): own (\d+) vs foe (\d+)")
_ORDER_DAYS = 90


def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not Path(path).is_file():
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def outcome(series: list[dict], date: str, horizon: int = HORIZON_DAYS) -> float | None:
    """Change in controlled states from the sample at ``date`` to the first one ``horizon`` days
    later, less DIVISION_LOSS_CHARGE per division lost. None when the run ended before that (censored)."""
    t0 = ordinal(date)
    if t0 is None:
        return None
    pts = [(ordinal(r.get("date")), r) for r in series]
    pts = sorted(((o, r) for o, r in pts if o is not None and r.get("controlled_states") is not None),
                 key=lambda x: x[0])
    before = [r for o, r in pts if o <= t0]
    after = [r for o, r in pts if o >= t0 + horizon]
    if not before or not after:
        return None
    a, b = before[-1], after[0]
    lost = max(0, (a.get("divisions") or 0) - (b.get("divisions") or 0))
    return float(b["controlled_states"] - a["controlled_states"]) - DIVISION_LOSS_CHARGE * lost


def _history_features(history: dict, date: str) -> tuple[int, int, int | None]:
    """Provinces gained and lost over the 90 days before ``date`` (all fronts). How long the line
    has been unchanged is not kept in the history file per date, so that is None."""
    gained = lost = 0
    for snap in history.get("snapshots", []):
        gap = days_between(snap.get("date"), date)
        if gap is None or gap < 0 or gap >= _ORDER_DAYS:
            continue
        for front in snap.get("fronts", {}).values():
            if front.get("baseline"):
                continue
            gained += front.get("gained", 0)
            lost += front.get("lost", 0)
    return gained, lost, None


def extract_run(run_dir: Path, horizon: int = HORIZON_DAYS) -> list[dict]:
    """Every decision of one run with its features and outcome. Censored decisions (no sample
    ``horizon`` days later) are kept with ``reward`` None so counts stay honest; training drops them."""
    run_dir = Path(run_dir)
    episode = run_dir.name
    series = read_jsonl(run_dir / "campaign_progress.jsonl")
    history: dict = {}
    if (run_dir / HISTORY_FILE).is_file():
        try:
            history = json.loads((run_dir / HISTORY_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            history = {}
    rows: list[dict] = []

    for rec in read_jsonl(run_dir / DECISIONS_FILE):
        if rec.get("order") not in ORDERS or not rec.get("features"):
            continue
        rows.append({"episode": episode, "date": rec.get("date"), "source": "micro",
                     "state": rec.get("state"), "action": rec["order"],
                     "features": rec["features"], "controller": rec.get("controller"),
                     "reward": outcome(series, rec.get("date") or "", horizon),
                     "horizon_days": horizon})

    date, own, foe = None, 0, 0
    pending: dict[str, dict] = {}
    for rec in read_jsonl(run_dir / "transcript.jsonl"):
        kind = rec.get("kind")
        if kind == "observe":
            date = rec.get("date") or date
            found = _PICTURE.search(rec.get("brief") or "")
            own, foe = (int(found.group(1)), int(found.group(2))) if found else (0, 0)
        elif kind == "llm":
            for call in rec.get("tool_calls") or []:
                args = call.get("arguments") or {}
                if call.get("name") == "set_front_order" and args.get("order") in ORDERS:
                    pending[call.get("id")] = {"date": date, "state": args.get("state"),
                                               "order": args["order"], "own": own, "foe": foe}
        elif kind == "actions":
            for res in rec.get("results") or []:
                call = pending.pop(res.get("call_id"), None)
                if call is None or res.get("is_error") or not call["date"]:
                    continue
                gained, lost, unchanged = _history_features(history, call["date"])
                rows.append({
                    "episode": episode, "date": call["date"], "source": "transcript",
                    "state": call["state"], "action": call["order"],
                    "features": feature_dict(call["own"], call["foe"], gained=gained, lost=lost,
                                             unchanged_days=unchanged),
                    "controller": "llm", "reward": outcome(series, call["date"], horizon),
                    "horizon_days": horizon})
    rows.sort(key=lambda r: (ordinal(r["date"]) or 0, str(r["state"])))
    return rows


def extract_runs(run_dirs: list[Path], horizon: int = HORIZON_DAYS) -> list[dict]:
    rows: list[dict] = []
    for d in run_dirs:
        rows += extract_run(Path(d), horizon)
    return rows


def write_jsonl(rows: list[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def load_dataset(path: Path) -> list[dict]:
    return read_jsonl(path)


def summary(rows: list[dict]) -> dict:
    labelled = [r for r in rows if r.get("reward") is not None]
    by_action: dict[str, int] = {}
    for r in labelled:
        by_action[r["action"]] = by_action.get(r["action"], 0) + 1
    return {"rows": len(rows), "with_outcome": len(labelled), "censored": len(rows) - len(labelled),
            "episodes": len({r["episode"] for r in rows}),
            "sources": sorted({r["source"] for r in rows}), "by_action": by_action}
