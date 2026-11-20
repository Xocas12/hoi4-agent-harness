"""Army: a tactical controller, through the controller bridge.

``army: script`` runs one of the controllers of ``tactics/registry.py`` (``army_controller``:
``scripted`` by default, ``learned``, ``vanilla_like``, and whatever else is registered) on the
simulator world built from the front picture (``tactics/bridge.py``), and sends its orders as
``move_division`` / ``attack_province``. It needs the map (``game_dir``), the front picture
(the log reader with the mod) and an adapter that offers the order tools; with any of those
missing it does nothing and says why in the brief. The same order is not repeated for
``army_cooldown_days`` (default 3): a march takes days, and the ledger lines in the brief say
what became of it.

``ArmyDelegate`` is the army half of ``army: game`` without ``human_ai``: with nobody steering
(``steer = off``) it hands every army to the game's AI once, the way the AI-only arm of the hybrid
experiment does.
"""

from __future__ import annotations

from ..adapters.clock import days_between
from ..tactics.bridge import BridgeError, ControllerBridge
from ..tactics.registry import make
from ..types import ActionCall, GameState
from .base import Script, call


class ArmyScript(Script):
    domain = "army"
    name = "tactical controller"

    def __init__(self, ctx=None):
        super().__init__(ctx)
        self.controller_name = self.ctx.config.army_controller
        self.cooldown = int(self.ctx.option("army_cooldown_days", 3))
        self._bridge: ControllerBridge | None = None
        self._why: str | None = None
        self._sent: dict[tuple, str] = {}

    @property
    def label(self) -> str:
        return f"{self.controller_name} tactical controller"

    def _get_bridge(self) -> ControllerBridge | None:
        if self._bridge is None and self._why is None:
            loader = self.ctx.layer
            layer = loader() if loader else None
            if layer is None:
                self._why = "no map (set game_dir): the controller cannot be built"
                return None
            self._bridge = ControllerBridge(layer, make(self.controller_name))
        return self._bridge

    def decide(self, state: GameState, objective: str, memory) -> list[ActionCall]:
        bridge = self._get_bridge()
        if bridge is None:
            return []
        try:
            calls = bridge.decide(state)
        except BridgeError as exc:
            self._why = str(exc)
            return []
        self._why = None
        out = []
        for c in calls:
            key = (c.name, c.arguments["from_state"], c.arguments["to_state"])
            sent = self._sent.get(key)
            if sent is not None and days_between(sent, state.date) < self.cooldown:
                continue
            out.append(call(c.name, c.rationale or self.controller_name, **c.arguments))
        return out

    def feedback(self, call_: ActionCall, result, state: GameState) -> None:
        if result.ok or result.error_kind == "unverified":
            key = (call_.name, call_.arguments["from_state"], call_.arguments["to_state"])
            self._sent[key] = state.date

    def idle_note(self, state: GameState) -> str:
        return self._why or "no order from the controller"


class ArmyDelegate(Script):
    domain = "army"
    name = "delegate every army to the game's AI"
    extra_actions = frozenset({"delegate_army_to_ai"})

    def decide(self, state: GameState, objective: str, memory) -> list[ActionCall]:
        if state.divisions and "all" not in state.delegated_armies:
            return [call("delegate_army_to_ai", "no model is steering: the game's AI runs the army",
                         army="all", delegate=True)]
        return []

    def idle_note(self, state: GameState) -> str:
        return "armies are with the game's AI"
