"""National focus: follow a configured path, else a scored pick.

The path is ``auto_options.focus_path`` (a list of focus ids), else the entry for the country's
tag in ``data/focus.json`` (none ship). With neither, and a playset index, the script scores the
focuses whose prerequisites are done by keywords in their ids (industry and rearmament first) and
takes the best; without an index it has nothing to go on and does nothing. A focus the game
refuses is left alone for 60 days.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..types import ActionCall, GameState
from .base import Script, call

DATA = Path(__file__).parent / "data" / "focus.json"
#: id fragment -> score, for the scored pick. Industry compounds, then the army, then the rest.
KEYWORDS = {"industr": 5, "factory": 5, "construction": 4, "infrastructure": 3, "rearm": 4,
            "army": 3, "military": 3, "research": 3, "mobilis": 2, "mobiliz": 2, "doctrine": 2,
            "air": 1, "navy": 1, "naval": 1}


def paths() -> dict[str, list[str]]:
    return json.loads(DATA.read_text(encoding="utf-8"))["paths"]


def score(focus_id: str) -> int:
    low = focus_id.lower()
    return sum(points for word, points in KEYWORDS.items() if word in low)


class FocusScript(Script):
    domain = "focus"
    name = "focus path"

    def path_for(self, tag: str) -> list[str]:
        own = self.ctx.option("focus_path")
        if isinstance(own, list):
            return [str(f) for f in own]
        return list(paths().get((tag or "").upper(), []))

    def decide(self, state: GameState, objective: str, memory) -> list[ActionCall]:
        if state.national_focus or not state.known("national_focus"):
            return []
        tag = state.country if state.country != "___" else (self.ctx.config.country or "")
        done = set(state.completed_focuses)
        for focus in self.path_for(tag):
            if focus not in done and not self.blocked(focus, state.date):
                return [call("set_national_focus", "the focus path says so", focus_id=focus)]
        index = self.ctx.index
        if index is not None:
            options = [f for f in index.available_focuses(tag, done)
                       if not self.blocked(f, state.date)]
            if options:
                best = max(options, key=lambda f: (score(f), f))
                return [call("set_national_focus", f"best scored available focus ({score(best)})",
                             focus_id=best)]
        return []

    @staticmethod
    def key(call_: ActionCall) -> str | None:
        return str(call_.arguments.get("focus_id")) if call_.name == "set_national_focus" else None

    def idle_note(self, state: GameState) -> str:
        return "a focus is running" if state.national_focus else "no focus left to pick"
