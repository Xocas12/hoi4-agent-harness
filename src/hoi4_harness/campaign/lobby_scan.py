"""The lobby's Custom Game Rules window, driven by reading it instead of by fixed points (#160).

The window is a long, collapsible, scrolling list: sections ("Major Nation Buffs", "Foreign
Policy", ...) each with a collapse arrow at the right end of its header, rows with a label on the
left and a dropdown on the right, and Apply / Reset / Load Preset / Save Preset along the
bottom. The country's AI behaviour row is far down it, and no fixed point reaches it. So:

1. read the window (Windows OCR, the rectangle of the game window only) and find the footer
   buttons (they give the panel's right edge) and the section headers (their text);
2. collapse every section: click the arrow of the top-most header that still has rows under it,
   read again, repeat. A collapse arrow is a toggle, so a header is never clicked twice without
   the screen having changed; a click that changes nothing stops the scan;
3. scroll (the wheel, else dragging the scrollbar) until the AI behaviour header shows, click its
   arrow to expand it, scroll until the country's row shows, open its dropdown, click the wanted
   option in the list that opens, and check the row now shows it;
4. click Apply.

Every click goes through the clicker, which checks the game window is in front first; nothing
here clicks into another window. Reads and clicks are injected, so the tests drive the whole
scan against a fake screen. ``calibrate=True`` reads once and logs every line with its box, the
headers and the panel edge it found, and clicks nothing: the first live step, and what to send
back when a section name or an offset is wrong.

UNVERIFIED live: the section names, the arrow and dropdown offsets, that the wheel scrolls this
list, where the scrollbar is. Each is a field of ``ScanLayout`` (``campaign_lobby_points`` can
override them, see ``layout_from``); docs/campaign.md has the live check.
"""

from __future__ import annotations

import difflib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path

from ..adapters.ocr import OcrLine, OcrPage, OcrUnavailable
from .lobby import LobbyError


class ScanError(LobbyError):
    """The window could not be read or did not do what the click should have done."""


FOOTER = ("apply", "reset", "load preset", "save preset")


@dataclass
class ScanLayout:
    #: Header texts (case-insensitive, matched whole after trimming). Extend from a calibration run.
    section_names: tuple = (
        "major nation buffs", "foreign policy", "ai behavior", "ai behaviour", "ai behaviours",
        "ai behavior rules", "game difficulty")
    #: A header whose text contains one of these is the AI behaviour section.
    ai_section: tuple = ("ai behavio",)
    #: Footer buttons' right edge to the panel's right edge, and the collapse arrow / dropdown
    #: click point measured leftwards from the panel's right edge.
    panel_pad: int = 40
    arrow_dx: int = 30
    dropdown_dx: int = 130
    #: Scrollbar x, relative to the panel's right edge (positive = right of it), and the wheel.
    scrollbar_dx: int = 10
    wheel_clicks: int = 5
    drag_step: int = 160
    max_passes: int = 60
    max_scrolls: int = 40
    settle: float = 0.6
    #: Fuzzy match threshold for OCR text against a wanted label.
    match: float = 0.82


def layout_from(points: dict | None) -> ScanLayout:
    """The layout with any ``scan_<field>`` entries of ``campaign_lobby_points`` applied."""
    layout, known = ScanLayout(), {f.name for f in fields(ScanLayout)}
    for key, value in (points or {}).items():
        name = key[5:] if key.startswith("scan_") else None
        if name in known:
            setattr(layout, name, tuple(value) if isinstance(value, list) else value)
    return layout


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (text or "").lower()).strip()


def _same(a: str, b: str, threshold: float) -> bool:
    a, b = norm(a), norm(b)
    return bool(a and b) and (a == b or difflib.SequenceMatcher(None, a, b).ratio() >= threshold)


