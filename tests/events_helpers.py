"""A fake event window for the tests: the screen text OCR would return, and the mouse that closes it.

No real screen, no real OCR: ``FakeEventScreen.backend()`` is an ``adapters.ocr.FakeOcr`` whose lines
are laid out with the window layout of ``adapters/event_choice.py`` (so a click computed from the
layout lands in the slot the text is drawn in), with deterministic typos the way OCR makes them.
"""

from __future__ import annotations

from pathlib import Path

from hoi4_harness.adapters.event_choice import EventLayout, layout_for, option_boxes, window_box
from hoi4_harness.adapters.ocr import FakeOcr, OcrLine
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.modcontext.events import EventCatalog, display
from hoi4_harness.uilayout import Geometry

FIX = Path(__file__).parent / "fixtures" / "eventctx" / "game"


def catalog(tag: str | None = "GER") -> EventCatalog:
    index = IdentifierIndex.build(Playset.from_paths(FIX))
    return EventCatalog(index.mod_context()).for_country(tag)


def playset_layout() -> EventLayout:
    return layout_for(Playset.from_paths(FIX))


def typo(text: str, every: int = 9) -> str:
    """OCR-like damage: every ``every``-th letter becomes a look-alike or is dropped."""
    swaps = {"o": "0", "l": "1", "e": "c", "i": "l", "a": "o", "n": "h"}
    out = []
    for i, ch in enumerate(text):
        if ch.isalpha() and i % every == every - 1:
            if i % 2:
                out.append(swaps.get(ch.lower(), ""))
            continue
        out.append(ch)
    return "".join(out)


class FakeEventScreen:
    """One event window on a fake screen."""

    def __init__(self, geometry: Geometry, cat: EventCatalog, layout: EventLayout | None = None,
                 grow: float = 0.0, damage: int = 0):
        self.geometry, self.catalog = geometry, cat
        self.layout = layout or playset_layout()
        self.grow = grow
        self.damage = damage            # 0: clean text; N: a typo every N letters
        self.event_id: str | None = None
        self.shown: list[int] = []
        self.noise: list[tuple[str, int, int]] = []    # lines of the map behind (text, x, y)
        self.clicks: list[tuple[int, int]] = []
        self.chosen: list[tuple[str, int]] = []
        self.on_close = None
        self.dead_zone = False          # clicks do nothing (a window that ignores them)

    # --- the window -----------------------------------------------------------------------

    def open(self, event_id: str, shown: list[int] | None = None) -> None:
        info = self.catalog.get(event_id)
        self.event_id = event_id
        self.shown = shown if shown is not None else [
            o.index for o in info.options if o.shown is not False]

    def close(self) -> None:
        self.event_id = None

    def _text(self, text: str) -> str:
        return typo(text, self.damage) if self.damage else text

    def lines(self, rect) -> list[OcrLine]:
        out: list[OcrLine] = []
        s = self.geometry.scale
        if self.event_id is None:
            return [OcrLine(t, x, y, 12 * len(t), 14) for t, x, y in self.noise]
        info = self.catalog.get(self.event_id)
        win = window_box(self.layout, self.geometry, self.grow)
        title = display(info.title_raw[0]) if info.title_raw else info.id
        title = title.replace("  ", " ")
        out.append(OcrLine(self._text(title), win.x + round(80 * s), win.y + round(70 * s),
                           round(300 * s), round(22 * s)))
        body = display(info.desc_raw[0]) if info.desc_raw else ""
        words, row, y = body.split(), "", win.y + round(130 * s)
        for word in words:
            if len(row) + len(word) > 52:
                out.append(OcrLine(self._text(row), win.x + round(31 * s), y, round(500 * s),
                                   round(16 * s)))
                row, y = "", y + round(18 * s)
            row = (row + " " + word).strip()
        if row:
            out.append(OcrLine(self._text(row), win.x + round(31 * s), y, round(400 * s), round(16 * s)))
        boxes = option_boxes(self.layout, self.geometry, max(len(self.shown), 1), self.grow)
        for slot, index in enumerate(self.shown):
            option = info.options[index - 1]
            box = boxes[slot]
            text = self._text(option.text)
            out.append(OcrLine(text, box.x + round(20 * s), box.y + box.h // 2 - 8,
                               round(260 * s), round(16 * s)))
        out += [OcrLine(t, x, y, 12 * len(t), 14) for t, x, y in self.noise]
        left, top, width, height = rect
        return [ln for ln in out if left <= ln.x < left + width and top <= ln.y < top + height]

    def backend(self, why: str | None = None) -> FakeOcr:
        return FakeOcr(lambda rect: self.lines(rect), why=why)

    # --- the mouse ------------------------------------------------------------------------

    def moveTo(self, x, y, duration=0):  # noqa: N802 - pyautogui's name
        self._at = (x, y)

    def click(self, button="left"):
        x, y = self._at
        self.clicks.append((x, y))
        if self.event_id is None or self.dead_zone:
            return
        boxes = option_boxes(self.layout, self.geometry, max(len(self.shown), 1), self.grow)
        for slot, index in enumerate(self.shown):
            if boxes[slot].contains(x, y):
                self.chosen.append((self.event_id, index))
                self.event_id = None
                if self.on_close:
                    self.on_close()
                return
