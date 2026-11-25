"""Write-side sink: perform actions by driving keyboard and mouse.

The mechanics (focus the window, move, click, type, hotkey) are here. So is the
part this file is structured around: a blind click is unverifiable, so every
scripted action acts, re-reads through the reader it is composed with, and
confirms the state actually changed -- reporting failure, or "unverified",
rather than assuming success. The click paths themselves are recorded by the
operator (``ui_scripts.py``); an action with no recorded path is refused. The
clock is held to the same rule: the pause key is a toggle, so the game's date is
watched before and after it is pressed (``_drive_clock``).

Hotkeys are far more reliable than coordinates and should be preferred wherever
the game exposes one. Coordinates are resolution- and UI-scale-dependent and
belong in a calibration file, not in code; ``load_calibration`` fills
``InputConfig.coordinates`` from that file and refuses one captured at another
resolution.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..calibration import load_calibration, screen_size
from ..observation.builder import snapshot
from ..paths import find_game_dir
from ..types import ActionCall, ActionResult, GameState
from ..uilayout import DisplaySettings, Geometry, LayoutRefused, geometry_for, read_display_settings
from .base import AdapterInfo, GameAdapter
from .event_windows import EventOpen
from .keys import GuiKeys, ScanCodeKeys, text_chords
from .ui_resolve import Unresolvable, is_computed, resolve
from .ui_scripts import Step, placeholders, unsafe_escape, verify
from .window import FocusCheck, check_focus, client_rect

#: Panel and clock hotkeys for HOI4 1.19.5. Where the game states a binding it is
#: in ``interface/topbar.gui`` (a button's ``shortcut =``; see ``topbar_shortcuts``);
#: each was also tried in the live game. Two are not in that file as literals:
#: ``research`` is the ``research_open`` keybind (w), and the political panel
#: opens from the player flag (q). There is no diplomacy panel hotkey: topbar.gui
#: binds ``e`` to the international market button, and ``r`` to trade.
HOTKEYS = {
    "pause": "space",
    "speed_up": "kp_plus",       # topbar.gui button_speedup
    "speed_down": "kp_minus",    # topbar.gui button_speeddown
    "national_focus": "alt+q",   # topbar.gui national_focus_shortcut
    "research": "w",             # keybind research_open
    "construction": "t",         # topbar.gui construction_button
    "production": "y",           # topbar.gui production_button
    "decisions": "shift+q",      # topbar.gui decisionview_button
    "politics": "q",             # topbar.gui player_flag
    "international_market": "e",
    "trade": "r",
}
#: Searching inside a panel: the focus tree's search bar, and the map search when
#: no focus tree is open (the magnifier at the map's bottom right does the same).
SEARCH_KEY = "ctrl+f"


def topbar_shortcuts(text: str) -> dict[str, str]:
    """Every ``shortcut = "..."`` in a topbar.gui, keyed by the button name it
    sits in. How the table above was derived, and how to re-derive it for another
    game version or a mod that rebinds them."""
    found: dict[str, str] = {}
    name = None
    for line in text.splitlines():
        match = re.match(r'\s*name\s*=\s*"([^"]+)"', line)
        if match:
            name = match.group(1)
        match = re.match(r'\s*shortcut\s*=\s*"([^"]+)"', line)
        if match and name:
            found.setdefault(name, match.group(1).lower())
    return found


#: How often the clock check re-reads the date while it watches.
CLOCK_POLL_SECONDS = 0.5


def _wanted(steps: list[Step]) -> set[str]:
    return placeholders(steps) | {s.value for s in steps if s.kind == "click"}


def _expand_repeats(steps: list[Step]) -> list[Step]:
    """``{"repeat": N}`` runs the step before it N times in all."""
    out: list[Step] = []
    for step in steps:
        if step.kind == "repeat":
            times = int(float(step.value))
            if times < 1:
                raise KeyError(f"repeat {step.value}: must be at least 1")
            out.extend([out[-1]] * (times - 1))
        else:
            out.append(step)
    return out


class NotFocused(RuntimeError):
    """The game was not the focused window, so nothing was sent."""


class InputInterrupted(Exception):
    """A guard stopped the keys between two of them (rival mode: the person started typing,
    unpaused, or took the keyboard back). Not an error to report as a failed action: the
    caller re-queues what had not run."""


def default_display_settings() -> DisplaySettings | None:
    """The display settings of the user's game directory, or None when unreadable."""
    user_dir = find_game_dir()
    if user_dir is None:
        return None
    try:
        return read_display_settings(user_dir)
    except LayoutRefused:
        return None


