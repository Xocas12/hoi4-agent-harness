"""The world: provinces, divisions, battles, movement, supply and control, one hour at a time."""

from __future__ import annotations

import heapq
import math
import random
from collections import deque

from .combat import shoot
from .model import Constants, Division, Order, load_constants
from .scenario import Scenario


def other(side: str) -> str:
    return "B" if side == "A" else "A"


class World:
    def __init__(self, scenario: Scenario, seed: int = 0, constants: Constants | None = None) -> None:
        self.c = constants or load_constants()
        self.scenario = scenario
        self.seed = seed
        self.rng = random.Random(seed)
        self.provinces, self.divisions = scenario.build()
        self.hour = 0
        self.battle_age: dict[str, int] = {}
        self.illegal_orders = 0
        # metrics, per side
        self.strength_lost = {"A": 0.0, "B": 0.0}
        self.destroyed = {"A": 0, "B": 0}
        self.pockets_closed = {"A": 0, "B": 0}  # pockets this side closed on the other
        # divisions of that side that have ever been cut off
        self.cut_off_ever: dict[str, set[str]] = {"A": set(), "B": set()}
        self.initial_strength = {
            s: sum(d.template.hp * d.strength_frac for d in self.divisions.values() if d.side == s)
            for s in ("A", "B")
        }
        self.initial_control = {pid: p.controller for pid, p in self.provinces.items()}
        self.territory_hours = {"A": 0, "B": 0}
        self.supplied: dict[str, dict[str, int]] = {"A": {}, "B": {}}  # province -> hops from a hub
        self._supply_dirty = True
        self._update_supply()
        # divisions that start cut off: the ones an `escape` goal is about
        self.initially_cut_off = {d.id for d in self.alive() if d.encircled}

    # ---- queries -------------------------------------------------------------------------

    def alive(self, side: str | None = None) -> list[Division]:
        return [d for d in self.divisions.values() if d.alive and (side is None or d.side == side)]

    def units_at(self, pid: str, side: str | None = None) -> list[Division]:
        return [
            d for d in self.divisions.values()
            if d.alive and d.province == pid and (side is None or d.side == side)
        ]

    def occupant_side(self, pid: str) -> str | None:
        for d in self.divisions.values():
            if d.alive and d.province == pid:
                return d.side
        return None

    def controlled(self, side: str) -> list[str]:
        return [pid for pid, p in self.provinces.items() if p.controller == side]

    def terrain_width(self, pid: str) -> float:
        return self.c.terrain[self.provinces[pid].terrain]["width"]

    def river_between(self, a: str, b: str) -> int:
        return self.provinces[a].neighbors.get(b, 0)

    # ---- supply and encirclement ------------------------------------------------------------

    def supply_network(self, side: str, control: dict[str, str] | None = None) -> dict[str, int]:
        """Provinces connected to one of `side`'s hubs through provinces it controls, with hop count."""
        ctrl = control or {pid: p.controller for pid, p in self.provinces.items()}
        dist: dict[str, int] = {}
        queue: deque[str] = deque()
        for pid, p in self.provinces.items():
            if p.hub == side and ctrl[pid] == side:
                dist[pid] = 0
                queue.append(pid)
        while queue:
            cur = queue.popleft()
            for nb in self.provinces[cur].neighbors:
                if nb not in dist and ctrl[nb] == side:
                    dist[nb] = dist[cur] + 1
                    queue.append(nb)
        return dist

    def _update_supply(self) -> None:
        self._supply_dirty = False
        self.supplied = {s: self.supply_network(s) for s in ("A", "B")}
        rng_hops = self.c.recovery("supply_range_hops")
        fall = self.c.recovery("supply_falloff_per_hop")
        newly: dict[str, list[Division]] = {"A": [], "B": []}
        for d in self.alive():
            hops = self.supplied[d.side].get(d.province)
            was = d.encircled
            if hops is None:
                d.encircled, d.supply = True, 0.0
            else:
                d.encircled = False
                d.supply = 1.0 if hops <= rng_hops else max(0.2, 1 - fall * (hops - rng_hops))
                d.encircled_hours = 0
            if d.encircled and not was:
                newly[d.side].append(d)
            if d.encircled:
                self.cut_off_ever[d.side].add(d.id)
        for side, divs in newly.items():
            self.pockets_closed[other(side)] += self._count_pockets(divs)

    def _count_pockets(self, divs: list[Division]) -> int:
        """Connected groups (by province adjacency) among newly cut-off divisions."""
        provs = {d.province for d in divs}
        seen: set[str] = set()
        groups = 0
        for start in sorted(provs):
            if start in seen:
                continue
            groups += 1
            stack = [start]
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(nb for nb in self.provinces[cur].neighbors if nb in provs and nb not in seen)
        return groups

    def would_be_cut_off(self, side: str, province: str, new_controller: str) -> list[Division]:
        """Divisions of `side` that lose supply if `province` changed hands (a hypothetical)."""
        ctrl = {pid: p.controller for pid, p in self.provinces.items()}
        ctrl[province] = new_controller
        net = self.supply_network(side, ctrl)
        return [d for d in self.alive(side) if d.province not in net and not d.encircled]

    def would_be_freed(self, side: str, province: str) -> list[Division]:
        """Currently encircled divisions of `side` that regain supply if it took `province`."""
        ctrl = {pid: p.controller for pid, p in self.provinces.items()}
        ctrl[province] = side
        net = self.supply_network(side, ctrl)
        return [d for d in self.alive(side) if d.encircled and d.province in net]

    # ---- orders ----------------------------------------------------------------------------

    def hop_hours(self, d: Division, src: str, dst: str, retreat: bool = False) -> int:
        speed = d.template.speed * (1 + (self.c.combat("retreat_speed_bonus") if retreat else 0.0))
        river = self.river_between(src, dst)
        if river == 1:
            speed *= 1 + self.c.combat("river_speed_small")
        elif river == 2:
            speed *= 1 + self.c.combat("river_speed_large")
        cost = self.c.terrain[self.provinces[dst].terrain]["movement_cost"]
        return max(1, math.ceil(self.c.movement("edge_length_km") * cost / speed))

    def find_path(self, d: Division, goal: str) -> list[str] | None:
        """Quickest route to `goal` that does not enter a province holding enemy units."""
        if goal == d.province:
            return []
        foe = other(d.side)
        if self.occupant_side(goal) == foe:
            return None
        best = {d.province: 0}
        prev: dict[str, str] = {}
        heap = [(0, d.province)]
        while heap:
            t, cur = heapq.heappop(heap)
            if cur == goal:
                break
            if t > best.get(cur, 10**9):
                continue
            for nb in sorted(self.provinces[cur].neighbors):
                if self.occupant_side(nb) == foe:
                    continue
                nt = t + self.hop_hours(d, cur, nb)
                if nt < best.get(nb, 10**9):
                    best[nb] = nt
                    prev[nb] = cur
                    heapq.heappush(heap, (nt, nb))
        if goal not in prev:
            return None
        path = [goal]
        while prev[path[-1]] != d.province:
            path.append(prev[path[-1]])
        return path[::-1]

    def apply_orders(self, orders: list[Order]) -> None:
        for o in orders:
            d = self.divisions.get(o.division)
            if d is None or not d.alive or not self._apply(d, o):
                self.illegal_orders += 1

    def _apply(self, d: Division, o: Order) -> bool:
        here = self.provinces[d.province]
        if o.kind == "hold":
            self._clear(d)
            return True
        if o.target not in self.provinces:
            return False
        if o.kind == "move":
            if d.path and d.path[-1] == o.target and not d.engaged:
                return True  # already on its way: keep the progress made on the current hop
            path = self.find_path(d, o.target)
            if path is None:
                return False
            self._leave_battle_cost(d)
            self._clear(d)
            d.path = path
            return True
        if o.kind == "attack":
            if o.target not in here.neighbors or self.occupant_side(o.target) != other(d.side):
                return False
            self._clear(d)
            d.attack_target = o.target
            return True
        if o.kind == "retreat":
            if o.target not in here.neighbors or self.occupant_side(o.target) == other(d.side):
                return False
            self._leave_battle_cost(d)
            self._clear(d)
            d.path = [o.target]
            return True
        return False

    @staticmethod
    def _clear(d: Division) -> None:
        d.path, d.attack_target, d.move_left = [], None, 0

    def _leave_battle_cost(self, d: Division) -> None:
        if d.engaged:
            d.org = max(0.0, d.org - self.c.combat("disengage_org_loss") * d.template.org)
            d.engaged = False

    # ---- the hour -----------------------------------------------------------------------------

    def tick(self) -> None:
        self.hour += 1
        for d in self.divisions.values():
            d.engaged = False
        self._fight()
        self._move()
        self._update_control()
        if self._supply_dirty:
            self._update_supply()
        self._recover()
        for s in ("A", "B"):
            self.territory_hours[s] += len(self.controlled(s))

    # battles

    def _modifiers(
        self, attackers: list[Division], defenders: list[Division], target: str, origins: dict[str, str],
        att_cap: float, def_cap: float,
    ) -> tuple[dict[str, float], dict[str, float]]:
        """Per-division attack and defense modifiers (additive, applied as 1 + sum)."""
        c = self.c
        terrain = self.provinces[target].terrain
        t_attack = c.terrain[terrain]["attack"]
        a_mod: dict[str, float] = {}
        d_mod: dict[str, float] = {}
        directions = len(set(origins.values()))
        for units, is_att in ((attackers, True), (defenders, False)):
            width = sum(u.template.width for u in units)
            cap = att_cap if is_att else def_cap
            over = max(0.0, (width - cap) / cap) if cap else 0.0
            over_pen = 0.0
            if over:
                over_pen = max(c.combat("over_width_penalty_max"), c.combat("over_width_penalty") * over)
            stack_pen = c.combat("stacking_penalty") * max(0, len(units) - c.combat("stacking_start"))
            for u in units:
                lack = 1 - u.supply
                am = dm = over_pen + stack_pen
                if is_att:
                    am += t_attack + c.combat("supply_lack_attacker_attack") * lack
                    dm += c.combat("supply_lack_attacker_defend") * lack
                    ut = c.unit_terrain.get(u.template.kind, {})
                    am += ut.get(terrain, 0.0)
                    river = self.river_between(origins[u.id], target)
                    if river:
                        am += c.combat("river_attack_large" if river == 2 else "river_attack_small")
                        am += ut.get("river", 0.0)
                else:
                    am += c.combat("supply_lack_defender_attack") * lack
                    dm += c.combat("supply_lack_defender_defend") * lack
                    if directions > 1:
                        dm += c.combat("multiple_directions_defense")
                if u.encircled:
                    am += c.combat("encircled_penalty")
                    dm += c.combat("encircled_penalty")
                a_mod[u.id], d_mod[u.id] = am, dm
        return a_mod, d_mod

    def _collect_battles(self) -> dict[str, list[Division]]:
        min_org = self.c.combat("min_org_to_attack")
        by_target: dict[str, list[Division]] = {}
        for d in sorted(self.alive(), key=lambda x: x.id):
            t = d.attack_target
            if t is None:
                continue
            occ = self.occupant_side(t)
            if occ != other(d.side):
                # the enemy is gone: walk in
                d.attack_target = None
                if occ is None:
                    d.path = [t]
                continue
            if d.org_frac < min_org:
                continue
            by_target.setdefault(t, []).append(d)
        return by_target

    def _fight(self) -> None:
        c = self.c
        battles = self._collect_battles()
        taken: set[str] = set()
        results = []
        for target in sorted(battles):
            attackers_all = [d for d in battles[target] if d.id not in taken]
            if not attackers_all:
                continue
            att_side = attackers_all[0].side
            def_side = other(att_side)
            occupants = sorted(
                (u for u in self.units_at(target, def_side) if u.id not in taken),
                key=lambda x: (-x.strength, x.id),
            )
            if not occupants:
                continue
            width = self.terrain_width(target)
            origins = {d.id: d.province for d in attackers_all}
            directions = len(set(origins.values()))
            att_cap = width * (1 + c.combat("extra_direction_width") * (directions - 1))
            attackers = self._fill(attackers_all, att_cap)
            defenders = self._fill(occupants, width)
            room = width - sum(u.template.width for u in defenders)
            if room > 0:
                joiners = []
                for nb in sorted(self.provinces[target].neighbors):
                    for u in self.units_at(nb, def_side):
                        if (
                            u.attack_target is None and not u.path and u.id not in taken
                            and u.org_frac >= c.combat("min_org_to_attack") and nb not in battles
                        ):
                            joiners.append(u)
                defenders += self._fill(joiners, room)
            for u in attackers + defenders:
                taken.add(u.id)
                u.engaged = True
            a_mod, d_mod = self._modifiers(attackers, defenders, target, origins, att_cap, width)
            on_def = shoot(attackers, defenders, a_mod, d_mod, True, self.rng, c)
            on_att = shoot(defenders, attackers, a_mod, d_mod, False, self.rng, c)
            results.append((target, attackers, defenders, on_def, on_att))
        active = {r[0] for r in results}
        for t in list(self.battle_age):
            if t not in active:
                del self.battle_age[t]
        for target, attackers, defenders, on_def, on_att in results:
            self.battle_age[target] = self.battle_age.get(target, 0) + 1
            for dmg, units in ((on_def, defenders), (on_att, attackers)):
                for u in units:
                    self._hurt(u, dmg.strength.get(u.id, 0.0), dmg.org.get(u.id, 0.0))
        for target, attackers, defenders, *_ in results:
            if self.battle_age[target] < c.combat("minimum_combat_hours"):
                continue
            for u in defenders:
                if u.alive and u.org <= 0:
                    self._retreat(u)
            for u in attackers:
                if u.alive and u.org <= 0:
                    u.attack_target = None  # the assault is broken off; it stays in its own province

    def _fill(self, units: list[Division], capacity: float) -> list[Division]:
        """Take divisions up to the combat width. Tired ones (below the rotation threshold) go to
        the back of the queue, so fresh divisions in reserve replace them."""
        chosen, used = [], 0.0
        floor = self.c.combat("rotation_org")
        for u in sorted(units, key=lambda x: x.org_frac < floor):
            if used >= capacity:
                break
            chosen.append(u)
            used += u.template.width
        return chosen

    def _hurt(self, u: Division, strength: float, org: float) -> None:
        if not u.alive:
            return
        self.strength_lost[u.side] += min(strength, max(u.strength, 0.0))
        u.strength -= strength
        u.org = max(0.0, u.org - org)
        if u.strength <= 0:
            self._destroy(u)

    def _destroy(self, u: Division) -> None:
        if not u.alive:
            return
        u.alive = False
        u.strength = 0.0
        self.destroyed[u.side] += 1
        self._supply_dirty = True

    def _retreat(self, u: Division) -> None:
        """Pull a broken defender back one province; with nowhere to go it is destroyed."""
        side = u.side
        options = []
        for nb in self.provinces[u.province].neighbors:
            if self.provinces[nb].controller != side or self.occupant_side(nb) == other(side):
                continue
            hops = self.supplied[side].get(nb)
            options.append((hops is None, hops if hops is not None else 99, nb))
        if not options:
            self.strength_lost[side] += max(u.strength, 0.0)
            self._destroy(u)
            return
        u.province = min(options)[2]
        self._clear(u)
        u.engaged = False
        self._supply_dirty = True

    # movement and control

    def _move(self) -> None:
        for d in sorted(self.alive(), key=lambda x: x.id):
            if d.engaged or not d.path:
                continue
            nxt = d.path[0]
            if nxt not in self.provinces[d.province].neighbors:
                self._clear(d)
                continue
            if self.occupant_side(nxt) == other(d.side):
                self._clear(d)
                d.attack_target = nxt  # walked into the enemy: it becomes an attack
                continue
            if d.move_left == 0:
                d.move_left = self.hop_hours(d, d.province, nxt)
            d.move_left -= 1
            if d.move_left > 0:
                continue
            d.province = nxt
            d.path.pop(0)
            d.move_left = 0
            self._supply_dirty = True

    def _update_control(self) -> None:
        for pid, p in self.provinces.items():
            sides = {d.side for d in self.units_at(pid)}
            if len(sides) != 1:
                continue
            side = next(iter(sides))
            if p.controller == side:
                continue
            p.controller = side
            self._supply_dirty = True
            for d in self.units_at(pid, side):
                if not d.engaged:
                    d.org = max(0.0, d.org - self.c.combat("org_loss_on_conquer") * d.template.org)

    def _recover(self) -> None:
        c = self.c
        for d in self.alive():
            if d.encircled:
                d.encircled_hours += 1
                if d.encircled_hours > c.combat("supply_grace_hours") and not d.engaged:
                    self._hurt(d, c.recovery("encircled_attrition_per_hour_fraction") * d.template.hp, 0.0)
                continue
            if d.engaged:
                continue
            gain = c.recovery("org_regain_per_hour_fraction") * d.template.org * d.supply
            d.org = min(d.template.org, d.org + gain)
            reinforce = c.recovery("str_reinforce_per_hour_fraction") * d.template.hp * d.supply
            d.strength = min(d.template.hp, d.strength + reinforce)
