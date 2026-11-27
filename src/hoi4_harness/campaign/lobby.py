"""The new-game lobby click path: set the country's AI behaviour rule, turn Historical AI
Focuses off, press Start (#160).

NEEDS LIVE CALIBRATION. Only three points were measured (1920x1080, 2026-11-22): the
Custom Game Rules button, the Historical AI Focuses box and START. Where the country's rule
and its options sit inside the Custom Game Rules screen has not been recorded, so those steps
have no point until the maintainer gives them (``campaign_lobby_points``, see
``docs/campaign.md``). A path with an uncalibrated step is refused before the first click,
so a half-set lobby cannot happen for that reason.

Every click is preceded by a check that the game window is in front; if it is not, the path
stops there (nothing is clicked into another window). The clicker is injected: the tests give
one that records, the live one (``LiveClicker``) uses pyautogui and the window title guard.
It is never built unless ``campaign_lobby_rule`` is on.

The Historical AI Focuses box is a toggle the harness cannot read, so the path assumes the
lobby opened with the game's default (on) and clicks it once. The first save's header
(``historical=``) is the proof, read back by the runner and logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

#: Measured on the maintainer's machine, 1920x1080.
CUSTOM_RULES_BUTTON = (1640, 764)
HISTORICAL_BOX = (1862, 929)
START_BUTTON = (1742, 1028)


class LobbyError(RuntimeError):
    """The lobby path could not run (uncalibrated step, window not in front, no input)."""


class NeedsCalibration(LobbyError):
    """A step has no screen point yet."""


@dataclass
class Step:
    key: str
    label: str
    point: tuple[int, int] | None
    note: str = ""


class Clicker(Protocol):
    def in_front(self) -> bool: ...
    def click(self, point: tuple[int, int], label: str) -> None: ...


def steps_for(token: str | None, option: str | None,
              points: dict | None = None, scan: bool = False) -> list[Step]:
    """The path's steps, with any operator-supplied points filled in.

    ``points`` keys: ``rule_row`` (the country's rule in the list), ``option:<NAME>`` or
    ``option`` (the chosen option), ``rules_close`` (leave Custom Game Rules), and optionally
    ``custom_rules``/``historical_ai``/``start`` to replace the measured ones. With ``scan`` the
    three window-internal steps are left out: ``lobby_scan.RuleScanner`` does them by reading
    the window, and what is returned is Custom Game Rules, the Historical box and START."""
    points = {k: tuple(v) for k, v in (points or {}).items()}

    def pick(*keys, default=None):
        for key in keys:
            if key in points:
                return points[key]
        return default

    steps = [Step("custom_rules", "Custom Game Rules button",
                  pick("custom_rules", default=CUSTOM_RULES_BUTTON))]
    if token and option and not scan:
        steps += [
            Step("rule_row", f"the {token} row in the rules list", pick("rule_row"),
                 "position inside Custom Game Rules not recorded yet"),
            Step(f"option:{option}", f"option {option}", pick(f"option:{option}", "option"),
                 "position of the option not recorded yet"),
            Step("rules_close", "leave Custom Game Rules", pick("rules_close"),
                 "position of the close/confirm button not recorded yet"),
        ]
    steps += [
        Step("historical_ai", "Historical AI Focuses box (toggle, assumed on at entry)",
             pick("historical_ai", default=HISTORICAL_BOX)),
        Step("start", "START", pick("start", default=START_BUTTON)),
    ]
    return steps


def uncalibrated(steps: list[Step]) -> list[Step]:
    return [s for s in steps if s.point is None]


def run_steps(steps: list[Step], clicker: Clicker, log: Callable[[str], None],
              pause: Callable[[float], None] = time.sleep, settle: float = 0.8) -> int:
    """Click the steps in order, the window-in-front check before each one. Returns the number
    clicked. Raises ``NeedsCalibration`` before any click if a step has no point, and
    ``LobbyError`` at the step where the game window is not in front."""
    missing = uncalibrated(steps)
    if missing:
        raise NeedsCalibration(
            "the lobby click path needs live calibration for: "
            + "; ".join(f"{s.key} ({s.note or s.label})" for s in missing)
            + ". Record those points and pass them as campaign_lobby_points; nothing was clicked.")
    done = 0
    for step in steps:
        if not clicker.in_front():
            raise LobbyError(f"the game window is not in front before '{step.label}'; stopped "
                             f"after {done} of {len(steps)} clicks, nothing more was clicked")
        log(f"lobby: click {step.label} at {step.point}")
        clicker.click(step.point, step.label)
        done += 1
        pause(settle)
    return done


class LiveClicker:
    """The real one: pyautogui, behind the focused-window guard (adapters/window.py)."""

    def __init__(self, window_title: str, move_duration: float = 0.25):
        self.window_title, self.move_duration = window_title, move_duration
        try:
            import pyautogui
        except ImportError as exc:
            raise LobbyError("the lobby click path needs pyautogui: pip install "
                             "'hoi4-agent-harness[input]'") from exc
        pyautogui.FAILSAFE = True
        self._gui = pyautogui

    def in_front(self) -> bool:
        from ..adapters.window import check_focus

        return check_focus(self.window_title).focused

    def click(self, point: tuple[int, int], label: str) -> None:
        self._gui.moveTo(point[0], point[1], duration=self.move_duration)
        self._gui.click()

    def scroll(self, point: tuple[int, int], clicks: int) -> None:
        """Wheel at ``point``: positive clicks scroll up, negative down."""
        self._gui.moveTo(point[0], point[1], duration=self.move_duration)
        self._gui.scroll(clicks)

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        self._gui.moveTo(start[0], start[1], duration=self.move_duration)
        self._gui.dragTo(end[0], end[1], duration=0.6, button="left")
