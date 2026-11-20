"""Battle plans and the engine executor that carries them out (#85, stage 4).

HOI4 does not take a division order from the player when a front is planned: the player
draws a front line and an offensive arrow, assigns an army to them, and the game's own
logic spreads the divisions along the line and attacks across it. A plan is therefore the
unit of control here, one level above `Order`:

    BattlePlan(name, side, divisions, front, offensive, aggressiveness, kind)

* ``front``     own-side provinces to hold (the drawn front line);
* ``offensive`` enemy provinces to attack (the drawn arrow), possibly empty;
* ``aggressiveness`` 0..1, how readily the assigned divisions commit to an attack.

`EngineExecutor` turns the plans into the sim's per-division orders, every step, the way
the game is believed to. **Every rule below is an assumption**, written down as such: the
game's plan execution is engine code that no file in the install describes, and nothing
here was checked against the running game.

Assumptions about the engine's execution (A1-A8):

A1. Divisions assigned to a plan stand on that plan's front line, spread evenly over its
    provinces (even by count, nearest province first), and a division already on a front
    province, or already marching to one that has room, keeps it.
A2. Where an offensive arrow touches the front, the share of divisions on those provinces
    is doubled: the line is weighted toward the attack.
A3. A division attacks across the line only when it stands next to an enemy province of the
    arrow, has enough organisation, and (below aggressiveness 1) sees a local power ratio
    at or above ``min_odds(aggressiveness)``. Aggressiveness 0 never attacks.
A4. An enemy province on the arrow with no division in it is walked into, not fought for.
A5. A province of the arrow that the side captures joins the line: divisions advance to it
    when it touches the enemy and re-form there (the line "moves up with the arrow").
A6. Divisions in combat keep fighting; the executor does not pull them out. Retreat on zero
    organisation is the sim's, as for any order.
A7. Divisions not assigned to any plan get no order at all (they stand where they are).
A8. The executor never chooses where to attack or hold, never plans a pocket and never
    withdraws: it executes what was drawn. That layer is the planner's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .controllers import power
from .env import View
from .model import Division, Order
from .sim import World, other

PLAN_KINDS = ("defend", "offensive", "fallback", "reserve")


@dataclass(frozen=True)
class BattlePlan:
    """One army, one front line, optionally one offensive arrow."""

    name: str
    side: str
    divisions: tuple[str, ...]
    front: tuple[str, ...]
    offensive: tuple[str, ...] = ()
    aggressiveness: float = 0.5
    kind: str = "defend"

    def __post_init__(self) -> None:
        if self.kind not in PLAN_KINDS:
            raise ValueError(f"unknown plan kind {self.kind!r}; expected one of {PLAN_KINDS}")
        if not 0.0 <= self.aggressiveness <= 1.0:
            raise ValueError("aggressiveness is between 0 and 1")
        if self.offensive and not self.front:
            raise ValueError(f"plan {self.name}: an offensive needs a front line to start from")

    def to_dict(self) -> dict:
        return {
            "name": self.name, "side": self.side, "kind": self.kind,
            "divisions": list(self.divisions), "front": list(self.front),
            "offensive": list(self.offensive), "aggressiveness": self.aggressiveness,
        }


def min_odds(aggressiveness: float) -> float | None:
    """The local power ratio below which an assigned division will not attack (A3).

    None means it never attacks. 1.0 attacks at any ratio; 0.25 needs 1.5; 0.5 needs 1.0."""
    if aggressiveness <= 0.0:
        return None
    return 2.0 * (1.0 - aggressiveness)


def min_org_to_commit(aggressiveness: float) -> float:
    return 0.3 + 0.5 * (1.0 - aggressiveness)


def _hops(world: World, start: str) -> dict[str, int]:
    from .env import _hops as hops

    return hops(world, start)


def _enemy_touch(world: World, pid: str, side: str) -> bool:
    foe = other(side)
    return any(
        world.provinces[nb].controller == foe or world.occupant_side(nb) == foe
        for nb in world.provinces[pid].neighbors
    )


def _live(world: World, pid: str, side: str) -> bool:
    foe = other(side)
    return world.provinces[pid].controller == foe or world.occupant_side(pid) == foe


class EngineExecutor:
    """Carries out battle plans for one side. `set_plans` replaces them, `orders` is asked each step."""

    def __init__(self) -> None:
        self.plans: list[BattlePlan] = []

    def set_plans(self, plans: list[BattlePlan]) -> None:
        taken: set[str] = set()
        kept = []
        for plan in plans:  # a division serves one plan: the first that names it
            ids = tuple(d for d in plan.divisions if d not in taken)
            taken.update(ids)
            kept.append(plan if ids == plan.divisions else BattlePlan(
                plan.name, plan.side, ids, plan.front, plan.offensive, plan.aggressiveness, plan.kind))
        self.plans = kept

    def assigned(self) -> set[str]:
        return {d for p in self.plans for d in p.divisions}

    # ---- one step -----------------------------------------------------------------------

    def orders(self, world: World) -> list[Order]:
        out: list[Order] = []
        for plan in self.plans:
            out.extend(self._execute(world, plan))
        return out

    def _execute(self, world: World, plan: BattlePlan) -> list[Order]:
        side, foe = plan.side, other(plan.side)
        members = [world.divisions[i] for i in plan.divisions if i in world.divisions
                   and world.divisions[i].alive and world.divisions[i].side == side]
        if not members:
            return []
        live = [t for t in plan.offensive if t in world.provinces and _live(world, t, side)]
        won = [t for t in plan.offensive if t in world.provinces and world.provinces[t].controller == side]
        line = [p for p in dict.fromkeys([*plan.front, *won])
                if p in world.provinces and world.provinces[p].controller == side]
        front_eff = [p for p in line if _enemy_touch(world, p, side)] or line
        orders: dict[str, Order] = {}
        # A3 / A4: attack or walk in across the arrow
        ratio_floor = min_odds(plan.aggressiveness)
        if live and ratio_floor is not None:
            for d in sorted(members, key=lambda x: x.id):
                adj = [t for t in live if t in world.provinces[d.province].neighbors]
                if not adj:
                    continue
                empty = [t for t in adj if world.occupant_side(t) != foe]
                if empty and world.provinces[empty[0]].controller == foe:
                    orders[d.id] = Order(d.id, "move", empty[0])
                    continue
                held = [t for t in adj if world.occupant_side(t) == foe]
                if not held or d.org_frac < min_org_to_commit(plan.aggressiveness):
                    continue
                tgt = min(held, key=lambda t: (sum(power(u) for u in world.units_at(t, foe)), t))
                mine = sum(power(u) for u in world.units_at(d.province, side))
                theirs = sum(power(u) for u in world.units_at(tgt, foe)) or 1e-9
                if mine / theirs >= ratio_floor:
                    orders[d.id] = Order(d.id, "attack", tgt)
        # A1 / A2 / A5: spread everyone else along the line
        rest = [d for d in members if d.id not in orders and not d.engaged]
        orders.update(self._spread(world, rest, front_eff, live))
        return list(orders.values())

    @staticmethod
    def _spread(world: World, divs: list[Division], slots: list[str], live: list[str]) -> dict[str, Order]:
        if not divs or not slots:
            return {}
        arrow_adj = {p for p in slots if any(t in world.provinces[p].neighbors for t in live)}
        weight = {p: 2.0 if p in arrow_adj else 1.0 for p in slots}
        total = sum(weight.values())
        n = len(divs)
        quota = {p: n * weight[p] / total for p in slots}
        cap = {p: int(quota[p]) for p in slots}
        spare = n - sum(cap.values())
        for p in sorted(slots, key=lambda s: (-(quota[s] - cap[s]), s))[:spare]:
            cap[p] += 1
        load = dict.fromkeys(slots, 0)
        orders: dict[str, Order] = {}
        pending = []
        for d in sorted(divs, key=lambda x: x.id):
            slot = d.path[-1] if d.path else d.province
            if slot in load and load[slot] < cap[slot]:
                load[slot] += 1
                orders[d.id] = Order(d.id, "hold") if slot == d.province else Order(d.id, "move", slot)
            else:
                pending.append(d)
        for d in pending:
            hops = _hops(world, d.province)
            free = [p for p in slots if load[p] < cap[p]] or slots
            for p in sorted(free, key=lambda s: (hops.get(s, 99), load[s], s)):
                if p == d.province:
                    orders[d.id] = Order(d.id, "hold")
                    load[p] += 1
                    break
                if world.find_path(d, p) is not None:
                    orders[d.id] = Order(d.id, "move", p)
                    load[p] += 1
                    break
        return orders


@dataclass
class PlanController:
    """A planner plus the engine executor: a controller like any other (`act(view) -> orders`)."""

    planner: object
    name: str = "plans"
    executor: EngineExecutor = field(default_factory=EngineExecutor)
    history: list[tuple[int, list[BattlePlan]]] = field(default_factory=list)

    def act(self, view: View) -> list[Order]:
        plans = self.planner.update(view)
        if plans is not None:
            self.executor.set_plans(plans)
            self.history.append((view.hour, plans))
        return self.executor.orders(view.world)


def current_plans(controller: PlanController) -> list[BattlePlan]:
    return list(controller.executor.plans)
