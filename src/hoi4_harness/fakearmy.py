"""Divisions for the fake game: where they stand, how they move, what a click does.

``FakeGame`` (fakegame.py) models the files the harness reads and writes. This
adds the one thing those files cannot show -- armies on the map -- so the whole
division-order path (front picture, camera, click, ledger) runs with no game:

* divisions stand in provinces of a real :class:`MapLayer`, and march along its
  land adjacency at a terrain-dependent speed (days per province, below);
* an enemy division blocks its province: marching in is an attack, which wears
  both sides down a little each day until the defenders are gone;
* a state changes hands when a division of one side stands in it and none of the
  other does -- coarser than the game, but the same grain the mod reports;
* the mod's ``div`` / ``divend`` lines are printed from that picture, with the
  same rule for which states are listed (llm_bridge_divisions.txt);
* a mouse resolves clicks through a *true* camera the test supplies, so a harness
  that aims with a wrong fit visibly selects nothing or orders the wrong place.

It is not a combat model: the tactical simulator (tactics/) is. Speeds and wear are
round numbers chosen to make orders take days and fights take days, nothing more.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from .adapters.unit_orders import STACK_KIND, centre_point
from .mapdata import MapLayer

#: Days to march into a province, by its terrain (infantry at a plausible pace).
TERRAIN_DAYS = {
    "plains": 1.0, "urban": 1.5, "forest": 1.5, "desert": 1.5, "hills": 2.0,
    "marsh": 2.5, "jungle": 3.0, "mountain": 3.0,
}
#: Strength an attacker and a defender lose a day while they fight.
ATTACKER_WEAR = 0.04
DEFENDER_WEAR = 0.12
WEAK = 0.5


@dataclass
class FakeDivision:
    id: str
    owner: str
    province: int
    strength: float = 1.0
    path: list[int] = field(default_factory=list)
    progress: float = 0.0
    attacking: bool = False


class FakeArmy:
    """Divisions on a map layer, for one player and the countries it fights."""

    def __init__(
        self,
        layer: MapLayer,
        country: str,
        divisions: list[tuple[str, int, int]],
        at_war: set[str],
        camera_for: Callable[[int], object],
        *,
        control: dict[int, str] | None = None,
        counter_tolerance: float = 16.0,
        counter_offset: tuple[int, int] = (0, 0),
    ):
        """``divisions``: ``(owner tag, province, count)``. ``camera_for(province)``
        is the *true* camera once the view is centred on that province."""
        self.layer = layer
        self.country = country
        self.at_war = set(at_war)
        self.camera_for = camera_for
        self.counter_tolerance = counter_tolerance
        self.counter_offset = counter_offset
        self.control = dict(control) if control is not None else {
            sid: s.owner for sid, s in layer.states.items() if s.owner}
        self.divisions: dict[str, FakeDivision] = {}
        for owner, province, count in divisions:
            for _ in range(count):
                did = f"{owner}{len(self.divisions) + 1}"
                self.divisions[did] = FakeDivision(did, owner, province)
        self.view: int | None = None          # the province the view is centred on
        self.selected: list[str] = []
        self.pos = (0, 0)
        self.log: list[str] = []              # what the clicks did, for a person or a test

    def add(self, owner: str, province: int, strength: float = 1.0) -> FakeDivision:
        """One more division, for a test that changes the board."""
        did = f"{owner}{len(self.divisions) + 1}"
        while did in self.divisions:
            did += "x"
        self.divisions[did] = FakeDivision(did, owner, province, strength)
        return self.divisions[did]

    # --- queries ----------------------------------------------------------------

    def hostile(self, owner: str) -> bool:
        return owner in self.at_war

    def stand_in(self, province: int, owner: str | None = None) -> list[FakeDivision]:
        return [d for d in self.divisions.values()
                if d.province == province and (owner is None or d.owner == owner)]

    def foes_in(self, province: int) -> list[FakeDivision]:
        return [d for d in self.divisions.values()
                if d.province == province and self.hostile(d.owner)]

    def in_state(self, state: int) -> list[FakeDivision]:
        members = set(self.layer.states[state].provinces)
        return [d for d in self.divisions.values() if d.province in members]

    def _land_neighbours(self, province: int) -> list[int]:
        return [n for n in self.layer.neighbours(province, straits=False)
                if self.layer.provinces[n].kind == "land"]

    def route(self, start: int, goal: int) -> list[int] | None:
        """Shortest land route avoiding provinces the enemy holds (start excluded)."""
        prev = {start: None}
        queue = deque([start])
        while queue:
            here = queue.popleft()
            if here == goal:
                out = []
                while here != start:
                    out.append(here)
                    here = prev[here]
                return out[::-1]
            for n in self._land_neighbours(here):
                if n in prev or (self.foes_in(n) and n != goal):
                    continue
                prev[n] = here
                queue.append(n)
        return None

    # --- the screen -------------------------------------------------------------

    def goto(self, province: int) -> None:
        if province not in self.layer.provinces:
            raise ValueError(f"no province {province}")
        self.view = province
        self.selected = []

    def _camera(self):
        if self.view is None:
            return None
        return self.camera_for(self.view)

    def _province_at(self, x: float, y: float) -> int | None:
        camera = self._camera()
        if camera is None:
            return None
        mx, my = camera.to_map(x, y)
        return self.layer.province_at(round(mx), round(my))

    def _counter(self, province: int, camera) -> tuple[float, float] | None:
        stack = self.layer.stacks(province).get(STACK_KIND)
        if stack is None:
            return None
        sx, sy = camera.to_screen(stack.x, stack.y)
        return sx + self.counter_offset[0], sy + self.counter_offset[1]

    def move_to(self, x: float, y: float) -> None:
        self.pos = (x, y)

    def click(self, button: str, shift: bool) -> None:
        (self.left_click if button == "left" else self.right_click)(*self.pos, shift)

    def left_click(self, x: float, y: float, shift: bool = False) -> None:
        camera = self._camera()
        province = self._province_at(x, y)
        counter = self._counter(province, camera) if province is not None else None
        near = counter is not None and (
            (counter[0] - x) ** 2 + (counter[1] - y) ** 2 <= self.counter_tolerance ** 2)
        mine = [d.id for d in self.stand_in(province, self.country)] if near else []
        self.selected = mine
        self.log.append(f"left-click ({x:.0f}, {y:.0f}): province {province}, "
                        f"{'selected ' + str(len(mine)) if mine else 'selected nothing'}")

    def drag(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        camera = self._camera()
        if camera is None:
            self.selected = []
            return
        x0, x1 = sorted((start[0], end[0]))
        y0, y1 = sorted((start[1], end[1]))
        picked = []
        for d in self.divisions.values():
            if d.owner != self.country:
                continue
            counter = self._counter(d.province, camera)
            if counter and x0 <= counter[0] <= x1 and y0 <= counter[1] <= y1:
                picked.append(d.id)
        self.selected = picked
        self.log.append(f"box ({x0:.0f},{y0:.0f})-({x1:.0f},{y1:.0f}): selected {len(picked)}")

    def right_click(self, x: float, y: float, shift: bool = False) -> None:
        province = self._province_at(x, y)
        if not self.selected or province is None or self.layer.provinces[province].kind != "land":
            self.log.append(f"right-click ({x:.0f}, {y:.0f}): no order (province {province}, "
                            f"{len(self.selected)} selected)")
            return
        gave = 0
        for did in self.selected:
            d = self.divisions[did]
            start = d.path[-1] if shift and d.path else d.province
            route = self.route(start, province) if start != province else []
            if route is None:
                continue
            d.attacking = False
            d.path = (d.path + route) if shift else route
            if not shift:
                d.progress = 0.0
            gave += 1
        self.log.append(f"right-click ({x:.0f}, {y:.0f}): {'shift ' if shift else ''}order to "
                        f"province {province} for {gave} of {len(self.selected)}")

    # --- time -------------------------------------------------------------------

    def tick_day(self) -> None:
        for d in list(self.divisions.values()):
            if d.id not in self.divisions or not d.path:
                continue
            nxt = d.path[0]
            foes = self.foes_in(nxt)
            if foes:
                d.attacking = True
                for f in foes:
                    f.strength -= DEFENDER_WEAR
                    if f.strength <= 0:
                        del self.divisions[f.id]
                d.strength -= ATTACKER_WEAR
                if d.strength <= 0:
                    del self.divisions[d.id]
                continue
            d.attacking = False
            d.progress += 1.0
            if d.progress < TERRAIN_DAYS.get(self.layer.provinces[nxt].terrain, 1.0):
                continue
            d.progress = 0.0
            d.province = d.path.pop(0)
            self._take(d)

    def _take(self, d: FakeDivision) -> None:
        state = self.layer.state_of(d.province)
        if state is None or self.control.get(state) == d.owner:
            return
        if not any(o.owner != d.owner and self.hostile_pair(o.owner, d.owner)
                   for o in self.in_state(state)):
            self.control[state] = d.owner

    def hostile_pair(self, a: str, b: str) -> bool:
        return (a == self.country and self.hostile(b)) or (b == self.country and self.hostile(a))

    # --- what the mod prints -------------------------------------------------------

    def front_rows(self) -> list[dict]:
        """The ``div`` lines the mod would log now: states the enemy controls, and
        the player's own states that touch one, that hold a division of either side."""
        layer = self.layer
        enemy_held = {s for s, c in self.control.items() if self.hostile(c)}
        touching = {}
        for sid, info in layer.states.items():
            near = set()
            for p in info.provinces:
                for n in layer.neighbours(p, straits=False):
                    other = layer.state_of(n)
                    if other is not None and other != sid:
                        near.add(other)
            touching[sid] = near
        rows = []
        for sid in sorted(layer.states):
            listed = sid in enemy_held or (
                self.control.get(sid) == self.country and touching[sid] & enemy_held)
            if not listed:
                continue
            here = self.in_state(sid)
            own = [d for d in here if d.owner == self.country]
            foe = [d for d in here if self.hostile(d.owner)]
            if not own and not foe:
                continue
            rows.append({"state": sid, "ctl": self.control.get(sid, "---"), "own": len(own),
                         "own_weak": sum(d.strength < WEAK for d in own), "foe": len(foe),
                         "foe_weak": sum(d.strength < WEAK for d in foe)})
        return rows


