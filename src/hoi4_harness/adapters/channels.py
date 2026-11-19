"""Pick the channel per action: the console (console.py) or the UI (input_driver.py).

The router is the writer a composite wraps. Which channel an action takes is
configuration (``DEFAULT_CHANNELS``: the UI for everything until the console is
verified in a live game; the console is opt-in, and only for the actions the
game has a faithful effect for), and every result says which one ran, in
``changed["channel"]``, so a transcript shows it. A console action whose
preconditions fail is refused; it does not fall back to the UI, because that
would be a different channel than the one asked for.
"""

from __future__ import annotations

from collections.abc import Callable

from ..types import ActionCall, ActionResult, GameState
from .base import AdapterInfo, GameAdapter
from .console import CHANNELS, CONSOLE_ACTIONS, DEFAULT_CHANNELS, ConsoleChannel


def resolve_channels(overrides: dict[str, str] | None) -> dict[str, str]:
    """The default map with ``overrides`` applied; an unknown channel, or the
    console for an action it has no effect for, is an error, not a silent UI."""
    channels = dict(DEFAULT_CHANNELS)
    for action, channel in (overrides or {}).items():
        if channel not in CHANNELS:
            raise ValueError(f"channel for {action!r} is {channel!r}; known: {', '.join(CHANNELS)}")
        if channel == "console" and action not in CONSOLE_ACTIONS:
            raise ValueError(
                f"{action!r} has no faithful console effect (only "
                f"{', '.join(sorted(CONSOLE_ACTIONS))} do): keep it on the ui channel")
        channels[action] = channel
    return channels


class ChannelRouter(GameAdapter):
    def __init__(self, driver, console: ConsoleChannel, channels: dict[str, str] | None = None):
        self.driver = driver
        self.console = console
        self.channels = resolve_channels(channels)
        self.supported_actions = driver.supported_actions | {
            name for name, channel in self.channels.items()
            if channel == "console" and name in CONSOLE_ACTIONS
        }

    def __getattr__(self, name):
        # Only reached for what the router lacks: the driver's config, log, etc.
        return getattr(self.__dict__["driver"], name)

    def attach_reader(self, read_state: Callable[[], GameState]) -> None:
        self.driver.attach_reader(read_state)
        self.console.attach_reader(read_state)

    def info(self) -> AdapterInfo:
        inner = self.driver.info()
        used = sorted(n for n, c in self.channels.items() if c == "console")
        return AdapterInfo(name="input", readable=False, writable=True,
                           clock_control=inner.clock_control,
                           notes=f"{inner.notes}; console channel for: {', '.join(used) or 'none'}")

    def read_state(self) -> GameState:
        return self.driver.read_state()

    def apply(self, call: ActionCall) -> ActionResult:
        channel = self.channels.get(call.name)
        if channel == "console" and call.name in CONSOLE_ACTIONS:
            return self.console.execute([call])[0]
        result = self.driver.apply(call)
        if channel is not None and "channel" not in result.changed:
            result.changed = {**result.changed, "channel": channel}
        return result

    def apply_batch(self, calls: list[ActionCall]) -> list[ActionResult]:
        """Several actions: the console ones share one file and one ``run``."""
        picked = [c for c in calls
                  if self.channels.get(c.name) == "console" and c.name in CONSOLE_ACTIONS]
        done = {}
        if picked:
            done = {id(c): r for c, r in zip(picked, self.console.execute(picked), strict=True)}
        return [done[id(c)] if id(c) in done else self.apply(c) for c in calls]

    def pause(self) -> None:
        self.driver.pause()

    def resume(self, speed: int = 3) -> None:
        self.driver.resume(speed)

    def advance(self, days: int) -> GameState:
        return self.driver.advance(days)

    def close(self) -> None:
        self.driver.close()
