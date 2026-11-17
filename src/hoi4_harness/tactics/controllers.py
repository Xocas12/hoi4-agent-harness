"""Scripted controllers: the weak one imitates HOI4's AI habits, the strong one plays the map.

Both take a View (one side's window onto the world) and return Orders. Neither reads the other
side's orders; both can see the whole board, because the simulator has no fog of war.
"""

from __future__ import annotations

from .env import View, assign_line
from .model import Division, Order
from .sim import World, other

OFFENSIVE_GOALS = ("capture", "encircle", "escape")


def power(d: Division) -> float:
    """A rough fighting value: strength and organisation times attack, defense and armour."""
    t = d.template
    return d.strength_frac * (0.5 + 0.5 * d.org_frac) * (t.soft_attack + t.hard_attack + 0.5 * t.defense) * (
        1 + t.armor / 60
    )


def _reachable(w: World, d: Division, goal: str) -> bool:
    return d.province == goal or w.find_path(d, goal) is not None


def _enemy_neighbors(w: World, pid: str, foe: str) -> list[str]:
    return [nb for nb in sorted(w.provinces[pid].neighbors) if w.occupant_side(nb) == foe]


class VanillaLikeController:
    """Imitates the habits the issue blames the game's AI for.

    * Re-plans once a day (`react_hours`), so it is late to everything.
    * Spreads its divisions evenly along the front, whatever the enemy is doing.
    * Attacks straight across the front at the enemy's nearest-to-capital neighbour, in any
      terrain, with whoever is standing there (offence) or only with a clear local edge (defence).
    * Never plans an encirclement or a withdrawal: a cut-off division just keeps attacking out.
    """

    name = "vanilla"

    def __init__(self, react_hours: int = 24, defensive_edge: float = 1.5) -> None:
        self.react_hours = react_hours
        self.defensive_edge = defensive_edge
        self._last = -(10**9)

    def act(self, view: View) -> list[Order]:
        if view.hour - self._last < self.react_hours:
            return []
        self._last = view.hour
        w, foe = view.world, view.foe
        offense = view.goal["kind"] in OFFENSIVE_GOALS
        front = view.front_provinces()
        free = [d for d in view.own() if not d.engaged and not d.encircled]
        orders: list[Order] = []
        stay: dict[str, str] = {}
        for o in assign_line(w, [d.id for d in free], front):
            d = w.divisions[o.division]
            if o.kind == "move" and not _reachable(w, d, o.target):
                o = Order(d.id, "hold")
            orders.append(o)
            stay[d.id] = o.target if o.kind == "move" else d.province
        enemy_hub = next((p for p, pr in w.provinces.items() if pr.hub == foe), None)
        hops = view.hops_from(enemy_hub) if enemy_hub else {}
        for d in view.own():
            if stay.get(d.id, d.province) != d.province and d.id in stay:
                continue  # still walking to its slot
            targets = _enemy_neighbors(w, d.province, foe)
            if targets and d.org_frac >= 0.8 or (targets and d.encircled):
                t = min(targets, key=lambda p: (hops.get(p, 99), p))
                if offense or d.encircled or self._local_edge(w, d, t) >= self.defensive_edge:
                    orders = [o for o in orders if o.division != d.id]
                    orders.append(Order(d.id, "attack", t))
                    continue
            if offense and not targets:
                empties = [
                    nb for nb in sorted(w.provinces[d.province].neighbors)
                    if w.provinces[nb].controller == foe and w.occupant_side(nb) is None
                ]
                if empties:
                    t = min(empties, key=lambda p: (hops.get(p, 99), p))
                    orders = [o for o in orders if o.division != d.id]
                    orders.append(Order(d.id, "move", t))
        return orders

    @staticmethod
    def _local_edge(w: World, d: Division, target: str) -> float:
        mine = sum(power(u) for u in w.units_at(d.province, d.side))
        theirs = sum(power(u) for u in w.units_at(target)) or 1e-9
        return mine / theirs