class MouseDisabled(RuntimeError):
    """A step needed the mouse and this driver was built without one (headless)."""


class ClockUnobservable(RuntimeError):
    """Whether the game is paused could not be seen, so the clock was not driven."""


@dataclass
class InputConfig:
    #: Matched as a case-insensitive substring: the real title carries a suffix
    #: ("Hearts of Iron IV (OpenGL)"), so an exact match never fires.
    window_title: str = "Hearts of Iron IV"
    move_duration: float = 0.08
    settle_seconds: float = 0.35
    coordinates: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Refuse to send input unless the game is focused. Turning this off is an
    #: explicit choice to let keystrokes land wherever they land.
    enforce_focus: bool = True
    #: How long the date has to stand still before the game counts as paused.
    #: The readers see one date per in-game day, so this must be longer than a
    #: day takes at the speed the game runs. Too short shows up at an unpause:
    #: the date is not seen to move, and the clock refuses to go on.
    clock_probe_seconds: float = 10.0
    #: The resolution the calibration was captured at (set by load_calibration);
    #: the bound a computed click point is checked against.
    screen: tuple[int, int] | None = None
    #: Pin the window the computed points are placed in (what tests and a fixed
    #: setup pass); by default it is read from the settings and the live window.
    geometry: Geometry | None = None
    #: False builds a keyboard-only driver: ``click`` refuses, so no path through
    #: it can reach pyautogui or move the pointer (headless play).
    mouse: bool = True
    #: Switch the game window to the US keyboard layout before sending keys (the scan codes are
    #: the US layout's; window.ensure_us_layout). False leaves the layout alone.
    us_layout: bool = True


class PersonActive(InputInterrupted):
    """A person used the keyboard or mouse, so the harness did not start (``phase`` ``start``)
    or stopped sending partway (``mid``). Raised by ``input_guard.InputGuard`` (issue #33)."""

    def __init__(self, message: str, *, phase: str, path: str, label: str):
        super().__init__(message)
        self.phase, self.path, self.label = phase, path, label


