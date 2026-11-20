"""Production: keep the military factories on lines, in ratio.

Absorbs the old reflex (``reflex``: idle factories go to the biggest line, which
``Policy.reflex_actions`` still calls) and extends it (``decide``): idle factories are handed to
the equipment furthest under its share of the total, with shares from
``auto_options.production.ratios`` (default: infantry equipment 0.6, support 0.15, artillery
0.15, anti-air 0.1). A line the game refuses (a technology missing, a line that cannot be
changed) is left alone for 60 days, and the leftover goes to the biggest running line.

Equipment ids are the usual vanilla ones and are not checked against a live playset. Over the
console a line can only be OPENED, not changed (docs/headless.md), so on a live headless run the
top-up of an existing line is refused and reported as such.
"""

from __future__ import annotations

from ..types import ActionCall, GameState
from .base import Script, call

DEFAULT_RATIOS = {"infantry_equipment_1": 0.6, "support_equipment_1": 0.15,
                  "artillery_equipment_1": 0.15, "anti_air_equipment_1": 0.1}


class ProductionScript(Script):
    domain = "production"
    name = "production ratios"

    def __init__(self, ctx=None):
        super().__init__(ctx)
        self.ratios = dict((self.ctx.option("production") or {}).get("ratios") or DEFAULT_RATIOS)

    def reflex(self, state: GameState) -> list[ActionCall]:
        """The old reflex, exactly: idle military factories go to the biggest line."""
        if state.known("military_factories") and state.production:
            assigned = sum(line.factories for line in state.production)
            idle = state.military_factories - assigned
            if idle > 0:
                main = max(state.production, key=lambda line: line.factories)
                return [ActionCall(
                    name="set_production",
                    arguments={"equipment": main.equipment, "factories": main.factories + idle},
                    rationale=f"reflex: {idle} military factories were idle",
                )]
        return []

    def decide(self, state: GameState, objective: str, memory) -> list[ActionCall]:
        if not state.known("military_factories") or state.military_factories <= 0:
            return []
        if not state.known("production"):
            return []
        idle = state.military_factories - sum(line.factories for line in state.production)
        if idle <= 0:
            return []
        have = {line.equipment: line.factories for line in state.production}
        total = state.military_factories
        want = {eq: share * total for eq, share in self.ratios.items()
                if not self.blocked(eq, state.date)}
        add: dict[str, int] = {}
        for _ in range(idle):
            under = {eq: target - have.get(eq, 0) - add.get(eq, 0) for eq, target in want.items()}
            best = max(under, key=lambda eq: (under[eq], -list(want).index(eq)), default=None)
            if best is None or under[best] <= 0:
                break
            add[best] = add.get(best, 0) + 1
        out = [call("set_production", f"{n} idle factories go to the line under its share",
                    equipment=eq, factories=have.get(eq, 0) + n) for eq, n in add.items()]
        left = idle - sum(add.values())
        if left > 0 and state.production:
            main = max(state.production, key=lambda line: line.factories)
            if main.equipment not in add:
                out.append(call("set_production", f"{left} idle factories go to the biggest line",
                                equipment=main.equipment, factories=main.factories + left))
        return out

    @staticmethod
    def key(call_: ActionCall) -> str | None:
        return str(call_.arguments.get("equipment")) if call_.name == "set_production" else None

    def idle_note(self, state: GameState) -> str:
        return "every military factory is on a line"