class RuleScanner:
    def __init__(self, ocr, clicker, window_rect: Callable[[], tuple[int, int, int, int] | None],
                 log: Callable[[str], None], layout: ScanLayout | None = None,
                 pause: Callable[[float], None] = time.sleep,
                 calibrate: bool = False, dump: Path | str | None = None):
        self.ocr, self.clicker, self.window_rect, self.log = ocr, clicker, window_rect, log
        self.layout, self.pause = layout or ScanLayout(), pause
        self.calibrate, self.dump = calibrate, dump
        self.clicks = 0
        self._wheel_works: bool | None = None
        self._thumb: int | None = None

    # --- reading ------------------------------------------------------------------

    def read(self) -> OcrPage:
        rect = self.window_rect()
        if not rect or rect[2] <= 0 or rect[3] <= 0:
            raise ScanError("the game window's rectangle cannot be read, so the window cannot be "
                            "read either; nothing was clicked")
        why = self.ocr.available()
        if why:
            raise ScanError(f"the lobby scan reads the window with OCR, which cannot run: {why}")
        try:
            return self.ocr.read(rect)
        except OcrUnavailable as exc:
            raise ScanError(f"OCR failed reading the lobby window: {exc}") from exc

    def _footer(self, page: OcrPage) -> dict[str, OcrLine]:
        found: dict[str, OcrLine] = {}
        for line in page.lines:
            for name in FOOTER:
                if name not in found and _same(line.text, name, self.layout.match):
                    found[name] = line
        return found

    def geometry(self, page: OcrPage) -> tuple[int, int]:
        """``(panel right edge, footer top)`` from the footer buttons."""
        footer = self._footer(page)
        if "apply" not in footer or len(footer) < 2:
            raise ScanError("the Custom Game Rules footer (Apply / Reset / Load Preset / Save "
                            "Preset) was not found on screen: is the window open? "
                            f"Read {len(page.lines)} lines; run the calibration to see them")
        right = max(line.x + line.w for line in footer.values()) + self.layout.panel_pad
        return right, min(line.y for line in footer.values())

    def headers(self, page: OcrPage) -> list[OcrLine]:
        return sorted((ln for ln in page.lines if any(
            _same(ln.text, name, self.layout.match) for name in self.layout.section_names)),
            key=lambda ln: ln.y)

    def _is_header(self, line: OcrLine) -> bool:
        return any(_same(line.text, name, self.layout.match) for name in self.layout.section_names)

    def body(self, page: OcrPage, footer_top: int) -> list[OcrLine]:
        return sorted((ln for ln in page.lines if ln.y + ln.h <= footer_top), key=lambda ln: ln.y)

    @staticmethod
    def signature(page: OcrPage) -> tuple:
        return tuple((norm(ln.text), ln.y // 4) for ln in page.lines)

    # --- acting -------------------------------------------------------------------

    def click(self, point: tuple[int, int], label: str) -> None:
        if not self.clicker.in_front():
            raise LobbyError(f"the game window is not in front before '{label}'; stopped after "
                             f"{self.clicks} clicks of the scan, nothing more was clicked")
        self.log(f"lobby scan: click {label} at {point}")
        self.clicker.click(point, label)
        self.clicks += 1
        self.pause(self.layout.settle)

    def _guard(self, label: str) -> None:
        if not self.clicker.in_front():
            raise LobbyError(f"the game window is not in front before '{label}'; nothing more "
                             "was done")

    def scroll(self, down: bool, page: OcrPage) -> bool:
        """Scroll the list one step; True when the screen changed. The wheel first; if it does
        nothing, the scrollbar is dragged (and the wheel is not tried again)."""
        right, footer_top = self.geometry(page)
        rect = self.window_rect()
        anchor = (right - 300, (rect[1] + footer_top) // 2)
        before = self.signature(page)
        if self._wheel_works is not False and hasattr(self.clicker, "scroll"):
            self._guard("scroll the list")
            self.clicker.scroll(anchor, -self.layout.wheel_clicks if down else self.layout.wheel_clicks)
            self.pause(self.layout.settle)
            if self.signature(self.read()) != before:
                self._wheel_works = True
                return True
            if self._wheel_works is None and down:      # at the top an upward wheel changes nothing either
                self._wheel_works = False
                self.log("lobby scan: the mouse wheel did not scroll the list; dragging the scrollbar")
        if not hasattr(self.clicker, "drag"):
            return False
        x, top = right + self.layout.scrollbar_dx, rect[1] + 60
        if self._thumb is None:
            self._thumb = top
        target = self._thumb + (self.layout.drag_step if down else -self.layout.drag_step)
        target = max(top, min(footer_top - 10, target))
        if target == self._thumb:
            return False
        self._guard("drag the scrollbar")
        self.log(f"lobby scan: drag the scrollbar {self._thumb} -> {target}")
        self.clicker.drag((x, self._thumb), (x, target))
        self._thumb = target
        self.pause(self.layout.settle)
        return self.signature(self.read()) != before

    def to_top(self) -> None:
        for _ in range(self.layout.max_scrolls):
            if not self.scroll(False, self.read()):
                return

    # --- the steps ----------------------------------------------------------------

    def collapse_all(self) -> int:
        """Collapse every section; returns the number of arrows clicked."""
        clicked, passes = 0, 0
        self.to_top()
        while passes < self.layout.max_passes:
            passes += 1
            page = self.read()
            right, footer_top = self.geometry(page)
            body = self.body(page, footer_top)
            heads = [h for h in self.headers(page) if h.y + h.h <= footer_top]
            expanded = None
            for index, head in enumerate(heads):
                below = [ln for ln in body if ln.y > head.bottom and (
                    index + 1 >= len(heads) or ln.y < heads[index + 1].y)]
                if below:
                    expanded = head
                    break
            if expanded is None:
                if self.scroll(True, page):
                    continue
                return clicked
            before = self.signature(page)
            self.click((right - self.layout.arrow_dx, expanded.centre[1]),
                       f"collapse '{expanded.text}'")
            clicked += 1
            if self.signature(self.read()) == before:
                raise ScanError(f"clicking the collapse arrow of '{expanded.text}' changed nothing "
                                f"on screen; arrow_dx={self.layout.arrow_dx} is probably wrong "
                                "(run the calibration; scan_arrow_dx in campaign_lobby_points)")
        raise ScanError(f"sections were still open after {self.layout.max_passes} passes")

    def find(self, match: Callable[[OcrLine], bool], what: str) -> tuple[OcrPage, OcrLine]:
        """Scroll down from the top until a line matching is on screen."""
        self.to_top()
        for _ in range(self.layout.max_scrolls + 1):
            page = self.read()
            _, footer_top = self.geometry(page)
            for line in self.body(page, footer_top):
                if match(line):
                    return page, line
            if not self.scroll(True, page):
                break
        raise ScanError(f"{what} was not found in the Custom Game Rules window (scrolled to the "
                        "end); run the calibration and compare with what the window shows")

    def set_rule(self, row_labels: list[str], option_labels: list[str]) -> None:
        """Expand the AI behaviour section, find the country's row, choose the option."""
        wanted_opts = [o for o in option_labels if o]
        page, head = self.find(lambda ln: any(a in norm(ln.text) for a in self.layout.ai_section)
                               and len(norm(ln.text)) < 40, "the AI behaviour section header")
        right, _ = self.geometry(page)
        self.click((right - self.layout.arrow_dx, head.centre[1]), f"expand '{head.text}'")
        page, row = self.find(lambda ln: not self._is_header(ln) and any(
                                  _same(ln.text, label, self.layout.match)
                                  for label in row_labels if label),
                              f"the row {row_labels[0]!r}")
        right, footer_top = self.geometry(page)
        current = self._row_value(page, row, right)
        if current and any(_same(current, o, self.layout.match) for o in wanted_opts):
            self.log(f"lobby scan: {row.text} already shows {current!r}; left as it is")
            return
        self.click((right - self.layout.dropdown_dx, row.centre[1]), f"open the dropdown of '{row.text}'")
        page = self.read()
        row_y = row.centre[1]
        # The list may open over the footer's edge or upward: look at the whole window.
        options = [ln for ln in page.lines
                   if abs(ln.centre[1] - row_y) > row.h and any(
                       _same(ln.text, o, self.layout.match) for o in wanted_opts)]
        if not options:
            raise ScanError(f"the option {wanted_opts[0]!r} is not in the list that opened under "
                            f"'{row.text}' (read: {[ln.text for ln in page.lines][:30]})")
        # The nearest below the row; the list opens downward unless it is at the bottom.
        pick = min(options, key=lambda ln: abs(ln.centre[1] - row_y))
        self.click(pick.centre, f"option '{pick.text}'")
        page = self.read()
        _, row_now = self._find_again(page, row_labels)
        shown = self._row_value(page, row_now, self.geometry(page)[0]) if row_now else None
        if not (shown and any(_same(shown, o, self.layout.match) for o in wanted_opts)):
            raise ScanError(f"after choosing {wanted_opts[0]!r} the row shows {shown!r}, not the "
                            "option; the rule was not confirmed set")
        self.log(f"lobby scan: {row.text} now shows {shown!r}")

    def _find_again(self, page: OcrPage, row_labels: list[str]):
        for line in page.lines:
            if any(_same(line.text, label, self.layout.match) for label in row_labels if label):
                return page, line
        return page, None

    def _row_value(self, page: OcrPage, row: OcrLine, right: int) -> str | None:
        """The text on the row's line to the right of its label (the dropdown's shown value)."""
        same_line = [ln for ln in page.lines if ln is not row and ln.x > row.x + row.w
                     and abs(ln.centre[1] - row.centre[1]) <= max(row.h, ln.h) // 2 + 2]
        return " ".join(ln.text for ln in sorted(same_line, key=lambda ln: ln.x)) or None

    def apply(self) -> None:
        page = self.read()
        footer = self._footer(page)
        if "apply" not in footer:
            raise ScanError("the Apply button was not found")
        self.click(footer["apply"].centre, "Apply")

    # --- the whole path -------------------------------------------------------------

    def run(self, row_labels: list[str], option_labels: list[str]) -> int:
        """The scan's clicks, or (calibrate) the read-only report. Returns clicks made."""
        if self.calibrate:
            self.report()
            return 0
        self.collapse_all()
        self.set_rule(row_labels, option_labels)
        self.apply()
        return self.clicks

    def report(self) -> OcrPage:
        page = self.read()
        self.log(f"lobby calibration: {len(page.lines)} lines read in window {page.rect}")
        for line in page.lines:
            self.log(f"  [{line.x},{line.y} {line.w}x{line.h}] {line.text!r}")
        try:
            right, footer_top = self.geometry(page)
            self.log(f"lobby calibration: panel right edge {right}, footer top {footer_top}, "
                     f"arrow click x {right - self.layout.arrow_dx}, dropdown x "
                     f"{right - self.layout.dropdown_dx}")
        except ScanError as exc:
            self.log(f"lobby calibration: {exc}")
        self.log("lobby calibration: headers recognised: "
                 + (", ".join(repr(h.text) for h in self.headers(page)) or "none"))
        if self.dump:
            Path(self.dump).write_text(json.dumps({
                "rect": list(page.rect),
                "lines": [{"text": ln.text, "x": ln.x, "y": ln.y, "w": ln.w, "h": ln.h}
                          for ln in page.lines]}, indent=1), encoding="utf-8")
            self.log(f"lobby calibration: lines written to {self.dump}")
        return page


def run_scan_path(scanner: RuleScanner, steps: list, row_labels: list[str], option_labels: list[str],
                  clicker, log: Callable[[str], None], pause: Callable[[float], None] = time.sleep) -> int:
    """The whole lobby with the scan in the middle. ``steps`` are ``steps_for(..., scan=True)``:
    Custom Game Rules first, then the Historical box and START. Calibration reads the window
    as it stands and clicks nothing."""
    from .lobby import run_steps

    if scanner.calibrate:
        return scanner.run(row_labels, option_labels)
    opened, rest = steps[:1], steps[1:]
    done = run_steps(opened, clicker, log, pause=pause)
    done += scanner.run(row_labels, option_labels)
    return done + run_steps(rest, clicker, log, pause=pause)
