"""Battle plans by click: draw a front line, draw an offensive, assign an army (#85, stage 4).

**NOT live-verified.** Nothing here has run in the real game; it is built against the fake game
to the standard of ``unit_orders.py`` (refusals before any input, computed points, dry run,
the driver's guards) and every step marked below as an assumption has to be checked in the
live session before anything relies on it. ``docs/unit-orders.md`` section 7 lists them.

What the game's interface files say (``interface/battleplantools.gui``, 1.19.5):

* the *draw defence* tool is a button with ``shortcut = "z"`` (l.131-134): the front line;
* the *draw offence* tool is a button with ``shortcut = "x"`` (l.148-152): the offensive arrow;
* ``btn_assign_units`` (l.202-207) has **no shortcut**, so its screen position is a recorded
  point (``plan.assign_units`` in ``calibration.json``), not computed;
* the file does not say how the tool window is opened, whether a line is drawn by dragging or by
  clicking its points, or what "assign units" expects selected first.

So the click path is, as assumptions (A):

  A1 the planning tools are available (the window is open) when the action is given;
  A2 pressing the tool's shortcut selects that tool;
  A3 a line is drawn by pressing the left button on its first point, moving through the others
     and releasing on the last;
  A4 the army is selected by clicking its recorded point (``army.<name>``, as
     ``delegate_army_to_ai`` does) and ``plan.assign_units`` then assigns it to the plan;
  A5 the tool draws on the state's middle province (the same point unit orders click).

The actions:

``draw_front_line(army, enemy, states)``
    The own states, in order, that form the line to hold against ``enemy``.
``draw_offensive(army, from_state, target_state, via?)``
    An arrow from an own state across the border into an enemy one.
``assign_battle_plan(army)``
    Select the army and press assign.

``plan_front_line`` / ``plan_offensive`` / ``plan_assignment`` refuse with a message and send
nothing, as ``plan_order`` does: unknown or unconnected states, a line that is not the player's,
an enemy that borders nothing, a target that is not hostile or does not border the start, an army
without a recorded point. The result of a live action is always ``unverified``: nothing reads a
drawn plan back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ..mapcamera import OffScreen, screen_point
from ..mapdata import MapError, MapLayer
from ..types import ActionCall, ActionResult, GameState
from .hands import PLAN_ACTIONS, delegable_armies
from .unit_orders import (
    MAX_VIA,
    MIN_SEPARATION,
    CameraRecord,
    OrderRefused,
    _state_provinces,
    display_fingerprint,
    land_neighbours,
)

#: ``interface/battleplantools.gui`` l.131-134 and l.148-152.
FRONT_LINE_KEY = "z"
OFFENSIVE_KEY = "x"
#: A recorded point (nothing computes it): the "assign units" button, which has no shortcut.
ASSIGN_POINT = "plan.assign_units"
MAX_LINE_STATES = 8


@dataclass(frozen=True)
class PlanStep:
    kind: str                         # key | path | click_recorded
    value: object                     # the key, the points, or the recorded point's name
    label: str = ""

    def describe(self) -> str:
        if self.kind == "path":
            return f"drag {self.label}: " + " -> ".join(str(p) for p in self.value)
        if self.kind == "key":
            return f"press {self.value} ({self.label})"
        return f"click {self.value} ({self.label})"


@dataclass(frozen=True)
class PlanOrder:
    action: str
    army: str
    own_tag: str
    #: States whose middle the line / arrow passes through, in drawing order.
    states: tuple[int, ...]
    centre_state: int
    enemy: str | None = None

    def describe(self) -> str:
        route = " -> ".join(f"state {s}" for s in self.states)
        if self.action == "draw_front_line":
            return f"draw a front line for army {self.army!r} against {self.enemy} through {route}"
        if self.action == "draw_offensive":
            return f"draw an offensive for army {self.army!r}: {route}"
        return f"assign army {self.army!r} to the battle plan"


def _picture(state: GameState) -> dict:
    if state.state_forces is None:
        raise OrderRefused("the reader has no front picture (the mod's div lines), so there is no "
                           "telling which states are the player's")
    return {f.state: f for f in state.state_forces}


def _check_army(army: str | None, coordinates: dict) -> str:
    known = delegable_armies(coordinates)
    if not army or army not in known:
        raise OrderRefused(
            f"army {army!r} has no recorded point (army.<name> in calibration.json); "
            f"recorded: {', '.join(known) or 'none'}")
    return army


def _own_listed(layer: MapLayer, forces: dict, own_tag: str, sid: int, what: str) -> None:
    _state_provinces(layer, sid)
    f = forces.get(sid)
    if f is None or not (f.own > 0 or f.controller == own_tag):
        raise OrderRefused(f"state {sid} is not shown as the player's in the front picture, so it "
                           f"cannot be {what}")


def _borders(layer: MapLayer, a: int, b: int) -> bool:
    other = set(_state_provinces(layer, b))
    return any(n in other for p in _state_provinces(layer, a) for n in land_neighbours(layer, p))


def plan_front_line(layer: MapLayer, call: ActionCall, state: GameState, coordinates: dict) -> PlanOrder:
    args = call.arguments
    army = _check_army(args.get("army"), coordinates)
    enemy = str(args.get("enemy", "")).upper()
    states = [int(s) for s in args.get("states", [])]
    if not 1 <= len(states) <= MAX_LINE_STATES:
        raise OrderRefused(f"a front line passes through 1 to {MAX_LINE_STATES} states, not {len(states)}")
    if len(set(states)) != len(states):
        raise OrderRefused("the same state is named twice in the line")
    forces = _picture(state)
    own_tag = (state.country or "").upper()
    if not enemy or enemy == own_tag:
        raise OrderRefused(f"enemy {enemy or '(none)'!r} is not an opponent of {own_tag or 'the player'}")
    for sid in states:
        _own_listed(layer, forces, own_tag, sid, "part of the front line")
    for a, b in zip(states, states[1:], strict=False):
        if not _borders(layer, a, b):
            raise OrderRefused(f"states {a} and {b} do not border: a front line is a connected chain")
    facing = [s for s, f in forces.items()
              if f.controller == enemy and any(_borders(layer, s, t) for t in states)]
    if not facing:
        raise OrderRefused(f"no state of {enemy} in the front picture borders the line: "
                           "nothing for it to face")
    return PlanOrder("draw_front_line", army, own_tag, tuple(states), states[0], enemy)


def plan_offensive(layer: MapLayer, call: ActionCall, state: GameState, coordinates: dict) -> PlanOrder:
    args = call.arguments
    army = _check_army(args.get("army"), coordinates)
    src, dst = int(args["from_state"]), int(args["target_state"])
    via = [int(s) for s in args.get("via", [])]
    if src == dst:
        raise OrderRefused(f"from_state and target_state are both {src}")
    if len(via) > MAX_VIA:
        raise OrderRefused(f"at most {MAX_VIA} waypoints")
    forces = _picture(state)
    own_tag = (state.country or "").upper()
    _own_listed(layer, forces, own_tag, src, "the start of an offensive")
    _state_provinces(layer, dst)
    for sid in via:
        _state_provinces(layer, sid)
    there = forces.get(dst)
    if there is None or not (there.foe > 0 or there.controller not in (None, own_tag)):
        raise OrderRefused(f"nothing hostile is shown in state {dst} (no enemy division, not "
                           "enemy-controlled): an offensive goes against the enemy")
    chain = [src, *via, dst]
    for a, b in zip(chain, chain[1:], strict=False):
        if not _borders(layer, a, b):
            raise OrderRefused(f"states {a} and {b} do not border: an offensive arrow is a connected "
                               "chain of states")
    return PlanOrder("draw_offensive", army, own_tag, tuple(chain), src)


def plan_assignment(call: ActionCall, state: GameState, coordinates: dict) -> PlanOrder:
    army = _check_army(call.arguments.get("army"), coordinates)
    if ASSIGN_POINT not in coordinates:
        raise OrderRefused(f"no recorded point {ASSIGN_POINT!r} (the assign-units button has no "
                           "shortcut in the interface file): record it with `hoi4-harness calibrate`")
    return PlanOrder("assign_battle_plan", army, (state.country or "").upper(), (), 0)


def plan_steps(layer: MapLayer, camera, order: PlanOrder) -> list[PlanStep]:
    """The gestures for ``order`` at ``camera``; raises OrderRefused for an off-screen point or two
    points too close to tell apart."""
    if order.action == "assign_battle_plan":
        return [PlanStep("click_recorded", f"army.{order.army}", "select the army"),
                PlanStep("click_recorded", ASSIGN_POINT, "assign units")]
    points = []
    for sid in order.states:
        try:
            points.append(screen_point(layer, "state", sid, camera))
        except OffScreen as exc:
            raise OrderRefused(f"{exc}; the whole line has to fit one view") from None
    for a, b in zip(points, points[1:], strict=False):
        if abs(a[0] - b[0]) + abs(a[1] - b[1]) < MIN_SEPARATION:
            raise OrderRefused(f"two points of the line are under {MIN_SEPARATION} px apart on screen: "
                               "zoom in before drawing")
    if len(points) == 1:  # a one-state line is a short stroke across the state's middle
        x, y = points[0]
        points = [(x - 3 * MIN_SEPARATION, y), (x + 3 * MIN_SEPARATION, y)]
    key = FRONT_LINE_KEY if order.action == "draw_front_line" else OFFENSIVE_KEY
    tool = "front line tool" if order.action == "draw_front_line" else "offensive tool"
    steps = [PlanStep("click_recorded", f"army.{order.army}", "select the army"),
             PlanStep("key", key, tool),
             PlanStep("path", tuple(points), f"{order.action} over states {list(order.states)}")]
    return steps


class BattlePlanExecutor:
    """Gives one plan action through the driver and reports it honestly (always unverified)."""

    actions = PLAN_ACTIONS

    def __init__(self, driver, layer: MapLayer, camera: CameraRecord | Callable[[], CameraRecord],
                 centre: Callable[[int], None], *, sleep: Callable[[float], None] | None = None,
                 now: Callable[[], datetime] = datetime.now, settle: float = 0.8, unattended: bool = False):
        self.driver = driver
        self.layer = layer
        self._camera = camera
        self.centre = centre
        self._sleep = sleep or driver._sleep
        self._now = now
        self.settle = settle
        self.unattended = unattended
        self.last_steps: list[PlanStep] = []

    def record(self) -> CameraRecord:
        return self._camera() if callable(self._camera) else self._camera

    def __call__(self, call: ActionCall) -> ActionResult:
        driver = self.driver
        if driver._read_state is None:
            return self._result(call, "rejected", "no reader to plan against; nothing was sent")
        state = driver._read_state()
        coordinates = driver.config.coordinates
        try:
            if call.name == "draw_front_line":
                order = plan_front_line(self.layer, call, state, coordinates)
            elif call.name == "draw_offensive":
                order = plan_offensive(self.layer, call, state, coordinates)
            else:
                order = plan_assignment(call, state, coordinates)
            camera = None
            centre_province = None
            if order.states:
                centre_province = _state_provinces(self.layer, order.centre_state)[0]
                display = display_fingerprint(driver)
                screen = driver.config.screen or (tuple(display["resolution"]) if display else None)
                camera = self.record().camera_for(self.layer, centre_province, screen=screen,
                                                  now=self._now(), display=display,
                                                  unattended=self.unattended)
            steps = plan_steps(self.layer, camera, order)
        except (OrderRefused, MapError) as exc:
            return self._result(call, "rejected", f"refused before any input: {exc}; nothing was sent")
        self.last_steps = steps
        if centre_province is not None:
            self.centre(centre_province)
        if not driver.dry_run:
            self._sleep(self.settle)
        for step in steps:
            self._perform(step)
            if not driver.dry_run:
                self._sleep(0.25)
        if driver.dry_run:
            return self._result(call, "not_executed", f"dry run: would {order.describe()}: "
                                + "; ".join(s.describe() for s in steps))
        return self._result(call, "unverified", (
            f"{order.describe()}: sent. UNVERIFIED: the click path is built from the interface files "
            "and has not been run in the game, and a drawn plan cannot be read back. Check the map "
            "before drawing it again."), changed={"channel": "mouse"})

    def _perform(self, step: PlanStep) -> None:
        if step.kind == "key":
            self.driver.press(str(step.value))
        elif step.kind == "path":
            self.driver.drag_path(list(step.value), label=step.label)
        else:
            self.driver.click(str(step.value))

    @staticmethod
    def _result(call: ActionCall, kind: str, message: str, changed: dict | None = None) -> ActionResult:
        return ActionResult(ok=False, action=call.name, call_id=call.call_id, message=message,
                            error_kind=kind, changed=changed or {})


def enable_battle_plans(driver, layer: MapLayer, camera, centre, **kw) -> BattlePlanExecutor:
    executor = BattlePlanExecutor(driver, layer, camera, centre, **kw)
    driver.enable_battle_plans(executor)
    return executor
