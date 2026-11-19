"""What keeps an unattended run honest about the game it is playing.

Two failures an overnight run must not sit through: the game is gone (its process
died, or it hung with the log silent), and the game is not in front (another window
has the keyboard, so every key would land somewhere else). Neither is something to
retry blindly, and neither may end in typing into the wrong window.

``Watchdog`` is called from inside the clock wait (``CompositeAdapter.on_poll``), so a
hang is noticed while the harness is waiting for it, not on the next turn.
``PatientFocus`` wraps the focus check the key layer already gates every key on: it
waits, retrying every minute, instead of failing, and gives up with ``GameLost`` only
after a long time. Neither presses a key, and neither presses Escape.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..adapters.base import GameLost
from ..adapters.window import FocusCheck

#: How often, at most, the watchdog looks (the process list is not free).
CHECK_EVERY_SECONDS = 15.0


def process_running(name: str) -> bool | None:
    """Whether a process called ``name`` is running; None where that cannot be told.

    Windows only (``tasklist``). An unknown answer is never read as 'dead': the
    silence check still catches a game that is gone.
    """
    if not name or sys.platform != "win32":
        return None
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return name.lower() in out.stdout.lower()


class Watchdog:
    """Raises ``GameLost`` when the process is gone or the log stops growing."""

    def __init__(
        self,
        log_path: Path | None,
        *,
        silence_seconds: float = 1200.0,
        process_alive: Callable[[], bool | None] | None = None,
        now: Callable[[], float] = time.monotonic,
        check_every: float = CHECK_EVERY_SECONDS,
    ):
        self.log_path = Path(log_path) if log_path else None
        self.silence_seconds = silence_seconds
        self.process_alive = process_alive
        self.now = now
        self.check_every = check_every
        self._signature: tuple[int, int] | None = None
        self._grew_at = now()
        self._checked_at = float("-inf")

    def _log_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.log_path.stat() if self.log_path else None
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns) if stat else None

    def reset(self) -> None:
        """The game is about to run: time silence from now."""
        self._signature = self._log_signature()
        self._grew_at = self.now()
        self._checked_at = float("-inf")

    def check(self) -> None:
        now = self.now()
        if now - self._checked_at < self.check_every:
            return
        self._checked_at = now
        self.check_process()
        signature = self._log_signature()
        if signature != self._signature:
            self._signature, self._grew_at = signature, now
            return
        quiet = now - self._grew_at
        if self.silence_seconds and quiet >= self.silence_seconds:
            raise GameLost(
                f"game.log has not grown in {quiet / 60:.0f} minutes while the game was "
                "running: it is hung, the mod is not reporting for the player, or it is gone")

    def check_process(self) -> None:
        if self.process_alive is not None and self.process_alive() is False:
            raise GameLost("the game process is gone")


class PatientFocus:
    """A focus check that waits for the game to come back to the front.

    ``check`` is the real check (``window.check_focus``) or a fake's. A game that is
    not focused is waited for: every ``retry_seconds`` it looks again, and says so
    through ``log``, until it is focused (and the caller types) or ``max_wait_seconds``
    have gone by in all (``GameLost``). A platform that cannot tell which window has
    the focus is not waited for: the answer is passed on as it is, and the key layer
    refuses to send, as it always has.
    """

    def __init__(
        self,
        check: Callable[[str], FocusCheck],
        *,
        sleep: Callable[[float], None] = time.sleep,
        retry_seconds: float = 60.0,
        max_wait_seconds: float = 6 * 3600.0,
        log: Callable[[str], None] | None = None,
        take: Callable[[str], bool] | None = None,
    ):
        #: Opt-in (``campaign_take_focus``): asked to bring the window to the front each time
        #: it is found elsewhere, before waiting. None: wait, and never take the keyboard.
        self.take = take
        self.check, self.sleep = check, sleep
        self.retry_seconds, self.max_wait_seconds = retry_seconds, max_wait_seconds
        self.log = log
        self.waited = 0.0
        #: How many separate times the game was found out of focus.
        self.episodes = 0

    def __call__(self, title: str) -> FocusCheck:
        result = self.check(title)
        if result.focused or not result.supported:
            return result
        self.episodes += 1
        started = self.waited
        while not result.focused:
            if self.waited >= self.max_wait_seconds:
                raise GameLost(
                    f"the game window has not come back to the front in "
                    f"{self.waited / 60:.0f} minutes ({result.reason})")
            if self.take is not None:
                taken = self.take(title)
                if self.log:
                    self.log("campaign_take_focus is on: "
                             + ("asked Windows to bring the game window to the front"
                                if taken else "could not bring the game window to the front"))
                result = self.check(title)
                if result.focused or not result.supported:
                    break
            if self.log:
                self.log(f"waiting for the game window to take the focus back "
                         f"({result.reason}); {(self.waited - started) / 60:.0f} min so far")
            self.sleep(self.retry_seconds)
            self.waited += self.retry_seconds
            result = self.check(title)
            if not result.supported:
                return result
        if self.log:
            self.log(f"the game window is back in front after {(self.waited - started) / 60:.0f} min")
        return result
