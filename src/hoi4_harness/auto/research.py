"""Research: fill idle slots from a priority list.

The lists are in ``data/research.json`` (per role or doctrine; ``auto_options.research_list``
names one, or is a list of technology ids). The state does not say which technologies can be
researched now, so the script walks the list in order, skips what is already in a slot or was
started before, and when the game (or the playset index) refuses one it leaves it alone for 60
days and takes the next. The shipped ids are a starting point: they are not checked against a
live playset.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from ..types import ActionCall, ActionResult, GameState
from .base import Script, call

DATA = Path(__file__).parent / "data" / "research.json"


@cache
def lists() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def priority(option) -> list[str]:
    """The technology ids for ``option``: a list as given, a name from the data file, or the
    file's default."""
    data = lists()
    if isinstance(option, list):
        return [str(t) for t in option]
    name = option or data["default"]
    if name not in data["lists"]:
        raise KeyError(f"no research list {name!r}: one of {', '.join(sorted(data['lists']))}")
    return list(data["lists"][name])


class ResearchScript(Script):
    domain = "research"
    name = "research priority list"

    def __init__(self, ctx=None):
        super().__init__(ctx)
        self.order = priority(self.ctx.option("research_list"))
        self.started: set[str] = set()

    def decide(self, state: GameState, objective: str, memory) -> list[ActionCall]:
        if not state.known("research"):
            return []
        idle = [slot for slot in state.research if slot.technology is None]
        if not idle:
            return []
        busy = {slot.technology for slot in state.research if slot.technology}
        picks = [t for t in self.order
                 if t not in busy and t not in self.started and not self.blocked(t, state.date)]
        return [call("start_research", f"slot {slot.index} was idle", technology=tech)
                for slot, tech in zip(idle, picks, strict=False)]

    def feedback(self, call_: ActionCall, result: ActionResult, state: GameState) -> None:
        tech = self.key(call_)
        if tech is None:
            return
        if result.ok:
            self.started.add(tech)
        else:
            self._blocked[tech] = state.date

    @staticmethod
    def key(call_: ActionCall) -> str | None:
        return str(call_.arguments.get("technology")) if call_.name == "start_research" else None

    def idle_note(self, state: GameState) -> str:
        if not state.known("research"):
            return "research is not observed here"
        if all(s.technology for s in state.research):
            return "no idle slot"
        return "an idle slot, but nothing left on the list"
