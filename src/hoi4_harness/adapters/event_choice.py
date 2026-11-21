"""Read the event window that is open, and choose its option, in the LLM's own seat.

**NOT live-verified.** Built against a fake screen (tests/events_helpers.py) and checked offline
on text; docs/events.md says what the live session has to settle.

What a person does: an event window opens, they read the title and the text, weigh the options,
click one. What the harness does here, the same way and in the same order:

1. ``EventReader.look`` captures the window's region of the screen, reads it with Windows OCR
   (``adapters/ocr.py``) and matches the text against the playset's events
   (``modcontext/events.py``): title, description and option names from ``events/*.txt`` and the
   English localisation. The result is an event id and a confidence, never a bare guess.
2. The brief says an event is open and gives the title, the description, and each option's name
   and effects (``read_event`` gives the same in full). Nothing is clicked.
3. ``choose_event_option(option)`` clicks that option's button. Where the click lands is where
   the OCR FOUND the option's text; the layout of ``interface/eventwindow.gui`` (the grid of 300 x
   47 slots) scaled to the screen is the fallback, and the difference between the two is
   reported, because that difference is what a live session measures. The click goes through the
   driver (focus guard, dry run, bounds). Then the window is read again: it must be gone.

The rule this module keeps: it never clicks on a window it has not identified, and it says plainly
when it cannot read. The old blind answer (any mouse tool first takes option 1 of an open country
event, ``event_windows.EventGuard``) stays as the fallback for what this cannot identify, and the
brief says so when it applies.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..modcontext.events import (
    EventCatalog,
    EventInfo,
    EventOption,
    Identification,
    _fold,
    _ratio,
    display,
)
from ..types import ActionCall, ActionResult
from ..uilayout import Geometry, parse_gui
from .ocr import OcrBackend, OcrLine, OcrPage

ACTIONS = ("choose_event_option",)
#: A line of the screen is taken for an option's text when it is this similar.
LINE_MATCH = 0.72
#: Options below this are not located (their text was not read).
SLOT_COUNT_NOMINAL = 4


# --- the window's layout, from interface/eventwindow.gui ------------------------------------


@dataclass(frozen=True)
class EventLayout:
    """``EventWindow`` of ``interface/eventwindow.gui``, in GUI units.

    The window is placed from the screen's centre (``Orientation = CENTER``) by ``position``; its
    midsection (the description) takes the height the text needs, which the file does not give, so
    ``grow`` is how much taller than its nominal 100 it is; the options are a one-column grid of
    ``slot`` sized buttons that starts ``grid`` inside the bottom section."""
    position: tuple[float, float] = (-282.0, -310.0)
    size: tuple[float, float] = (581.0, 427.0)
    top_height: float = 121.0
    bottom_y: float = 221.0
    grid: tuple[float, float] = (215.0, -1.0)
    slot: tuple[float, float] = (300.0, 47.0)
    source: str = "built in (1.20 interface/eventwindow.gui)"

    @classmethod
    def from_gui(cls, text: str) -> EventLayout:
        """Read the numbers from the file's text; whatever it does not give keeps its default
        (a mod's override of the file is followed because ``text`` is the layered file)."""
        base = cls()
        try:
            root = parse_gui(text)
            gui_types = root.get("guiTypes")
            window = next((c for c in (gui_types.children() if gui_types else root.children())
                           if c.name == "EventWindow"), None)
            if window is None:
                return base

            def pair(node, key, a, b, default):
                value = node.get(key) if node is not None else None
                try:
                    return (float(value.get(a)), float(value.get(b)))
                except (AttributeError, TypeError, ValueError):
                    return default

            def child(parent, name):
                return next((c for c in parent.children() if c.name == name), None)

            bottom = child(window, "bottom_Window")
            grid = child(bottom, "options_grid") if bottom is not None else None
            top = child(window, "top_Window")
            return cls(
                position=pair(window, "position", "x", "y", base.position),
                size=pair(window, "size", "width", "height", base.size),
                top_height=pair(top, "size", "width", "height", (0, base.top_height))[1],
                bottom_y=pair(bottom, "position", "x", "y", (0, base.bottom_y))[1],
                grid=pair(grid, "position", "x", "y", base.grid),
                slot=pair(grid, "slotsize", "width", "height", base.slot),
                source="interface/eventwindow.gui")
        except (AttributeError, StopIteration):
            return base


def layout_for(playset) -> EventLayout:
    """The layout from the playset's own (layered) ``interface/eventwindow.gui``."""
    try:
        files = playset.layered("interface", "eventwindow.gui")
        if files:
            return EventLayout.from_gui(files[-1].read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, AttributeError):
        pass
    return EventLayout()


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def centre(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h


def _gui_origin(layout: EventLayout, geometry: Geometry, extra: float = 0.0,
                centre_growth: bool = False) -> tuple[float, float]:
    gui_w, gui_h = geometry.gui_size
    top = gui_h / 2 + layout.position[1] - (extra / 2 if centre_growth else 0.0)
    return (gui_w / 2 + layout.position[0], top)


def window_box(layout: EventLayout, geometry: Geometry, extra: float = 0.0,
               centre_growth: bool = False) -> Box:
    """The event window in screen pixels (``extra``: GUI units the window grew by)."""
    ox, oy = _gui_origin(layout, geometry, extra, centre_growth)
    x0, y0 = geometry.to_screen(ox, oy)
    x1, y1 = geometry.to_screen(ox + layout.size[0], oy + layout.size[1] + extra)
    return Box(x0, y0, x1 - x0, y1 - y0)


def capture_box(layout: EventLayout, geometry: Geometry) -> Box:
    """What to read: the window's width, from its top down to where six options and a long
    description would end (clamped to the screen)."""
    box = window_box(layout, geometry)
    bottom = geometry.to_screen(0, _gui_origin(layout, geometry)[1] + layout.size[1]
                                + 6 * layout.slot[1] + 220)[1]
    bottom = min(bottom, geometry.origin[1] + geometry.height)
    pad = round(10 * geometry.scale)
    left = max(geometry.origin[0], box.x - pad)
    right = min(geometry.origin[0] + geometry.width, box.x + box.w + pad)
    top = max(geometry.origin[1], box.y)
    return Box(left, top, right - left, bottom - top)


def option_boxes(layout: EventLayout, geometry: Geometry, count: int, grow: float = 0.0,
                 centre_growth: bool = False) -> list[Box]:
    """The screen boxes of the first ``count`` option buttons, top one first.

    ``grow``: how much the description made the window taller than nominal (GUI units).
    Options past the four the window has room for add a slot's height each; with
    ``centre_growth`` (the window re-centres as it grows) the whole window moves up by half of
    that. Both are assumptions the live session checks (docs/events.md)."""
    extra = grow + max(0, count - SLOT_COUNT_NOMINAL) * layout.slot[1]
    ox, oy = _gui_origin(layout, geometry, extra, centre_growth)
    boxes = []
    for i in range(count):
        gx = ox + layout.grid[0]
        gy = oy + layout.bottom_y + grow + layout.grid[1] + i * layout.slot[1]
        x0, y0 = geometry.to_screen(gx, gy)
        x1, y1 = geometry.to_screen(gx + layout.slot[0], gy + layout.slot[1])
        boxes.append(Box(x0, y0, x1 - x0, y1 - y0))
    return boxes


# --- what the screen showed ------------------------------------------------------------------


@dataclass
class EventView:
    """One look at the screen."""
    status: str                          # none | identified | unidentified | unavailable
    ident: Identification | None = None
    page: OcrPage | None = None
    title_text: str = ""
    #: option index -> the screen box of its text
    located: dict[int, Box] = field(default_factory=dict)
    why: str = ""
    open_seconds: float = 0.0
    first_date: str = ""
    date: str = ""

    @property
    def event(self) -> EventInfo | None:
        return self.ident.event if self.ident and self.status == "identified" else None


def _union(a: OcrLine, b: OcrLine | None) -> Box:
    if b is None:
        return Box(a.x, a.y, a.w, a.h)
    x0, y0 = min(a.x, b.x), min(a.y, b.y)
    x1, y1 = max(a.x + a.w, b.x + b.w), max(a.bottom, b.bottom)
    return Box(x0, y0, x1 - x0, y1 - y0)


def locate_options(event: EventInfo, lines: list[OcrLine]) -> dict[int, Box]:
    """Where each option's text is on the screen: the best line (or two neighbouring lines, for
    a name that wraps) per option, each line used once, in the order of the best scores."""
    candidates: list[tuple[float, int, int, Box]] = []
    for option in event.options:
        want = _fold(option.text)
        if len(want) < 3:
            continue
        for i, line in enumerate(lines):
            candidates.append((_ratio(want, _fold(line.text)), option.index, i, _union(line, None)))
            if i + 1 < len(lines):
                joined = _fold(line.text + " " + lines[i + 1].text)
                candidates.append((_ratio(want, joined), option.index, i,
                                   _union(line, lines[i + 1])))
    candidates.sort(key=lambda row: -row[0])
    found: dict[int, Box] = {}
    used: set[int] = set()
    for score, index, i, box in candidates:
        if score < LINE_MATCH or index in found:
            continue
        span = {i, i + 1} if box.h > max(lines[i].h * 1.6, 1) else {i}
        if span & used:
            continue
        found[index] = box
        used |= span
    return found


class EventReader:
    """Looks at the event window and says what it is."""

    def __init__(self, catalog: EventCatalog, backend: OcrBackend, geometry: Callable[[], Geometry],
                 *, layout: EventLayout | None = None, focused: Callable[[], bool] | None = None,
                 probe: Callable[[], object] | None = None,
                 clock: Callable[[], float] = time.monotonic, log_path: Path | None = None,
                 min_title: float = 0.0, grow: float = 0.0, centre_growth: bool = False):
        self.catalog = catalog
        self.backend = backend
        self._geometry = geometry
        self.layout = layout or EventLayout()
        self._focused = focused
        self._probe = probe
        #: The game date as the last brief saw it (the brief sets it).
        self.date_now = ""
        self._clock = clock
        self.log_path = log_path
        self.grow = grow
        self.centre_growth = centre_growth
        self._seen: dict[str, tuple[float, str]] = {}
        self._said_off = False
        self.last: EventView | None = None

    # --- reading --------------------------------------------------------------------------

    def unavailable(self) -> str | None:
        """Why events cannot be read now (OCR missing), or None."""
        return self.backend.available()

    def look(self) -> EventView:
        why = self.backend.available()
        if why:
            return self._keep(EventView("unavailable", why=why))
        if self._focused is not None and not self._focused():
            return self._keep(EventView("unavailable", why="the game window is not in front, so "
                                        "its screen is not read (nothing else is captured)"))
        try:
            geometry = self._geometry()
            box = capture_box(self.layout, geometry)
            page = self.backend.read((box.x, box.y, box.w, box.h))
        except Exception as exc:  # noqa: BLE001 - layout or OCR trouble degrades, it does not crash
            return self._keep(EventView("unavailable", why=f"the screen could not be read: {exc}"))
        probe_open = bool(self._probe()) if self._probe is not None else None
        window = window_box(self.layout, geometry, self.grow, self.centre_growth)
        band_bottom = window.y + round(self.layout.top_height * geometry.scale)
        lines = [ln for ln in page.lines if ln.w > 0]
        title_lines = [ln for ln in lines if ln.centre[1] < band_bottom]
        title = " ".join(ln.text for ln in title_lines)
        ident = self.catalog.identify(page.text, title=title, lines=[ln.text for ln in lines]) \
            if lines else Identification(None, 0.0, 0.0)
        date = self.date_now
        if ident.identified and ident.event is not None:
            now = self._clock()
            first = self._seen.get(ident.event.id, (now, date))
            self._seen = {ident.event.id: first}          # only the open one is tracked
            view = EventView("identified", ident, page, title,
                             locate_options(ident.event, lines), open_seconds=now - first[0],
                             first_date=first[1], date=date)
            return self._keep(view)
        self._seen = {}
        seems = ident.event is not None and ident.confidence >= 0.55 and bool(title_lines)
        if probe_open or (probe_open is None and seems):
            return self._keep(EventView("unidentified", ident, page, title, why=(
                f"best guess {ident.event.id} at {ident.confidence:.2f}" if ident.event
                else "nothing in the playset fits the text"), date=date))
        return self._keep(EventView("none", ident, page, title, date=date))

    def _keep(self, view: EventView) -> EventView:
        self.last = view
        return view

    # --- saying it ------------------------------------------------------------------------

    def describe(self, view: EventView, *, effects_width: int = 420) -> dict:
        """The full reading as a dict (``read_event``)."""
        if view.status == "unavailable":
            return {"status": "unavailable", "why": view.why,
                    "advice": "events cannot be read here; any mouse tool will take option 1 of an "
                              "open country event blind, and close a news window"}
        if view.status == "none":
            return {"status": "none", "message": "no event window is open (nothing on the screen "
                    "matches an event of this playset)"}
        if view.status == "unidentified":
            return {"status": "unidentified", "why": view.why,
                    "confidence": round(view.ident.confidence, 3) if view.ident else 0.0,
                    "candidates": view.ident.to_dict()["candidates"] if view.ident else [],
                    "text_seen": (view.page.text if view.page else "")[:900],
                    "advice": "a window is open that could not be matched to an event, so "
                              "choose_event_option refuses; the next mouse tool will answer it "
                              "blind (option 1 of a country event, close for news)"}
        event = view.event
        assert event is not None and view.ident is not None
        out = event.describe()
        out["status"] = "open"
        out["match"] = view.ident.to_dict()
        out["open_for_seconds"] = round(view.open_seconds, 1)
        if view.first_date:
            out["first_seen_date"] = view.first_date
        for opt in out["options"]:
            box = view.located.get(opt["option"])
            opt["on_screen"] = box is not None
        out["choose_with"] = "choose_event_option(option=<number or key>)"
        return out

    def brief_lines(self, state=None) -> list[str]:
        if state is not None:
            self.date_now = getattr(state, "date", "") or ""
        view = self.look()
        if view.status == "none":
            return []
        if view.status == "unavailable":
            if self._said_off:
                return []
            self._said_off = True
            return [f"Events: not read ({view.why}); a country event is answered blind (option 1) "
                    "before your next mouse tool."]
        if view.status == "unidentified":
            guess = f" Best guesses: {', '.join(f'{i} {c:.2f}' for i, c in view.ident.candidates)}." \
                if view.ident and view.ident.candidates else ""
            return [f"EVENT WINDOW OPEN but not matched to any event ({view.why}).{guess} Text seen: "
                    f"{(view.page.text if view.page else '')[:300]!r}. choose_event_option refuses; "
                    "your next mouse tool answers it blind (option 1 / close)."]
        event = view.event
        assert event is not None and view.ident is not None
        lines = [f"EVENT OPEN: {event.id} \"{event.title}\" (matched {view.ident.confidence:.2f}"
                 f", open {view.open_seconds:.0f} s"
                 + (f", first seen {view.first_date}" if view.first_date else "") + ")"]
        desc = " / ".join(display(d) for d in event.desc_raw[:2])
        if desc:
            lines.append("  " + (desc[:420] + "..." if len(desc) > 420 else desc))
        for option in event.options:
            tag = "" if option.index in view.located else " [not seen on screen]"
            if option.shown is False:
                tag = " [never shown to this country]"
            lines.append(f"  Option {option.index}{tag}: {option.text or option.key} -> "
                         + option.describe()["effects"][:240])
        lines.append("  Choose with choose_event_option (read_event has the full text). If you "
                     "send another mouse tool first, option 1 is taken blind.")
        return lines

    def record(self, entry: dict) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"t": round(time.time(), 3), **entry}) + "\n")
        except OSError:
            pass

    def open_title(self) -> str:
        view = self.last
        return view.event.title if view and view.event else ""


