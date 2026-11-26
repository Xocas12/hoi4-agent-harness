"""Armies by click: create one, put divisions in it, give it a front against a named enemy, a
fallback line, an execution pace, or take its plan away (#8, the armies part of #137).

**NOT live-verified.** Built against the fake game to the standard of ``battle_plans.py``: refusals
before any input, computed points, a dry run, the driver's guards. Every step marked as an assumption
has to be checked in the live session; ``docs/armies.md`` lists them with the steps to do it.

What the game's own files say (``interface/battleplantools.gui``, 1.19.5 and 1.20):

* ``btn_order_draw_defence`` has ``shortcut = "z"``: the front line;
* ``btn_order_draw_fallback`` has ``shortcut = "c"``: the fallback line;
* ``btn_priority`` has ``shortcut = "shift+s"`` and opens ``planning_speed_window``, whose three
  buttons ``btn_execute_0/1/2`` are careful, balanced and rush: how the plan is executed;
* ``btn_unassign_units`` has ``shortcut = "ctrl+h"``;
* ``btn_assign_units`` has no shortcut, and the window's own position is not in the file, so the
  three execute buttons and the assign button are *recorded points* (``plan.execute_careful``,
  ``plan.execute_balanced``, ``plan.execute_rush``, ``plan.assign_units``), like the army cards
  (``army.<name>``) and the create-army card (``army.create``).

Delegation (#8) is a front against a named enemy handed to an army and set to execute: the game
has no per-army AI toggle (the issue's own live exploration found none), so the army carrying out a
plan IS the delegation. ``assign_army_front`` is that, and it is verified from the next text save:
the army's ``orders_group`` has a plan with divisions assigned, passing through the province, and the
``execution_type`` asked for (``armies.py``). Until then the result is ``unverified`` and the army is
not in ``GameState.delegated_armies``: the brief's "AI control" line only names what the save shows.

The click path, as assumptions (A):

  A1 clicking the army's card (``army.<name>``) selects it, and drawing with it selected assigns the
     plan to it (``plan.assign_units`` is pressed too when it is recorded);
  A2 the tool's shortcut selects the tool, and one left click on a province of the border draws a
     front line that the game snaps to the border there;
  A3 ``shift+s`` opens the pace window and a recorded click on its button sets the pace;
  A4 box- or stack-selecting divisions and pressing the create-army card makes "Army N" (seen live by
     the issue's author on 1.19.5);
  A5 dragging a selected division counter onto an army's card adds the selection to it;
  A6 ``ctrl+h`` with the army selected takes its divisions off the plan.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..armies import EXECUTION, EXECUTION_CODE, group_named, held_plans
from ..mapcamera import OffScreen, screen_point
from ..mapdata import MapError, MapLayer
from ..types import ActionCall, ActionResult, GameState, StateForces
from .battle_plans import ASSIGN_POINT, FRONT_LINE_KEY, plan_fallback, plan_steps
from .hands import ARMY_UI_ACTIONS, delegable_armies
from .unit_orders import (
    MIN_SEPARATION,
    STACK_KIND,
    CameraRecord,
    Gesture,
    OrderPlan,
    OrderRefused,
    _box,
    _on_screen,
    _state_provinces,
    display_fingerprint,
    land_neighbours,
)

#: What the delegation is made of: the others manage armies, these two hand them a job.
DELEGATION_ACTIONS = frozenset({"assign_army_front", "set_army_execution", "release_army"})
CREATE_POINT = "army.create"
EXECUTE_KEY = "shift+s"
RELEASE_KEY = "ctrl+h"
DEFAULT_MODE = "balanced"
MODES = tuple(EXECUTION_CODE)
#: Days before an order the save does not show is called not taken.
NO_CHANGE_DAYS = 4


def execute_point(mode: str) -> str:
    return f"plan.execute_{mode}"


@dataclass(frozen=True)
class AStep:
    kind: str                         # key | click_recorded | gesture | path | drag_to_recorded
    value: object
    label: str = ""

    def describe(self) -> str:
        if self.kind == "gesture":
            return self.value.describe()
        if self.kind == "path":
            return f"drag {self.label}: " + " -> ".join(str(p) for p in self.value)
        if self.kind == "key":
            return f"press {self.value} ({self.label})"
        if self.kind == "drag_to_recorded":
            return f"drag {self.value[0]} onto {self.value[1]} ({self.label})"
        return f"click {self.value} ({self.label})"


@dataclass(frozen=True)
class ArmyOrder:
    action: str
    own_tag: str
    army: str | None = None
    #: The province the view is centred on, or None for an action that touches no map.
    centre_province: int | None = None
    from_state: int | None = None
    from_province: int | None = None
    from_provinces: tuple[int, ...] = ()
    target_province: int | None = None
    states: tuple[int, ...] = ()
    enemy: str | None = None
    mode: str | None = None

    def describe(self) -> str:
        if self.action == "create_army":
            return f"make an army of the divisions in state {self.from_state}"
        if self.action == "assign_divisions":
            return f"put the divisions in state {self.from_state} into army {self.army!r}"
        if self.action == "assign_army_front":
            return (f"give army {self.army!r} a front against {self.enemy} at province "
                    f"{self.target_province} and set it to execute {self.mode}")
        if self.action == "draw_fallback_line":
            return f"draw a fallback line for army {self.army!r} through states {list(self.states)}"
        if self.action == "set_army_execution":
            return f"set army {self.army!r} to execute {self.mode}"
        return f"take army {self.army!r} off its plan"


# --- the plan: refusals before any input -----------------------------------------------------


def _forces(state: GameState) -> dict[int, StateForces]:
    if state.state_forces is None:
        raise OrderRefused("the reader has no front picture (the mod's div lines), so there is no "
                           "telling which states are the player's")
    return {f.state: f for f in state.state_forces}


def _known_army(name, coordinates: dict) -> str:
    known = [a for a in delegable_armies(coordinates) if a not in ("all", "create")]
    if not name or name not in known:
        raise OrderRefused(f"army {name!r} has no recorded point (army.<name> in calibration.json); "
                           f"recorded: {', '.join(known) or 'none'}")
    return str(name)


def _need_point(coordinates: dict, name: str, why: str) -> None:
    if name not in coordinates:
        raise OrderRefused(f"no recorded point {name!r} ({why}): record it with "
                           f"`hoi4-harness calibrate {name}`")


def _is_own(f: StateForces | None, own_tag: str) -> bool:
    return f is not None and (f.own > 0 or (f.controller or "").upper() == own_tag)


def border_provinces(layer: MapLayer, forces: dict[int, StateForces], own_tag: str,
                     enemy: str) -> list[int]:
    """Provinces of states ``enemy`` controls that touch a province of one of the player's states,
    those next to a state with the player's divisions first. Ownership is the front picture's."""
    own_states = {s for s, f in forces.items() if _is_own(f, own_tag)}
    enemy_states = {s for s, f in forces.items() if (f.controller or "").upper() == enemy}
    own_province: dict[int, int] = {}
    for sid in own_states:
        info = layer.states.get(sid)
        for p in getattr(info, "provinces", []) or []:
            own_province[p] = sid
    found: dict[int, int] = {}
    for sid in enemy_states:
        info = layer.states.get(sid)
        for p in getattr(info, "provinces", []) or []:
            if layer.provinces.get(p) is None or layer.provinces[p].kind != "land":
                continue
            contact = [own_province[n] for n in land_neighbours(layer, p) if n in own_province]
            if contact:
                found[p] = max(forces[s].own for s in contact)
    return sorted(found, key=lambda p: (-found[p], p))


def plan_army_action(layer: MapLayer, call: ActionCall, state: GameState,
                     coordinates: dict) -> ArmyOrder:
    args, name = call.arguments, call.name
    own_tag = (state.country or "").upper()
    if name in ("set_army_execution", "assign_army_front"):
        mode = str(args.get("mode", args.get("execution", DEFAULT_MODE))).lower()
        if mode not in MODES:
            raise OrderRefused(f"mode {mode!r} is not one of {', '.join(MODES)}")
        army = _known_army(args.get("army"), coordinates)
        _need_point(coordinates, execute_point(mode), "the pace window's button has no computable "
                    "position in the interface file")
        if name == "set_army_execution":
            return ArmyOrder(name, own_tag, army=army, mode=mode)
        enemy = str(args.get("enemy", "")).upper()
        if not enemy or enemy == own_tag:
            raise OrderRefused(f"enemy {enemy or '(none)'!r} is not an opponent of "
                               f"{own_tag or 'the player'}")
        forces = _forces(state)
        border = border_provinces(layer, forces, own_tag, enemy)
        if not border:
            raise OrderRefused(f"no province held by {enemy} borders a state of the player's in the "
                               "front picture: nothing for a front to face")
        target = args.get("province")
        if target is not None:
            target = int(target)
            if target not in border:
                raise OrderRefused(f"province {target} is not a province {enemy} holds on the border "
                                   f"(front picture); on the border: {border[:8]}")
        else:
            target = border[0]
        return ArmyOrder(name, own_tag, army=army, centre_province=target, target_province=target,
                         enemy=enemy, mode=mode)
    if name == "release_army":
        return ArmyOrder(name, own_tag, army=_known_army(args.get("army"), coordinates))
    if name == "draw_fallback_line":
        army = _known_army(args.get("army"), coordinates)
        order = plan_fallback(layer, call, state, coordinates)
        return ArmyOrder(name, own_tag, army=army, centre_province=_state_provinces(
            layer, order.centre_state)[0], states=order.states)
    # create_army / assign_divisions: select the divisions of one state.
    army = None
    if name == "assign_divisions":
        army = _known_army(args.get("army"), coordinates)
    else:
        _need_point(coordinates, CREATE_POINT, "the create-army card is in no interface file")
    sid = int(args["from_state"])
    forces = _forces(state)
    here = forces.get(sid)
    if here is None or here.own <= 0:
        raise OrderRefused(f"the front picture shows none of the player's divisions in state {sid} "
                           "(it lists only the front): nothing to select")
    provinces = _state_provinces(layer, sid)
    from_province = args.get("from_province")
    if from_province is not None:
        from_province = int(from_province)
        if from_province not in provinces:
            raise OrderRefused(f"province {from_province} is not in state {sid}")
    return ArmyOrder(name, own_tag, army=army, centre_province=from_province or provinces[0],
                     from_state=sid, from_province=from_province, from_provinces=tuple(provinces))


# --- the gestures ----------------------------------------------------------------------------


def _select(layer: MapLayer, camera, order: ArmyOrder, state: GameState,
            counter_offset: tuple[int, int]) -> tuple[Gesture, tuple[int, int]]:
    """The gesture that selects the divisions, and a point on one of their counters."""
    dx, dy = counter_offset
    if order.from_province is not None:
        ax, ay = layer.anchor(order.from_province, STACK_KIND)
        sx, sy = camera.to_screen(ax, ay)
        point = (round(sx + dx), round(sy + dy))
        if not _on_screen(camera, point[0], point[1]):
            raise OrderRefused(f"the unit counter of province {order.from_province} would be at "
                               f"{point}, outside the map view")
        return Gesture("click", point, label=f"select stack in province {order.from_province}"), point
    plan = OrderPlan(order.action, order.from_state, order.from_state, None, order.from_provinces[0],
                     (), (), order.own_tag, order.from_provinces)
    box = _box(layer, camera, plan, state, counter_offset)
    counter = next((p for p in order.from_provinces if STACK_KIND in layer.stacks(p)), None)
    ax, ay = layer.anchor(counter, STACK_KIND)
    sx, sy = camera.to_screen(ax, ay)
    return box, (round(sx + dx), round(sy + dy))


def army_steps(layer: MapLayer, camera, order: ArmyOrder, state: GameState, coordinates: dict,
               counter_offset: tuple[int, int] = (0, 0)) -> list[AStep]:
    """The gestures for ``order``; OrderRefused for a point off the view or two too close."""
    name = order.action
    card = f"army.{order.army}"
    if name == "create_army":
        select, _ = _select(layer, camera, order, state, counter_offset)
        return [AStep("gesture", select, "select the divisions"),
                AStep("click_recorded", CREATE_POINT, "the create-army card")]
    if name == "assign_divisions":
        select, counter = _select(layer, camera, order, state, counter_offset)
        return [AStep("gesture", select, "select the divisions"),
                AStep("drag_to_recorded", (counter, card), "drop the selection on the army")]
    if name == "set_army_execution":
        return [AStep("click_recorded", card, "select the army"),
                AStep("key", EXECUTE_KEY, "open the pace window"),
                AStep("click_recorded", execute_point(order.mode), f"execute {order.mode}")]
    if name == "release_army":
        return [AStep("click_recorded", card, "select the army"),
                AStep("key", RELEASE_KEY, "unassign units")]
    if name == "draw_fallback_line":
        from .battle_plans import PlanOrder

        steps = plan_steps(layer, camera, PlanOrder(
            "draw_fallback_line", order.army, order.own_tag, order.states, order.states[0]))
        return [AStep(s.kind, s.value, s.label) for s in steps]
    # assign_army_front: the army, the front tool, one click on the enemy province on the border.
    try:
        point = screen_point(layer, "province", order.target_province, camera)
    except OffScreen as exc:
        raise OrderRefused(f"{exc}; the front's province has to be in view") from None
    steps = [AStep("click_recorded", card, "select the army"),
             AStep("key", FRONT_LINE_KEY, "front line tool"),
             AStep("gesture", Gesture("click", point, label=f"enemy province {order.target_province}"),
                   "draw the front")]
    if ASSIGN_POINT in coordinates:
        steps.append(AStep("click_recorded", ASSIGN_POINT, "assign units"))
    steps += [AStep("key", EXECUTE_KEY, "open the pace window"),
              AStep("click_recorded", execute_point(order.mode), f"execute {order.mode}")]
    return steps


# --- the ledger: did the save show it? -------------------------------------------------------


@dataclass
class Pending:
    number: int
    order: ArmyOrder
    issued: str | None
    before_ids: frozenset
    before_members: int
    status: str = "sent, nothing in the save yet"
    settled: str | None = None
    #: assign_army_front only: the province zone the front must pass through.
    zone: frozenset = frozenset()

    def line(self) -> str:
        return f"Army order #{self.number} ({self.order.describe()}): {self.status}"


def _days(a: str | None, b: str | None) -> int | None:
    try:
        ya, ma, da = (int(x) for x in str(a).split(".")[0:3])
        yb, mb, db = (int(x) for x in str(b).split(".")[0:3])
        return (datetime(yb, mb, db) - datetime(ya, ma, da)).days
    except (TypeError, ValueError):
        return None


class ArmyLedger:
    """What the army actions asked for and what the next saves say about each.

    ``update`` re-reads every pending order against ``GameState.army_groups`` (a text save; a
    reader without one leaves them "sent, no save read yet"). It also keeps ``fronts``, the armies
    whose front was confirmed, and ``delegated`` derives the brief's "AI control" list from the save
    each time, so an army that lost its plan drops off it and an unconfirmed one never appears."""

    def __init__(self, layer: MapLayer | None = None) -> None:
        self.layer = layer
        self.orders: list[Pending] = []
        self.fronts: dict[str, frozenset] = {}
        self._count = 0

    def add(self, order: ArmyOrder, state: GameState) -> Pending:
        groups = state.army_groups or []
        self._count += 1
        group = group_named(groups, order.army or "")
        zone = frozenset()
        if order.action == "assign_army_front" and self.layer is not None:
            p = order.target_province
            zone = frozenset({p, *land_neighbours(self.layer, p)})
        elif order.action == "draw_fallback_line" and self.layer is not None:
            zone = frozenset(p for s in order.states for p in _state_provinces(self.layer, s))
        pending = Pending(self._count, order, state.date, frozenset(g.id for g in groups),
                          group.members if group else 0, zone=zone)
        self.orders.append(pending)
        return pending

    def update(self, state: GameState) -> list[str]:
        if state.army_groups is None:
            for o in self.orders:
                if o.settled is None:
                    o.status = "sent; no save read yet, so nothing confirms it"
            return [o.line() for o in self.orders]
        keep = []
        for o in self.orders:
            self._judge(o, state)
            if o.settled is not None:
                age = _days(o.settled, state.date)
                if age is not None and age > 3:
                    continue
            keep.append(o)
        self.orders = keep
        return [o.line() for o in self.orders]

    def _judge(self, o: Pending, state: GameState) -> None:
        if o.settled is not None:
            return
        groups = state.army_groups or []
        order = o.order
        group = group_named(groups, order.army or "")
        age = _days(o.issued, state.date)
        done: str | None = None
        if order.action == "create_army":
            new = [g for g in groups if g.id not in o.before_ids]
            if new:
                done = f"confirmed: {new[0].name} exists with {new[0].members} division(s)"
        elif group is None:
            done = None
        elif order.action == "assign_divisions":
            if group.members > o.before_members:
                done = f"confirmed: {group.name} went {o.before_members} -> {group.members} divisions"
        elif order.action == "set_army_execution":
            if group.execution == EXECUTION_CODE[order.mode]:
                done = f"confirmed: {group.name} executes {order.mode}"
        elif order.action == "release_army":
            if not held_plans(group):
                done = f"confirmed: {group.name} carries out no plan"
                self.fronts.pop(group.name, None)
        else:  # assign_army_front, draw_fallback_line
            wanted = "fallback" if order.action == "draw_fallback_line" else None
            held = [p for p in held_plans(group) if set(p.path) & o.zone
                    and (wanted is None or p.kind == wanted)]
            if held and order.action == "draw_fallback_line":
                done = f"confirmed: {group.name} has a fallback line through the states"
            elif held:
                if group.execution == EXECUTION_CODE[order.mode]:
                    done = (f"confirmed: {group.name} holds a {held[0].kind} line through province "
                            f"{order.target_province}, executing {order.mode}")
                    self.fronts[group.name] = o.zone
                else:
                    o.status = (f"the line is there but {group.name} executes "
                                f"{EXECUTION.get(group.execution, 'an unread pace')}, not {order.mode}")
        if done:
            o.status, o.settled = done, state.date
        elif age is not None and age >= NO_CHANGE_DAYS:
            o.status = f"not seen in the save after {age} days: the click path may not have taken"
            o.settled = state.date
        elif not o.status.startswith("the line is there"):
            o.status = "sent, nothing in the save yet"

    def delegated(self, state: GameState) -> list[str]:
        """Armies whose confirmed front the save still shows with divisions on it."""
        out = []
        for name, zone in self.fronts.items():
            group = group_named(state.army_groups, name)
            if any(set(p.path) & zone for p in held_plans(group)):
                out.append(name)
        return sorted(out)

    def decorate(self, state: GameState) -> GameState:
        lines = self.update(state)
        state.order_status = [*state.order_status, *lines]
        if state.army_groups is not None:
            state.delegated_armies = sorted({*state.delegated_armies, *self.delegated(state)})
        return state


# --- the executor ----------------------------------------------------------------------------


@dataclass
class ArmyExecutor:
    """Gives one army action through the driver and reports it honestly (always unverified)."""

    driver: object
    layer: MapLayer
    camera: CameraRecord | Callable[[], CameraRecord]
    centre: Callable[[int], None]
    ledger: ArmyLedger = field(default=None)
    sleep: Callable[[float], None] | None = None
    now: Callable[[], datetime] = datetime.now
    settle: float = 0.8
    unattended: bool = False
    actions = ARMY_UI_ACTIONS

    def __post_init__(self):
        self.ledger = self.ledger or ArmyLedger(self.layer)
        self._sleep = self.sleep or self.driver._sleep
        self.last_steps: list[AStep] = []

    def record(self) -> CameraRecord:
        return self.camera() if callable(self.camera) else self.camera

    def __call__(self, call: ActionCall) -> ActionResult:
        driver = self.driver
        if driver._read_state is None:
            return self._result(call, "rejected", "no reader to plan against; nothing was sent")
        state = driver._read_state()
        coordinates = driver.config.coordinates
        try:
            order = plan_army_action(self.layer, call, state, coordinates)
            camera, offset = None, (0, 0)
            if order.centre_province is not None:
                record = self.record()
                display = display_fingerprint(driver)
                screen = driver.config.screen or (tuple(display["resolution"]) if display else None)
                camera = record.camera_for(self.layer, order.centre_province, screen=screen,
                                           now=self.now(), display=display,
                                           unattended=self.unattended)
                offset = record.counter_offset
            steps = army_steps(self.layer, camera, order, state, coordinates, offset)
            self._spread(steps)
        except (OrderRefused, MapError) as exc:
            return self._result(call, "rejected", f"refused before any input: {exc}; nothing was sent")
        self.last_steps = steps
        if order.centre_province is not None:
            self.centre(order.centre_province)
        if not driver.dry_run:
            self._sleep(self.settle)
            if getattr(driver, "open_panel", None) == "production":
                # A production edit leaves its screen up and the army card, the map and the pace
                # window are under it: its hotkey toggles it shut (production_ui.py, P2).
                driver.press("production")
                driver.open_panel = None
                self._sleep(self.settle)
        for step in steps:
            self._perform(step)
            if not driver.dry_run:
                self._sleep(0.25)
        if driver.dry_run:
            return self._result(call, "not_executed", f"dry run: would {order.describe()}: "
                                + "; ".join(s.describe() for s in steps))
        pending = self.ledger.add(order, state)
        return self._result(call, "unverified", (
            f"army order #{pending.number} sent ({order.describe()}). UNVERIFIED: the click path is "
            "built from the interface files and has not been run in the game; the next text save "
            "decides, and the brief's Army order line says what it shows. Do not repeat it "
            "meanwhile."), changed={"channel": "mouse"})

    @staticmethod
    def _spread(steps: list[AStep]) -> None:
        """Two clicks on the map under MIN_SEPARATION px apart are one click to the game."""
        points = [s.value.point for s in steps if s.kind == "gesture" and s.value.kind == "click"]
        for a, b in zip(points, points[1:], strict=False):
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) < MIN_SEPARATION:
                raise OrderRefused(f"two clicks are under {MIN_SEPARATION} px apart on screen: "
                                   "zoom in first")

    def _perform(self, step: AStep) -> None:
        d = self.driver
        if step.kind == "key":
            d.press(str(step.value))
        elif step.kind == "gesture":
            g = step.value
            if g.kind == "drag":
                d.drag(g.point, g.end, label=g.label)
            else:
                d.click_at(g.point, g.button, shift=g.shift, label=g.label)
        elif step.kind == "path":
            d.drag_path(list(step.value), label=step.label)
        elif step.kind == "drag_to_recorded":
            start, name = step.value
            d.drag(start, tuple(d.config.coordinates[name]), label=step.label)
        else:
            d.click(str(step.value))

    @staticmethod
    def _result(call: ActionCall, kind: str, message: str, changed: dict | None = None) -> ActionResult:
        return ActionResult(ok=False, action=call.name, call_id=call.call_id, message=message,
                            error_kind=kind, changed=changed or {})


def enable_armies(driver, layer: MapLayer, camera, centre, **kw) -> ArmyExecutor:
    executor = ArmyExecutor(driver, layer, camera, centre, **kw)
    driver.enable_army_ui(executor)
    return executor
