"""Telemetry -> tactical simulator -> orders -> harness actions (#85).

The simulator (sim.py) scores controllers on scenarios; the live game gives the
harness a front picture (divisions per state) and two click actions. This is the
plumbing between them, so a scripted or learned controller written against the
simulator's ``View`` can drive real orders:

    bridge = ControllerBridge(layer, ScriptedController())
    calls = bridge.decide(game_state)      # [ActionCall("attack_province", ...), ...]

What it does, in order: build a small simulator world from the map layer around
the states the front picture lists (provinces, terrain, adjacency), put the
player's divisions and the enemy's where the picture says, run the controller's
``act`` on the player's side, and translate each order into a ``move_division`` or
``attack_province`` between states.

Where it is coarser than the game, and says so rather than hiding it:

* **Position is per state.** The picture counts divisions per state, so each state's
  divisions are placed together in one province (the state's middle, the one
  ``mapcamera.map_point`` would click). The controller therefore sees fewer, fatter
  stacks than the game has, and its province-level orders collapse into state-level
  ones.
* **Strength is two-valued.** The picture says how many divisions are below half
  strength, not how strong each is: weak ones are placed at 40%, the rest at full.
* **An order moves a whole state.** The click selects every counter in the state (or
  one stack), so the controller's "send one of the three" becomes "send all three":
  the action names how many the controller meant in its rationale, nothing more.
* **No fog of war either way**: the simulator is full-information; the telemetry
  lists what the player can see of each state it reports, no more.
* **Supply hubs are guessed**: the rearmost province of each side.

None of this is validated against the game; ``docs/tactics.md`` and
``docs/unit-orders.md`` list it as the sim-to-real gap this stage cannot close.
"""

from __future__ import annotations

from collections import deque

from ..mapdata import MapLayer
from ..types import ActionCall, GameState
from .env import View
from .model import load_constants
from .scenario import Scenario
from .sim import World

WEAK_STRENGTH = 0.4
DEFAULT_TEMPLATE = "inf_9"
#: Map terrain -> the simulator's terrain table (anything unknown plays as plains).
TERRAIN = {
    "plains": "plains", "forest": "forest", "hills": "hills", "mountain": "mountain",
    "urban": "urban", "marsh": "marsh", "jungle": "jungle", "desert": "desert",
}


class BridgeError(ValueError):
    """The telemetry cannot be turned into a simulator world."""


