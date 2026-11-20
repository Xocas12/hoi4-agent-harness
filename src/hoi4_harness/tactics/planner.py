"""The front planner: chooses battle plans, leaves the fighting to the engine (#85, stage 4).

A deterministic, non-LLM layer between grand strategy and execution. It does not give a
division an order. Each time it re-plans it reads a `Picture` (per-region counts by default,
the resolution the real game gives; see picture.py), decides *where* to attack, *where* to
hold and *what to keep back*, and emits `BattlePlan`s, which `EngineExecutor` then carries out
(plans.py), as the game's own logic would.

What it decides, in order:

1. **Pockets against us.** A frontier region whose loss would cut our divisions off from supply
   is garrisoned, and when the enemy next door is strong enough to take it the endangered
   divisions get a *fallback* plan to ground still in supply (pull back from a forming pocket).
2. **Where to attack** (when the goal is offensive). Every enemy region next to our force is
   scored as scripted.py scores provinces: objective value, enemy divisions its capture would
   cut off, own divisions it would free, times the force ratio after terrain and river
   (thin sectors score high). Pairs of targets are scored too: a *pincer* is a pair whose
   joint capture cuts off more than the two separately (two axes converging behind a salient).
   The better of the best single axis and the best pair is drawn; a drawn plan is kept for
   ``patience`` hours unless something scores 1.8x better.
3. **Exploiting a breakthrough.** When the region attacked is empty or all weak, the arrow is
   extended one region deeper, toward the objective, so the divisions do not stop at the line.
4. **Where to hold.** Remaining frontier regions share the remaining divisions half by threat
   (enemy power next door, discounted by the terrain and river it attacks into) and half
   evenly; a non-objective region that is outmatched, with better ground behind it (a river,
   hills), is given up for a fallback line there. Defensive goals counter-attack an enemy
   region whose divisions are all weak and that we outmatch.
5. **Reserve.** About a tenth of the force (a fifth on defence) is kept behind the hottest
   sector as a hold plan with aggressiveness 0; the sim's defenders pull in idle neighbours,
   which is the reserve at work.

It re-plans every ``cadence`` hours and at once on an event: a region changed hands, a
division of ours was lost, more of ours were cut off, or an axis was completed.

All of it tuned against the simulator; none of it is evidence about the real game.
"""

from __future__ import annotations

import math

from .controllers import OFFENSIVE_GOALS
from .env import View
from .picture import Picture, build_picture, reference_power
from .plans import BattlePlan, PlanController

MIN_ODDS = 0.9
PATIENCE = 96
MAX_AXES = 3
#: Attack modifier of a river between two regions, indexed by the river flag (none, small, large).
RIVER_PEN = (0.0, -0.3, -0.6)