def true_camera_factory(layer: MapLayer, base, fit_province: int, rule: str) -> Callable[[int], object]:
    """The camera at each view: ``base`` (the camera when centred on ``fit_province``)
    slid so the game's centre point for the new province lands where it did."""
    from .adapters.unit_orders import ShiftedCamera

    here = centre_point(layer, fit_province, rule)

    def camera_for(province: int):
        there = centre_point(layer, province, rule)
        return ShiftedCamera(base, there[0] - here[0], there[1] - here[1])

    return camera_for


class FakeGui:
    """A pyautogui-shaped mouse that talks to a :class:`FakeArmy` (what a test hands
    the input driver as ``gui``). ``shift`` reads the fake keyboard's held keys."""

    def __init__(self, army: FakeArmy, held: Callable[[], set[str]] = lambda: set()):
        self.army = army
        self._held = held
        self.calls: list[tuple] = []
        self._down: tuple[float, float] | None = None

    def moveTo(self, x, y, duration=0.0):  # noqa: N802 - pyautogui's name
        self.army.move_to(x, y)

    def click(self, button="left"):
        self.calls.append(("click", button, self.army.pos, "shift" in self._held()))
        self.army.click(button, "shift" in self._held())

    def mouseDown(self, button="left"):  # noqa: N802
        self._down = self.army.pos

    def mouseUp(self, button="left"):  # noqa: N802
        start, self._down = self._down, None
        if start is not None:
            self.calls.append(("drag", start, self.army.pos))
            self.army.drag(start, self.army.pos)

    def keyDown(self, key):  # noqa: N802
        raise AssertionError("keys go through scan codes, not the gui")

    keyUp = keyDown  # noqa: N815
