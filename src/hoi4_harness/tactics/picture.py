"""What a planner sees of the board: regions with counts, not divisions with positions.

The real game hands the harness a *front picture*: per state, how many divisions of each side
stand there and how many of those are under half strength (docs/unit-orders.md section 1).
It does not give a division's exact province, its organisation, its template or whether it is
cut off. `Picture` is that, built from a simulator `View`:

* ``observe="aggregated"`` (the default, the real game's resolution): a region is a state
  (the scenario's ``region`` field, else a pair of neighbouring provinces); it carries own
  and enemy division counts, the weak counts, the controller, and the static map (terrain,
  width, rivers, supply hubs, objectives). Power is estimated from counts alone, every
  division taken as a full-organisation infantry division and a weak one at 40 % strength.
* ``observe="full"``: a region is one province and the numbers are the simulator's own
  (real power, real counts). The upper bound the aggregation is measured against.

Not visible in either mode: the other side's orders. Foreign divisions are visible
everywhere (the mod lists every state the enemy controls and every own state touching one).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .controllers import power
from .env import View
from .model import Division, load_constants
from .sim import World, other

OBSERVE_MODES = ("aggregated", "full")
WEAK = 0.5            # the mod's definition: unit_strength under one half
WEAK_AS_POWER = 0.4   # a weak division is placed at 40 % strength (as tactics/bridge.py does)
REFERENCE = "inf_9"


def reference_power() -> float:
    tpl = load_constants().templates[REFERENCE]
    return power(Division("ref", "A", tpl, "x", tpl.hp, tpl.org))


@dataclass
class Region:
    id: str
    provinces: tuple[str, ...]
    controller: str
    own: int = 0
    own_weak: int = 0
    foe: int = 0
    foe_weak: int = 0
    own_power: float = 0.0
    foe_power: float = 0.0
    attack_mod: float = 0.0          # terrain modifier for a unit attacking INTO this region
    width: float = 0.0
    hub: str | None = None
    objective: bool = False
    neighbors: dict[str, int] = field(default_factory=dict)   # region -> river on the easiest crossing


@dataclass
class Picture:
    side: str
    hour: int
    goal: dict
    regions: dict[str, Region]
    region_of: dict[str, str]        # province -> region
    observe: str

    @property
    def foe(self) -> str:
        return other(self.side)

    def own_total(self) -> int:
        return sum(r.own for r in self.regions.values())

    def frontier(self) -> list[str]:
        """Own-controlled regions that touch enemy-controlled ones, or hold an enemy division."""
        out = []
        for rid, r in sorted(self.regions.items()):
            if r.controller != self.side:
                continue
            if r.foe or any(self.regions[n].controller == self.foe for n in r.neighbors):
                out.append(rid)
        return out

    def control(self) -> dict[str, str]:
        return {rid: r.controller for rid, r in self.regions.items()}

    def supply_net(self, side: str, ctrl: dict[str, str] | None = None) -> set[str]:
        ctrl = ctrl or self.control()
        net = {rid for rid, r in self.regions.items() if r.hub == side and ctrl[rid] == side}
        queue = deque(net)
        while queue:
            cur = queue.popleft()
            for nb in self.regions[cur].neighbors:
                if nb not in net and ctrl[nb] == side:
                    net.add(nb)
                    queue.append(nb)
        return net

    def cut_off(self, side: str, flips: dict[str, str]) -> tuple[int, int]:
        """(divisions of `side` newly cut off, divisions of `side` freed) if `flips` changed hands.

        Divisions standing in a flipped region are fought, not cut off, and are not counted."""
        base_ctrl = self.control()
        base = self.supply_net(side, base_ctrl)
        ctrl = {**base_ctrl, **flips}
        net = self.supply_net(side, ctrl)
        cut = freed = 0
        for rid, r in self.regions.items():
            n = r.own if side == self.side else r.foe
            if not n:
                continue
            if rid in base and rid not in net and rid not in flips:
                cut += n
            elif rid not in base and rid in net:
                freed += n
        return cut, freed

    def hops(self, sources: list[str]) -> dict[str, int]:
        dist = dict.fromkeys(sources, 0)
        queue = deque(sources)
        while queue:
            cur = queue.popleft()
            for nb in self.regions[cur].neighbors:
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    queue.append(nb)
        return dist


def region_key(raw: dict) -> str:
    """The region a scenario province belongs to: its ``region`` field, else a pair of columns
    that began under the same side (a region never straddles the starting front, as a state
    does not)."""
    if "region" in raw:
        return str(raw["region"])
    if "x" in raw and "y" in raw:
        return f"r{int(raw['x']) // 2}_{int(raw['y'])}{raw.get('controller', '')}"
    return str(raw["id"])


def build_picture(view: View, observe: str = "aggregated") -> Picture:
    if observe not in OBSERVE_MODES:
        raise ValueError(f"observe must be one of {OBSERVE_MODES}")
    w: World = view.world
    c = w.c
    raw = {p["id"]: p for p in w.scenario.provinces}
    region_of = {pid: (pid if observe == "full" else region_key(raw[pid])) for pid in w.provinces}
    members: dict[str, list[str]] = {}
    for pid in sorted(w.provinces):
        members.setdefault(region_of[pid], []).append(pid)
    goal = view.goal
    objective = set(goal.get("provinces", ()))
    p0 = reference_power()
    regions: dict[str, Region] = {}
    for rid, pids in members.items():
        votes = [w.provinces[p].controller for p in pids]
        # a region changes hands on a strict majority; a tie stays with whoever held it first
        controller = max(sorted(set(votes)), key=votes.count)
        if len(set(votes)) > 1 and votes.count("A") == votes.count("B"):
            controller = w.initial_control[pids[0]]
        hubs = {w.provinces[p].hub for p in pids} - {None}
        regions[rid] = Region(
            id=rid, provinces=tuple(pids), controller=controller,
            attack_mod=sum(c.terrain[w.provinces[p].terrain]["attack"] for p in pids) / len(pids),
            width=sum(w.terrain_width(p) for p in pids),
            hub=min(hubs) if hubs else None, objective=bool(objective & set(pids)),
        )
    best: dict[tuple[str, str], int] = {}
    for pid, p in w.provinces.items():
        for nb, river in p.neighbors.items():
            a, b = region_of[pid], region_of[nb]
            if a != b:
                best[(a, b)] = min(best.get((a, b), 9), river)
    for (a, b), river in best.items():
        regions[a].neighbors[b] = river
    for d in w.alive():
        r = regions[region_of[d.province]]
        mine = d.side == view.side
        weak = d.strength_frac < WEAK
        if mine:
            r.own += 1
            r.own_weak += weak
        else:
            r.foe += 1
            r.foe_weak += weak
        if observe == "full":
            if mine:
                r.own_power += power(d)
            else:
                r.foe_power += power(d)
    if observe == "aggregated":
        for r in regions.values():
            r.own_power = p0 * ((r.own - r.own_weak) + WEAK_AS_POWER * r.own_weak)
            r.foe_power = p0 * ((r.foe - r.foe_weak) + WEAK_AS_POWER * r.foe_weak)
    return Picture(view.side, w.hour, goal, regions, region_of, observe)
