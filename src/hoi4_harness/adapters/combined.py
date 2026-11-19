"""The headless reader that uses both eyes: the log every tick, a text save on demand.

The mod's telemetry in ``game.log`` is free and fresh (a state line a day) but
says nothing about production, the construction queue, the divisions or which
focuses are done: no trigger or variable exposes them. A text save has all of
that and is expensive (the console's ``savegame`` writes 66 MB and the file
appears a few seconds later; ``savegame.read_save`` takes ~1.4 s to read the
blocks that matter). So this reader keeps the log as the live view and *sits a
save on top of it*:

* **the log wins** for what it has: political power, stability, war support,
  factories, manpower, the running focus and research, the wars, the events;
* **the save fills** what the log cannot see (production lines, the construction
  queue, divisions, completed focuses, fuel), and anything the log has not yet
  said (the first ticks after a load);
* a field that came from the save is as old as the save. ``state.raw["save_date"]``
  says when, and the brief says it, so a stale queue is not read as a current one.

When a save is taken is decided elsewhere, never inside a poll: at the start, by
the console channel around any action that has to be verified through the save
(construction, production), and every ``every_days`` in-game days otherwise
(``HeadlessComposite.read_state``, once a turn). Reading never sends a key.

A save that cannot be used (binary, never appeared, unreadable) *invalidates* what
the last one said, so the fields it filled turn unknown instead of staying as a
confident stale answer; the reason is kept in ``last_error`` for the result of
whatever needed it.
"""

from __future__ import annotations

import copy
import datetime as dt
import time
from collections.abc import Callable
from pathlib import Path

from ..identifiers import IdentifierIndex
from ..types import GameState
from .base import AdapterInfo, GameAdapter
from .logtail import LogTailAdapter
from .savegame import SaveGameAdapter, find_save_dir, read_save, save_date

#: What a save contributes that the log cannot: always taken from the save.
SAVE_ONLY = ("production", "construction", "divisions", "completed_focuses", "fuel")
#: What the log has once it has ticked. A save fills them only until then.
LOG_FIRST = ("political_power", "stability", "war_support", "manpower", "civilian_factories",
             "military_factories", "dockyards", "national_focus", "research")


class SaveUnavailable(Exception):
    """A save was asked for and none usable came back."""


