"""Input safety for rival mode: when may the harness type, and when must it stop.

A person is at the keyboard of the same machine. Synthetic keys land in whatever the game is
showing, so the harness types only in a window the person's own behaviour makes safe, and
checks again between every key it sends. Three policies decide when a window is open
(``rival_act_when``):

* ``paused`` -- the game has been paused by the person for a while (the in-game date in
  game.log has stood still), the game window is in front, and nobody has touched the keyboard
  or the mouse for a while (Windows' ``GetLastInputInfo``, injectable);
* ``handover`` -- only while the person has granted a window with ``hoi4-harness handover``
  (the grant file of co-op, handover.py);
* ``never`` -- no window ever opens; what the model asks for is shown, not run.

Nothing here is a guarantee, and docs/rival.md says where it can fail. The checks it makes:

* **before the first key**: the policy's own test (``WindowPolicy.check``);
* **between keys**: ``KeyWatch`` is called by the input driver before every key and every
  typed character. It re-asks the policy (did the date move, did anyone else press something,
  did the window lose the focus, was the grant withdrawn) and raises ``InputInterrupted`` at
  the next key. The console key that *closes* a console this window opened is the one key
  that always goes through, because an open console swallows the person's keys; nothing else
  is sent, and Escape never is.

The harness's own keys show up in ``GetLastInputInfo`` too (SendInput is input). So the guard
does not compare idle time: it remembers the last-input stamp right after each key event it sent
(``ScanCodeKeys.on_send``), looks again right before the next one (``before_send``) and treats
any other stamp as the person. Whatever the person does between two of our key events is seen;
what is not seen is input in the milliseconds between that look and the event itself.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..adapters.input_driver import InputInterrupted
from ..adapters.logtail import ENGINE_DATE, parse_date
from ..adapters.window import FocusCheck

#: How much of the end of game.log is read to find the in-game date. The log can be many MB.
TAIL_BYTES = 65536

POLICIES = ("paused", "handover", "never")


# --- what the person is doing -------------------------------------------------------


class InputWatch:
    """The time of the last keyboard or mouse input on this machine, from the OS.

    ``last_input_ms`` returns the stamp of the last input event (any source, ours included)
    and ``now_ms`` the current time on the same clock, both in milliseconds and wrapping at
    2**32 like ``GetTickCount``. Neither given: the platform cannot say, ``supported`` is
    False, and the paused policy refuses to act rather than guess.
    """

    def __init__(self, last_input_ms: Callable[[], int | None] | None = None,
                 now_ms: Callable[[], int] | None = None):
        self._last, self._now = last_input_ms, now_ms

    @property
    def supported(self) -> bool:
        return self._last is not None and self._now is not None

    def tick(self) -> int | None:
        return self._last() if self._last is not None else None

    def idle_seconds(self) -> float | None:
        """Seconds since the last input of anyone, or None when it cannot be told."""
        stamp = self.tick()
        if stamp is None or self._now is None:
            return None
        return ((self._now() - stamp) & 0xFFFFFFFF) / 1000.0


def _windows_last_input() -> int | None:  # pragma: no cover - needs Windows
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    return int(info.dwTime)


def _windows_tick() -> int:  # pragma: no cover - needs Windows
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.GetTickCount.restype = wintypes.DWORD
    return int(kernel.GetTickCount())


def system_input_watch() -> InputWatch:
    """The OS's own idle clock where there is one (Windows); an unsupported watch elsewhere."""
    if sys.platform == "win32":
        return InputWatch(_windows_last_input, _windows_tick)  # pragma: no cover
    return InputWatch()


# --- whether the game is paused -------------------------------------------------------


class LogClock:
    """The in-game date as game.log last stamped it, and how long it has not changed.

    Every line the engine writes carries the date, so the end of the file says what day the
    game is on without the mod's help; a paused game stops moving it. ``still_for`` counts
    wall-clock seconds from the first time the harness saw the current date, so a harness
    that has just started has seen nothing for no time and never acts on a guess.
    """

    def __init__(self, log_path: Path | None, now: Callable[[], float] = time.monotonic):
        self.log_path = Path(log_path) if log_path else None
        self.now = now
        self._date: str | None = None
        self._since = now()

    def last_date(self) -> str | None:
        if self.log_path is None:
            return None
        try:
            with self.log_path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - TAIL_BYTES))
                tail = handle.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        for line in reversed(tail.splitlines()):
            match = ENGINE_DATE.match(line.strip())
            if match:
                return parse_date(match.group("date"))
        return None

    def update(self) -> str | None:
        date = self.last_date()
        if date != self._date:
            self._date, self._since = date, self.now()
        return date

    def still_for(self) -> float | None:
        """Seconds the date has stood still, None when no date has been seen."""
        self.update()
        return None if self._date is None else self.now() - self._since


# --- the policies ---------------------------------------------------------------------


@dataclass
class Gate:
    ok: bool
    #: A short stable label ("not paused", "typing"), so a log says it once, not per poll.
    kind: str = ""
    text: str = ""