# --- choosing --------------------------------------------------------------------------------


class EventExecutor:
    """Performs ``choose_event_option`` through the input driver."""

    actions = frozenset(ACTIONS)

    def __init__(self, driver, reader: EventReader, *, settle: float = 0.8,
                 sleep: Callable[[float], None] | None = None, recorded_first: str = "event.option1"):
        self.driver = driver
        self.reader = reader
        self.settle = settle
        self._sleep = sleep or driver._sleep
        self.recorded_first = recorded_first

    # --- the call -------------------------------------------------------------------------

    def __call__(self, call: ActionCall) -> ActionResult:
        raw = str(call.arguments.get("option", "")).strip()
        if not raw:
            return self._fail(call, "invalid_args", "option is needed: a number from read_event's "
                              "options, or the option's key")
        view = self.reader.look()
        if view.status == "unavailable":
            return self._fail(call, "rejected", f"events cannot be read, so nothing was clicked: "
                              f"{view.why}")
        if view.status == "none":
            return self._fail(call, "rejected", "no event window is open: nothing was clicked "
                              "(read_event says what the screen shows)")
        if view.status == "unidentified":
            return self._fail(call, "rejected", (
                f"an event window is open but it could not be matched to an event ({view.why}), "
                "so no option is known and nothing was clicked. read_event has the text seen; "
                "the next mouse tool will answer it blind (option 1 of a country event)"))
        event = view.event
        assert event is not None
        option = self._resolve(event, raw)
        if isinstance(option, str):
            return self._fail(call, "invalid_args", option)
        if option.shown is False:
            return self._fail(call, "rejected", (
                f"option {option.index} ({option.text or option.key}) is not shown to this "
                f"country: its trigger is {display(option.describe().get('shown_if', ''))}. "
                "Nothing was clicked"))
        try:
            point, how, note = self._where(view, event, option)
        except ValueError as exc:
            return self._fail(call, "rejected", f"refused before any click: {exc}")
        label = f"{event.id} option {option.index}"
        if self.driver.dry_run:
            return self._result(call, False, "not_executed", (
                f"dry run: would click {label} ({option.text or option.key}) at {point} "
                f"({how}); the window was read as {event.id} at "
                f"{view.ident.confidence:.2f}"), {"event": event.id, "option": option.index,
                                                  "point": list(point)})
        self.driver.click_event_option(point, label)
        self._sleep(self.settle)
        return self._verify(call, event, option, point, how, note, view)

    # --- steps ----------------------------------------------------------------------------

    @staticmethod
    def _resolve(event: EventInfo, raw: str) -> EventOption | str:
        if not event.options:
            return f"{event.id} has no options in the files"
        if re.fullmatch(r"\d+", raw):
            n = int(raw)
            for option in event.options:
                if option.index == n:
                    return option
            return f"{event.id} has options 1 to {len(event.options)}, not {n}"
        folded = _fold(raw)
        for option in event.options:
            if raw == option.key or (folded and folded in (_fold(option.key), _fold(option.text))):
                return option
        return (f"no option of {event.id} is called {raw!r}: "
                + "; ".join(f"{o.index} {o.key} ({o.text})" for o in event.options))

    def _where(self, view: EventView, event: EventInfo, option: EventOption):
        """The point to click, how it was found, and a note on the layout model's error."""
        geometry = self.reader._geometry()
        slot_px = self.reader.layout.slot[1] * geometry.scale
        box = view.located.get(option.index)
        # The layout model's idea of where this option is: slot i of the visible ones, the first
        # slot from the recorded point when there is one.
        slot = self._slot_of(view, event, option)
        predicted = None
        if slot is not None:
            recorded = self.driver.config.coordinates.get(self.recorded_first)
            if recorded is not None:
                predicted = (recorded[0], round(recorded[1] + slot * slot_px))
            else:
                shown = [o for o in event.options if o.shown is not False]
                boxes = option_boxes(self.reader.layout, geometry, max(len(shown), slot + 1),
                                     self.reader.grow, self.reader.centre_growth)
                predicted = boxes[slot].centre
        if box is not None:
            point, how = box.centre, "where the option's text was read on the screen"
            note = ""
            if predicted is not None:
                note = (f"layout model predicted {predicted}, the text was at {point} "
                        f"(off by {point[0] - predicted[0]}, {point[1] - predicted[1]} px)")
        elif predicted is not None:
            point, how = predicted, ("computed from the window layout"
                                     + (" and the recorded event.option1" if
                                        self.driver.config.coordinates.get(self.recorded_first)
                                        else ""))
            note = "the option's text was not found on the screen, so its place is the layout's"
        else:
            raise ValueError(
                f"option {option.index} was not found on the screen and its slot cannot be "
                "computed: an option before it has a trigger the harness cannot evaluate, so "
                "the number of buttons above it is unknown")
        x, y = point
        left, top = geometry.origin
        if not (left <= x < left + geometry.width and top <= y < top + geometry.height):
            raise ValueError(f"the click point {point} is outside the {geometry.width}x"
                             f"{geometry.height} screen")
        return (int(x), int(y)), how, note

    @staticmethod
    def _slot_of(view: EventView, event: EventInfo, option: EventOption) -> int | None:
        """How many buttons are above ``option`` (its 0-based slot), or None when that depends on
        a trigger nothing here can evaluate."""
        slot = 0
        for earlier in event.options:
            if earlier.index == option.index:
                return slot
            if earlier.index in view.located:
                slot += 1
            elif earlier.shown is True or (earlier.shown is None and not earlier.trigger):
                slot += 1
            elif earlier.shown is False:
                continue
            else:
                return None
        return slot

    def _verify(self, call, event, option, point, how, note, before: EventView) -> ActionResult:
        after = self.reader.look()
        probe = self.driver.event_guard.probe() if self.driver.event_guard is not None else None
        changed = {"event": event.id, "option": option.index, "key": option.key,
                   "clicked_at": list(point), "located_by": how, "channel": "mouse"}
        if note:
            changed["layout_note"] = note
        entry = {"tool": "choose_event_option", "kind": "country", "event": event.id,
                 "title": event.title, "answer": f"model chose option {option.index} "
                 f"({option.key}) at {point}", "how": how}
        if after.status == "unavailable":
            self.reader.record({**entry, "closed": None})
            return self._result(call, False, "unverified", (
                f"clicked {event.id} option {option.index} ({option.text or option.key}) at "
                f"{point}, but the screen could not be read afterwards ({after.why}); treat it as "
                "not confirmed"), changed)
        same = after.event is not None and after.event.id == event.id
        guess = after.ident.event if after.ident is not None else None
        if (after.status == "unidentified" and guess is not None and guess.id == event.id
                and after.ident.confidence >= 0.55):
            same = True                                 # the window's text still reads as it
        if same:
            self.reader.record({**entry, "closed": False})
            return self._result(call, False, "rejected", (
                f"clicked {event.id} option {option.index} at {point} ({how}) but the event "
                "window is still open: the click missed, or the game ignored it. Nothing else "
                "was clicked; read_event again before trying once more"), changed)
        if after.status == "unidentified" or (probe is not None and after.status == "none"):
            self.reader.record({**entry, "closed": None})
            return self._result(call, False, "unverified", (
                f"clicked {event.id} option {option.index} at {point}; the text is gone but the "
                "window probe still sees a window (another may have opened)"), changed)
        next_note = ""
        if after.event is not None:
            next_note = f" The next event is already open: {after.event.id} \"{after.event.title}\"."
            changed["next_event"] = after.event.id
        self.reader.record({**entry, "closed": True})
        effects = option.describe()["effects"]
        return self._result(call, True, None, (
            f"chose option {option.index} of {event.id} ({option.text or option.key}) at {point} "
            f"({how}); the window closed. What it does: {effects}.{next_note}"
            + (f" [{note}]" if note else "")), changed)

    # --- results --------------------------------------------------------------------------

    @staticmethod
    def _result(call, ok, kind, message, changed=None) -> ActionResult:
        return ActionResult(ok=ok, action=call.name, call_id=call.call_id, message=message,
                            error_kind=kind, changed=changed or {})

    def _fail(self, call, kind, message) -> ActionResult:
        return self._result(call, False, kind, message)


def enable_events(driver, reader: EventReader, **kw) -> EventExecutor:
    executor = EventExecutor(driver, reader, **kw)
    driver.enable_events(executor)
    return executor