class InputDriverAdapter(GameAdapter):
    """Applies actions through synthetic input. Reads nothing on its own.

    Compose with a read-capable adapter (savegame or screen) via CompositeAdapter.
    """

    BUILT_IN = frozenset({"set_game_speed", "advance_time", "note"})

    def __init__(
        self,
        config: InputConfig | None = None,
        dry_run: bool = True,
        focus_check=check_focus,
        scripts: dict[str, list[Step]] | None = None,
        read_state: Callable[[], GameState] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        verify_timeout: float = 3.0,
        gui=None,
        keys=None,
        index=None,
        display_settings: Callable[[], DisplaySettings | None] | None = None,
        window_rect: Callable[[], tuple[int, int, int, int] | None] | None = None,
    ):
        """``gui`` stands in for pyautogui -- what tests inject, so a test suite
        for an input driver cannot drive the machine it runs on.

        ``keys`` is the keyboard: anything with ``chord(spec)`` and
        ``type_text(text)``. By default hardware scan codes (keys.py), the only
        keys HOI4 accepts; with only ``gui`` injected, keys go through it. ``index``
        is the identifier index that localised names and tech positions come from."""
        self.config = config or InputConfig()
        self.dry_run = dry_run
        self.log: list[str] = []
        self._focus_check = focus_check
        #: Recorded click paths, one per action (see ui_scripts.py). An action
        #: without one is refused: refusing is the honest answer.
        self.scripts = dict(scripts or {})
        self._read_state = read_state
        self._sleep = sleep
        self.verify_timeout = verify_timeout
        self.supported_actions = self.BUILT_IN | frozenset(self.scripts)
        self._gui_override = gui
        self._keys_override = keys
        self._keys_cache = None
        self.index = index
        self._display_settings = display_settings or (lambda: default_display_settings())
        self._window_rect = window_rect or (lambda: client_rect())
        #: Performs the division orders, once ``enable_unit_orders`` has set it.
        self.order_executor = None
        self.plan_executor = None
        self.army_executor = None
        #: Edits production lines by click (adapters/production_ui.py).
        self.production_executor = None
        #: Click points computed for the action in flight (see ui_resolve.py).
        self._computed: dict[str, tuple[int, int]] = {}
        #: Whether the game was running when the clock was last seen: True,
        #: False, or None for not known. It decides whether to look or to press
        #: first, never whether to look at all (see _drive_clock).
        self._running: bool | None = None
        #: Called once after a scripted action's input and before it is verified: the
        #: independent campaign asks the mod for fresh telemetry there, so a paused game can
        #: still show what the click did. None: nothing (every other mode).
        self.after_input: Callable[[], None] | None = None
        #: Rival mode's guard (``rival.safety.KeyWatch``): asked before every key and every
        #: typed character, it may raise ``InputInterrupted``. None: no guard.
        self.key_watch = None
        #: The shared person-activity guard (``input_guard.InputGuard``, issue #33): every key,
        #: typed text, click and drag goes through it. None: no guard.
        self.input_guard = None
        #: Clears the event and news windows before a mouse tool runs (event_windows.py); None
        #: when no probe is configured, in which case nothing here knows a window is open.
        self.event_guard = None
        self._in_event_steps = False
        #: Chooses an option of the open event window (adapters/event_choice.py); None: off.
        self.event_executor = None

    def geometry(self) -> Geometry:
        """The window computed click points are placed in. Raises LayoutRefused.

        Pinned, else the calibration's screen (checked against the real screen when
        it was loaded) with the settings' UI scale, else -- live -- the settings
        checked against the window actually on screen: a mismatch, or a window or
        settings that cannot be read, refuses. A dry run sends nothing, so it
        falls back to the settings' size or 1920x1080 rather than refusing."""
        if self.config.geometry is not None:
            return self.config.geometry
        settings = self._display_settings()
        scale = settings.gui_scale if settings else 1.0
        if self.config.screen is not None:
            return Geometry(*self.config.screen, gui_scale=scale)
        if self.dry_run:
            size = settings.resolution if settings and settings.resolution else (1920, 1080)
            return Geometry(*size, gui_scale=scale)
        return geometry_for(settings, self._window_rect())

    def attach_reader(self, read_state: Callable[[], GameState]) -> None:
        """Give the writer eyes: the reader that verifies what it did."""
        self._read_state = read_state

    # --- the guard -----------------------------------------------------------

    def focus(self) -> FocusCheck:
        """Where would input land right now?"""
        return self._focus_check(self.config.window_title)

    def _require_focus(self) -> None:
        """Raise unless it is safe to send input.

        Skipped in dry-run, where nothing is sent. An unsupported platform
        refuses rather than passing: a guard that cannot check must not pretend
        it did.
        """
        if self.dry_run or not self.config.enforce_focus:
            return
        result = self.focus()
        if result.focused:
            return
        if not result.supported:
            raise NotFocused(
                f"cannot verify the focused window ({result.reason}); refusing to send input. "
                "Set enforce_focus=False to send anyway."
            )
        raise NotFocused(f"{self.config.window_title!r} is not focused: {result.reason}")

    def _require_layout(self) -> None:
        """Before a key: the focused game window must type with the US layout the scan codes
        assume. Switched once if it does not (logged); refused (NotFocused) if it cannot be."""
        if self.dry_run or not self.config.us_layout:
            return
        if self._keys_override is not None or self._gui_override is not None:
            return                      # a fake or injected sender: no real window to switch
        from .window import LayoutUnavailable, ensure_us_layout

        try:
            line = ensure_us_layout()
        except LayoutUnavailable as exc:
            raise NotFocused(str(exc)) from None
        if line:
            self.log.append(line)

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="input",
            readable=False,
            writable=True,
            clock_control=True,
            notes=f"dry_run={self.dry_run}; requires the 'input' extra (pyautogui)",
        )

    def _gui(self):
        if self._gui_override is not None:
            return self._gui_override
        try:
            import pyautogui
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pip install 'hoi4-agent-harness[input]' for input") from exc
        pyautogui.FAILSAFE = True  # slam the mouse into a screen corner to abort
        return pyautogui

    def _keys(self):
        if self._keys_override is not None:
            return self._keys_override
        if self._gui_override is not None:
            return GuiKeys(self._gui_override)
        if self._keys_cache is None:
            self._keys_cache = ScanCodeKeys(sleep=self._sleep)
        return self._keys_cache

    def _guarded(self, path: str, label: str):
        """The shared guard's bracket around one input sequence (a no-op without a guard and in a
        dry run, where nothing is sent)."""
        if self.input_guard is None or self.dry_run:
            return nullcontext()
        return self.input_guard.sequence(path, label)

    def _checkpoint(self, key: str | None = None) -> None:
        """Before each key, character, click or drag step: stop if a person has taken over. The
        console key that closes a console the guard saw opened is the one that still goes."""
        if self.input_guard is not None and not self.dry_run:
            self.input_guard.checkpoint(closing=key is not None and key == self.input_guard.console_key)

    def _person_took_over(self) -> bool:
        """A soft look (nothing raised): True once a person has taken over, so a caller holding a
        mouse button can let go before the hard checkpoint raises."""
        if self.input_guard is None or self.dry_run:
            return False
        self.input_guard.checkpoint(soft=True)
        return self.input_guard.tripped is not None

    def _sent_keys(self) -> None:
        """After keys we sent: the key layer reports each event itself when the guard is hooked
        to it (reading the stamp here would swallow a person's key pressed in a chord's gaps)."""
        if self.input_guard is not None and not self.input_guard.keys_hooked:
            self._sent()

    def _sent(self) -> None:
        if self.input_guard is not None and not self.dry_run:
            self.input_guard.sent()

    def press(self, key: str) -> None:
        """Press a key or chord (``alt+q``); a name in HOTKEYS means its binding."""
        key = HOTKEYS.get(key, key)
        with self._guarded("key", key):
            if self.key_watch is not None and not self.dry_run:
                self.key_watch.before("press", key)
            self._checkpoint(key)
            self._require_focus()
            self._require_layout()
            self.log.append(f"press {key}")
            if not self.dry_run:
                self._keys().chord(key)
                self._sent_keys()
                if self.input_guard is not None:
                    self.input_guard.key_pressed(key)
                    self._checkpoint(key)      # a keypress inside our chord is raised now
                if self.key_watch is not None:
                    self.key_watch.after("press", key)

    def _event_gate(self, what: str) -> None:
        """Refuse a click while an event window is known to be open (a window that opened in the
        middle of a click path stops the rest of it). Not for the guard's own answering."""
        if self.event_guard is None or self._in_event_steps or self.dry_run:
            return
        sighting = self.event_guard.probe()
        if sighting is not None:
            raise EventOpen(f"a {sighting.kind} event window is open: {what} was not clicked")

    def run_event_steps(self, steps: list[Step]) -> None:
        """The guard's answer to a window: its steps, without the gate that would refuse them."""
        self._in_event_steps = True
        try:
            for step in steps:
                self._run_step(step)
        finally:
            self._in_event_steps = False

    def click_event_option(self, point: tuple[int, int], label: str) -> None:
        """Click an option of the open event window. The same guards as ``click_at`` (mouse,
        focus, bounds, dry run), except the open-window gate: the window being open is the
        reason for the click."""
        self._in_event_steps = True
        try:
            self.click_at(point, label=label)
        finally:
            self._in_event_steps = False

    def click(self, target: str) -> None:
        if not self.config.mouse:
            raise MouseDisabled(f"click {target}: this driver has no mouse (headless)")
        with self._guarded("click", target):
            self._require_focus()
            self._event_gate(f"click {target}")
            point = self._computed.get(target) or self.config.coordinates.get(target)
            if point is None:
                raise KeyError(f"No calibrated coordinate for {target!r}. Run: hoi4-harness calibrate")
            self.log.append(f"click {target} at {point}")
            if not self.dry_run:
                gui = self._gui()
                self._checkpoint()
                gui.moveTo(point[0], point[1], duration=self.config.move_duration)
                self._checkpoint()
                gui.click()
                self._sent()

    def _checked_point(self, point: tuple[int, int]) -> tuple[int, int]:
        """A computed click point, refused when it is off the screen it was fitted for."""
        x, y = int(point[0]), int(point[1])
        if self.config.screen and not (0 <= x < self.config.screen[0] and 0 <= y < self.config.screen[1]):
            raise ValueError(f"click point ({x}, {y}) is outside the {self.config.screen[0]}x"
                             f"{self.config.screen[1]} screen")
        return x, y

    def click_at(self, point: tuple[int, int], button: str = "left", *, shift: bool = False,
                 label: str = "") -> None:
        """Click a computed screen point (unit orders). Same guards as ``click``:
        no mouse refuses, focus is required, a dry run only logs. ``shift`` holds
        the key (as a scan code) around the click, which queues a waypoint."""
        if not self.config.mouse:
            raise MouseDisabled(f"{button}-click at {point}: this driver has no mouse (headless)")
        x, y = self._checked_point(point)
        with self._guarded("click", label or f"({x}, {y})"):
            self._require_focus()
            self._event_gate(f"click {label or 'point'} at ({x}, {y})")
            self.log.append(
                f"{'shift+' if shift else ''}{button}-click {label or 'point'} at ({x}, {y})")
            if self.dry_run:
                return
            gui = self._gui()
            self._checkpoint()
            gui.moveTo(x, y, duration=self.config.move_duration)
            self._checkpoint()
            if shift:
                with self._keys().held("shift"):
                    self._sent()
                    gui.click(button=button)
            else:
                gui.click(button=button)
            self._sent()

    def drag(self, start: tuple[int, int], end: tuple[int, int], *, label: str = "") -> None:
        """Press at ``start``, move to ``end``, release: a selection box."""
        if not self.config.mouse:
            raise MouseDisabled(f"drag {start} -> {end}: this driver has no mouse (headless)")
        x0, y0 = self._checked_point(start)
        x1, y1 = self._checked_point(end)
        with self._guarded("drag", label or "box"):
            self._require_focus()
            self.log.append(f"drag {label or 'box'} {(x0, y0)} -> {(x1, y1)}")
            if self.dry_run:
                return
            gui = self._gui()
            self._checkpoint()
            gui.moveTo(x0, y0, duration=self.config.move_duration)
            self._checkpoint()
            gui.mouseDown(button="left")
            self._sent()
            try:
                gui.moveTo(x1, y1, duration=self.config.move_duration * 3)
            finally:
                gui.mouseUp(button="left")      # a button is never left down
                self._sent()
            self._checkpoint()

    def drag_path(self, points: list[tuple[int, int]], *, label: str = "") -> None:
        """Press at the first point, move through the others, release at the last: a line drawn
        with a battle-plan tool (adapters/battle_plans.py). Same guards as ``drag``."""
        if not self.config.mouse:
            raise MouseDisabled(f"drag path {points}: this driver has no mouse (headless)")
        if len(points) < 2:
            raise ValueError("a path needs at least two points")
        checked = [self._checked_point(p) for p in points]
        with self._guarded("drag", label or "line"):
            self._require_focus()
            self.log.append(f"drag path {label or 'line'} " + " -> ".join(str(p) for p in checked))
            if self.dry_run:
                return
            gui = self._gui()
            self._checkpoint()
            gui.moveTo(checked[0][0], checked[0][1], duration=self.config.move_duration)
            self._checkpoint()
            gui.mouseDown(button="left")
            self._sent()
            try:
                for x, y in checked[1:]:
                    if self._person_took_over():
                        break          # let go below, then the stop is raised
                    gui.moveTo(x, y, duration=self.config.move_duration * 3)
            finally:
                gui.mouseUp(button="left")      # a button is never left down
                self._sent()
            self._checkpoint()

    def enable_battle_plans(self, executor) -> None:
        """Offer ``draw_front_line``, ``draw_offensive`` and ``assign_battle_plan``, performed by
        ``executor`` (adapters/battle_plans.py). UNVERIFIED against the real game."""
        from .battle_plans import PLAN_ACTIONS

        if not self.config.mouse:
            raise MouseDisabled("battle plans are drawn with the mouse and this driver has none")
        self.plan_executor = executor
        self.supported_actions = self.supported_actions | frozenset(PLAN_ACTIONS)

    def enable_army_ui(self, executor) -> None:
        """Offer the army actions (create_army, assign_divisions, assign_army_front, ...), performed
        by ``executor`` (adapters/armies.py). UNVERIFIED against the real game."""
        from .armies import ARMY_UI_ACTIONS

        if not self.config.mouse:
            raise MouseDisabled("armies are managed with the mouse and this driver has none")
        self.army_executor = executor
        self.supported_actions = self.supported_actions | frozenset(ARMY_UI_ACTIONS)

    def enable_events(self, executor) -> None:
        """Offer ``choose_event_option``, performed by ``executor`` (adapters/event_choice.py).
        UNVERIFIED against the real game."""
        from .event_choice import ACTIONS

        if not self.config.mouse:
            raise MouseDisabled("choosing an event option is a click and this driver has no mouse")
        self.event_executor = executor
        self.supported_actions = self.supported_actions | frozenset(ACTIONS)

    def enable_production(self, executor) -> None:
        """Perform ``set_production`` (editing a line that exists) and, when ``offer_equipment``,
        ``change_production_equipment``, by click, through ``executor`` (adapters/production_ui.py).
        UNVERIFIED against the real game."""
        from .production_ui import PRODUCTION_ACTIONS

        if not self.config.mouse:
            raise MouseDisabled("production by click needs a mouse and this driver has none")
        self.production_executor = executor
        self.supported_actions = self.supported_actions | frozenset(PRODUCTION_ACTIONS)

    def enable_unit_orders(self, executor) -> None:
        """Offer ``move_division`` and ``attack_province``, performed by ``executor``
        (adapters/unit_orders.py). Needs a mouse: a headless driver refuses."""
        from .unit_orders import ORDER_ACTIONS

        if not self.config.mouse:
            raise MouseDisabled("division orders are clicks and this driver has no mouse")
        self.order_executor = executor
        self.supported_actions = self.supported_actions | frozenset(ORDER_ACTIONS)

    def type_text(self, text: str) -> None:
        with self._guarded("type", text):
            self._checkpoint()
            self._require_focus()
            self._require_layout()
            self.log.append(f"type {text}")
            if not self.dry_run:
                if self.key_watch is None and self.input_guard is None:
                    self._keys().type_text(text)
                    return
                text_chords(text)         # every character checked before any is sent
                for char in text:         # one at a time, so a guard can stop between them
                    if self.key_watch is not None:
                        self.key_watch.before("type", char)
                    self._checkpoint()
                    self._keys().type_text(char)
                    self._sent_keys()
                self._checkpoint()

    def _run_step(self, step: Step) -> None:
        if step.kind == "repeat":      # expanded before the run (_expand_repeats)
            return
        if step.kind == "press":
            self.press(step.value)
        elif step.kind == "click":
            self.click(step.value)
        elif step.kind == "type":
            self.type_text(step.value)
        elif step.kind == "wait":
            self.log.append(f"wait {step.value}")
            if not self.dry_run:
                self._sleep(float(step.value))

    def _scripted(self, call: ActionCall) -> ActionResult:
        """Run the recorded click path, then prove it worked."""
        template = self.scripts[call.name]
        escape = unsafe_escape(template)
        if escape:
            return self._result(call, False, "rejected", f"the {call.name} script {escape}")
        try:
            resolved = resolve(call.name, call.arguments, self.index, self.config.coordinates,
                               self.config.screen, wanted=_wanted(template),
                               geometry=self.geometry)
        except Unresolvable as exc:
            return self._result(call, False, "rejected", f"{exc}; nothing was sent")
        self._computed = resolved.coordinates
        steps = _expand_repeats([step.fill(resolved.arguments) for step in template])
        have = set(self.config.coordinates) | set(self._computed)
        missing = [s.value for s in steps if s.kind == "click" and s.value not in have
                   and not is_computed(s.value)]
        if missing:
            # Before any input: half a script is worse than none.
            return self._result(call, False, "rejected", (
                f"no calibrated coordinate for {', '.join(missing)}. Run: hoi4-harness calibrate "
                + " ".join(missing)
            ))
        if self.dry_run:
            try:
                for step in steps:
                    self._run_step(step)
            finally:
                self._computed = {}
            return self._result(call, False, "not_executed", (
                "dry run: would " + "; ".join(s.describe() for s in steps)
            ))
        if self._read_state is None:
            return self._result(call, False, "unverified",
                                "no reader to verify with; nothing was sent")
        try:
            return self._send_and_verify(call, steps)
        finally:
            self._computed = {}

    def _send_and_verify(self, call: ActionCall, steps: list[Step]) -> ActionResult:
        before = snapshot(self._read_state())
        for step in steps:
            self._run_step(step)
        self._sleep(self.config.settle_seconds)
        if self.after_input is not None:
            try:
                self.after_input()
            except (NotFocused, MouseDisabled, ClockUnobservable, OSError, KeyError, ValueError):
                pass          # the verification below reports what the reader can see
        outcome = verify(call, before, self._read_state, timeout=self.verify_timeout,
                         sleep=self._sleep)
        return self._result(call, outcome.ok, outcome.kind, outcome.message)

    @staticmethod
    def _result(call: ActionCall, ok: bool, kind: str | None, message: str) -> ActionResult:
        return ActionResult(ok=ok, action=call.name, call_id=call.call_id,
                            message=message, error_kind=kind)

    def load_calibration(
        self, path: Path, *, current_resolution: tuple[int, int] | None = None
    ) -> None:
        """Populate ``config.coordinates`` from a calibration file, refusing a stale one.

        ``current_resolution`` stands in for the real screen -- what tests inject.
        Without it a live run probes the screen (so it needs the 'input' extra to
        load at all), while a dry run skips the probe: it sends no clicks, so a
        stale calibration cannot misfire there.
        """
        resolution = current_resolution
        if resolution is None and not self.dry_run:
            resolution = screen_size()
        calibration = load_calibration(path, current_resolution=resolution)
        self.config.coordinates = dict(calibration.coordinates)
        self.config.screen = calibration.screen

    def read_state(self) -> GameState:
        raise NotImplementedError("Write-only adapter; compose it with a reader.")

    def apply(self, call: ActionCall) -> ActionResult:
        try:
            return self._apply(call)
        except PersonActive as exc:
            # A person is at the keyboard: say so and that nothing (more) was sent. Not "rejected":
            # the action was fine, the moment was not.
            return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                message=str(exc), error_kind="not_executed")
        except (NotFocused, MouseDisabled, ClockUnobservable, EventOpen, KeyError,
                ValueError) as exc:
            # The same contract as every other adapter failure: report it, do not
            # pretend the action happened. The agent can read this and wait.
            return ActionResult(
                ok=False,
                action=call.name,
                call_id=call.call_id,
                message=str(exc),
                error_kind="rejected",
            )

    def _apply(self, call: ActionCall) -> ActionResult:
        if call.name == "note":
            return ActionResult(ok=True, action=call.name, call_id=call.call_id, message="Noted.")
        if call.name == "set_game_speed":
            speed = int(call.arguments["speed"])
            if speed == 0:
                # The pause key is a toggle: pressed on a paused game it unpauses.
                self.pause()
                return ActionResult(ok=True, action=call.name, call_id=call.call_id,
                                    message="Paused.")
            # Nothing reads the game's speed back, and '+' moves it one step from
            # wherever it is, so a speed cannot be set -- only guessed at. Say so
            # rather than press blind and report a speed that may not be true.
            return ActionResult(
                ok=False, action=call.name, call_id=call.call_id,
                message=(f"Speed {speed} not set: this driver cannot read the game's speed, "
                         "so it cannot set one. The harness runs the clock between turns; "
                         "set the speed in the game before the run."),
                error_kind="unsupported",
            )
        if call.name == "advance_time":
            return ActionResult(ok=True, action=call.name, call_id=call.call_id,
                                message="Clock handed back to the harness.")
        notes = self._clear_events(call)
        result = self._dispatch_mouse(call)
        if notes:
            result = replace(
                result, message=("Before this, " + "; ".join(
                    f"a {n['kind']} event window was open and I {n['answer']}"
                    for n in notes) + ". " + result.message),
                changed={**result.changed, "events_answered": notes})
        return result

    def _uses_mouse(self, call: ActionCall) -> bool:
        if self.event_executor is not None and call.name in self.event_executor.actions:
            return False          # answering the window is the point: it is not cleared first
        if call.name in self.scripts:
            return any(step.kind == "click" for step in self.scripts[call.name])
        for executor in (self.production_executor, self.plan_executor, self.army_executor):
            if (executor is not None and call.name in executor.actions
                    and call.name in self.supported_actions):
                return True
        return self.order_executor is not None and call.name in self.supported_actions

    def _clear_events(self, call: ActionCall) -> list[dict]:
        """Close the event windows that are open before a mouse tool clicks (event_windows.py).
        Raises EventOpen when one cannot be closed, with nothing clicked."""
        if self.event_guard is None or self.dry_run or not self._uses_mouse(call):
            return []
        if not self.config.mouse:
            return []
        self._require_focus()
        return self.event_guard.clear(self, call.name)

    def _dispatch_mouse(self, call: ActionCall) -> ActionResult:
        if (self.event_executor is not None and call.name in self.event_executor.actions
                and call.name in self.supported_actions):
            return self.event_executor(call)
        if call.name in self.scripts:
            return self._scripted(call)
        if (self.production_executor is not None and call.name in self.production_executor.actions
                and call.name in self.supported_actions):
            return self.production_executor(call)
        if (self.plan_executor is not None and call.name in self.plan_executor.actions
                and call.name in self.supported_actions):
            return self.plan_executor(call)
        if (self.army_executor is not None and call.name in self.army_executor.actions
                and call.name in self.supported_actions):
            return self.army_executor(call)
        if self.order_executor is not None and call.name in self.supported_actions:
            return self.order_executor(call)
        return self.unsupported(call)

    # --- the clock -----------------------------------------------------------

    def pause(self) -> None:
        self._drive_clock(running=False)

    def resume(self, speed: int = 3) -> None:
        # ``speed`` is not applied: nothing reads the game's speed back, so the
        # game runs at whatever speed it was left at, as before.
        self._drive_clock(running=True)

    def _drive_clock(self, running: bool) -> None:
        """Leave the game running or paused, whatever it was doing before.

        The pause key is a toggle, so a blind press is right only when the game
        happens to be in the state the harness assumes -- and HOI4 loads paused.
        So: look first when the game is believed to be there already, or nothing
        is known; press first when it is believed to be in the other state; and
        look after every press. A press that did not take (the belief was stale,
        or someone else pressed the key) is followed by one more. If the date
        still does not do what it should, the clock cannot be seen from here,
        and it is not driven blind: this raises.
        """
        if self.dry_run:
            self.log.append(f"{'resume' if running else 'pause'}: dry run, nothing sent")
            return
        if self._read_state is None:
            raise ClockUnobservable(
                "no reader to see whether the game is paused, and the pause key is a "
                "toggle; refusing to press it blind"
            )
        self._require_focus()          # before spending a probe window on a lost cause
        believed, self._running = self._running, None
        if believed is None or believed == running:
            if self._date_moves() == running:
                self._running = running
                return
        for _ in range(2):
            self.press("pause")
            self._sleep(self.config.settle_seconds)
            if self._date_moves() == running:
                self._running = running
                return
        probe = self.config.clock_probe_seconds
        if running:
            raise ClockUnobservable(
                f"the in-game date did not move within {probe:g} s after either of two "
                "presses of the pause key, so whether the game is paused cannot be seen. "
                f"If a day takes longer than {probe:g} s at this speed, raise "
                "clock_probe_seconds or run the game faster."
            )
        raise ClockUnobservable(
            f"the in-game date kept moving after each of two presses of the pause key "
            f"(watched {probe:g} s each time), so the game could not be paused"
        )

    def _date_moves(self) -> bool:
        """Watch the date for up to ``clock_probe_seconds``; True once it moves.

        A moving date proves the game runs. A still one is taken as paused only
        after the whole window, since a running game shows a new date just once
        per in-game day. Time is counted in the sleeps, so slow reads only make
        the window longer.
        """
        start = self._read_state().date
        waited = 0.0
        while waited < self.config.clock_probe_seconds:
            self._sleep(CLOCK_POLL_SECONDS)
            waited += CLOCK_POLL_SECONDS
            if self._read_state().date != start:
                return True
        return False

    def advance(self, days: int) -> GameState:
        raise NotImplementedError("Compose with a reader to observe the clock.")
