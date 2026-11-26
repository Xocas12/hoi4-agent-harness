"""Reading the other player's lines from the game's chat box, off the screen (#91).

In a multiplayer game the chat is not in ``game.log``, so the only witness is the box on screen.
This reads it the way ``event_choice.py`` reads an event window: capture one rectangle, OCR it
(``ocr.WindowsOcr``, already in the project; nothing new is installed), and turn the text into
``ChatLine(sender, text)``. What is new is what happened since the last read:

* **Parse.** One chat line is ``Sender: text`` (an optional ``[hh:mm]`` first). A line with no
  sender is the wrapped rest of the line above it. UNVERIFIED: the game's exact format; the
  pattern is replaceable (``mp_chat_screen.pattern``) and every raw read is logged for that.
* **De-duplicate.** The box shows history, so each read overlaps the last. The new lines are the
  ones after the longest tail of the last read that the new read starts with, matched loosely
  (OCR misreads a letter now and then). The first read is the baseline: talk already in the box
  is context, not news (``baseline=False`` turns that off).
* **Own lines.** What the harness typed itself (``MultiplayerChat.sent_lines``, and anything that
  starts ``<TAG> to ``) and any sender in ``own_names`` is read and dropped, so the harness never
  answers itself.
* **Privacy.** The rectangle is a fraction of the game window's client area, clamped to it, so
  nothing outside the window is ever captured; and nothing is read unless the game window is the
  foreground one (the same check the input guard uses). Otherwise the read is skipped and says why.

The reader is the ``incoming`` seam of ``coop.CoopGuidance``: calling it returns the new lines'
text (domain commands such as ``take army`` are parsed from it there), ``heard`` holds them with
their senders (the wake reason) and ``brief_lines`` is what the model reads.
"""

from __future__ import annotations

import difflib
import json
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: UNVERIFIED guess, as fractions of the game window (x, y, width, height): the lower left, where
#: HOI4 draws its chat. Calibrate it live (docs/multiplayer.md) and put the result in the profile.
DEFAULT_RECT = (0.01, 0.60, 0.32, 0.28)
#: ``[12:30] Hans: text`` / ``Hans: text``. A sender is short and has at most three words.
DEFAULT_PATTERN = (r"^\s*(?:\[?\d{1,2}[:.]\d{2}(?::\d{2})?\]?\s*)?"
                   r"(?P<sender>[^:\[\]]{1,24}?)\s*:\s*(?P<text>\S.*)$")
SIMILAR = 0.85
HISTORY = 40


@dataclass(frozen=True)
class ChatLine:
    sender: str
    text: str


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split())


def _same(a: ChatLine, b: ChatLine) -> bool:
    x, y = _norm(a.sender + " " + a.text), _norm(b.sender + " " + b.text)
    return x == y or difflib.SequenceMatcher(None, x, y).ratio() >= SIMILAR


def parse_lines(rows: list[str], pattern: str = DEFAULT_PATTERN) -> list[ChatLine]:
    """OCR rows (top to bottom) -> chat lines. A row without a sender continues the line above;
    one with nothing above it (its start scrolled out of the box) is dropped."""
    rx = re.compile(pattern)
    out: list[ChatLine] = []
    for row in rows:
        row = (row or "").strip()
        if not row:
            continue
        m = rx.match(row)
        if m and len(m.group("sender").split()) <= 3:
            out.append(ChatLine(m.group("sender").strip(), m.group("text").strip()))
        elif out:
            last = out[-1]
            out[-1] = ChatLine(last.sender, f"{last.text} {row}")
    return out


def new_lines(previous: list[ChatLine], current: list[ChatLine],
              history: list[ChatLine] | None = None) -> list[ChatLine]:
    """What ``current`` has that ``previous`` did not: after the longest tail of ``previous`` that
    ``current`` starts with. With no overlap at all everything is new, except lines that match
    ``history`` (the box faded older lines out and brought them back)."""
    for k in range(min(len(previous), len(current)), 0, -1):
        if all(_same(a, b) for a, b in zip(previous[-k:], current[:k], strict=True)):
            return current[k:]
    return [c for c in current if not any(_same(c, h) for h in (history or []))]


