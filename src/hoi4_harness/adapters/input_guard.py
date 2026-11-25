"""One guard every input path of the driver goes through (issue #33): the harness does not type
or click over a person.

Two people cannot share a keyboard. The input driver asks this guard around EVERY sequence it
sends -- a key or chord (``press``, which also carries the console key and Enter of a run-file),
typed text (the console line, ``run <file>``), a click, a drag -- and it enforces three things:

* **never starts over a person**: a sequence begins only when nobody has used the keyboard or
  mouse for ``quiet_seconds``. It waits up to ``wait_seconds`` for that (polling with the injected
  sleep) and then refuses with ``PersonActive``; nothing was sent;
* **stops mid-sequence**: before every key, typed character, click and drag step it compares the
  OS's last-input stamp with the one it took right after its own last event. A different stamp is
  a person (our own SendInput moves the same clock, which is why stamps are compared, not idle
  time). The sequence stops with ``PersonActive`` saying so. The one key that still goes out is
  the console key that closes a console this guard saw opened, because an open console swallows
  the person's keys;
* **never steals the focus from a person**: ``gate_take`` wraps the "bring the game to the front"
  call (``campaign_take_focus``) and withholds it while the person is active.

Every handover is recorded: ``handovers.jsonl`` in the run dir, ``record`` in memory, and a line in
the driver's log, with the path (key, type, click, drag, focus), what was being sent, the outcome
(``completed``, ``refused``, ``aborted``, ``focus_withheld``) and the reason.

The watch is anything with ``tick()`` (the last-input stamp) and ``idle_seconds()`` --
``rival.safety.InputWatch`` is one. A platform that cannot say refuses: a guard that cannot look
does not pretend it did. Residual risk, as in rival mode: input in the milliseconds between the
last look and the event itself is not seen.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from .input_driver import PersonActive

__all__ = ["InputGuard", "PersonActive"]


class InputGuard:
    def __init__(self, watch, *, quiet_seconds: float = 5.0, wait_seconds: float = 30.0,
                 poll_seconds: float = 0.5, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, console_key: str = "grave",
                 log: Callable[[str], None] | None = None, log_path: Path | None = None):
        self.watch = watch
        self.quiet_seconds, self.wait_seconds, self.poll_seconds = quiet_seconds, wait_seconds, poll_seconds
        self._sleep, self._clock = sleep, clock
        self.console_key = console_key
        self.log, self.log_path = log, log_path
        #: Every handover event, in order (also appended to ``log_path`` as JSON lines).
        self.record: list[dict] = []
        self._depth = 0
        self._started = 0.0
        self._own: int | None = None
        self._tripped: str | None = None
        self._console_open = False
        #: A person was seen and not yet followed by real quiet: our own stamp does not vouch.
        self._dirty = False
        #: Set by ``attach_keys``: the key layer reports each of our events itself.
        self.keys_hooked = False
        self._path, self._label = "", ""

    # --- what the person is doing -------------------------------------------------

    def _quiet(self) -> tuple[bool, str]:
        """Whether no person has touched the keyboard or mouse (and why not)."""
        if not getattr(self.watch, "supported", True):
            return False, ("this platform cannot say when the keyboard was last used, so a "
                           "person cannot be told from our own input")
        tick = self.watch.tick()
        if tick is None:
            return False, "the last-input time could not be read"
        if not self._dirty and self._own is not None and tick == self._own:
            return True, ""        # nothing but our own events since we last looked
        idle = self.watch.idle_seconds()
        if idle is None or idle < self.quiet_seconds:
            return False, (f"keyboard or mouse input {0 if idle is None else idle:.0f}s ago "
                           f"({self.quiet_seconds:g}s of quiet wanted)")
        self._dirty = False
        return True, ""

    # --- the bracket around one sequence --------------------------------------------

    @contextmanager
    def sequence(self, path: str, label: str, *, closing: bool = False):
        """Bracket one input sequence. Nested sequences (a click that holds shift) share the
        outer one's start check; each still gets ``checkpoint`` calls."""
        outer = self._depth == 0
        closes_console = (closing or (path == "key" and label == self.console_key)) and self._console_open
        if outer:
            self._path, self._label, self._tripped = path, label, None
            if not closes_console:
                self._wait_for_quiet(path, label)
            elif self._own is not None and self.watch.tick() != self._own:
                self._dirty = True         # we close our console, but the person is still there
            self._own = self.watch.tick()
            self._started = self._clock()
        self._depth += 1
        try:
            yield self
        except PersonActive:
            raise
        else:
            if outer:
                self._note("completed", path, label, "")
        finally:
            self._depth -= 1

    def _wait_for_quiet(self, path: str, label: str) -> None:
        deadline = self._clock() + self.wait_seconds
        waited = False
        while True:
            ok, why = self._quiet()
            if ok:
                if waited:
                    self._note("resumed", path, label, "the person went quiet")
                return
            if self._clock() >= deadline:
                self._note("refused", path, label, why)
                raise PersonActive(
                    f"a person is using the keyboard or mouse ({why}): {path} {label!r} was not "
                    "sent, nothing was typed or clicked", phase="start", path=path, label=label)
            if not waited:
                waited = True
                self._note("waiting", path, label, why)
            self._sleep(self.poll_seconds)

    def checkpoint(self, *, soft: bool = False, closing: bool = False) -> None:
        """Called before each key, character, click or drag step. A person's input since our
        last event stops the sequence (``soft``: only remembered, for places where raising would
        leave a key held down; the next hard checkpoint raises)."""
        if self._depth == 0 or (closing and self._console_open):
            return
        if self._tripped is None:
            tick = self.watch.tick()
            if tick is not None and self._own is not None and tick != self._own:
                self._tripped = "the person started using the keyboard or mouse"
                self._dirty = True
                self._note("aborted", self._path, self._label, self._tripped)
        if self._tripped is not None and not soft:
            raise PersonActive(
                f"{self._tripped}: {self._path} {self._label!r} was stopped before it finished "
                "(whatever was not sent stays unsent)", phase="mid", path=self._path,
                label=self._label)

    @property
    def tripped(self) -> str | None:
        """Why the current sequence was stopped (None: it was not)."""
        return self._tripped

    def sent(self) -> None:
        """One of our own input events has just gone out: its stamp is not a person's."""
        if self._depth:
            self._own = self.watch.tick()

    def key_pressed(self, key: str) -> None:
        if key == self.console_key:
            self._console_open = not self._console_open

    def attach_keys(self, keys) -> None:
        """Hook a ``ScanCodeKeys`` so a keypress inside one of its chords is seen at its next
        event. Chains whatever hooks (rival's KeyWatch) are already there."""
        before, after = keys.before_send, keys.on_send
        self.keys_hooked = True

        def check() -> None:
            if before is not None:
                before()
            self.checkpoint(soft=True)

        def own() -> None:
            if after is not None:
                after()
            self.sent()

        keys.before_send, keys.on_send = check, own

    # --- the focus ----------------------------------------------------------------------

    def gate_take(self, take: Callable[[str], bool]) -> Callable[[str], bool]:
        """``take`` (bring the game to the front), but never while a person is active."""
        def gated(title: str) -> bool:
            ok, why = self._quiet()
            if not ok:
                self._note("focus_withheld", "focus", title, why)
                return False
            result = take(title)
            self._note("focus_taken" if result else "focus_failed", "focus", title, "")
            return result
        return gated

    # --- the record -----------------------------------------------------------------------

    def _note(self, outcome: str, path: str, label: str, why: str) -> None:
        entry = {"t": round(time.time(), 3), "outcome": outcome, "path": path, "what": label,
                 "why": why}
        if outcome in ("completed", "aborted"):
            entry["seconds"] = round(max(0.0, self._clock() - self._started), 3)
        self.record.append(entry)
        if self.log is not None and outcome != "completed":
            self.log(f"handover: {outcome} {path} {label!r}" + (f" ({why})" if why else ""))
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry) + "\n")
            except OSError:
                pass
