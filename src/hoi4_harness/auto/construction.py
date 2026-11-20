"""Construction: keep the queue busy, with a rule set for what to build.

This absorbs the old reflex (``reflex``: an empty queue gets two civilian factories in the
capital, which ``Policy.reflex_actions`` still calls) and extends it (``decide``):

* the queue is kept at ``queue_target`` items (default 3), a batch of ``batch`` (2) at a time;
* civilian factories first (they compound) while it is peace and before ``military_from``
  (default 1937-07-01);
* from that date, or as soon as there is a war, military factories until they are
  ``military_ratio`` (0.6) of the civilian ones;
* infrastructure when a front reports supply under the low-supply line and none is queued;
* in ``state`` (default ``capital``, which the adapters resolve).

Options: ``auto_options.construction`` with those keys. The state does not say which states
have room, so a refused build is simply retried elsewhere by the operator's ``state`` option.
"""

from __future__ import annotations

from collections import Counter

from ..observation.fronts import LOW_SUPPLY
from ..types import ActionCall, GameState
from .base import Script, call

DEFAULTS = {"state": "capital", "queue_target": 3, "batch": 2, "military_from": "1937-07-01",
            "military_ratio": 0.6}


class ConstructionScript(Script):
    domain = "construction"
    name = "construction rules"

    def __init__(self, ctx=None):
        super().__init__(ctx)
        self.opts = {**DEFAULTS, **(self.ctx.option("construction") or {})}

    def reflex(self, state: GameState) -> list[ActionCall]:
        """The old reflex, exactly: an empty queue is not allowed to stay empty."""
        if state.known("construction") and not state.construction and state.civilian_factories:
            return [ActionCall(
                name="queue_construction",
                arguments={"building": "civilian_factory", "state": "capital", "count": 2},
                rationale="reflex: construction queue was empty",
            )]
        return []

    def decide(self, state: GameState, objective: str, memory) -> list[ActionCall]:
        if not state.known("construction") or not state.civilian_factories:
            return []
        want = int(self.opts["queue_target"]) - len(state.construction)
        if want <= 0:
            return []
        queued = Counter(item.building for item in state.construction)
        civ = state.civilian_factories + queued["civilian_factory"]
        mil = state.military_factories + queued["military_factory"]
        starved = any(f.supply is not None and f.supply < LOW_SUPPLY for f in state.fronts)
        militarise = bool(state.wars) or state.date >= str(self.opts["military_from"])
        plan: Counter = Counter()
        reasons: dict[str, str] = {}
        for _ in range(want):
            if starved and not queued["infrastructure"] and not plan["infrastructure"]:
                pick, why = "infrastructure", "a front is short of supply"
            elif militarise and mil < float(self.opts["military_ratio"]) * civ:
                pick, why = "military_factory", "military factories are under the ratio"
                mil += 1
            else:
                pick, why = "civilian_factory", "civilian factories compound"
                civ += 1
            plan[pick] += 1
            reasons[pick] = why
        out = []
        batch = int(self.opts["batch"])
        for building, count in plan.items():
            if self.blocked(f"{building}@{self.opts['state']}", state.date, days=14):
                continue
            out.append(call("queue_construction", reasons[building], building=building,
                            state=str(self.opts["state"]), count=min(count, batch)))
        return out

    def feedback(self, call_, result, state) -> None:
        if not result.ok and call_.name == "queue_construction":
            self._blocked[f"{call_.arguments['building']}@{call_.arguments['state']}"] = state.date

    def idle_note(self, state: GameState) -> str:
        return "the queue is long enough" if state.known("construction") else "not observed here"