class ControllerBridge:
    def __init__(self, layer: MapLayer, controller, *, template: str = DEFAULT_TEMPLATE,
                 reach: int = 1, seed: int = 0):
        if template not in load_constants().templates:
            raise ValueError(f"no division template {template!r} in the simulator")
        self.layer = layer
        self.controller = controller
        self.template = template
        self.reach = reach
        self.seed = seed
        #: Filled by ``build``: simulator division id -> the state it stands in.
        self.home: dict[str, int] = {}

    # --- state -> world --------------------------------------------------------------

    def _province_of(self, state: int) -> int:
        x, y = self.layer.interior_point("state", state)
        found = self.layer.province_at(x, y)
        members = [p for p in self.layer.states[state].provinces
                   if self.layer.provinces[p].kind == "land"]
        if not members:
            raise BridgeError(f"state {state} has no land province")
        return found if found in members else sorted(members)[0]

    def _neighbour_states(self, states: set[int]) -> set[int]:
        out = set(states)
        frontier = set(states)
        for _ in range(self.reach):
            nxt = set()
            for sid in frontier:
                for p in self.layer.states[sid].provinces:
                    for n in self.layer.neighbours(p, straits=False):
                        other = self.layer.state_of(n)
                        if other is not None and other not in out:
                            nxt.add(other)
            out |= nxt
            frontier = nxt
        return out

    def build(self, state: GameState, seed: int | None = None) -> World:
        """A simulator world standing for ``state``'s front picture."""
        if not state.state_forces:
            raise BridgeError("the front picture is empty or not observed: nothing to control")
        own_tag = (state.country or "").upper()
        picture = {f.state: f for f in state.state_forces if f.state in self.layer.states}
        if not picture:
            raise BridgeError("no state of the front picture is on this map")
        included = self._neighbour_states(set(picture))
        side_of: dict[int, str] = {}
        for sid in included:
            f = picture.get(sid)
            controller = f.controller if f is not None else self.layer.states[sid].owner
            side_of[sid] = "A" if controller == own_tag else "B"
        land = {p for sid in included for p in self.layer.states[sid].provinces
                if self.layer.provinces[p].kind == "land"}
        edges = sorted({tuple(sorted((p, n))) for p in land
                        for n in self.layer.neighbours(p, straits=False) if n in land})
        owner = {p: side_of[self.layer.state_of(p)] for p in land}
        hubs = self._hubs(land, edges, owner)
        provinces = [{"id": str(p), "terrain": TERRAIN.get(self.layer.provinces[p].terrain, "plains"),
                      "controller": owner[p], **({"hub": hubs[p]} if p in hubs else {})}
                     for p in sorted(land)]
        divisions, self.home = [], {}
        for sid, f in sorted(picture.items()):
            at = str(self._province_of(sid))
            for side, count, weak in (("A", f.own, f.own_weak), ("B", f.foe, f.foe_weak)):
                for index in range(count):
                    divisions.append({"side": side, "template": self.template, "province": at,
                                      "strength": WEAK_STRENGTH if index < weak else 1.0})
        targets = sorted(str(p) for p in land if owner[p] == "B")
        mine = sorted(str(p) for p in land if owner[p] == "A")
        scenario = Scenario.from_dict({
            "name": "live-front", "description": "built from the front picture", "scored_side": "A",
            "max_hours": 24 * 30, "step_hours": 24,
            "provinces": provinces, "edges": [[str(a), str(b), 0] for a, b in edges],
            "divisions": divisions,
            "goals": {"A": {"kind": "capture", "provinces": targets or mine},
                      "B": {"kind": "hold", "provinces": targets or mine}},
        })
        world = World(scenario, self.seed if seed is None else seed)
        # The simulator numbers divisions A1.. and B1.. in the order they were listed.
        for did, d in world.divisions.items():
            self.home[did] = self.layer.state_of(int(d.province))
        return world

    def _hubs(self, land: set[int], edges, owner: dict[int, str]) -> dict[int, str]:
        """The rearmost province of each side: the most borders from the other side."""
        nbrs: dict[int, list[int]] = {p: [] for p in land}
        for a, b in edges:
            nbrs[a].append(b)
            nbrs[b].append(a)
        hubs = {}
        for side in ("A", "B"):
            sources = [p for p in land if owner[p] != side]
            dist = {p: 0 for p in sources}
            queue = deque(sources)
            while queue:
                here = queue.popleft()
                for n in nbrs[here]:
                    if n not in dist:
                        dist[n] = dist[here] + 1
                        queue.append(n)
            mine = [p for p in land if owner[p] == side]
            if mine:
                hubs[max(mine, key=lambda p: (dist.get(p, 0), -p))] = side
        return hubs

    # --- orders -> actions --------------------------------------------------------------

    def decide(self, state: GameState, seed: int | None = None) -> list[ActionCall]:
        """Run the controller on the world ``state`` stands for; return the actions."""
        world = self.build(state, seed)
        orders = self.controller.act(View(world, "A"))
        return self.translate(world, orders)

    def translate(self, world: World, orders) -> list[ActionCall]:
        """Simulator orders -> one action per (from state, to state, kind)."""
        wanted: dict[tuple[str, int, int], int] = {}
        for order in orders:
            if order.kind == "hold" or order.target is None or order.division not in self.home:
                continue
            src = self.home[order.division]
            dst = self.layer.state_of(int(order.target))
            if dst is None or dst == src:
                continue
            kind = "attack_province" if order.kind == "attack" else "move_division"
            wanted[(kind, src, dst)] = wanted.get((kind, src, dst), 0) + 1
        calls = [ActionCall(kind, {"from_state": src, "to_state": dst},
                            rationale=f"{self.controller.name}: {n} division(s)")
                 for (kind, src, dst), n in sorted(wanted.items())]
        return calls
