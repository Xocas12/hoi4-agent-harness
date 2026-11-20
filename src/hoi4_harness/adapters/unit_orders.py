"""Division orders by click: select a stack, right-click a target (#85).

No script reaches a division's order (docs/unit-orders.md lists every candidate
and why it was refused), so a move or an attack is what a player does: bring the
area into view, select the units, right-click the target. Three things make that
safe enough to hand to a model:

* **A plan is checked against the map layer and the telemetry before any
  input.** States must exist and be connected, an attack target must touch the
  attacker's state and hold the enemy, the front picture must show a division of
  the player's in the state it is ordered from. A refusal sends nothing.
* **A click point is computed, never guessed.** It comes from the map files
  (``map/unitstacks.txt`` type 0 is where a province draws its unit counter) and
  a camera fitted in the live game (``camera.json``, see :class:`CameraRecord`).
  A missing, stale, unvalidated or wrong-resolution fit refuses; so does a
  point outside the viewport, because a click on a panel is worse than none.
* **Nothing is claimed that the game has not shown.** A move takes days, so the
  result is "sent, unverified"; the :class:`OrderLedger` then compares the front
  picture on later reads and says in the brief whether the order took.

The only input this module sends goes through the driver (``click_at``, ``drag``
and the console for ``goto_province``), so its focus guard, dry run and
injectable devices apply unchanged.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from ..mapcamera import (
    CameraModelError,
    OffScreen,
    Viewport,
    fit_camera,
    screen_point,
)
from ..mapdata import MapError, MapLayer
from ..types import ActionCall, ActionResult, GameState, StateForces
from .composite import CompositeAdapter

SCHEMA_VERSION = 1
DEFAULT_FILENAME = "camera.json"
ORDER_ACTIONS = ("move_division", "attack_province")
#: Where a province draws its unit counter (``map/unitstacks.txt`` type 0; see mapdata).
STACK_KIND = 0
CENTRE_RULES = ("anchor0", "anchor38", "centroid", "interior")
CENTRE_MODES = ("goto_province", "map_search")
MAX_HOPS = 15
MAX_VIA = 4
#: A fit older than this is refused: zooming or a changed UI scale since then
#: makes it wrong, and nothing here can see either.
DEFAULT_MAX_AGE_HOURS = 24.0
#: Pixels between the counter and a target below which the two clicks are not
#: told apart by the game.
MIN_SEPARATION = 12
#: Days an order may show no change before the ledger says it did not take.
NO_CHANGE_DAYS = 4
#: Days a settled order stays in the brief.
KEEP_SETTLED_DAYS = 3


class OrderRefused(Exception):
    """The order cannot be given as asked; nothing was sent."""


class CameraRefused(OrderRefused):
    """There is no camera fit that can be trusted for this order."""


# --- the camera fit -------------------------------------------------------------


def centre_point(layer: MapLayer, province: int, rule: str) -> tuple[float, float]:
    """The map pixel the game puts at the middle of the viewport after it centres
    on ``province`` -- by ``rule``, which a live check settles (docs/live-session.md)."""
    if rule == "anchor0":
        return layer.anchor(province, STACK_KIND)
    if rule == "anchor38":
        return layer.anchor(province, 38)
    if rule == "centroid":
        return layer.provinces[province].centroid
    if rule == "interior":
        x, y = layer.interior_point("province", province)
        return float(x), float(y)
    raise CameraRefused(f"unknown centre rule {rule!r}; known: {', '.join(CENTRE_RULES)}")


@dataclass(frozen=True)
class ShiftedCamera:
    """``base`` moved over the plane by (dx, dy) map pixels.

    Sliding a pinhole camera parallel to a flat map leaves its tilt and height
    alone, so the new view is the old one with the map shifted: exact, as long
    as centring does not also change the zoom. That is what ``camera.json`` asserts
    and the live check validates on a second province.
    """

    base: object
    dx: float
    dy: float

    @property
    def viewport(self) -> Viewport:
        return self.base.viewport

    def to_screen(self, mx: float, my: float) -> tuple[float, float]:
        return self.base.to_screen(mx - self.dx, my - self.dy)

    def to_map(self, sx: float, sy: float) -> tuple[float, float]:
        x, y = self.base.to_map(sx, sy)
        return x + self.dx, y + self.dy


@dataclass
class CameraRecord:
    """A camera fit recorded in the live game, and what it is good for.

    ``pairs`` are ``(map x, map y, screen x, screen y)`` for landmarks seen with
    the view centred on ``fit_province`` (by ``centre_rule``). ``validated`` says a
    landmark held out of the fit landed where predicted: required for a fit that
    four points determine exactly, whose zero residual proves nothing.
    """

    screen: tuple[int, int]
    viewport: Viewport
    pairs: list[tuple[float, float, float, float]]
    fit_province: int
    centre_rule: str = "anchor0"
    validated: bool = False
    recorded: str = ""
    #: Where the unit counter is drawn relative to the type-0 anchor, in screen px.
    counter_offset: tuple[int, int] = (0, 0)
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS
    #: The display the fit was recorded under (``display_fingerprint``): UI scale, window
    #: mode and resolution from settings.txt. What an unattended run can check of "has
    #: anything changed since": it cannot see the map's zoom, but it can see these.
    display: dict | None = None

    # --- file ---------------------------------------------------------------

    def to_dict(self) -> dict:
        vp = self.viewport
        return {
            "schema": SCHEMA_VERSION, "screen": list(self.screen),
            "viewport": {"left": vp.left, "top": vp.top, "right": vp.right, "bottom": vp.bottom},
            "pairs": [list(p) for p in self.pairs], "fit_province": self.fit_province,
            "centre_rule": self.centre_rule, "validated": self.validated,
            "recorded": self.recorded, "counter_offset": list(self.counter_offset),
            "max_age_hours": self.max_age_hours, "display": self.display,
        }

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> CameraRecord:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise CameraRefused(
                f"no camera fit at {path}: unit orders need one recorded in the live game "
                "(docs/live-session.md, the camera step)") from None
        except ValueError as exc:
            raise CameraRefused(f"{path} is not valid JSON: {exc}") from None
        if data.get("schema") != SCHEMA_VERSION:
            raise CameraRefused(f"{path}: schema {data.get('schema')!r}, this harness reads "
                                f"{SCHEMA_VERSION}")
        try:
            width, height = (int(v) for v in data["screen"])
            vp = data["viewport"]
            return cls(
                screen=(width, height),
                viewport=Viewport(width, height, int(vp.get("left", 0)), int(vp.get("top", 0)),
                                  int(vp.get("right", 0)), int(vp.get("bottom", 0))),
                pairs=[tuple(float(v) for v in p) for p in data["pairs"]],
                fit_province=int(data["fit_province"]),
                centre_rule=str(data.get("centre_rule", "anchor0")),
                validated=bool(data.get("validated", False)),
                recorded=str(data.get("recorded", "")),
                counter_offset=tuple(int(v) for v in data.get("counter_offset", (0, 0))),
                max_age_hours=float(data.get("max_age_hours", DEFAULT_MAX_AGE_HOURS)),
                display=data.get("display") if isinstance(data.get("display"), dict) else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CameraRefused(f"{path} is incomplete or malformed ({exc!r})") from None

    # --- use ----------------------------------------------------------------

    def check_display(self, display: dict | None) -> str | None:
        """Why the display now is not the one the fit was recorded under, or None. Needs a
        recorded fingerprint and a reading of the current one; without either it says nothing
        (the age limit then stands)."""
        if self.display is None or display is None:
            return None
        changed = [f"{key} {self.display[key]} -> {display.get(key)}" for key in sorted(self.display)
                   if display.get(key) is not None and self.display[key] != display.get(key)]
        return ", ".join(changed) or None

    def camera_for(self, layer: MapLayer, province: int, *, screen: tuple[int, int] | None = None,
                   now: datetime | None = None, display: dict | None = None,
                   unattended: bool = False) -> ShiftedCamera:
        """The camera after the game centres on ``province``. Refuses, saying why,
        when the fit cannot be trusted.

        ``unattended`` (an independent campaign, which owns the PC) lifts the age limit
        for a fit that carries a display fingerprint matching ``display``: the age limit
        stood in for "the UI scale may have changed", and that is now checked directly. The
        zoom of the map cannot be read, so it is assumed unchanged (the harness never scrolls
        the map; a person who does breaks it, and the ledger's 'no change' line is the
        symptom)."""
        if screen is not None and tuple(screen) != tuple(self.screen):
            raise CameraRefused(
                f"the camera was fitted at {self.screen[0]}x{self.screen[1]} but the screen is "
                f"{screen[0]}x{screen[1]}: record it again")
        if not self.recorded:
            raise CameraRefused("the camera fit has no recording time, so its age is unknown")
        try:
            age = (now or datetime.now()) - datetime.fromisoformat(self.recorded)
        except ValueError:
            raise CameraRefused(f"the camera fit's time {self.recorded!r} is not ISO") from None
        moved = self.check_display(display)
        if moved:
            raise CameraRefused(f"the display changed since the camera was recorded ({moved}): "
                                "record it again")
        lifted = unattended and self.display is not None and display is not None
        if age > timedelta(hours=self.max_age_hours) and not lifted:
            raise CameraRefused(
                f"the camera fit is {age.total_seconds() / 3600:.0f} h old (limit "
                f"{self.max_age_hours:g} h): zoom or UI scale may have changed; record it again"
                + ("" if self.display is not None or not unattended else
                   " (a record made by the current scripts/record_camera.py carries the display "
                   "fingerprint an unattended run accepts at any age)"))
        try:
            fit = fit_camera([((p[0], p[1]), (p[2], p[3])) for p in self.pairs], self.viewport)
        except CameraModelError as exc:
            raise CameraRefused(f"the recorded landmarks do not give a camera: {exc}") from None
        if fit.exactly_determined and not self.validated:
            raise CameraRefused(
                "the camera is a homography through exactly four landmarks, whose zero residual "
                "proves nothing: hold a fifth landmark out, check it lands where predicted, and "
                "set validated=true")
        try:
            here = centre_point(layer, self.fit_province, self.centre_rule)
            there = centre_point(layer, province, self.centre_rule)
        except MapError as exc:
            raise CameraRefused(str(exc)) from None
        return ShiftedCamera(fit.camera, there[0] - here[0], there[1] - here[1])


def display_fingerprint(driver) -> dict | None:
    """What the driver can read of the display the clicks are placed in: the UI scale, the
    window mode and the resolution. None when it cannot be read (a window or settings file
    that does not resolve), in which case nothing is compared."""
    from ..uilayout import LayoutRefused

    try:
        geometry = driver.geometry()
        settings = driver._display_settings()
    except (LayoutRefused, OSError):
        return None
    return {"gui_scale": round(float(geometry.gui_scale), 4),
            "mode": settings.mode if settings else None,
            "resolution": [int(geometry.width), int(geometry.height)]}


def stamp_now(now: datetime | None = None) -> str:
    return (now or datetime.now()).replace(microsecond=0).isoformat()


# --- the plan: what the order means on the map ----------------------------------


@dataclass(frozen=True)
class OrderPlan:
    action: str                       # move_division | attack_province
    from_state: int
    to_state: int
    #: The stack to click, or None: select every counter in the state with a box.
    from_province: int | None
    target_province: int
    #: Shift-right-clicked after the target, in order.
    via_provinces: tuple[int, ...]
    via_states: tuple[int, ...]
    #: The player's tag, for the ledger.
    own_tag: str
    #: Provinces of ``from_state``, for the box and for the view's centre.
    from_provinces: tuple[int, ...] = field(default=())

    @property
    def centre_province(self) -> int:
        return self.from_province if self.from_province is not None else self.from_provinces[0]

    def describe(self) -> str:
        who = (f"the stack in province {self.from_province}" if self.from_province is not None
               else f"the divisions in state {self.from_state}")
        verb = "attack" if self.action == "attack_province" else "move to"
        via = "".join(f"state {s}, then " for s in self.via_states)
        return f"{who} {verb} {via}province {self.target_province} (state {self.to_state})"


def _land(layer: MapLayer, province: int) -> bool:
    info = layer.provinces.get(province)
    return info is not None and info.kind == "land"


def _state_provinces(layer: MapLayer, state: int) -> list[int]:
    info = layer.states.get(state)
    if info is None:
        raise OrderRefused(f"there is no state {state} in this playset's map")
    land = sorted(p for p in info.provinces if _land(layer, p))
    if not land:
        raise OrderRefused(f"state {state} has no land province")
    return land


def land_neighbours(layer: MapLayer, province: int) -> list[int]:
    return [n for n in layer.neighbours(province, straits=False) if _land(layer, n)]


def land_hops(layer: MapLayer, sources: list[int], goals: set[int], limit: int = MAX_HOPS) -> int | None:
    """Fewest land borders crossed from any of ``sources`` to any of ``goals``, or None."""
    seen = {s: 0 for s in sources}
    queue = deque(sources)
    while queue:
        here = queue.popleft()
        if here in goals:
            return seen[here]
        if seen[here] >= limit:
            continue
        for n in land_neighbours(layer, here):
            if n not in seen:
                seen[n] = seen[here] + 1
                queue.append(n)
    return None


def _owned_by(forces: dict[int, StateForces], state: int) -> StateForces | None:
    f = forces.get(state)
    return f if f is not None and f.own > 0 else None


def plan_order(layer: MapLayer, call: ActionCall, state: GameState) -> OrderPlan:
    """Check ``call`` against the map layer and the front picture; refuse or plan.

    ``from_state`` / ``to_state`` are the brief's coordinates. ``from_province``
    and ``to_province`` narrow them to one stack and one target province when the
    caller knows them; without them the harness selects the state's counters with
    a box and aims at a province of the target state itself.
    """
    args = call.arguments
    attack = call.name == "attack_province"
    src, dst = int(args["from_state"]), int(args["to_state"])
    via = [int(s) for s in args.get("via", [])]
    if src == dst:
        raise OrderRefused(f"from_state and to_state are both {src}: an order within one state "
                           "cannot be told apart from no order by the front picture")
    if len(via) > MAX_VIA:
        raise OrderRefused(f"at most {MAX_VIA} waypoints")
    from_provinces = _state_provinces(layer, src)
    to_provinces = _state_provinces(layer, dst)
    for sid in via:
        _state_provinces(layer, sid)

    from_province = args.get("from_province")
    if from_province is not None:
        from_province = int(from_province)
        if from_province not in from_provinces:
            raise OrderRefused(f"province {from_province} is not a land province of state {src}")
        if STACK_KIND not in layer.stacks(from_province):
            raise OrderRefused(f"province {from_province} has no unit-counter position "
                               "(type 0) in map/unitstacks.txt, so its stack cannot be clicked")

    forces = {f.state: f for f in state.state_forces or []}
    if state.state_forces is None:
        raise OrderRefused("the reader has no front picture (the mod's div lines), so there is "
                           "no telling where the player's divisions are")
    if _owned_by(forces, src) is None:
        raise OrderRefused(
            f"the front picture (as of {state.state_forces_date or 'an unknown date'}) shows no "
            f"division of yours in state {src}; it lists only states at or behind the front")
    own_tag = (state.country or "").upper()
    there = forces.get(dst)

    to_province = args.get("to_province")
    if attack:
        enemy = there is not None and (there.foe > 0 or (there.controller not in (None, own_tag)))
        if not enemy:
            raise OrderRefused(f"nothing hostile is shown in state {dst} (no enemy division, not "
                               "enemy-controlled): use move_division")
        pool = {from_province} if from_province is not None else set(from_provinces)
        touching = {p: [n for n in land_neighbours(layer, p) if n in to_provinces] for p in pool}
        if not any(touching.values()):
            raise OrderRefused(f"state {dst} does not border state {src}"
                               + (f" at province {from_province}" if from_province else "")
                               + ": an attack goes to an adjacent province; move up first")
        if to_province is not None:
            to_province = int(to_province)
            if to_province not in to_provinces or not any(to_province in v for v in touching.values()):
                edge = f"province {from_province}" if from_province else f"state {src}"
                raise OrderRefused(f"province {to_province} is not a province of state {dst} "
                                   f"that borders {edge}")
        else:
            counts: dict[int, int] = {}
            for v in touching.values():
                for n in v:
                    counts[n] = counts.get(n, 0) + 1
            to_province = sorted(counts, key=lambda n: (-counts[n], n))[0]
    else:
        if there is not None and there.foe > 0 and to_province is None:
            raise OrderRefused(f"the front picture shows {there.foe} enemy division(s) in state "
                               f"{dst}: use attack_province, or name the to_province you know is clear")
        if to_province is not None:
            to_province = int(to_province)
            if to_province not in to_provinces:
                raise OrderRefused(f"province {to_province} is not a land province of state {dst}")
        else:
            x, y = layer.interior_point("state", dst)
            found = layer.province_at(x, y)
            to_province = found if found in to_provinces else to_provinces[0]
        starts = [from_province] if from_province is not None else from_provinces
        if land_hops(layer, starts, {to_province}) is None:
            raise OrderRefused(f"no land route of {MAX_HOPS} borders or fewer from state {src} to "
                               f"province {to_province}: a sea crossing is not a click order")

    via_provinces = []
    for sid in via:
        x, y = layer.interior_point("state", sid)
        found = layer.province_at(x, y)
        via_provinces.append(found if found is not None and _land(layer, found)
                             else _state_provinces(layer, sid)[0])
    return OrderPlan(call.name, src, dst, from_province, int(to_province), tuple(via_provinces),
                     tuple(via), own_tag, tuple(from_provinces))


# --- where to click -------------------------------------------------------------


@dataclass(frozen=True)
class Gesture:
    kind: str                         # click | drag
    point: tuple[int, int]
    button: str = "left"
    shift: bool = False
    end: tuple[int, int] | None = None
    label: str = ""

    def describe(self) -> str:
        if self.kind == "drag":
            return f"drag {self.point} -> {self.end} ({self.label})"
        return f"{'shift+' if self.shift else ''}{self.button}-click {self.point} ({self.label})"


def _on_screen(camera, x: float, y: float, margin: int = 4) -> bool:
    vp = camera.viewport
    return (vp.left + margin <= x < vp.width - vp.right - margin
            and vp.top + margin <= y < vp.height - vp.bottom - margin)


def plan_gestures(layer: MapLayer, camera, plan: OrderPlan, state: GameState,
                  counter_offset: tuple[int, int] = (0, 0)) -> list[Gesture]:
    """The mouse gestures for ``plan`` at ``camera``. Raises OrderRefused when a
    point is outside the viewport or two of them would be one click."""
    gestures: list[Gesture] = []
    dx, dy = counter_offset
    if plan.from_province is not None:
        ax, ay = layer.anchor(plan.from_province, STACK_KIND)
        sx, sy = camera.to_screen(ax, ay)
        sx, sy = sx + dx, sy + dy
        if not _on_screen(camera, sx, sy):
            raise OrderRefused(f"the unit counter of province {plan.from_province} would be at "
                               f"({sx:.0f}, {sy:.0f}), outside the map view")
        select = Gesture("click", (round(sx), round(sy)), "left",
                         label=f"select stack in province {plan.from_province}")
    else:
        select = _box(layer, camera, plan, state, counter_offset)
    gestures.append(select)
    # Waypoints first, the target last: the first right-click sets the order, each
    # shift-right-click after it queues the next leg.
    legs = [(f"waypoint state {sid}", prov) for sid, prov in
            zip(plan.via_states, plan.via_provinces, strict=True)]
    legs.append((f"target province {plan.target_province}", plan.target_province))
    for position, (label, prov) in enumerate(legs):
        try:
            point = screen_point(layer, "province", prov, camera)
        except OffScreen as exc:
            raise OrderRefused(f"{exc}; {label} is too far from the stack to share one view") from None
        gestures.append(Gesture("click", point, "right", shift=position > 0, label=label))
    start = select.point if select.kind == "click" else None
    if start is not None:
        for g in gestures[1:]:
            if abs(g.point[0] - start[0]) + abs(g.point[1] - start[1]) < MIN_SEPARATION:
                raise OrderRefused("the stack and a target are under "
                                   f"{MIN_SEPARATION} px apart on screen: zoom in before ordering")
    return gestures


def _box(layer: MapLayer, camera, plan: OrderPlan, state: GameState,
         counter_offset: tuple[int, int]) -> Gesture:
    """A selection box over every unit counter of ``from_state``, refused when it
    would also take counters of another state that holds divisions of the player's."""
    dx, dy = counter_offset
    points = []
    for p in plan.from_provinces:
        if STACK_KIND not in layer.stacks(p):
            continue
        ax, ay = layer.anchor(p, STACK_KIND)
        sx, sy = camera.to_screen(ax, ay)
        points.append((sx + dx, sy + dy))
    if not points:
        raise OrderRefused(f"state {plan.from_state} has no unit-counter positions to select")
    for sx, sy in points:
        if not _on_screen(camera, sx, sy):
            raise OrderRefused(f"state {plan.from_state} does not fit the map view (a counter "
                               f"would be at ({sx:.0f}, {sy:.0f})): name from_province instead")
    pad = 10
    x0, x1 = min(p[0] for p in points) - pad, max(p[0] for p in points) + pad
    y0, y1 = min(p[1] for p in points) - pad, max(p[1] for p in points) + pad
    # The picture lists only states at or behind the front, so a state it does not
    # list may well hold divisions of the player's. Only a listed state with none
    # is known to be safe to box across.
    empty = {f.state for f in state.state_forces or [] if f.own == 0}
    for pid, info in layer.provinces.items():
        sid = layer.state_of(pid)
        if info.kind != "land" or sid is None or sid == plan.from_state or sid in empty:
            continue
        stack = layer.stacks(pid).get(STACK_KIND)
        if stack is None:
            continue
        sx, sy = camera.to_screen(stack.x, stack.y)
        if x0 <= sx + dx <= x1 and y0 <= sy + dy <= y1:
            raise OrderRefused(
                f"a box over state {plan.from_state} would also catch the counter of province "
                f"{pid} in state {sid}, which may hold divisions of yours (the front picture lists "
                "only the front): name from_province to click one stack instead")
    return Gesture("drag", (round(x0), round(y0)), "left", end=(round(x1), round(y1)),
                   label=f"box over state {plan.from_state}")


