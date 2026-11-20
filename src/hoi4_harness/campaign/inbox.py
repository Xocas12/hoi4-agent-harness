"""Guidance from the person while a campaign runs (docs/campaign.md, "Guiding a run").

``hoi4-harness guide --run-dir runs/X "Take Poland before France"`` appends one JSON line
to ``<run-dir>/guidance_inbox.jsonl``; the running campaign reads the file at the top of
every turn. The file is append-only so a writer in another process never races the reader:
an item is ``{"id", "ts", "text"}`` and a removal is a ``{"clear": [ids]}`` line.

Guidance is advice. It reaches the model as a standing block in the brief (new items
marked), and it wakes the model once; the harness never executes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

INBOX_FILE = "guidance_inbox.jsonl"
#: One item's cap, so a pasted document cannot swamp every brief for the rest of the run.
MAX_CHARS = 1200


@dataclass(frozen=True)
class Guidance:
    id: int
    ts: str
    text: str


def inbox_path(run_dir: Path | str) -> Path:
    return Path(run_dir) / INBOX_FILE


def _records(run_dir: Path | str) -> list[dict]:
    try:
        raw = inbox_path(run_dir).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in raw:
        try:
            record = json.loads(line)
        except ValueError:
            continue          # a half-written last line: the next read has it whole
        if isinstance(record, dict):
            out.append(record)
    return out


def _append(run_dir: Path | str, record: dict) -> None:
    path = inbox_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def add(run_dir: Path | str, text: str, now: datetime | None = None) -> Guidance:
    """Append one item and return it. Raises ValueError for empty text."""
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("guidance text is empty")
    text = text[:MAX_CHARS]
    ids = [r["id"] for r in _records(run_dir) if isinstance(r.get("id"), int)]
    item = Guidance(max(ids, default=0) + 1, (now or datetime.now()).isoformat(timespec="seconds"),
                    text)
    _append(run_dir, {"id": item.id, "ts": item.ts, "text": item.text})
    return item


def active(run_dir: Path | str) -> list[Guidance]:
    """The standing items, oldest first: everything added and not cleared."""
    items: dict[int, Guidance] = {}
    for record in _records(run_dir):
        if isinstance(record.get("id"), int) and isinstance(record.get("text"), str):
            items[record["id"]] = Guidance(record["id"], str(record.get("ts", "")), record["text"])
        elif isinstance(record.get("clear"), list):
            for gone in record["clear"]:
                items.pop(gone, None)
    return [items[i] for i in sorted(items)]


def clear(run_dir: Path | str, ids: list[int] | None = None) -> list[int]:
    """Remove the given items, or every standing one; returns the ids removed."""
    standing = [g.id for g in active(run_dir)]
    gone = standing if ids is None else [i for i in ids if i in standing]
    if gone:
        _append(run_dir, {"clear": gone})
    return gone


def expire(run_dir: Path | str, days: float, now: datetime | None = None) -> list[int]:
    """Remove items older than ``days`` days (wall clock, by their timestamp)."""
    cutoff = (now or datetime.now()) - timedelta(days=days)
    old = []
    for item in active(run_dir):
        try:
            when = datetime.fromisoformat(item.ts)
        except ValueError:
            continue
        if when < cutoff:
            old.append(item.id)
    return clear(run_dir, old)


HEADING = ("PLAYER GUIDANCE (advice from the person running this campaign, standing until they "
           "clear it; weigh it against what you see, it is not an order, and say in your note "
           "when you decline part of it):")


class GuidanceBoard:
    """What the supervisor keeps: the standing items, which of them the model has not yet
    been shown, and the lines for the brief."""

    def __init__(self, run_dir: Path | str, seen: list[int] | None = None):
        self.run_dir = Path(run_dir)
        #: Ids the model has been shown in a brief.
        self.seen: set[int] = set(seen or [])
        self.items: list[Guidance] = []
        #: Ids the clock wait has already been cut short for (once each).
        self._announced: set[int] = set()

    def waiting(self) -> bool:
        """For the clock wait: True once for each batch of guidance that arrived while the
        game was running, so the wait ends and the next turn can wake the model for it.
        Once per item, so a wake the budget refuses cannot make every wait zero days long."""
        fresh = {g.id for g in active(self.run_dir)} - self.seen - self._announced
        if not fresh:
            return False
        self._announced |= fresh
        return True

    def poll(self) -> list[Guidance]:
        """Re-read the file; returns the items that are new (not yet shown to the model)."""
        self.items = active(self.run_dir)
        return self.new

    @property
    def new(self) -> list[Guidance]:
        return [g for g in self.items if g.id not in self.seen]

    def wake_reason(self) -> str | None:
        new = self.new
        if not new:
            return None
        return "guidance from the player" + (f" ({len(new)} new items)" if len(new) > 1 else "")

    def shown(self) -> None:
        """The model has read a brief that carried these items."""
        self.seen |= {g.id for g in self.items}

    def brief_lines(self, state=None) -> list[str]:
        if not self.items:
            return []
        lines = [HEADING]
        for item in self.items:
            mark = "NEW " if item.id not in self.seen else ""
            lines.append(f"  [{mark}#{item.id}, {item.ts.replace('T', ' ')}] {item.text}")
        return lines
