"""What every script controller shares.

A script controller is a small deterministic class for one domain of ``control.py``:

    decide(state, objective, memory) -> list[ActionCall]

It reads the situation report, returns the actions it wants (only ones of its own domain, and
only ones the adapter offers: the layer drops the rest), and hears how each went through
``feedback`` so it does not retry what the game refused. No model, no network, no clock.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..adapters.clock import days_between
from ..config import HarnessConfig
from ..types import ActionCall, ActionResult, GameState

#: Days a refused choice is left alone before the script tries it again.
BLOCK_DAYS = 60


@dataclass
class AutoContext:
    """What a script may be given beyond the state: the run's config, the playset index (None
    without a playset) and a loader for the map (None without one)."""

    config: HarnessConfig
    index: object | None = None
    layer: Callable[[], object] | None = None
    options: dict = field(default_factory=dict)

    def option(self, name: str, default=None):
        return self.options.get(name, default)


class Script:
    """One domain's deterministic controller."""

    domain = ""
    name = "script"
    #: Actions beyond the domain's own that this script may send (the layer drops the rest).
    extra_actions: frozenset[str] = frozenset()

    def __init__(self, ctx: AutoContext | None = None):
        self.ctx = ctx or AutoContext(HarnessConfig())
        #: choice -> date it was refused, for ``blocked``.
        self._blocked: dict[str, str] = {}

    def decide(self, state: GameState, objective: str, memory) -> list[ActionCall]:
        raise NotImplementedError

    def feedback(self, call_: ActionCall, result: ActionResult, state: GameState) -> None:
        """Hear how a call went. The default remembers a refusal of ``key(call_)``."""
        key = self.key(call_)
        if key is not None and not result.ok:
            self._blocked[key] = state.date

    @staticmethod
    def key(call_: ActionCall) -> str | None:
        return None

    def blocked(self, key: str, date: str, days: int = BLOCK_DAYS) -> bool:
        since = self._blocked.get(key)
        return since is not None and days_between(since, date) < days

    def idle_note(self, state: GameState) -> str:
        """The brief's line when the script did nothing this turn."""
        return "nothing to do"


def call(name: str, rationale: str, **arguments) -> ActionCall:
    return ActionCall(name=name, arguments=arguments, rationale=f"auto: {rationale}")
