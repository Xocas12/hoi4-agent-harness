"""A learnable controller: score every legal order with a small parametric policy (#85, stage 3).

For each division the controller lists candidate orders (hold, attack or move to a neighbour,
retreat, move to a few far provinces worth heading for), turns each into a feature vector, scores
it with a linear model (one weight block per order kind) and plays the best. No randomness:
the same parameters on the same game give the same orders. Pure Python, no dependencies.
"""

from __future__ import annotations

import json
import math
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .controllers import power
from .env import View
from .model import DATA_DIR, Division, Order
from .sim import World

PARAMS_PATH = DATA_DIR / "learned_params.json"
KINDS = ("hold", "move", "attack", "retreat")
GOALS = ("hold", "capture", "encircle", "escape")
BASE_FEATURES = (
    "bias", "log_odds", "terrain_attack", "terrain_cost", "river", "org", "strength", "supply",
    "q_is_goal", "q_foe_controlled", "q_foe_empty", "q_anchor_dist", "move_cost", "threat_ratio",
    "foe_cut_off", "own_freed", "own_cut_risk", "width_use", "over_width", "load", "is_current",
    "engaged", "encircled", "q_supply", "q_unsupplied", "q_front", "armor", "goal_hold",
    "goal_capture", "goal_encircle", "goal_escape",
)
N_BASE = len(BASE_FEATURES)
FEATURE_NAMES = tuple(f"{k}.{f}" for k in KINDS for f in BASE_FEATURES)
N_FEATURES = len(FEATURE_NAMES)


@dataclass
class Candidate:
    order: Order
    dest: str
    feats: list[float] = field(default_factory=list)
    is_current: bool = False


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def _hops(w: World, start: str) -> dict[str, int]:
    dist = {start: 0}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nb in w.provinces[cur].neighbors:
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                queue.append(nb)
    return dist


def current_intent(d: Division) -> tuple[str, str | None]:
    if d.attack_target:
        return ("attack", d.attack_target)
    if d.path:
        return ("move", d.path[-1])
    return ("hold", None)


class Context:
    """Everything that is the same for every division this turn."""

    def __init__(self, view: View) -> None:
        w, side, foe = view.world, view.side, view.foe
        self.w, self.side, self.foe, self.view = w, side, foe, view
        self.kind_goal = view.goal["kind"]
        self.own = view.own()
        self.occ = {pid: w.occupant_side(pid) for pid in w.provinces}
        self.pow = {"A": Counter(), "B": Counter()}
        self.width_at: Counter = Counter()
        for d in w.alive():
            self.pow[d.side][d.province] += power(d)
            if d.side == side:
                self.width_at[d.province] += d.template.width
        self.net = w.supply_network(side)
        self.front = set(view.front_provinces())
        goal = set(view.goal.get("provinces", ()))
        hubs_foe = {p for p, pr in w.provinces.items() if pr.hub == foe}
        hubs_own = {p for p, pr in w.provinces.items() if pr.hub == side}
        self.goal_provs = goal
        anchors = goal or (hubs_foe if self.kind_goal == "encircle" else hubs_own)
        self.anchor_dist = self._multi_hops(anchors)
        self.hubs = hubs_foe | hubs_own
        self._cut_foe: dict[str, float] = {}
        self._freed: dict[str, float] = {}
        self._risk: dict[str, float] = {}
        self.dest_of = {d.id: self._dest(d) for d in self.own}
        self.load = Counter(self.dest_of.values())

    def _multi_hops(self, starts: set[str]) -> dict[str, int]:
        dist = {s: 0 for s in starts}
        queue = deque(starts)
        while queue:
            cur = queue.popleft()
            for nb in self.w.provinces[cur].neighbors:
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    queue.append(nb)
        return dist

    @staticmethod
    def _dest(d: Division) -> str:
        kind, tgt = current_intent(d)
        return d.province if tgt is None else tgt

    def cut_foe(self, q: str) -> float:
        if q not in self._cut_foe:
            lost = self.w.would_be_cut_off(self.foe, q, self.side)
            self._cut_foe[q] = float(len([x for x in lost if x.province != q]))
        return self._cut_foe[q]

    def freed(self, q: str) -> float:
        if q not in self._freed:
            self._freed[q] = float(len(self.w.would_be_freed(self.side, q)))
        return self._freed[q]

    def risk(self, q: str) -> float:
        if q not in self._risk:
            lost = self.w.would_be_cut_off(self.side, q, self.foe)
            self._risk[q] = float(len([x for x in lost if x.province != q]))
        return self._risk[q]


def candidates(ctx: Context, d: Division) -> list[Candidate]:
    w, side, foe = ctx.w, ctx.side, ctx.foe
    here = w.provinces[d.province]
    cur_kind, cur_tgt = current_intent(d)
    cands: list[Candidate] = [Candidate(Order(d.id, "hold"), d.province, is_current=cur_kind == "hold")]
    seen_move = {d.province}
    for nb in sorted(here.neighbors):
        if ctx.occ[nb] == foe:
            now = (cur_kind, cur_tgt) == ("attack", nb)
            cands.append(Candidate(Order(d.id, "attack", nb), nb, is_current=now))
        else:
            now = (cur_kind, cur_tgt) == ("move", nb)
            cands.append(Candidate(Order(d.id, "move", nb), nb, is_current=now))
            seen_move.add(nb)
            if w.provinces[nb].controller == side:
                cands.append(Candidate(Order(d.id, "retreat", nb), nb))
    hops = _hops(w, d.province)
    far = set(ctx.goal_provs) | ctx.front | ctx.hubs
    near_enemy = sorted(
        (p for p, pr in w.provinces.items() if pr.controller == foe and ctx.occ[p] != foe),
        key=lambda p: (hops.get(p, 99), p),
    )
    far.update(near_enemy[:3])
    if cur_kind == "move" and cur_tgt:
        far.add(cur_tgt)
    for p in sorted(far):
        if p in seen_move or ctx.occ[p] == foe or p not in hops:
            continue
        seen_move.add(p)
        cands.append(Candidate(Order(d.id, "move", p), p, is_current=(cur_kind, cur_tgt) == ("move", p)))
    for c in cands:
        c.feats = featurize(ctx, d, c, hops)
    return cands