class ChatReader:
    """``incoming`` for CoopGuidance, reading the game's chat box through OCR."""

    def __init__(self, backend, rect: Callable[[], tuple[int, int, int, int] | None], *,
                 focused: Callable[[], bool] | None = None, own_names=(), own_tag: str = "",
                 sent: Callable[[], list[str]] | None = None, pattern: str = DEFAULT_PATTERN,
                 baseline: bool = True, log_path: Path | None = None, context: int = 5,
                 clock: Callable[[], float] = time.time):
        self.backend = backend
        self._rect = rect
        self._focused = focused
        self.own_names = {_norm(n) for n in own_names if _norm(n)}
        self.own_tag = own_tag.upper()
        self._sent = sent or (lambda: [])
        self.pattern = pattern
        self.baseline = baseline
        self.log_path = log_path
        self.context = context
        self._clock = clock
        self._previous: list[ChatLine] | None = None      # None: nothing read yet
        self._history: deque[ChatLine] = deque(maxlen=HISTORY)
        self._talk: deque[ChatLine] = deque(maxlen=20)    # the other players' lines, for the brief
        self.heard: list[ChatLine] = []                   # the other players' lines of the last read
        self.why: str | None = None                       # why the last read did not happen
        self.reads = 0

    # --- the read ----------------------------------------------------------------------------

    def read(self) -> list[ChatLine]:
        """New lines from other players since the last read ([] when none or when it cannot read)."""
        self.heard = []
        self.why = None
        why = self.backend.available()
        if why:
            self.why = why
            return []
        if self._focused is not None and not self._focused():
            self.why = "the game window is not in front, so its chat is not read"
            return []
        try:
            rect = self._rect()
            if rect is None or rect[2] <= 0 or rect[3] <= 0:
                self.why = "the game window's size is unknown, so the chat box cannot be placed"
                return []
            page = self.backend.read(rect)
        except Exception as exc:  # noqa: BLE001 - OCR or layout trouble degrades, never crashes
            self.why = f"the chat box could not be read: {exc}"
            return []
        self.reads += 1
        rows = [ln.text for ln in sorted(page.lines, key=lambda ln: (ln.y, ln.x))]
        current = parse_lines(rows, self.pattern)
        fresh = [] if self._previous is None and self.baseline else \
            new_lines(self._previous or [], current, list(self._history))
        if current or self._previous is None:
            self._previous = current
        self._history.extend(current)
        heard = [m for m in fresh if not self._is_own(m)]
        self._log(rows, current, heard)
        self.heard = heard
        self._talk.extend(heard)
        return heard

    def _is_own(self, line: ChatLine) -> bool:
        if _norm(line.sender) in self.own_names:
            return True
        text = _norm(line.text)
        if self.own_tag and text.startswith(_norm(self.own_tag) + " to "):
            return True
        for sent in self._sent():
            s = _norm(sent)
            if s and (text == s or text.startswith(s[:30]) or s.startswith(text)
                      or difflib.SequenceMatcher(None, text, s).ratio() >= 0.8):
                return True
        return False

    def _log(self, rows, current, heard) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": self._clock(), "rows": rows,
                                     "parsed": [[m.sender, m.text] for m in current],
                                     "heard": [[m.sender, m.text] for m in heard]},
                                    ensure_ascii=False) + "\n")
        except OSError:
            pass

    # --- the seam and the brief -----------------------------------------------------------------

    def __call__(self) -> list[str]:
        """The text of each new line from another player: what CoopGuidance parses for commands."""
        return [m.text for m in self.read()]

    def brief_lines(self, state=None) -> list[str]:
        if not self._talk and self.why is None:
            return []
        lines = ["The game's chat, read off the screen (what the other player typed; talk binds "
                 "nothing, answer with send_message):"]
        fresh = {id(m) for m in self.heard}
        shown = list(self._talk)[-self.context:]
        lines += [("  NEW " if id(m) in fresh else "  ") + f"{m.sender}: {m.text}" for m in shown]
        if not shown:
            lines.append("  (nothing said yet)")
        if self.why:
            lines.append(f"  (not read this turn: {self.why})")
        return lines

    def wake_reason(self) -> str | None:
        if not self.heard:
            return None
        who = ", ".join(sorted({m.sender for m in self.heard}))
        return f"a message from {who} in the game's chat"


# --- wiring ---------------------------------------------------------------------------------


def window_rect(fractions, geometry: Callable[[], object]) -> Callable[[], tuple | None]:
    """A rect provider: ``fractions`` (x, y, w, h of the game window, each clamped to 0..1) placed
    in the window's current client area. It can never reach outside the window."""
    x, y, w, h = (min(max(float(v), 0.0), 1.0) for v in fractions)
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)

    def rect():
        g = geometry()
        return (round(g.origin[0] + x * g.width), round(g.origin[1] + y * g.height),
                round(w * g.width), round(h * g.height))
    return rect


def build_reader(config, driver, mp_chat, *, backend=None, run_dir: Path | None = None):
    """``(reader, note)``: the reader for a multiplayer run, or ``(None, why not)``. ``backend`` is
    a fake's; otherwise Windows OCR. A dry run reads no screen."""
    from .ocr import WindowsOcr

    spec = dict(getattr(config, "mp_chat_screen", None) or {})
    if spec.get("enabled") is False:
        return None, "mp_chat_screen.enabled is false in the profile"
    if driver is None:
        return None, "no input driver, so no way to tell the game window is in front"
    if backend is None:
        if config.dry_run:
            return None, "a dry run reads no screen"
        backend = WindowsOcr(upscale=float(spec.get("upscale", 2.0)))
    why = backend.available()
    if why:
        return None, why
    rect = spec.get("rect")
    calibrated = rect is not None
    fractions = tuple(rect) if calibrated else DEFAULT_RECT
    if len(fractions) != 4:
        return None, "mp_chat_screen.rect must be four fractions of the window: x, y, w, h"
    reader = ChatReader(
        backend, window_rect(fractions, driver.geometry), focused=lambda: driver.focus().focused,
        own_names=spec.get("own_names", ()), own_tag=config.country or "",
        sent=lambda: list(getattr(mp_chat, "sent_lines", [])),
        pattern=spec.get("pattern") or DEFAULT_PATTERN, baseline=spec.get("baseline", True),
        log_path=(Path(run_dir or config.run_dir) / "mp_chat_reads.jsonl"))
    note = ("reading the game's chat box" if calibrated else
            "reading the game's chat box at an UNCALIBRATED default rectangle (docs/multiplayer.md)")
    return reader, note