class CombinedReader(GameAdapter):
    """``log`` for the live numbers, a console-made text save for the rest."""

    supported_actions = frozenset()

    def __init__(
        self,
        log: LogTailAdapter,
        save_dir: Path | None,
        *,
        index: IdentifierIndex | None = None,
        sleep: Callable[[float], None] = time.sleep,
        every_days: int = 7,
        timeout: float = 30.0,
        poll: float = 0.5,
    ):
        self.log = log
        self.save_dir = find_save_dir(save_dir)
        self.index = index
        self._sleep = sleep
        self.every_days = every_days
        self.timeout = timeout
        self.poll = poll
        #: Sends the console's ``savegame``; set by the builder, which owns the console.
        self.take: Callable[[], None] | None = None
        #: False in a dry run: nothing is sent, so no save will ever appear.
        self.enabled = True
        self._cache: GameState | None = None
        self._cache_date: str | None = None
        self._cache_path: Path | None = None
        self._capital: str | None = None
        self._mark_ns = 0
        #: Why the last save failed (None when it worked); shown on results.
        self.last_error: str | None = None
        self.saves_taken = 0
        self.last_read_seconds: float | None = None

    # --- the save ---------------------------------------------------------------

    def _newest_ns(self) -> int:
        if not self.save_dir or not self.save_dir.is_dir():
            return 0
        return max((p.stat().st_mtime_ns for p in self.save_dir.glob("*.hoi4")), default=0)

    def mark(self) -> None:
        """Remember the newest save there is now; ``collect`` wants one newer."""
        self._mark_ns = self._newest_ns()

    def fresh_for(self, iso_date: str | None) -> bool:
        """Whether the cached save is from this in-game day."""
        return self._cache is not None and iso_date is not None and self._cache_date == iso_date

    def due(self, iso_date: str | None) -> bool:
        """Whether a save should be taken now, outside any action: none yet, or the
        last one is ``every_days`` old (0 turns the periodic one off)."""
        if self._cache is None or self._cache_date is None:
            return True
        if not self.every_days or iso_date is None:
            return False
        try:
            age = (dt.date.fromisoformat(iso_date) - dt.date.fromisoformat(self._cache_date)).days
        except ValueError:
            return False
        return age >= self.every_days

    def refresh(self) -> str | None:
        """Take a save through the console and read it. None when it worked, else why not."""
        if not self.enabled:
            return None
        if self.take is None:
            return self._fail("no console to ask for a save with")
        self.mark()
        try:
            self.take()
        except Exception as exc:   # noqa: BLE001 - not focused, no folder, a key error: all "no save"
            return self._fail(f"the savegame command could not be sent: {exc}")
        return self.collect()

    def collect(self) -> str | None:
        """Wait for the save the console was told to write, and read it."""
        try:
            path = self._await_new()
            started = time.perf_counter()
            raw = read_save(path)
            self.last_read_seconds = time.perf_counter() - started
        except (SaveUnavailable, ValueError, OSError) as exc:
            return self._fail(str(exc))
        state = SaveGameAdapter(self.save_dir)._to_state(raw)
        if not state.country or state.country == "___":
            return self._fail(f"{path.name} names no player country")
        self._cache, self._cache_path = state, path
        self._cache_date = state.date
        country = raw.get("country")
        self._capital = str(country.get("capital")) if isinstance(country, dict) else None
        self.last_error = None
        self.saves_taken += 1
        return None

    def invalidate(self, reason: str) -> None:
        self._cache = self._cache_date = self._cache_path = None
        self.last_error = reason

    def _fail(self, reason: str) -> str:
        self.invalidate(reason)
        return reason

    def _await_new(self) -> Path:
        """The save the game wrote after ``mark``: newer by modification time (two
        saves in one game hour share a name), and no longer growing."""
        if not self.save_dir:
            raise SaveUnavailable(
                "no save folder found: set HOI4_SAVE_DIR (or save_dir) to the folder holding *.hoi4")
        waited, last = 0.0, None
        while True:
            newer = [p for p in self.save_dir.glob("*.hoi4") if p.stat().st_mtime_ns > self._mark_ns]
            if newer:
                path = max(newer, key=lambda p: (p.stat().st_mtime_ns, p.name))
                stat = path.stat()
                signature = (path, stat.st_size, stat.st_mtime_ns)
                if signature == last and stat.st_size > 0:
                    return path
                last = signature
            if waited >= self.timeout:
                raise SaveUnavailable(
                    f"no new save appeared in {self.save_dir} within {self.timeout:g} s of the "
                    "savegame command (is the console open or the game stuck on a dialog?)"
                    if not newer else f"{path.name} was still being written after {self.timeout:g} s")
            self._sleep(self.poll)
            waited += self.poll

    def maybe_save(self) -> None:
        """The start-of-run and periodic save. Never raises: a save that fails
        leaves the fields unknown and the reason in ``last_error``."""
        try:
            today = self.log.peek_state().date
        except (RuntimeError, FileNotFoundError):
            return          # no telemetry yet: nothing to date a save against
        if self.due(today):
            self.refresh()

    # --- the merged view ------------------------------------------------------

    def info(self) -> AdapterInfo:
        base = self.log.info()
        return AdapterInfo(
            name="log+save", readable=True, writable=False, clock_control=False,
            notes=f"{base.notes}; text saves from {self.save_dir or 'no save folder'} "
                  f"every {self.every_days or 'no'} day(s) and around verified actions")

    def read_state(self) -> GameState:
        return self._merge(self.log.read_state())

    def peek_state(self) -> GameState:
        return self._merge(self.log.peek_state())

    def acknowledge_events(self, events) -> None:
        self.log.acknowledge_events(events)

    def _merge(self, live: GameState) -> GameState:
        merged = copy.copy(live)
        merged.unknown_fields = list(live.unknown_fields)
        saved = self._cache
        if saved is None:
            return merged
        for name in SAVE_ONLY:
            if saved.known(name):
                setattr(merged, name, copy.deepcopy(getattr(saved, name)))
                self._known(merged, name)
        for name in LOG_FIRST:
            if not merged.known(name) and saved.known(name):
                setattr(merged, name, copy.deepcopy(getattr(saved, name)))
                self._known(merged, name)
        if self._cache_date and self.index is not None:
            for item in merged.construction:
                info = self.index.states.get(item.state)
                if info is not None:
                    item.state = info.name
        merged.raw = {**live.raw, "save_date": self._cache_date}
        return merged

    @staticmethod
    def _known(state: GameState, name: str) -> None:
        if name in state.unknown_fields:
            state.unknown_fields.remove(name)

    def capital_state_name(self) -> str | None:
        """The name of the state the capital is in, from the last save."""
        if self._capital is None:
            return None
        info = self.index.states.get(self._capital) if self.index is not None else None
        return info.name if info is not None else self._capital

    def apply(self, call):
        return self.unsupported(call)

    def advance(self, days: int) -> GameState:
        return self.log.advance(days)

    def close(self) -> None:
        self.log.close()


__all__ = ["CombinedReader", "SaveUnavailable", "save_date"]