# --- the ledger: did the order take? --------------------------------------------


@dataclass
class PendingOrder:
    number: int
    plan: OrderPlan
    issued: str                       # in-game date
    from_own: int
    to_own: int
    to_foe: int
    to_controller: str | None = None
    status: str = "sent"
    settled: str | None = None        # in-game date it was last reported settled

    def line(self) -> str:
        what = (f"{'attack' if self.plan.action == 'attack_province' else 'move'} "
                f"state {self.plan.from_state} -> {self.plan.to_state}")
        return f"#{self.number} {what} (given {self.issued}): {self.status}"


def _days(a: str, b: str) -> int | None:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except ValueError:
        return None


class OrderLedger:
    """What was ordered, and what the front picture says about it since."""

    def __init__(self) -> None:
        self.orders: list[PendingOrder] = []
        self._count = 0

    def add(self, plan: OrderPlan, state: GameState) -> PendingOrder:
        forces = {f.state: f for f in state.state_forces or []}
        here, there = forces.get(plan.from_state), forces.get(plan.to_state)
        self._count += 1
        order = PendingOrder(self._count, plan, state.date, here.own if here else 0,
                             there.own if there else 0, there.foe if there else 0,
                             there.controller if there else None)
        self.orders.append(order)
        return order

    def update(self, state: GameState) -> list[str]:
        """Re-read each order against the picture; the lines for the brief."""
        if state.state_forces is None:
            return [o.line() for o in self.orders]
        forces = {f.state: f for f in state.state_forces}
        keep = []
        for order in self.orders:
            self._judge(order, state, forces)
            if order.settled is not None:
                age = _days(order.settled, state.date)
                if age is not None and age > KEEP_SETTLED_DAYS:
                    continue
            keep.append(order)
        self.orders = keep
        return [o.line() for o in self.orders]

    def _judge(self, order: PendingOrder, state: GameState, forces: dict[int, StateForces]) -> None:
        if order.settled is not None:
            return
        plan = order.plan
        here, there = forces.get(plan.from_state), forces.get(plan.to_state)
        own_from = here.own if here else 0
        own_to = there.own if there else 0
        foe_to = there.foe if there else 0
        age = _days(order.issued, state.date)
        held = order.to_foe > 0 or order.to_controller not in (None, plan.own_tag)
        if plan.action == "attack_province" and held and there is not None and (
                there.controller == plan.own_tag):
            order.status = f"taken: state {plan.to_state} is now yours"
            order.settled = state.date
        elif own_to > order.to_own:
            order.status = (f"arrived: your divisions in state {plan.to_state} went "
                            f"{order.to_own} -> {own_to}")
            order.settled = state.date
        elif plan.action == "attack_province" and foe_to < order.to_foe:
            order.status = (f"landing: enemy divisions in state {plan.to_state} went "
                            f"{order.to_foe} -> {foe_to}")
        elif own_from < order.from_own:
            order.status = (f"underway: {order.from_own - own_from} division(s) have left "
                            f"state {plan.from_state}")
        elif age is not None and age >= NO_CHANGE_DAYS:
            order.status = (f"no change after {age} days: the order may not have taken "
                            "(wrong stack selected, or still marching inside the state)")
            order.settled = state.date
        else:
            order.status = "sent, nothing to see yet"