class Planner:
    def __init__(self, observe: str = "aggregated", cadence: int = 24, patience: int = PATIENCE) -> None:
        self.observe = observe
        self.cadence = cadence
        self.patience = patience
        self._last_hour = -(10**9)
        self._last_control: dict[str, str] | None = None
        self._last_own = 0
        self._last_cut = 0
        self._commit: tuple[tuple[str, ...], int, float] | None = None
        self.last_reason = ""

    # ---- entry point -----------------------------------------------------------------------

    def update(self, view: View) -> list[BattlePlan] | None:
        pic = build_picture(view, self.observe)
        reason = self._event(pic)
        if reason is None and pic.hour - self._last_hour < self.cadence:
            return None
        return self._replan(view, pic, reason or "cadence")

    def replan(self, view: View) -> list[BattlePlan]:
        """Plan now, whatever the cadence (the real-game bridge asks once per decision)."""
        return self._replan(view, build_picture(view, self.observe), "asked")

    def _replan(self, view: View, pic: Picture, reason: str) -> list[BattlePlan]:
        self.last_reason = reason
        plans = self.plan(view, pic)
        self._last_hour = pic.hour
        self._last_control = pic.control()
        self._last_own = pic.own_total()
        self._last_cut = self._cut_now(pic)
        return plans

    def _cut_now(self, pic: Picture) -> int:
        net = pic.supply_net(pic.side)
        return sum(r.own for rid, r in pic.regions.items() if rid not in net)

    def _event(self, pic: Picture) -> str | None:
        if self._last_control is None:
            return "start"
        if pic.control() != self._last_control:
            return "control changed"
        if pic.own_total() < self._last_own:
            return "division lost"
        if self._cut_now(pic) > self._last_cut:
            return "pocket against us"
        if self._commit is not None:
            done = all(pic.regions[r].controller == pic.side and not pic.regions[r].foe
                       for r in self._commit[0])
            if done:
                return "axis completed"
        return None

    # ---- the plan ---------------------------------------------------------------------------

    def plan(self, view: View, pic: Picture) -> list[BattlePlan]:
        side = pic.side
        world = view.world
        offense = pic.goal["kind"] in OFFENSIVE_GOALS
        own_ids: dict[str, list[str]] = {}
        for d in sorted(world.alive(side), key=lambda x: x.id):
            own_ids.setdefault(pic.region_of[d.province], []).append(d.id)
        n_own = sum(len(v) for v in own_ids.values())
        if not n_own:
            return []
        specs: list[dict] = []   # {name, kind, anchor, front, offensive, aggr, want}
        used: set[str] = set()   # regions already assigned a plan role

        # 1. pockets against us
        fallbacks = self._pocket_defence(pic, specs, used)
        # 2-3. attack
        axes: list[str] = []
        axis_specs: list[dict] = []
        if offense:
            axes = self._choose_axes(pic)
            axis_specs = [self._axis_spec(pic, tgt, f"axis{i + 1}", extend=True)
                          for i, tgt in enumerate(axes)]
            specs.extend(axis_specs)
        else:
            self._commit = None
            specs.extend(self._counter_attacks(pic))
        # 4. holds
        specs.extend(self._holds(pic, used, axes, fallbacks, offense))
        # 5. reserve
        reserve = self._reserve_spec(pic, specs, n_own, offense)
        if reserve:
            specs.append(reserve)
        if axis_specs:  # the axes get what the holds and the reserve do not need
            rest = n_own - sum(s["want"] for s in specs if s not in axis_specs)
            for s in axis_specs:
                s["want"] = max(s["want"], rest // len(axis_specs))
        if not specs:
            specs.append(self._stage_spec(pic))
        return self._resolve(pic, world, own_ids, specs, n_own)

    # ---- attack -----------------------------------------------------------------------------

    def _odds(self, pic: Picture, tgt: str) -> tuple[float, float]:
        """(local power ratio of an attack on `tgt`, attacking power available next to it)."""
        r = pic.regions[tgt]
        srcs = [n for n in r.neighbors if pic.regions[n].own and pic.regions[n].controller == pic.side]
        if not srcs:
            return 0.0, 0.0
        div_width = 18.0
        cap = max(1.0, r.width / div_width * (1 + 0.5 * (len(srcs) - 1)))
        count = sum(pic.regions[s].own for s in srcs)
        raw = sum(pic.regions[s].own_power for s in srcs)
        capped = raw * min(1.0, cap / count)
        eff = capped + 0.4 * (raw - capped)
        river = min(pic.regions[s].neighbors[tgt] for s in srcs)
        pen = r.attack_mod
        if river:
            pen += -0.3 if river == 1 else -0.6
        support = sum(pic.regions[n].foe_power for n in r.neighbors if pic.regions[n].controller == pic.foe)
        foes = r.foe_power + 0.4 * support
        ratio = eff * max(0.2, 1 + pen) / max(foes, 1e-9)
        return min(ratio, 9.0), eff

    def _value(self, pic: Picture, flips: list[str]) -> float:
        goal = pic.goal
        cut, _ = pic.cut_off(pic.foe, dict.fromkeys(flips, pic.side))
        _, own_freed = pic.cut_off(pic.side, dict.fromkeys(flips, pic.side))
        value = 0.3 * len(flips) + 4.0 * sum(pic.regions[t].objective for t in flips)
        value += 2.0 * cut + 3.0 * own_freed
        if goal["kind"] == "encircle" and cut >= goal.get("min_cut_off", 99):
            value += 8.0
        return value

    def _choose_axes(self, pic: Picture) -> list[str]:
        cands = []
        for rid, r in sorted(pic.regions.items()):
            if r.controller != pic.foe and not r.foe:
                continue
            ratio, _ = self._odds(pic, rid)
            if r.foe == 0 and r.controller == pic.foe:
                near = any(pic.regions[n].own and pic.regions[n].controller == pic.side for n in r.neighbors)
                near = near or any(
                    pic.regions[m].own for n in r.neighbors for m in pic.regions[n].neighbors
                    if pic.regions[n].controller == pic.side
                )
                if not near:
                    continue
                ratio = max(ratio, 3.0)
            if ratio < MIN_ODDS:
                continue
            value = self._value(pic, [rid]) / (1.0 + 0.15 * self._march(pic, rid))
            cands.append((value * min(ratio, 3.0) * (1.0 if r.foe else 1.3), rid, ratio))
        cands.sort(key=lambda x: (-x[0], x[1]))
        if not cands:
            return []
        best_single = cands[0]
        best = ([best_single[1]], best_single[0])
        top = cands[:6]
        for i in range(len(top)):
            for j in range(i + 1, len(top)):
                a, b = top[i], top[j]
                cut_pair, _ = pic.cut_off(pic.foe, {a[1]: pic.side, b[1]: pic.side})
                cut_a, _ = pic.cut_off(pic.foe, {a[1]: pic.side})
                cut_b, _ = pic.cut_off(pic.foe, {b[1]: pic.side})
                if cut_pair <= cut_a + cut_b or cut_pair == 0:
                    continue   # not a pincer: the pair cuts off nothing that one axis would not
                score = self._value(pic, [a[1], b[1]]) * min(a[2], b[2], 3.0) * 0.85
                if score > best[1]:
                    best = ([a[1], b[1]], score)
        axes, score = best
        if len(axes) == 1 and pic.goal["kind"] == "capture":
            # a big force may push a second and third axis where the odds are good (6+ divisions each)
            for _, rid, ratio in cands[1:]:
                big_enough = pic.own_total() >= 6 * (len(axes) + 1)
                if len(axes) < MAX_AXES and ratio >= 1.3 and rid not in axes and big_enough:
                    axes.append(rid)
        # keep a drawn plan unless something is much better (as scripted.py does)
        if self._commit is not None:
            old, since, old_score = self._commit
            alive = [r for r in old if pic.regions[r].controller == pic.foe or pic.regions[r].foe]
            if alive and pic.hour - since < self.patience and score < 1.8 * max(old_score, 1e-9):
                return list(old) if len(alive) == len(old) else alive
        self._commit = (tuple(axes), pic.hour, score)
        return axes

    def _march(self, pic: Picture, tgt: str) -> float:
        """Mean number of region hops our divisions are from the sector facing `tgt`."""
        srcs = [n for n in pic.regions[tgt].neighbors if pic.regions[n].controller == pic.side] or [tgt]
        dist = pic.hops(srcs)
        n = sum(r.own for r in pic.regions.values())
        return sum(r.own * dist.get(rid, 9) for rid, r in pic.regions.items()) / max(n, 1)

    def _axis_spec(self, pic: Picture, tgt: str, name: str, extend: bool) -> dict:
        r = pic.regions[tgt]
        srcs = sorted(n for n in r.neighbors if pic.regions[n].controller == pic.side)
        offensive = list(r.provinces)
        arrow = [tgt]
        if extend and (r.foe == 0 or r.foe_weak == r.foe):
            nxt = self._next_target(pic, tgt)
            if nxt is not None:
                offensive += list(pic.regions[nxt].provinces)
                arrow.append(nxt)
        want = max(2, math.ceil(1.5 * r.foe_power / reference_power()))
        return {"name": name, "kind": "offensive", "anchor": srcs or [tgt], "regions": srcs,
                "target": tgt, "offensive": offensive, "aggr": 0.9, "want": want, "arrow": arrow}

    def _next_target(self, pic: Picture, tgt: str) -> str | None:
        goal = [rid for rid, r in pic.regions.items() if r.objective]
        hub = [rid for rid, r in pic.regions.items() if r.hub == pic.foe]
        dist = pic.hops(goal or hub) if (goal or hub) else {}
        options = [n for n in pic.regions[tgt].neighbors if pic.regions[n].controller == pic.foe]
        if not options:
            return None
        return min(options, key=lambda n: (dist.get(n, 99), pic.regions[n].foe_power, n))

    def _counter_attacks(self, pic: Picture) -> list[dict]:
        out = []
        for rid, r in sorted(pic.regions.items()):
            if not r.foe or r.foe_weak < r.foe:
                continue
            ratio, _ = self._odds(pic, rid)
            if ratio >= 1.2:
                spec = self._axis_spec(pic, rid, f"counter{len(out) + 1}", extend=False)
                spec["aggr"] = 0.8
                out.append(spec)
        return out

    # ---- pockets ----------------------------------------------------------------------------

    def _threat(self, pic: Picture, rid: str) -> float:
        r = pic.regions[rid]
        total = 0.0
        for n, river in r.neighbors.items():
            nb = pic.regions[n]
            if nb.foe:
                pen = r.attack_mod + (0.0 if not river else (-0.3 if river == 1 else -0.6))
                total += nb.foe_power * max(0.2, 1 + pen)
        return total

    def _pocket_defence(self, pic: Picture, specs: list[dict], used: set[str]) -> set[str]:
        """Garrison every frontier region whose loss would cut our divisions off, and pull back
        what a lost one would strand when the enemy next door can take it."""
        side, foe = pic.side, pic.foe
        out: set[str] = set()
        net_now = pic.supply_net(side)
        for rid in pic.frontier():
            r = pic.regions[rid]
            if not any(pic.regions[n].controller == foe or pic.regions[n].foe for n in r.neighbors):
                continue
            if pic.cut_off(side, {rid: foe})[0] <= 0:
                continue
            used.add(rid)
            if self._threat(pic, rid) < 0.7 * max(r.own_power, 1e-9) or r.objective:
                continue
            net_after = pic.supply_net(side, {**pic.control(), rid: foe})
            for rid2, r2 in sorted(pic.regions.items()):
                if r2.own and rid2 in net_now and rid2 not in net_after and rid2 != rid:
                    dest = self._nearest(
                        pic, rid2, lambda q, net=net_after: q in net and not pic.regions[q].foe)
                    if dest is not None:
                        specs.append(self._fallback_spec(pic, rid2, dest))
                        out.add(rid2)
        return out

    def _nearest(self, pic: Picture, start: str, ok) -> str | None:
        dist = pic.hops([start])
        options = sorted((d, rid) for rid, d in dist.items() if rid != start and ok(rid))
        return options[0][1] if options else None

    # ---- holds, reserve ----------------------------------------------------------------------

    def _hold_spec(self, pic: Picture, rid: str, name: str, want: int, kind: str = "defend") -> dict:
        return {"name": name, "kind": kind, "anchor": [rid], "front": list(pic.regions[rid].provinces),
                "offensive": [], "aggr": 0.0, "want": want, "regions": [rid]}

    def _fallback_spec(self, pic: Picture, src: str, dest: str) -> dict:
        spec = self._hold_spec(pic, dest, f"fallback-{src}", want=max(1, pic.regions[src].own),
                               kind="fallback")
        spec["from"] = src
        spec["anchor"] = [src]
        return spec

    def _holds(
        self, pic: Picture, guarded: set[str], axes: list[str], fallbacks: set[str], offense: bool
    ) -> list[dict]:
        side = pic.side
        axis_sources = {n for t in axes for n in pic.regions[t].neighbors
                        if pic.regions[n].controller == side}
        holders = [rid for rid in pic.frontier() if rid not in axis_sources and rid not in fallbacks]
        out = []
        if not holders:
            return out
        threat = {rid: self._threat(pic, rid) for rid in holders}
        if offense:
            # on the attack only threatened or must-keep sectors get a garrison, within a budget
            budget = max(len(guarded & set(holders)), int(0.35 * pic.own_total()))
            ranked = sorted(holders, key=lambda r: (r not in guarded, -threat[r], r))
            holders = [r for r in ranked if r in guarded or threat[r] > 0][:max(budget, 0)]
            if not holders:
                return out
        weight = {rid: max(threat[rid], 0.05) + (1.0 if pic.regions[rid].objective else 0.0)
                  for rid in holders}
        total = sum(weight.values())
        n = pic.own_total()
        share = 0.35 if offense else 1.0
        for rid in holders:
            r = pic.regions[rid]
            want = n * share * (0.5 * weight[rid] / total + 0.5 / len(holders))
            # give up bad ground that is outmatched when there is better ground right behind it
            if not offense and not r.objective and threat[rid] > 1.2 * r.own_power:
                behind = [q for q, river in r.neighbors.items()
                          if pic.regions[q].controller == side and q not in holders
                          and not pic.regions[q].foe
                          and pic.regions[q].attack_mod + RIVER_PEN[river] < r.attack_mod]
                if behind:
                    out.append(self._fallback_spec(pic, rid, sorted(behind)[0]))
                    continue
            if rid in guarded:
                want = max(want, 1)
            out.append(self._hold_spec(pic, rid, f"hold-{rid}", want=max(1, round(want))))
        return out

    def _reserve_spec(self, pic: Picture, specs: list[dict], n_own: int, offense: bool) -> dict | None:
        k = int(n_own * (0.1 if offense else 0.2))
        if n_own < 8 or k < 1:
            return None
        def heat(s: dict) -> tuple[float, str]:
            return (pic.regions[s["regions"][0]].foe_power if s.get("regions") else 0.0, s["name"])

        hot = max((s for s in specs if s["kind"] != "reserve"), default=None, key=heat)
        if hot is None or not hot.get("regions"):
            return None
        anchor = hot["regions"][0]
        rear = [q for q in pic.regions[anchor].neighbors
                if pic.regions[q].controller == pic.side and not any(
                    pic.regions[m].controller == pic.foe for m in pic.regions[q].neighbors)]
        if not rear:
            return None
        dest = sorted(rear)[0]
        spec = self._hold_spec(pic, dest, "reserve", want=k, kind="reserve")
        return spec

    def _stage_spec(self, pic: Picture) -> dict:
        front = pic.frontier()
        goal = [rid for rid, r in pic.regions.items() if r.objective]
        dist = pic.hops(goal) if goal else {}
        pick = min(front, key=lambda r: (dist.get(r, 99), r)) if front else next(iter(sorted(pic.regions)))
        return self._hold_spec(pic, pick, f"stage-{pick}", want=pic.own_total())

    # ---- turning specs into plans with named divisions -----------------------------------------

    def _resolve(self, pic: Picture, world, own_ids: dict[str, list[str]], specs: list[dict],
                 n_own: int) -> list[BattlePlan]:
        side = pic.side
        free = {rid: list(ids) for rid, ids in own_ids.items()}
        # axes first (they get the divisions standing next to the target), reserve last
        order = sorted(range(len(specs)), key=lambda i: ({"offensive": 0, "fallback": 1}.get(
            specs[i]["kind"], 2 if specs[i]["kind"] == "defend" else 3), i))
        # scale the wanted numbers so they add up to the force
        wants = {i: specs[i]["want"] for i in order}
        total = sum(wants.values())
        if total > n_own:
            for i in wants:
                wants[i] = max(1, int(wants[i] * n_own / total))
        plans: dict[int, BattlePlan] = {}
        for i in order:
            spec = specs[i]
            dist = pic.hops(spec["anchor"])
            take: list[str] = []
            pool = sorted(((dist.get(rid, 99), rid) for rid, ids in free.items() if ids))
            if spec["kind"] == "fallback":
                pool = [(d, rid) for d, rid in pool if rid == spec["from"]]
            for _, rid in pool:
                while free[rid] and len(take) < wants[i]:
                    take.append(free[rid].pop(0))
                if len(take) >= wants[i]:
                    break
            if not take:
                continue
            plans[i] = self._to_plan(pic, spec, take, side)
        # whoever is left joins the nearest plan that holds ground (one army per plan, none idle)
        leftovers = [(rid, d) for rid, ids in free.items() for d in ids]
        for rid, d in leftovers:
            best = None
            for i, plan in plans.items():
                if plan.kind == "reserve":
                    continue
                dist = pic.hops(specs[i]["anchor"]).get(rid, 99)
                if best is None or dist < best[0]:
                    best = (dist, i)
            if best is None and plans:
                best = (0, next(iter(plans)))
            if best is not None:
                p = plans[best[1]]
                plans[best[1]] = BattlePlan(p.name, p.side, (*p.divisions, d), p.front, p.offensive,
                                            p.aggressiveness, p.kind)
        return [plans[i] for i in sorted(plans)]

    def _to_plan(self, pic: Picture, spec: dict, ids: list[str], side: str) -> BattlePlan:
        if spec["kind"] == "offensive":
            # the line is the whole of the regions facing the arrow; the executor keeps the
            # provinces that actually touch the enemy
            front = [p for rid in spec["regions"] for p in pic.regions[rid].provinces]
            return BattlePlan(spec["name"], side, tuple(ids), tuple(front) or tuple(spec["offensive"]),
                              tuple(spec["offensive"]), spec["aggr"], "offensive")
        return BattlePlan(spec["name"], side, tuple(ids), tuple(spec["front"]), (), spec["aggr"],
                          spec["kind"])


def planner_controller(observe: str = "aggregated", **kw) -> PlanController:
    name = "planner" if observe == "aggregated" else "planner_full"
    return PlanController(Planner(observe, **kw), name=name)


class VanillaPlanner:
    """What the game's own AI does with plans, as the issue describes it (the "HOI4 AI" analogue).

    Every day: all divisions on one plan, the front line is the whole frontier, the arrow goes
    straight across at the enemy neighbour nearest its capital in any terrain. It never picks
    a sector by odds, never plans a pocket and never falls back or keeps a reserve. Offensive
    goals attack at any odds; on defence it attacks only with a clear local edge."""

    name = "vanilla_plans"

    def __init__(self, react_hours: int = 24) -> None:
        self.react_hours = react_hours
        self._last = -(10**9)

    def update(self, view: View) -> list[BattlePlan] | None:
        if view.hour - self._last < self.react_hours:
            return None
        self._last = view.hour
        w, foe = view.world, view.foe
        front = view.front_provinces()
        ids = tuple(sorted(d.id for d in view.own()))
        if not front or not ids:
            return []
        enemy_hub = next((p for p, pr in w.provinces.items() if pr.hub == foe), None)
        hops = view.hops_from(enemy_hub) if enemy_hub else {}
        arrow = []
        for p in front:
            targets = [nb for nb in sorted(w.provinces[p].neighbors)
                       if w.provinces[nb].controller == foe or w.occupant_side(nb) == foe]
            if targets:
                arrow.append(min(targets, key=lambda t: (hops.get(t, 99), t)))
        offense = view.goal["kind"] in OFFENSIVE_GOALS
        return [BattlePlan("main", view.side, ids, tuple(front), tuple(dict.fromkeys(arrow)),
                           1.0 if offense else 0.25, "offensive" if offense else "defend")]


def vanilla_plan_controller() -> PlanController:
    return PlanController(VanillaPlanner(), name="vanilla_plans")


__all__ = ["Planner", "VanillaPlanner", "planner_controller", "vanilla_plan_controller"]