def featurize(ctx: Context, d: Division, c: Candidate, hops: dict[str, int]) -> list[float]:
    w, side, foe = ctx.w, ctx.side, ctx.foe
    q, kind = c.dest, c.order.kind
    prov = w.provinces[q]
    pen = w.c.terrain[prov.terrain]
    river = w.river_between(d.province, q) if q in w.provinces[d.province].neighbors else 0
    mine_q = ctx.pow[side][q] + (power(d) if q != d.province else 0.0)
    theirs_q = ctx.pow[foe][q]
    threat = sum(ctx.pow[foe][nb] for nb in prov.neighbors)
    if kind == "attack":
        odds = math.log((power(d) + 1e-3) / (theirs_q + 1e-3))
    else:
        pressure = threat + 0.3 * theirs_q
        odds = math.log((mine_q + 1e-3) / (pressure + 1e-3)) if pressure > 0 else 3.0
    width_after = ctx.width_at[q] + (d.template.width if q != d.province else 0.0)
    tw = max(w.terrain_width(q), 1.0)
    sup = ctx.net.get(q)
    foe_ctrl = prov.controller == foe
    base = [
        1.0,
        _clip(odds, -3.0, 3.0),
        pen["attack"] * 4,
        pen["movement_cost"] / 2,
        float(river),
        d.org_frac,
        d.strength_frac,
        d.supply,
        1.0 if q in ctx.goal_provs else 0.0,
        1.0 if foe_ctrl else 0.0,
        1.0 if foe_ctrl and ctx.occ[q] is None else 0.0,
        min(ctx.anchor_dist.get(q, 9), 9) / 5.0,
        min(hops.get(q, 9), 9) / 3.0,
        _clip(threat / (mine_q + 0.5), 0.0, 4.0) / 2.0,
        (ctx.cut_foe(q) / 3.0) if foe_ctrl else 0.0,
        (ctx.freed(q) / 3.0) if foe_ctrl else 0.0,
        (ctx.risk(q) / 3.0) if (prov.controller == side and q in ctx.front) else 0.0,
        _clip(width_after / tw, 0.0, 2.0),
        1.0 if width_after > tw * 1.3 else 0.0,
        min(ctx.load[q], 4) / 2.0,
        1.0 if c.is_current else 0.0,
        1.0 if d.engaged else 0.0,
        1.0 if d.encircled else 0.0,
        (min(sup, 6) / 3.0) if sup is not None else 0.0,
        1.0 if sup is None else 0.0,
        1.0 if q in ctx.front else 0.0,
        1.0 if d.template.kind == "armor" else 0.0,
        *(1.0 if ctx.kind_goal == g else 0.0 for g in GOALS),
    ]
    out = [0.0] * N_FEATURES
    off = KINDS.index(kind) * N_BASE
    out[off:off + N_BASE] = base
    return out


class Policy:
    """Linear scores over the order features. `weights` has N_FEATURES entries."""

    def __init__(self, weights: list[float] | None = None, meta: dict[str, Any] | None = None) -> None:
        self.weights = list(weights) if weights is not None else [0.0] * N_FEATURES
        if len(self.weights) != N_FEATURES:
            raise ValueError(f"expected {N_FEATURES} weights, got {len(self.weights)}")
        self.meta = meta or {}

    def score(self, feats: list[float]) -> float:
        w = self.weights
        return sum(w[i] * x for i, x in enumerate(feats) if x)

    def to_json(self) -> dict[str, Any]:
        return {"version": 1, "features": list(FEATURE_NAMES), "weights": [round(x, 6) for x in self.weights],
                "meta": self.meta}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Policy:
        if list(raw.get("features", FEATURE_NAMES)) != list(FEATURE_NAMES):
            raise ValueError("parameter file was trained for a different feature set; retrain")
        return cls(raw["weights"], raw.get("meta"))

    def save(self, path: str | Path = PARAMS_PATH) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=1) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path = PARAMS_PATH) -> Policy:
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


class LearnedController:
    """Plays the orders its policy scores highest. Deterministic given the policy and the game."""

    name = "learned"

    def __init__(self, policy: Policy | None = None) -> None:
        self.policy = policy if policy is not None else default_policy()

    def act(self, view: View) -> list[Order]:
        ctx = Context(view)
        w = ctx.w
        orders: list[Order] = []
        for d in sorted(ctx.own, key=lambda x: x.id):
            cands = candidates(ctx, d)
            ranked = sorted(range(len(cands)), key=lambda i: (-self.policy.score(cands[i].feats), i))
            for i in ranked:
                c = cands[i]
                if c.is_current:
                    pick = None
                    break
                if c.order.kind == "move" and w.find_path(d, c.dest) is None:
                    continue
                pick = c
                break
            else:
                pick = None
            if pick is None:
                continue
            ctx.load[ctx.dest_of[d.id]] -= 1
            ctx.dest_of[d.id] = pick.dest
            ctx.load[pick.dest] += 1
            orders.append(pick.order)
        return orders


_DEFAULT: Policy | None = None


def default_policy() -> Policy:
    """The committed parameters (data/learned_params.json); all zeros if the file is missing."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Policy.load() if PARAMS_PATH.exists() else Policy()
    return _DEFAULT