# --- the executor ----------------------------------------------------------------


Centre = Callable[[int], None]


class UnitOrderExecutor:
    """Gives one division order through the driver and reports it honestly."""

    def __init__(
        self,
        driver,
        layer: MapLayer,
        camera: CameraRecord | Callable[[], CameraRecord],
        centre: Centre,
        *,
        ledger: OrderLedger | None = None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], datetime] = datetime.now,
        settle: float = 0.8,
        unattended: bool = False,
    ):
        self.driver = driver
        self.layer = layer
        self._camera = camera
        self.centre = centre
        self.ledger = ledger or OrderLedger()
        self._sleep = sleep or driver._sleep
        self._now = now
        self.settle = settle
        #: An independent campaign: the camera's age limit is replaced by the display check.
        self.unattended = unattended
        self.last_gestures: list[Gesture] = []

    def record(self) -> CameraRecord:
        return self._camera() if callable(self._camera) else self._camera

    def __call__(self, call: ActionCall) -> ActionResult:
        driver = self.driver
        if driver._read_state is None:
            return self._result(call, False, "unverified", "no reader to plan against; nothing was sent")
        state = driver._read_state()
        try:
            plan = plan_order(self.layer, call, state)
            record = self.record()
            display = display_fingerprint(driver)
            screen = driver.config.screen or (tuple(display["resolution"]) if display else None)
            camera = record.camera_for(self.layer, plan.centre_province, screen=screen,
                                       now=self._now(), display=display, unattended=self.unattended)
            gestures = plan_gestures(self.layer, camera, plan, state, record.counter_offset)
        except OrderRefused as exc:
            return self._result(call, False, "rejected", f"refused before any input: {exc}; nothing was sent")
        except MapError as exc:
            return self._result(call, False, "rejected", f"refused before any input: {exc}; nothing was sent")
        self.last_gestures = gestures
        if driver.dry_run:
            self.centre(plan.centre_province)
            for g in gestures:
                self._perform(g)
            steps = "; ".join(g.describe() for g in gestures)
            return self._result(call, False, "not_executed",
                                f"dry run: would {plan.describe()}: {steps}")
        self.centre(plan.centre_province)
        self._sleep(self.settle)
        for g in gestures:
            self._perform(g)
            self._sleep(0.25)
        order = self.ledger.add(plan, state)
        return self._result(call, False, "unverified", (
            f"order #{order.number} sent ({plan.describe()}). A move takes days, so it cannot be "
            "confirmed yet: the brief's Order lines will say whether it took. Do not repeat it "
            "meanwhile."))

    def _perform(self, g: Gesture) -> None:
        if g.kind == "drag":
            self.driver.drag(g.point, g.end, label=g.label)
        else:
            self.driver.click_at(g.point, g.button, shift=g.shift, label=g.label)

    @staticmethod
    def _result(call: ActionCall, ok: bool, kind: str | None, message: str) -> ActionResult:
        return ActionResult(ok=ok, action=call.name, call_id=call.call_id, message=message,
                            error_kind=kind, changed={"channel": "mouse"} if kind == "unverified" else {})