class ScriptedController:
    """The stronger baseline: it reads the map instead of the front.

    Offence: score every enemy province by what taking it achieves (the objective, enemy
    divisions it would cut off, own divisions it would free) against the odds there after
    terrain and rivers, pour the nearest divisions into the best one up to its combat width,
    and send fast units round into empty enemy ground that cuts supply.
    Defence: weight divisions to threat (enemy power next door, discounted by the terrain the
    enemy has to attack into), keep reserves behind the hottest province so they join the
    battle, give up non-objective ground in bad terrain, and counter-attack broken enemies.
    Both: before an enemy can take a province whose loss would cut divisions off, pull those
    divisions back to supply.
    """

    name = "scripted"

    def __init__(self, min_odds: float = 0.9, pocket_margin: float = 0.7, patience: int = 96) -> None:
        self.min_odds = min_odds
        self.pocket_margin = pocket_margin
        self.patience = patience  # hours a plan is kept before a better one may replace it freely
        self._plan: tuple[str, int, float] | None = None  # (target, started, score)

    # ---- entry point -----------------------------------------------------------------------

    def act(self, view: View) -> list[Order]:
        w = view.world
        orders: dict[str, Order] = {}
        busy: set[str] = set()
        self._avoid_pockets(view, orders, busy)
        if view.goal["kind"] in OFFENSIVE_GOALS:
            self._offense(view, orders, busy)
        else:
            self._defense(view, orders, busy)
        return [o for o in orders.values() if w.divisions[o.division].alive]

    # ---- shared pieces ------------------------------------------------------------------------

    def _terrain_pen(self, w: World, pid: str, src: str | None = None) -> float:
        """Attack modifier for someone attacking into `pid` (from `src`, which adds any river)."""
        pen = w.c.terrain[w.provinces[pid].terrain]["attack"]
        river = w.river_between(src, pid) if src else 0
        if river:
            pen += w.c.combat("river_attack_large" if river == 2 else "river_attack_small")
        return pen

    def _avoid_pockets(self, view: View, orders: dict[str, Order], busy: set[str]) -> None:
        """Keep every choke on our supply line garrisoned, and pull back what a lost choke would cut off."""
        w, side, foe = view.world, view.side, view.foe
        for v in sorted(view.front_provinces()):
            adj = _enemy_neighbors(w, v, foe)
            if not adj:
                continue
            cut = [d for d in w.would_be_cut_off(side, v, foe) if d.province != v]
            if not cut:
                continue
            here = w.units_at(v, side)
            if here:
                keep = max(here, key=lambda d: (d.org_frac, d.id))
                if keep.attack_target or keep.path:
                    orders[keep.id] = Order(keep.id, "hold")
                busy.add(keep.id)
            else:
                spare = [d for d in view.own() if d.id not in busy and not d.engaged and _reachable(w, d, v)]
                if spare:
                    d = min(spare, key=lambda x: (len(w.find_path(x, v) or ()), x.id))
                    orders[d.id] = Order(d.id, "move", v)
                    busy.add(d.id)
            threat = sum(power(u) for nb in adj for u in w.units_at(nb, foe))
            guard = sum(power(u) for u in here) or 1e-9
            if threat * (1 + self._terrain_pen(w, v)) < self.pocket_margin * guard:
                continue
            ctrl = {pid: p.controller for pid, p in w.provinces.items()}
            ctrl[v] = foe
            net = w.supply_network(side, ctrl)
            for d in cut:
                if d.engaged or d.id in busy:
                    continue
                dest = self._nearest(
                    w, d, lambda pid, net=net, v=v: pid in net and pid != v and w.occupant_side(pid) != foe
                )
                if dest and dest != d.province:
                    orders[d.id] = Order(d.id, "move", dest)
                    busy.add(d.id)

    @staticmethod
    def _nearest(w: World, d: Division, ok) -> str | None:
        best = None
        for pid in sorted(w.provinces):
            if not ok(pid) or pid == d.province:
                continue
            path = w.find_path(d, pid)
            if path is None:
                continue
            cost = sum(w.hop_hours(d, a, b) for a, b in zip([d.province, *path[:-1]], path, strict=True))
            if best is None or cost < best[0]:
                best = (cost, pid)
        return best[1] if best else None

    @staticmethod
    def _lead(d: Division) -> tuple:
        """Armour goes to the front of an assault line: it out-classes infantry piercing."""
        return (d.template.kind != "armor", -power(d), d.id)

    # ---- offence ----------------------------------------------------------------------------

    def _lineup(self, w: World, tgt: str, pool: list[Division]) -> tuple[list[Division], float]:
        """Who can attack `tgt` from `pool` (up to its combat width) and the odds they would have."""
        adj = [
            d for d in pool
            if tgt in w.provinces[d.province].neighbors and (d.org_frac >= 0.5 or d.attack_target == tgt)
        ]
        if not adj:
            return [], 0.0
        foe = other(adj[0].side)
        extra = w.c.combat("extra_direction_width") * (len({d.province for d in adj}) - 1)
        cap = w.terrain_width(tgt) * (1 + extra)
        line, used = [], 0.0
        for d in sorted(adj, key=self._lead):
            if used >= cap:
                break
            line.append(d)
            used += d.template.width
        pen = min(self._terrain_pen(w, tgt, d.province) for d in line)
        ours = sum(power(d) for d in line) * max(0.2, 1 + pen)
        foes = sum(power(u) for u in w.units_at(tgt, foe))
        support = sum(power(u) for nb in w.provinces[tgt].neighbors for u in w.units_at(nb, foe))
        return line, ours / max(foes + 0.4 * support, 1e-9)

    def _offense(self, view: View, orders: dict[str, Order], busy: set[str]) -> None:
        w, side, foe = view.world, view.side, view.foe
        goal = view.goal
        objective = set(goal.get("provinces", ()))
        usable = [d for d in view.own() if d.id not in busy]
        if not usable:
            return
        scored = []  # (score, target, occupied)
        for tgt, p in sorted(w.provinces.items()):
            if p.controller != foe and w.occupant_side(tgt) != foe:
                continue
            occupied = w.occupant_side(tgt) == foe
            cut = len([d for d in w.would_be_cut_off(foe, tgt, side) if d.province != tgt])
            freed = len(w.would_be_freed(side, tgt))
            value = 0.3 + 4.0 * (tgt in objective) + 2.0 * cut + 3.0 * freed
            if goal["kind"] == "encircle" and cut >= goal["min_cut_off"]:
                value += 8.0  # this one finishes the job
            if occupied:
                line, odds = self._lineup(w, tgt, usable)
                if not line or odds < self.min_odds:
                    continue
                scored.append((value * min(odds, 3.0), tgt, True))
            else:
                if value < 1.0 or not any(len(w.find_path(d, tgt) or [0] * 9) <= 2 for d in usable):
                    continue
                scored.append((value * 2.0, tgt, False))
        scored.sort(key=lambda x: (-x[0], x[1]))
        best = scored[0] if scored else None
        plan = self._plan
        if plan is not None:
            ptgt, since, pscore = plan
            alive = w.provinces[ptgt].controller == foe or w.occupant_side(ptgt) == foe
            if alive and view.hour - since < self.patience and (best is None or best[0] < 1.8 * pscore):
                best = (pscore, ptgt, w.occupant_side(ptgt) == foe)
            else:
                self._plan = None
        if best is None:
            self._stage(view, orders, busy, objective)
            return
        if self._plan is None or self._plan[0] != best[1]:
            self._plan = (best[1], view.hour, best[0])
        # the primary axis gets the best divisions; whoever is left may push a second axis with good odds
        axes = [best] + [x for x in scored if x[1] != best[1]]
        pool = list(usable)
        for _, tgt, occupied in axes:
            if occupied:
                line, odds = self._lineup(w, tgt, pool)
                if not line or (tgt != best[1] and odds < 1.2 * self.min_odds):
                    continue
                for d in line:
                    orders[d.id] = Order(d.id, "attack", tgt)
                    busy.add(d.id)
                pool = [d for d in pool if d.id not in busy]
                if tgt == best[1]:
                    stage = sorted(
                        nb for nb in w.provinces[tgt].neighbors
                        if w.provinces[nb].controller == side and w.occupant_side(nb) != foe
                    )
                    for d in pool:
                        if d.engaged or not stage:
                            continue
                        dest = min(stage, key=lambda p: (len(w.find_path(d, p) or [None] * 99), p))
                        if dest != d.province and _reachable(w, d, dest):
                            orders[d.id] = Order(d.id, "move", dest)
                            busy.add(d.id)
                    pool = [d for d in pool if d.id not in busy]
            elif tgt == best[1]:
                runners = sorted(
                    (d for d in pool if not d.engaged and len(w.find_path(d, tgt) or [0] * 9) <= 2),
                    key=lambda d: (-d.template.speed, -power(d), d.id),
                )
                for d in runners[:2]:
                    orders[d.id] = Order(d.id, "move", tgt)
                    busy.add(d.id)
                pool = [d for d in pool if d.id not in busy]
        self._stage(view, orders, busy, objective)

    def _stage(self, view: View, orders: dict[str, Order], busy: set[str], objective: set[str]) -> None:
        """No good attack yet: gather idle divisions on the front province nearest the objective."""
        w, foe = view.world, view.foe
        front = view.front_provinces()
        if not front:
            return
        enemy_hub = next((p for p, pr in w.provinces.items() if pr.hub == foe), None)
        anchor = next(iter(sorted(objective)), None) or enemy_hub
        hops = view.hops_from(anchor) if anchor else {}
        stage = min(front, key=lambda p: (hops.get(p, 99), p))
        for d in view.own():
            if d.id in busy or d.engaged or d.province == stage:
                continue
            if _reachable(w, d, stage):
                orders[d.id] = Order(d.id, "move", stage)
                busy.add(d.id)

    # ---- defence ----------------------------------------------------------------------------

    def _defense(self, view: View, orders: dict[str, Order], busy: set[str]) -> None:
        w, side, foe = view.world, view.side, view.foe
        goal = set(view.goal.get("provinces", ()))
        front = view.front_provinces()
        if not front:
            return
        threat = {}
        for p in front:
            threat[p] = sum(
                power(u) * max(0.2, 1 + self._terrain_pen(w, p, nb))
                for nb in _enemy_neighbors(w, p, foe) for u in w.units_at(nb, foe)
            )
        # counter-attack enemy divisions that are broken
        for p in front:
            for t in _enemy_neighbors(w, p, foe):
                foes = w.units_at(t, foe)
                if not foes or sum(u.org_frac for u in foes) / len(foes) > 0.35:
                    continue
                mine = [d for d in w.units_at(p, side) if d.id not in busy and d.org_frac >= 0.7]
                if sum(power(d) for d in mine) >= 1.2 * sum(power(u) for u in foes):
                    for d in mine:
                        orders[d.id] = Order(d.id, "attack", t)
                        busy.add(d.id)
        # give up non-objective ground in bad terrain when outmatched; fight on the better ground behind
        for p in front:
            if p in goal or p not in threat:
                continue
            here = [d for d in w.units_at(p, side) if d.id not in busy]
            if not here or threat[p] <= 1.2 * sum(power(d) for d in here):
                continue
            behind = [
                q for q in sorted(w.provinces[p].neighbors)
                if w.provinces[q].controller == side and q not in front and w.occupant_side(q) != foe
                and self._terrain_pen(w, q) < self._terrain_pen(w, p)
            ]
            if behind:
                for d in here:
                    orders[d.id] = Order(d.id, "retreat", behind[0])
                    busy.add(d.id)
        # allocate the rest to the threatened provinces
        def where(d: Division) -> str:
            return d.path[-1] if d.path else d.province

        holders = [p for p in front if p in goal or threat[p] > 0] or front
        weight = {p: max(threat[p], 0.05) + (1.0 if p in goal else 0.0) for p in holders}
        n = len(view.own())
        # half by threat, half evenly: a line that must hold everywhere cannot starve a quiet province
        want = {p: n * (0.5 * weight[p] / sum(weight.values()) + 0.5 / len(holders)) for p in holders}
        have = dict.fromkeys(holders, 0)
        for d in view.own():
            if where(d) in have:
                have[where(d)] += 1
        pool = []
        for d in sorted((x for x in view.own() if x.id not in busy and not x.engaged), key=lambda x: x.id):
            loc = where(d)
            if loc in have and have[loc] <= round(want[loc] + 0.01):
                continue  # its slot is fine; leave it (a fresh order would restart its march)
            if loc in have:
                have[loc] -= 1
            pool.append(d)
        hottest = max(holders, key=lambda p: (threat[p], p))
        rear = [
            q for q in sorted(w.provinces[hottest].neighbors)
            if w.provinces[q].controller == side and w.occupant_side(q) != foe
        ]
        rear.sort(key=lambda q: (q in front, q))
        for d in pool:
            gaps = sorted(holders, key=lambda p: (have[p] - want[p], p))
            dest = None
            for p in gaps:
                if have[p] < want[p] - 0.25 and _reachable(w, d, p):
                    dest = p
                    break
            if dest is None and rear:
                dest = rear[0]
            if dest is None:
                continue
            if dest in have:
                have[dest] += 1
            if dest == d.province:
                orders.setdefault(d.id, Order(d.id, "hold"))
            elif _reachable(w, d, dest):
                orders[d.id] = Order(d.id, "move", dest)


CONTROLLERS = {"vanilla": VanillaLikeController, "scripted": ScriptedController}
