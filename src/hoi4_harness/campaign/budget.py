"""The performance budget of a campaign run: real minutes per game month (issue #157).

``hoi4-harness campaign-report --run-dir runs/x --budget`` reads ``transcript.jsonl`` and
``campaign.log`` and says where the wall-clock time went, per game year:

* **model**: from the previous record to the model's reply (every ``llm`` record);
* **game**: from the last action of a turn to its ``turn_end``, the clock running (advance,
  pause probe, waiting for the date to land);
* **overhead**: everything else the harness does around a call: the state read before it
  (``observe``: saves, log reads, briefings), running the actions (console, mouse), skipped turns.

Time while the campaign was down (a crash, a restart, a resume) is not counted: a record that
follows a ``crash``, ``resumed`` or ``playset`` record starts a new stretch. A turn is counted
in the year it ended in, and only when it has both ends (a ``turn_end`` with a ``from_date``).
Game months are days / 30.4375. Memory is what the log carries: ``HOI4 holds N GB`` lines, which
the campaign writes when it reaches ``campaign_memory_limit_gb``; a run that never got there has
none, and the report says so rather than guessing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DAYS_PER_MONTH = 30.4375
#: The targets in docs/cost-and-timing.md. Minutes of wall clock per game month.
TARGET_MIN_PER_MONTH = 5.0
TARGET_OVERHEAD_MIN_PER_MONTH = 1.0
RESET_KINDS = {"crash", "resumed", "playset"}
_WAKE = re.compile(r"^\[[^\]]*\] (\d{4}-\d{2}-\d{2})\s+wake:")
_HOLDS = re.compile(r"^\[([^\]]*)\] HOI4 holds (\d+(?:\.\d+)?) GB of private memory")


@dataclass
class Bucket:
    """Seconds and game days summed over some turns."""

    turns: int = 0
    days: float = 0.0
    model: float = 0.0
    game: float = 0.0
    overhead: float = 0.0
    calls: int = 0
    memory_gb: list[float] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.model + self.game + self.overhead

    @property
    def months(self) -> float:
        return self.days / DAYS_PER_MONTH

    def per_month(self, seconds: float) -> float | None:
        """Minutes per game month for ``seconds`` of this bucket; None with no game time."""
        return seconds / 60.0 / self.months if self.months > 0 else None

    def add(self, other: Bucket) -> None:
        self.turns += other.turns
        self.days += other.days
        self.model += other.model
        self.game += other.game
        self.overhead += other.overhead
        self.calls += other.calls


@dataclass
class Budget:
    run_dir: Path
    by_year: dict[int, Bucket]
    total: Bucket
    memory: list[tuple[str, float, str]]        # (log time, GB, last game date seen)
    down_seconds: float                          # gaps skipped after a crash, resume or restart
    turns_dropped: int                           # turns without both ends

    def render(self) -> str:
        return render_budget(self)


def _days(a: str, b: str) -> float | None:
    try:
        return float((date.fromisoformat(b) - date.fromisoformat(a)).days)
    except ValueError:
        return None


def load_budget(run_dir: Path) -> Budget:
    run_dir = Path(run_dir)
    path = run_dir / "transcript.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{run_dir} has no transcript.jsonl: nothing to take a budget from")
    by_year: dict[int, Bucket] = {}
    total = Bucket()
    down = 0.0
    dropped = 0
    anchor: float | None = None
    cur = Bucket()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
            t = float(rec["t"])
        except (ValueError, KeyError, TypeError):
            continue
        kind = rec.get("kind")
        gap = t - anchor if anchor is not None else None
        if kind in RESET_KINDS:
            if gap is not None and kind != "playset" and gap > 0:
                down += gap
            cur, anchor = Bucket(), t
            continue
        anchor = t
        if gap is None:
            continue
        gap = max(gap, 0.0)
        if kind == "llm":
            cur.model += gap
            cur.calls += 1
        elif kind == "turn_end":
            cur.game += gap
            span = _days(rec.get("from_date", ""), rec.get("date", ""))
            if span is None or span <= 0:
                dropped += 1
            else:
                cur.days, cur.turns = span, 1
                year = int(str(rec["date"])[:4])
                by_year.setdefault(year, Bucket()).add(cur)
                total.add(cur)
            cur = Bucket()
        else:                                    # observe, actions, skip, anything else
            cur.overhead += gap
    memory = _memory_from_log(run_dir / "campaign.log")
    return Budget(run_dir, dict(sorted(by_year.items())), total, memory, down, dropped)


def _memory_from_log(path: Path) -> list[tuple[str, float, str]]:
    if not path.exists():
        return []
    out: list[tuple[str, float, str]] = []
    game_date = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        wake = _WAKE.match(line)
        if wake:
            game_date = wake.group(1)
            continue
        held = _HOLDS.match(line)
        if held:
            out.append((held.group(1), float(held.group(2)), game_date))
    return out


def _fmt(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def _share(part: float, whole: float) -> str:
    return f"{100 * part / whole:.0f}%" if whole > 0 else "-"


def _row(label: str, b: Bucket) -> str:
    return (f"| {label} | {b.turns} | {b.months:.1f} | {b.calls} | {_fmt(b.per_month(b.total))} | "
            f"{_fmt(b.per_month(b.model))} | {_fmt(b.per_month(b.game))} | "
            f"{_fmt(b.per_month(b.overhead))} | {_share(b.model, b.total)} |")


def render_budget(budget: Budget) -> str:
    lines = [f"# Performance budget: {budget.run_dir.name}", ""]
    if not budget.total.turns:
        return "\n".join(lines + ["No complete turn (a turn_end with a from_date) in the transcript."]) + "\n"
    lines += ["Wall-clock minutes per game month, split into model time (waiting for the reply), game "
              "time (the clock running) and overhead (reads, saves, console, briefings).", "",
              "| year | turns | game months | model calls | min/month | model | game | overhead "
              "| model share |",
              "|---|---|---|---|---|---|---|---|---|"]
    lines += [_row(str(year), b) for year, b in budget.by_year.items()]
    lines.append(_row("all", budget.total))
    t = budget.total
    wall = t.total / 3600
    over, month = t.per_month(t.overhead), t.per_month(t.total)
    lines += ["", f"{t.turns} turns, {t.months:.1f} game months, {wall:.1f} h of counted wall clock.",
              "",
              "| target | measured | met |", "|---|---|---|",
              f"| at most {TARGET_MIN_PER_MONTH:g} min per game month | {_fmt(month)} | "
              f"{'yes' if month is not None and month <= TARGET_MIN_PER_MONTH else 'NO'} |",
              f"| overhead at most {TARGET_OVERHEAD_MIN_PER_MONTH:g} min per game month | {_fmt(over)} | "
              f"{'yes' if over is not None and over <= TARGET_OVERHEAD_MIN_PER_MONTH else 'NO'} |"]
    if budget.down_seconds:
        lines.append(f"Not counted: {budget.down_seconds / 60:.0f} min the run was down (crash, "
                     "resume or restart).")
    if budget.turns_dropped:
        lines.append(f"{budget.turns_dropped} turn(s) had no usable date span and are left out.")
    lines += ["", "## Memory", ""]
    if budget.memory:
        lines += ["| log time | HOI4 private GB | game date |", "|---|---|---|"]
        lines += [f"| {when} | {gb:g} | {day or '-'} |" for when, gb, day in budget.memory]
    else:
        lines.append("The log has no `HOI4 holds N GB` line: the run never reached "
                     "campaign_memory_limit_gb, or the memory guard was off. Measure it with "
                     "`hoi4-harness memory-probe`.")
    return "\n".join(lines) + "\n"