class WindowPolicy:
    """When the queue may run. ``check`` opens a window, ``interrupted`` is asked between keys."""

    name = "?"
    #: The policy never types: the queue is shown, not run.
    advice_only = False

    def check(self) -> Gate:
        raise NotImplementedError

    def begin(self) -> None:
        """A window is opening: remember what the world looks like now."""

    def note_own_input(self) -> None:
        """The harness just sent a key: its own input is not the person's."""

    def interrupted(self) -> str | None:
        """A reason to stop typing now, or None."""
        return None

    def end(self) -> None:
        """The window is over (whatever way it ended)."""

    def idle(self) -> None:
        """Asked to run with nothing queued."""


class NeverPolicy(WindowPolicy):
    name = "never"
    advice_only = True

    def check(self) -> Gate:
        return Gate(False, "never", "rival_act_when is never: the harness does not type")


class HandoverPolicy(WindowPolicy):
    """A window is open while the grant file exists (``hoi4-harness handover``); deleting it
    mid-window stops the harness at the next key."""

    name = "handover"

    def __init__(self, grant_path: Path):
        self.grant_path = Path(grant_path)

    def granted(self) -> bool:
        return self.grant_path.exists()

    def check(self) -> Gate:
        if self.granted():
            return Gate(True)
        return Gate(False, "no grant", "waiting for `hoi4-harness handover`")

    def interrupted(self) -> str | None:
        return None if self.granted() else "the person took the keyboard back (grant withdrawn)"

    def end(self) -> None:
        self._release()

    def idle(self) -> None:
        # A grant with nothing queued is used up, as co-op's is: it must not linger and
        # let a later action type while the person is no longer expecting it.
        self._release()

    def _release(self) -> None:
        try:
            self.grant_path.unlink()
        except FileNotFoundError:
            pass


class PausedPolicy(WindowPolicy):
    """The person paused the game, left the keyboard and mouse alone, and the game is in front."""

    name = "paused"

    def __init__(self, clock: LogClock, watch: InputWatch, focus: Callable[[], FocusCheck],
                 paused_seconds: float = 10.0, idle_seconds: float = 5.0):
        self.clock, self.watch, self.focus = clock, watch, focus
        self.paused_seconds, self.idle_seconds = paused_seconds, idle_seconds
        self._date: str | None = None
        self._expect: int | None = None

    def check(self) -> Gate:
        still = self.clock.still_for()
        if still is None:
            return Gate(False, "no date", "no in-game date in game.log yet")
        if still < self.paused_seconds:
            return Gate(False, "not paused",
                        f"the game is not paused (the date moved {still:.0f}s ago, "
                        f"{self.paused_seconds:g}s of stillness wanted)")
        focus = self.focus()
        if not focus.focused:
            return Gate(False, "not in front", "the game window is not in front"
                        + (f" ({focus.reason})" if focus.reason else ""))
        if not self.watch.supported:
            return Gate(False, "no idle clock",
                        "this platform cannot say when the keyboard was last used, so a pause "
                        "cannot be told from someone mid-typing: use --rival-act-when handover")
        idle = self.watch.idle_seconds()
        if idle is None or idle < self.idle_seconds:
            return Gate(False, "typing",
                        f"there was keyboard or mouse input {0 if idle is None else idle:.0f}s ago "
                        f"({self.idle_seconds:g}s of quiet wanted)")
        return Gate(True)

    def begin(self) -> None:
        self._date = self.clock.last_date()
        self._expect = self.watch.tick()

    def note_own_input(self) -> None:
        self._expect = self.watch.tick()

    def interrupted(self) -> str | None:
        if self.clock.last_date() != self._date:
            return "the game was unpaused (the in-game date moved)"
        tick = self.watch.tick()
        if tick is not None and tick != self._expect:
            return "the person started using the keyboard or mouse"
        if not self.focus().focused:
            return "the game window lost the focus"
        return None


# --- the guard the input driver calls --------------------------------------------------


class KeyWatch:
    """Called by the input driver before every key and typed character of a window.

    Active only between ``begin`` and ``end`` (the queue brackets every window with them), so
    a key sent outside one is not its business. Once it has stopped a window it keeps
    stopping it: nothing more is typed, except the console key that closes a console this
    window opened.
    """

    def __init__(self, policy: WindowPolicy, console_key: str = "grave"):
        self.policy, self.console_key = policy, console_key
        self.active = False
        #: Why the current (or last) window was stopped, None when it was not.
        self.tripped: str | None = None
        self._console_open = False

    def begin(self) -> None:
        self.active, self.tripped, self._console_open = True, None, False
        self.policy.begin()

    def end(self) -> None:
        self.active = False
        self.policy.end()

    def before(self, kind: str, text: str) -> None:
        if not self.active:
            return
        if kind == "press" and text == self.console_key and self._console_open:
            # The one exemption: close the console we opened, whatever happened since.
            return
        if self.tripped is None:
            self.tripped = self.policy.interrupted()
        if self.tripped is not None:
            raise InputInterrupted(self.tripped)

    def after(self, kind: str, text: str) -> None:
        if self.active and kind == "press" and text == self.console_key:
            self._console_open = not self._console_open

    def own_input(self) -> None:
        """``ScanCodeKeys.on_send``: a key of ours has just gone out."""
        if self.active:
            self.policy.note_own_input()

    def check(self) -> None:
        """``ScanCodeKeys.before_send``: look for the person right before each key event.

        Only records (the next ``before`` raises): stopping between a key's down and up would
        leave it held. This is what catches a keypress that falls inside one of our chords."""
        if self.active and self.tripped is None:
            self.tripped = self.policy.interrupted()
