"""The environment a controller plays against: reset / observe / legal_actions / step."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from .model import Order
from .scenario import Scenario, load_scenario
from .sim import World, other


class Controller(Protocol):
    def act(self, view: View) -> list[Order]: ...


@dataclass
class StepResult:
    observation: dict[str, Any]
    reward: float
    done: bool
    info: dict[str, Any] = field(default_factory=dict)


class View:
    """What a scripted controller gets: one side's read-only window onto the world.

    The simulator is full-information (no fog of war); see docs/tactics.md for that gap."""

    def __init__(self, world: World, side: str) -> None:
        self.world = world
        self.side = side
        self.foe = other(side)

    @property
    def hour(self) -> int:
        return self.world.hour

    @property
    def goal(self) -> dict[str, Any]:
        return self.world.scenario.goals[self.side]

    def own(self) -> list:
        return self.world.alive(self.side)

    def enemies(self) -> list:
        return self.world.alive(self.foe)

    def neighbors(self, pid: str) -> list[str]:
        return sorted(self.world.provinces[pid].neighbors)

    def front_provinces(self) -> list[str]:
        return front_provinces(self.world, self.side)

    def hops_from(self, start: str) -> dict[str, int]:
        dist = {start: 0}
        queue = deque([start])
        while queue:
            cur = queue.popleft()
            for nb in self.world.provinces[cur].neighbors:
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    queue.append(nb)
        return dist

    def to_dict(self) -> dict[str, Any]:
        return observation(self.world, self.side)


def front_provinces(world: World, side: str) -> list[str]:
    """Provinces `side` controls that touch enemy-controlled ground or enemy divisions."""
    foe = other(side)
    out = []
    for pid, p in sorted(world.provinces.items()):
        if p.controller != side:
            continue
        if any(world.provinces[nb].controller == foe or world.occupant_side(nb) == foe for nb in p.neighbors):
            out.append(pid)
    return out


def assign_line(world: World, division_ids: list[str], provinces: list[str]) -> list[Order]:
    """Spread divisions over a set of provinces as evenly as possible, nearest first.

    The 'assign divisions to a line' helper: each division gets one move order."""
    if not provinces or not division_ids:
        return []
    cap = -(-len(division_ids) // len(provinces))
    load = dict.fromkeys(provinces, 0)
    orders = []
    for did in sorted(division_ids):
        d = world.divisions[did]
        hops = _hops(world, d.province)
        choices = sorted((p for p in provinces if load[p] < cap), key=lambda p: (hops.get(p, 99), load[p], p))
        pick = choices[0]
        load[pick] += 1
        orders.append(Order(did, "hold") if pick == d.province else Order(did, "move", pick))
    return orders


def _hops(world: World, start: str) -> dict[str, int]:
    dist = {start: 0}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nb in world.provinces[cur].neighbors:
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                queue.append(nb)
    return dist


def observation(world: World, side: str) -> dict[str, Any]:
    provinces = {}
    for pid, p in world.provinces.items():
        provinces[pid] = {
            "terrain": p.terrain,
            "controller": p.controller,
            "hub": p.hub,
            "width": world.terrain_width(pid),
            "neighbors": dict(p.neighbors),
            "divisions": [d.id for d in world.units_at(pid)],
        }
    divisions = {}
    for d in world.alive():
        order = None
        if d.attack_target:
            order = {"kind": "attack", "target": d.attack_target}
        elif d.path:
            order = {"kind": "move", "target": d.path[-1]}
        divisions[d.id] = {
            "side": d.side, "template": d.template.name, "province": d.province,
            "strength": round(d.strength, 3), "org": round(d.org, 3),
            "max_strength": d.template.hp, "max_org": d.template.org, "width": d.template.width,
            "engaged": d.engaged, "encircled": d.encircled, "supply": round(d.supply, 3), "order": order,
        }
    return {
        "hour": world.hour, "side": side, "goal": world.scenario.goals[side],
        "provinces": provinces, "divisions": divisions,
        "front": front_provinces(world, side),
        "battles": sorted(world.battle_age),
    }


class TacticalEnv:
    """reset(scenario, seed) -> observation; step(orders) -> StepResult. Deterministic per seed.

    `opponent` (a controller) plays the side that is not `scored_side`; without one the caller
    passes orders for both sides in the same list."""

    def __init__(self, scenario: str | Scenario | None = None, *, opponent: Controller | None = None) -> None:
        self.opponent = opponent
        self.world: World | None = None
        self.scenario: Scenario | None = None
        self._last = (0.0, 0.0, 0.0)
        if scenario is not None:
            self.reset(scenario)

    # ---- lifecycle ------------------------------------------------------------------------

    def reset(self, scenario: str | Scenario | None = None, seed: int = 0) -> dict[str, Any]:
        if scenario is not None:
            self.scenario = load_scenario(scenario) if isinstance(scenario, str) else scenario
        if self.scenario is None:
            raise ValueError("reset needs a scenario the first time")
        self.world = World(self.scenario, seed)
        self.side = self.scenario.scored_side
        self._last = self._progress()
        return self.observe()

    def _w(self) -> World:
        if self.world is None:
            raise RuntimeError("call reset() first")
        return self.world

    def view(self, side: str | None = None) -> View:
        return View(self._w(), side or self.side)

    def observe(self, side: str | None = None) -> dict[str, Any]:
        return observation(self._w(), side or self.side)

    # ---- actions --------------------------------------------------------------------------

    def legal_actions(self, side: str | None = None) -> dict[str, list[Order]]:
        w = self._w()
        side = side or self.side
        foe = other(side)
        acts: dict[str, list[Order]] = {}
        for d in w.alive(side):
            here = w.provinces[d.province]
            options = [Order(d.id, "hold")]
            for nb in sorted(here.neighbors):
                occ = w.occupant_side(nb)
                if occ == foe:
                    options.append(Order(d.id, "attack", nb))
                else:
                    options.append(Order(d.id, "move", nb))
                    if w.provinces[nb].controller == side:
                        options.append(Order(d.id, "retreat", nb))
            acts[d.id] = options
        return acts

    def step(self, orders: list[Order] | tuple[Order, ...] = (), hours: int | None = None) -> StepResult:
        w = self._w()
        orders = list(orders)
        if self.opponent is not None:
            orders = [
                o for o in orders if o.division in w.divisions and w.divisions[o.division].side == self.side
            ]
            orders += self.opponent.act(View(w, other(self.side)))
        w.apply_orders(orders)
        outcome = None
        for _ in range(hours or self.scenario.step_hours):
            w.tick()
            outcome = self.outcome()
            if outcome:
                break
        progress = self._progress()
        reward = (
            (progress[0] - self._last[0])
            - (progress[1] - self._last[1])
            + 0.2 * (progress[2] - self._last[2])
        )
        self._last = progress
        if outcome:
            reward += 1.0 if outcome == "win" else -1.0
        return StepResult(self.observe(), reward, outcome is not None, {"outcome": outcome, "hour": w.hour})

    @property
    def done(self) -> bool:
        return self.outcome() is not None

    # ---- scoring ----------------------------------------------------------------------------

    def _progress(self) -> tuple[float, float, float]:
        w = self._w()
        s, f = self.side, other(self.side)
        return (
            w.strength_lost[f] / max(w.initial_strength[s], 1.0),
            w.strength_lost[s] / max(w.initial_strength[s], 1.0),
            len(w.controlled(s)) / len(w.provinces),
        )

    def outcome(self, side: str | None = None) -> str | None:
        """'win', 'loss' or None (still running) for `side` under its goal."""
        w = self._w()
        side = side or self.side
        goal = w.scenario.goals[side]
        foe = other(side)
        at_limit = w.hour >= w.scenario.max_hours
        kind = goal["kind"]
        if kind == "hold":
            if any(w.provinces[p].controller != side for p in goal["provinces"]):
                return "loss"
            return "win" if at_limit else None
        if kind == "capture":
            if all(w.provinces[p].controller == side for p in goal["provinces"]):
                return "win"
            return "loss" if at_limit else None
        if kind == "encircle":
            if self.cut_off_count(foe) >= goal["min_cut_off"]:
                return "win"
            return "loss" if at_limit else None
        if kind == "escape":
            # the divisions that began cut off: enough of them alive and back in supply wins
            group = [w.divisions[i] for i in w.initially_cut_off if w.divisions[i].side == side]
            alive = [d for d in group if d.alive]
            if len(alive) < goal["min_alive"]:
                return "loss"
            if sum(1 for d in alive if not d.encircled) >= goal["min_alive"]:
                return "win"
            return "loss" if at_limit else None
        raise ValueError(f"unknown goal kind {kind!r}")

    def cut_off_count(self, side: str) -> int:
        """Divisions of `side` that are cut off right now, or were destroyed after being cut off."""
        w = self._w()
        now = sum(1 for d in w.alive(side) if d.encircled)
        dead = sum(1 for did in w.cut_off_ever[side] if not w.divisions[did].alive)
        return now + dead

    def metrics(self) -> dict[str, Any]:
        w = self._w()
        s, f = self.side, other(self.side)
        outcome = self.outcome()
        mine_lost = w.strength_lost[s]
        return {
            "scenario": self.scenario.name,
            "seed": w.seed,
            "hours": w.hour,
            "outcome": outcome or "undecided",
            "objective_met": outcome == "win",
            "territory_held": len(w.controlled(s)),
            "territory_total": len(w.provinces),
            "territory_avg": w.territory_hours[s] / max(w.hour, 1),
            "divisions_destroyed": w.destroyed[f],
            "divisions_lost": w.destroyed[s],
            "strength_lost_own": round(mine_lost, 2),
            "strength_lost_enemy": round(w.strength_lost[f], 2),
            "casualty_ratio": round(w.strength_lost[f] / max(mine_lost, 1.0), 3),
            "pockets_closed": w.pockets_closed[s],
            "pockets_suffered": w.pockets_closed[f],
            "illegal_orders": w.illegal_orders,
        }
