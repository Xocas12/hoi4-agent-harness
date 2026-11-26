"""A toy front simulator for offline practice (#85). It is a TOY.

Why it exists: the real data is one monthly save per decision, a handful of campaigns, and no way to
ask "what if the other order had been given". A simulator can be asked, which lets the whole loop
(explore, log propensities, join outcomes, estimate off-policy, learn) be run end to end on a problem
where the answer is known. It says nothing about how Hearts of Iron IV plays; do not read a result
here as a result about the game.

What it models: a line of ``n`` sectors (a graph: neighbours share a flank effect). Each sector has our
divisions and theirs, a terrain with a defence bonus, our organisation and supply, their organisation,
and a breakthrough meter. Every day, under the order standing on the sector:

* attacker strength = own divisions x effort x organisation factor x supply factor (a push needs
  supply far more than a probe does) x an armour bonus on open ground;
* defender strength = their divisions x terrain bonus x their organisation factor;
* the share ``s = A / (A + D)`` sets both sides' casualties and the breakthrough meter, which fills
  when ``s`` is above 0.45 and drains when it is below; a full meter takes a province (``gained``)
  and lowers the neighbours' defenders' organisation; the enemy's own pressure takes provinces back
  (``lost``) only where we hold some;
* orders: ``push`` (effort 1.0), ``attack`` (0.6), ``probe`` (0.25), ``hold`` (0, organisation
  recovers, defence bonus), ``reserve`` (0, recovers fast: the rotation order).

It runs in 30 day turns, matching the monthly save the real controller sees, and writes what the real
pipeline would have: an ``Observation`` per turn and a ``front_history.json``-shaped dict (provinces
gained and lost per state, closed battles), so the dataset extractor runs on it unchanged.

Calibration, such as it is (``calibrate()`` returns it; a test pins it): the real run in
docs/results/overnight-2026-11-22.md had the foe at about 3 to 1 overall and attacks going in as 3
divisions against 8 or 6 against 7, which the front report calls piecemeal (attacker under half the
defender's divisions), with the attacker taking the ground in 14 to 41 per cent of the battles seen.
Here a push at under half the defender's numbers takes ground in a minority of turns and at 2 to 1 or
better in most. Casualty and meter rates were set by hand to those orders of magnitude, not fitted.
Terrain, armour and the neighbour effect are in the dynamics but terrain is not in the controller's
features: it is a hidden cause, like most of what matters in the game.
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .dataset import sector_reward
from .features import sector_features
from .interface import MicroOrder, Observation, SectorState

TURN_DAYS = 30
ENEMY = "ENE"
TERRAIN = {"plains": 1.0, "forest": 1.25, "hills": 1.4, "mountains": 1.8, "urban": 1.5}
EFFORT = {"push": 1.0, "attack": 0.6, "probe": 0.25, "hold": 0.0, "reserve": 0.0}
#: A sector's rear state, where the rotation (reserve) order for it goes.
REAR_BASE = 10000


@dataclass
class Sector:
    state: int
    own: float
    foe: float
    terrain: str
    org: float = 0.8
    supply: float = 0.8
    foe_org: float = 0.8
    armour: float = 0.0
    progress: float = 0.0
    counter: float = 0.0
    held: int = 0                      # provinces of this state we have taken and still hold
    own0: float = 0.0
    foe0: float = 0.0
    gained_last: int = 0
    lost_last: int = 0
    unchanged: int | None = None
    luck: float = 0.0                  # this turn's weather for the sector, drawn once per turn

    def __post_init__(self) -> None:
        self.own0 = self.own0 or self.own
        self.foe0 = self.foe0 or self.foe


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class FrontSim:
    def __init__(self, n_sectors: int = 6, seed: int = 0, start: str = "1940-01-01"):
        self.n = n_sectors
        self.seed = seed
        self.rng = random.Random(f"sim|{seed}")
        y, m, d = (int(x) for x in start.split("-"))
        self.day = date(y, m, d)
        self.turn = 0
        self.sectors = [self._draw_sector(i) for i in range(n_sectors)]
        self.history: dict = {"version": 1, "snapshots": [], "closed": [], "open": {}, "since": {}}
        self._snapshot(True, {}, {})

    def _draw_sector(self, i: int) -> Sector:
        rng = self.rng
        own = rng.randint(4, 30)
        ratio = min(4.0, max(0.25, rng.lognormvariate(0.0, 0.7)))      # own / foe, median 1
        foe = max(2, round(own / ratio))
        return Sector(state=100 + i, own=float(own), foe=float(foe), terrain=rng.choice(list(TERRAIN)),
                      org=rng.uniform(0.45, 1.0), supply=rng.uniform(0.35, 1.0),
                      foe_org=rng.uniform(0.6, 1.0), armour=rng.choice([0.0, 0.0, 0.15, 0.3]),
                      progress=rng.uniform(0.0, 0.4))

    def copy(self) -> FrontSim:
        """An independent copy to run a counterfactual turn on (the history is not carried over)."""
        twin = copy.copy(self)
        twin.sectors = [copy.copy(s) for s in self.sectors]
        twin.rng = random.Random()
        twin.rng.setstate(self.rng.getstate())
        twin.history = {"version": 1, "snapshots": [], "closed": [], "open": {}, "since": {}}
        return twin

    # -- what the controller sees ----------------------------------------------------------------

    def date_string(self) -> str:
        return f"{self.day.year}-{self.day.month:02d}-{self.day.day:02d}"

    def observation(self, current: dict[int, str] | None = None) -> Observation:
        secs = []
        for s in self.sectors:
            own = round(s.own)
            secs.append(SectorState(
                state=s.state, enemy=ENEMY, own=own, foe=round(s.foe),
                own_low_org=round(own * _clip((0.7 - s.org) / 0.5)),
                own_short_supply=round(own * _clip((0.9 - s.supply) / 0.6)),
                own_armoured=round(own * s.armour), org_average=100 * s.org, terrain=s.terrain,
                gained=s.gained_last, lost=s.lost_last, unchanged_days=s.unchanged,
                rear_state=REAR_BASE + s.state))
        return Observation(self.date_string(), secs, dict(current or {}))

    # -- dynamics --------------------------------------------------------------------------------

    def step(self, orders: list[MicroOrder], rng: random.Random | None = None) -> dict[int, dict]:
        """Advance one turn. An order names a sector's state, or a reserve order its rear state; a
        sector with none holds. Returns, per sector state, the counters ``dataset.sector_reward``
        reads: gained, lost, battles_won, battles_lost, and the change in our divisions."""
        rng = rng or self.rng
        by_sector = {s.state: "hold" for s in self.sectors}
        for o in orders:
            sid = o.state - REAR_BASE if o.state >= REAR_BASE else o.state
            if sid in by_sector:
                by_sector[sid] = o.order
        before = {s.state: s.own for s in self.sectors}
        start_date = self.date_string()
        start_forces = {s.state: (round(s.own), round(s.foe)) for s in self.sectors}
        gained = {s.state: 0 for s in self.sectors}
        lost = {s.state: 0 for s in self.sectors}
        for s in self.sectors:
            s.luck = rng.gauss(0.0, 0.09)
        for _ in range(TURN_DAYS):
            flank = {s.state: 0.0 for s in self.sectors}
            for idx, s in enumerate(self.sectors):
                before_g = gained[s.state]
                self._day(s, by_sector[s.state], rng, gained, lost)
                if gained[s.state] > before_g:           # a breakthrough outflanks the neighbours
                    for j in (idx - 1, idx + 1):
                        if 0 <= j < self.n:
                            flank[self.sectors[j].state] += 0.1
            for s in self.sectors:
                s.foe_org = _clip(s.foe_org - flank[s.state])
        self.day += timedelta(days=TURN_DAYS)
        self.turn += 1
        end_date = self.date_string()
        result: dict[int, dict] = {}
        for s in self.sectors:
            g, lo = gained[s.state], lost[s.state]
            s.gained_last, s.lost_last = g, lo
            if g or lo:
                s.unchanged = 0
            elif s.unchanged is not None:
                s.unchanged += TURN_DAYS
            attacking = EFFORT[by_sector[s.state]] > 0
            won = lostb = 0
            if attacking:
                won, lostb = (1, 0) if g else (0, 1)
            if lo:
                lostb += 1
            if attacking or lo:
                att, dfn = start_forces[s.state]
                self.history["closed"].append({
                    "key": f"{s.state}:{self.turn}", "front": ENEMY, "province": s.state, "state": s.state,
                    "terrain": s.terrain, "own_side": "attacker" if attacking else "defender",
                    "attacker": ["SIM"] if attacking else [ENEMY],
                    "defender": [ENEMY] if attacking else ["SIM"],
                    "att_div": att if attacking else dfn, "def_div": dfn if attacking else att,
                    "first": start_date, "last": end_date, "ended": end_date,
                    "outcome": ("attacker" if g else "defender") if attacking else "attacker"})
            result[s.state] = {"gained": g, "lost": lo, "battles_won": won, "battles_lost": lostb,
                               "own_change": s.own - before[s.state]}
        self._snapshot(False, gained, lost)
        return result

    def _day(self, s: Sector, order: str, rng: random.Random, gained: dict, lost: dict) -> None:
        e = EFFORT[order]
        qo = 0.4 + 0.6 * s.org
        qs = 1.0 - (1.0 - s.supply) * (0.2 + 0.8 * e)
        arm = 1.0 + s.armour * (1.0 if s.terrain == "plains" else 0.3)
        a = s.own * e * qo * qs * arm
        d = s.foe * TERRAIN[s.terrain] * (0.5 + 0.5 * s.foe_org)
        share = a / (a + d) if a > 0 else 0.0
        if e > 0:
            share = _clip(share + s.luck + rng.gauss(0.0, 0.05))
        # the enemy presses on us in every sector
        b = s.foe * 0.3 * (0.5 + 0.5 * s.foe_org)
        dd = s.own * qo * (1.3 if e == 0 else 1.0)
        pressure = b / (b + dd) if b + dd > 0 else 0.0
        if e > 0:
            s.foe -= s.foe * 0.020 * e * share
            s.own -= s.own * (0.014 * e * (1 - share) + 0.002 * pressure)
            s.foe_org = _clip(s.foe_org - 0.012 * e * share + 0.004)
            s.org = _clip(s.org - (0.004 + 0.018 * e * (0.4 + (1 - share))), 0.05, 1.0)
            if share > 0.35:
                s.progress += 0.08 * (share - 0.35) * (1 + e)
            else:
                s.progress = max(0.0, s.progress - 0.015)
        else:
            s.own -= s.own * 0.002 * pressure
            s.org = _clip(s.org + (0.03 if order == "reserve" else 0.012), 0.05, 1.0)
            s.foe_org = _clip(s.foe_org + 0.004)
            s.progress = max(0.0, s.progress - 0.005)
        if pressure > 0.4:
            s.counter += 0.05 * (pressure - 0.4) * (1.5 if e > 0 else 0.6)
        if s.progress >= 1.0:
            s.progress = 0.3
            s.held += 1
            gained[s.state] += 1
            s.foe = max(1.0, s.foe - 0.6)
            s.foe_org = _clip(s.foe_org - 0.1)
        if s.counter >= 1.0:
            s.counter = 0.3
            if s.held > 0:
                s.held -= 1
                lost[s.state] += 1
        s.own += (s.own0 - s.own) * 0.006                       # reinforcements
        s.foe += (s.foe0 - s.foe) * 0.010

    def _snapshot(self, first: bool, gained: dict, lost: dict) -> None:
        g, lo = sum(gained.values()), sum(lost.values())
        self.history["snapshots"].append({"date": self.date_string(), "fronts": {ENEMY: {
            "gained": g, "lost": lo, "baseline": first,
            "gained_states": sum(1 for v in gained.values() if v),
            "lost_states": sum(1 for v in lost.values() if v),
            "gained_by_state": {str(k): v for k, v in gained.items() if v},
            "lost_by_state": {str(k): v for k, v in lost.items() if v}}}})


class FeaturePolicyController:
    """Every sector gets ``policy(features)``: a per-sector policy as a controller (it can be wrapped in
    ``ExploringController``). The rule controller orders only its one main-effort sector per front and
    holds the rest; this one is what ``evaluate`` and ``ope`` mean by a policy."""

    max_orders = 1000

    def __init__(self, policy, name: str = "per-sector"):
        self.policy, self.name = policy, name

    def plan(self, intents, obs: Observation) -> dict[int, MicroOrder]:
        return {s.state: MicroOrder(s.state, self.policy(sector_features(s)), self.name) for s in obs.sectors}

    def decide(self, intents, obs: Observation) -> list[MicroOrder]:
        return [o for o in self.plan(intents, obs).values() if obs.current.get(o.state) != o.order]


# -- running policies in it ------------------------------------------------------------------------


def run_episode(controller, seed: int, turns: int = 12, n_sectors: int = 6, run_dir=None,
                keep_states: list | None = None) -> list[dict]:
    """Run ``controller`` for ``turns`` turns. Returns one dict per sector decision with the true
    reward (``dataset.sector_reward`` of what the sector did that turn). With ``run_dir`` it also writes
    micro_decisions.jsonl and front_history.json as a real run would, which ``dataset.extract_run``
    then reads. ``keep_states`` collects a copy of the sim before each turn."""
    sim = FrontSim(n_sectors, seed)
    log: list[dict] = []
    lines: list[str] = []
    standing: dict[int, str] = {}
    for _ in range(turns):
        obs = sim.observation(standing)
        if keep_states is not None:
            keep_states.append(sim.copy())
        orders = controller.decide([], obs)
        decisions = getattr(controller, "decisions", None)
        if decisions is None:
            decisions = [{"state": o.state, "order": o.order, "is_sector": o.state < REAR_BASE,
                          "applied": True} for o in orders]
        for o in orders:
            standing[o.state] = o.order
        turn = sim.turn
        outcome = sim.step(orders, random.Random(f"{seed}|turn|{turn}"))
        for d in decisions:
            if not d.get("applied"):
                continue
            sec = obs.sector(d["state"]) or next((x for x in obs.sectors if x.rear_state == d["state"]), None)
            if sec is None:
                continue
            res = outcome[sec.state]
            if d.get("is_sector", True):
                log.append({"sector": sec.state, "order": d["order"], "turn": turn,
                            "reward": sector_reward(res, res["own_change"])})
            rec = {"date": obs.date, "state": d["state"], "order": d["order"], "reason": "",
                   "controller": controller.name, "enemy": ENEMY, "sector_state": sec.state,
                   "own": sec.own, "foe": sec.foe, "org_average": sec.org_average,
                   "features": sector_features(sec)}
            rec.update({k: v for k, v in d.items() if k not in ("state", "order", "applied", "enemy")})
            lines.append(json.dumps(rec, sort_keys=True))
    if run_dir is not None:
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "micro_decisions.jsonl").write_text("".join(x + "\n" for x in lines), encoding="utf-8")
        (run_dir / "front_history.json").write_text(json.dumps(sim.history), encoding="utf-8")
    return log


def policy_value(controller, seeds, turns: int = 12, n_sectors: int = 6) -> float:
    """True mean reward per sector decision when ``controller`` plays whole episodes (on-policy)."""
    rewards = [r["reward"] for sd in seeds for r in run_episode(controller, sd, turns, n_sectors)]
    return sum(rewards) / len(rewards)


def one_step_value(policy, states: list[FrontSim], samples: int = 6) -> float:
    """The value off-policy evaluation estimates: on each logged state, give one sector ``policy``'s
    order (read from its features; the others hold) and average the true one-turn sector reward over
    ``samples`` weather draws. ``policy`` is features -> order."""
    total, n = 0.0, 0
    for st in states:
        for sec in st.observation().sectors:
            order = policy(sector_features(sec))
            for k in range(samples):
                res = st.copy().step([MicroOrder(sec.state, order)],
                                     random.Random(f"ov|{st.turn}|{sec.state}|{k}"))
                total += sector_reward(res[sec.state], res[sec.state]["own_change"])
                n += 1
    return total / max(n, 1)


def calibrate(n: int = 300, seed: int = 1) -> dict:
    """Share of one-turn pushes on plains that took ground, by force ratio: the piecemeal case (our
    divisions under half theirs), an even one, and the favourable one (2 to 1 or better)."""
    rng = random.Random(seed)
    buckets = {"piecemeal": [0, 0], "even": [0, 0], "favourable": [0, 0]}
    for i in range(n):
        sim = FrontSim(1, seed=1000 + i)
        s = sim.sectors[0]
        s.terrain, s.supply, s.org, s.foe_org, s.armour = "plains", 0.8, 0.85, 0.85, 0.0
        ratio = rng.choice([0.25, 0.35, 0.45, 0.8, 1.0, 1.2, 2.0, 2.5, 3.0])
        s.own = s.own0 = 16.0
        s.foe = s.foe0 = float(round(16.0 / ratio))
        res = sim.step([MicroOrder(s.state, "push")], random.Random(i))
        key = "piecemeal" if ratio < 0.5 else "even" if ratio < 2 else "favourable"
        buckets[key][0] += 1 if res[s.state]["gained"] else 0
        buckets[key][1] += 1
    return {k: (v[0] / v[1] if v[1] else None) for k, v in buckets.items()}
