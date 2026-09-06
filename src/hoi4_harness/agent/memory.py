"""Campaign memory: what the agent knows that is not in the current snapshot.

Two layers, both bounded:

* a **journal** of dated lines the model wrote itself via the ``note`` action --
  intent, plans, tripwires;
* a **rolling digest** of recent turns, so the conversation itself can be reset
  every turn without the agent losing the thread.

Resetting the conversation every turn is the point. Keeping a 200-turn
transcript in context is both expensive and worse: old briefs contradict the
current one, and the model re-litigates settled decisions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JournalEntry:
    date: str
    turn: int
    text: str


@dataclass
class Memory:
    max_journal: int = 12
    max_digest: int = 6
    journal: list[JournalEntry] = field(default_factory=list)
    digest: list[str] = field(default_factory=list)

    def note(self, date: str, turn: int, text: str) -> None:
        self.journal.append(JournalEntry(date=date, turn=turn, text=text.strip()))
        del self.journal[: max(0, len(self.journal) - self.max_journal)]

    def record_turn(self, date: str, actions: list[str]) -> None:
        summary = ", ".join(actions) if actions else "no action"
        self.digest.append(f"{date}: {summary}")
        del self.digest[: max(0, len(self.digest) - self.max_digest)]

    def context_block(self) -> str:
        """Compact memory text for the prompt. Empty string when there is nothing."""
        parts: list[str] = []
        if self.journal:
            parts.append(
                "Standing plan (your own notes):\n"
                + "\n".join(f"- [{e.date}] {e.text}" for e in self.journal)
            )
        if self.digest:
            parts.append("Recent turns:\n" + "\n".join(f"- {d}" for d in self.digest))
        return "\n\n".join(parts)

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "journal": [e.__dict__ for e in self.journal],
                    "digest": self.digest,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> Memory:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        memory = cls()
        memory.journal = [JournalEntry(**e) for e in data.get("journal", [])]
        memory.digest = list(data.get("digest", []))
        return memory
