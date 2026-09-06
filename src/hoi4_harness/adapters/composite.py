"""Glue one read-capable adapter to one write-capable adapter.

Every realistic configuration is a split: a savegame reader with an input-driver
writer, or a screen reader with an input-driver writer. Nothing observes and acts
through the same channel except the mock.
"""

from __future__ import annotations

from ..types import ActionCall, ActionResult, GameState
from .base import AdapterInfo, GameAdapter


class CompositeAdapter(GameAdapter):
    def __init__(self, reader: GameAdapter, writer: GameAdapter):
        self.reader = reader
        self.writer = writer
        self.supported_actions = writer.supported_actions

    def info(self) -> AdapterInfo:
        r, w = self.reader.info(), self.writer.info()
        return AdapterInfo(
            name=f"{r.name}+{w.name}",
            readable=r.readable,
            writable=w.writable,
            clock_control=w.clock_control,
            notes=f"read: {r.notes} | write: {w.notes}",
        )

    def read_state(self) -> GameState:
        return self.reader.read_state()

    def apply(self, call: ActionCall) -> ActionResult:
        return self.writer.apply(call)

    def pause(self) -> None:
        self.writer.pause()

    def resume(self, speed: int = 3) -> None:
        self.writer.resume(speed)

    def advance(self, days: int) -> GameState:
        """Let the writer run the clock, then observe through the reader.

        TODO: block until the in-game date has actually moved `days` forward, and
        cut the wait short when the reader reports a critical event.
        """
        self.writer.resume()
        state = self.reader.read_state()
        self.writer.pause()
        return state

    def close(self) -> None:
        self.reader.close()
        self.writer.close()
