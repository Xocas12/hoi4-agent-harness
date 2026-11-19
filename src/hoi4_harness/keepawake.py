"""Keep the PC awake while a live run types into the game.

A sleeping machine or a locked or blanked screen means ``SendInput`` reaches nothing, and
an unattended run would then sit there pretending. Windows has one call for this,
``SetThreadExecutionState``: a thread asks for "system required" and "display required"
and the idle timers stop counting until it clears the request (or the thread ends).

``KeepAwake`` is a context manager around it. The setter is injectable so tests never
touch the real machine, and it is a no-op anywhere but Windows. The request belongs to a
*thread*, so ``__enter__`` and ``__exit__`` must run on the same thread, which they do for
a ``with`` block around the run.

``keep_awake_for(config, log)`` is the one hook the play commands use: it returns a
no-op for a dry run or the fake game, and the real thing for a live run. Rival mode calls
the same helper.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_CONTINUOUS = 0x80000000
HELD = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED


def _windows_setter(flags: int) -> int:  # pragma: no cover - needs Windows
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    return int(kernel32.SetThreadExecutionState(flags))


class KeepAwake(AbstractContextManager):
    """Hold the machine awake inside a ``with`` block; always let go on the way out."""

    def __init__(self, setter: Callable[[int], int] | None = None,
                 log: Callable[[str], None] | None = None):
        if setter is None and sys.platform == "win32":
            setter = _windows_setter
        self.setter, self.log = setter, log
        self.held = False

    def __enter__(self) -> KeepAwake:
        if self.setter is None:
            return self
        try:
            result = self.setter(HELD)
        except Exception as exc:  # noqa: BLE001 - failing to hold must not end the run
            self._say(f"keep-awake: could not be held ({type(exc).__name__}: {exc}); "
                      "the PC may sleep or blank the screen")
            return self
        if result == 0:
            self._say("keep-awake: Windows refused the request; the PC may sleep or blank the screen")
            return self
        self.held = True
        self._say("keep-awake: held (the PC will not sleep and the display stays on) until the run ends")
        return self

    def __exit__(self, *exc_info) -> None:
        if not self.held:
            return None
        self.held = False
        try:
            self.setter(ES_CONTINUOUS)
            self._say("keep-awake: released")
        except Exception as exc:  # noqa: BLE001 - the request dies with the thread anyway
            self._say(f"keep-awake: release failed ({type(exc).__name__}: {exc})")
        return None

    def _say(self, text: str) -> None:
        if self.log is not None:
            self.log(text)


def keep_awake_for(config, log: Callable[[str], None] | None = None,
                   setter: Callable[[int], int] | None = None) -> AbstractContextManager:
    """The context a live run wraps itself in: a no-op for a dry run or the fake game."""
    if config.dry_run or getattr(config, "fake_game", False) or getattr(config, "fake_game_files", None):
        return nullcontext()
    return KeepAwake(setter, log)