# --- centring the view -----------------------------------------------------------


def console_centre(console) -> Centre:
    """Centre the view on a province with the console's ``goto_province`` (console
    command, console_commands_documentation.md l.988: "Centers to province")."""
    def centre(province: int) -> None:
        console.send_lines([f"goto_province {int(province)}"])
    return centre


def decorate_state(state: GameState, ledger: OrderLedger | None, names: dict[int, str] | None) -> GameState:
    """Fill the order lines and state names into a state the reader returned."""
    if names and state.state_forces:
        for f in state.state_forces:
            f.name = f.name or names.get(f.state)
    if ledger is not None:
        state.order_status = ledger.update(state)
    return state


def state_names(layer: MapLayer, index) -> dict[int, str]:
    """State id -> display name, from the identifier index when it has them."""
    names: dict[int, str] = {}
    if index is None:
        return names
    for sid in layer.states:
        info = getattr(index, "states", {}).get(str(sid))
        if info is not None and getattr(info, "name", None):
            names[sid] = info.name
    return names


def enable_from_config(driver, console, config) -> UnitOrderExecutor:
    """Turn division orders on for a driver, from the run's configuration.

    Reads the map from the playset (``game_dir`` is required: the click points come
    from its map files) and re-reads ``camera.json`` on every order, so recording a
    new fit mid-session takes effect without a restart. The view is centred with the
    console's ``goto_province``; the map search is not used, because it needs a click
    on its first result and where it centres is unmeasured (docs/unit-orders.md).
    """
    from ..identifiers import Playset

    if config.game_dir is None:
        raise ValueError("division orders need the map: pass --game-dir")
    layer = MapLayer.load(Playset.from_paths(config.game_dir, config.mod_dirs))
    path = Path(config.camera_path or Path(config.run_dir) / DEFAULT_FILENAME)
    executor = UnitOrderExecutor(driver, layer, lambda: CameraRecord.load(path), console_centre(console),
                                 unattended=bool(config.campaign))
    driver.enable_unit_orders(executor)
    driver.order_ledger = executor.ledger
    driver.order_layer = layer
    return executor


class OrdersComposite(CompositeAdapter):
    """The composite, with the order ledger and state names added to every read:
    this is what puts ``Order:`` lines and state names in the brief."""

    def __init__(self, reader, writer, ledger: OrderLedger, names: dict[int, str] | None = None,
                 **kwargs):
        super().__init__(reader, writer, **kwargs)
        self.ledger = ledger
        self.names = names or {}

    def read_state(self) -> GameState:
        return decorate_state(super().read_state(), self.ledger, self.names)
