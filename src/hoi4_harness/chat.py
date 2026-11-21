"""Chat between the person and the LLM countries, and between LLM countries, in single player (#133).

The game has no text input a script can read, so the chat lives beside it: one append-only JSON
lines file every party reads and writes, ``~/.hoi4-harness/chat.jsonl`` unless ``HOI4_CHAT`` or
``--chat`` says otherwise. A record is ``{"id", "ts", "date", "from", "to", "text"}``:

* ``from``: ``person`` or a country tag;
* ``to``: ``global`` (everyone reads it), ``person``, or a country tag (direct).

The person writes with ``hoi4-harness chat``. Each LLM country gets the lines addressed to it or to
global in its brief, is woken by a new one addressed to it, and answers with ``send_message``.
Talk is not an action: what a country says binds nothing, and the game's state is still what
counts. A country only ever sees the global channel and its own direct lines, so a message is the
only thing that crosses between two strategists.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .home import home_dir

MAX_CHARS = 800
#: Lines of context the brief shows besides the new ones.
CONTEXT = 6


def default_path() -> Path:
    env = os.environ.get("HOI4_CHAT")
    return Path(env) if env else home_dir() / "chat.jsonl"


@dataclass(frozen=True)
class Message:
    id: int
    ts: float
    date: str | None
    frm: str
    to: str
    text: str

    def line(self) -> str:
        where = "to everyone" if self.to == "global" else f"to {self.to}"
        when = f"{self.date} " if self.date else ""
        return f"[{when}{self.frm} {where}] {self.text}"


def _norm(party: str) -> str:
    party = (party or "").strip()
    low = party.lower()
    if low in ("global", "all", "everyone"):
        return "global"
    if low in ("person", "player", "me", "human"):
        return "person"
    return party.upper()


class ChatLog:
    def __init__(self, path: Path | None = None, clock=time.time):
        self.path = Path(path) if path else default_path()
        self.clock = clock

    def messages(self) -> list[Message]:
        try:
            raw = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in raw:
            try:
                r = json.loads(line)
                out.append(Message(int(r["id"]), float(r.get("ts", 0)), r.get("date"),
                                   str(r["from"]), str(r["to"]), str(r["text"])))
            except (ValueError, KeyError, TypeError):
                continue          # a half-written last line: the next read has it whole
        return out

    def post(self, frm: str, to: str, text: str, date: str | None = None) -> Message:
        text = " ".join((text or "").split())
        if not text:
            raise ValueError("an empty message")
        frm, to = _norm(frm), _norm(to)
        if to != "global" and to != "person" and not (len(to) == 3 and to.isalnum()):
            raise ValueError(f"{to!r} is not global, person or a country tag")
        known = self.messages()
        message = Message((known[-1].id + 1) if known else 1, self.clock(), date, frm, to,
                          text[:MAX_CHARS])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"id": message.id, "ts": message.ts, "date": message.date,
                                     "from": message.frm, "to": message.to,
                                     "text": message.text}, ensure_ascii=False) + "\n")
        return message

    def visible_to(self, party: str) -> list[Message]:
        """What ``party`` reads: global, its direct lines, and what it sent itself."""
        party = _norm(party)
        return [m for m in self.messages() if m.to in ("global", party) or m.frm == party]


class ChatChannel:
    """One LLM country's end of the chat: the brief section, the wake, and send_message."""

    def __init__(self, log: ChatLog, tag: str, seen_path: Path | None = None):
        self.log = log
        self.tag = _norm(tag)
        self.seen_path = Path(seen_path) if seen_path else None
        self.seen = self._load_seen()

    def _load_seen(self) -> int:
        if self.seen_path and self.seen_path.exists():
            try:
                return int(json.loads(self.seen_path.read_text(encoding="utf-8")).get(self.tag, 0))
            except (OSError, ValueError, AttributeError):
                return 0
        # A new run starts after what is already there: old talk is context, not news.
        known = self.log.messages()
        return known[-1].id if known else 0

    def _save_seen(self) -> None:
        if self.seen_path:
            self.seen_path.parent.mkdir(parents=True, exist_ok=True)
            self.seen_path.write_text(json.dumps({self.tag: self.seen}), encoding="utf-8")

    def new(self) -> list[Message]:
        return [m for m in self.log.visible_to(self.tag) if m.id > self.seen and m.frm != self.tag]

    def wake_reason(self) -> str | None:
        """A new line addressed to this country (direct) wakes it; global talk waits for a turn."""
        direct = [m for m in self.new() if m.to == self.tag]
        if not direct:
            return None
        who = ", ".join(sorted({m.frm for m in direct}))
        return f"a message from {who}"

    def mark_seen(self) -> None:
        known = self.log.visible_to(self.tag)
        if known:
            self.seen = max(self.seen, known[-1].id)
            self._save_seen()

    def brief_lines(self, state=None) -> list[str]:
        visible = self.log.visible_to(self.tag)
        if not visible:
            return []
        fresh = [m for m in visible if m.id > self.seen and m.frm != self.tag]
        older = [m for m in visible if m not in fresh][-CONTEXT:]
        lines = ["Messages (reply with send_message to=person|global|<TAG>; talk binds nothing, "
                 "acts do):"]
        lines += ["  " + m.line() for m in older]
        lines += ["  NEW " + m.line() for m in fresh]
        return lines

    def send(self, to: str, text: str, date: str | None = None) -> Message:
        message = self.log.post(self.tag, to, text, date)
        self.seen = max(self.seen, message.id)
        self._save_seen()
        return message
