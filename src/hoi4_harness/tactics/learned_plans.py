"""A learned plan chooser: a scorer over candidate attack axes, fed only the aggregated picture (#85).

`Planner` (planner.py) decides *where to attack* by a hand-written score and leaves the rest
(holds, fallbacks, reserve, the executor) to fixed rules. This module replaces that one decision
with a linear scorer. Everything else stays the planner's, so the comparison to `planner` is
the scorer against the heuristic and nothing more.

**Candidates.** Each re-plan on an offensive goal, the candidate set is built from the aggregated
picture alone: the empty plan, the best few single enemy regions, pairs and a triple of the best
ones (pincers are pairs whose joint capture cuts off more than the two apart), and whatever the
hand-written aggregated planner would have drawn (so the scorer can start from it).

**Features** (`FEATURE_NAMES`) are computed from a `Picture` with ``observe == "aggregated"`` and
a `History` of earlier pictures of the same game, and from nothing else: region counts, weak
counts, static map (terrain, width, rivers, hubs, objectives, how many provinces and neighbours
a region has), the planner's own value and odds estimates, and what changed since the last
re-plan (the enemy count in a region and around it, and the peak it once held). The deltas are
the part meant to reveal what the coarse picture hides: an enemy that moved out of a region
shows up in its neighbours. A full-information `Picture` is refused.

**Training** is in train_plans.py: behaviour cloning on a full-information teacher (the same
planner with ``observe="full"``), then evolution strategies, kept only if better on validation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

from .env import View
from .model import DATA_DIR
from .picture import Picture, build_picture
from .planner import MIN_ODDS, Planner
from .plans import BattlePlan, PlanController

PARAMS_PATH = DATA_DIR / "learned_plans_params.json"
TOP_SINGLES = 8
TOP_FOR_PAIRS = 6
SWITCH_MARGIN = 0.6   # a drawn axis set is kept unless another scores this much higher (log scale)

FEATURE_NAMES = (
    "bias_k0", "bias_k1", "bias_k2", "bias_k3", "is_default",
    "log_base", "ratio", "ratio_min", "below_min_odds", "value_joint", "cut_joint", "cut_gain",
    "freed_joint", "objective", "foe", "foe_weak_share", "empty_target", "n_prov", "width",
    "n_nbrs", "foe_nbrs", "river", "attack_mod", "march", "hub_dist", "own_adjacent", "foe_around",
    "d_member", "d_neigh", "d_neigh_pos", "ema_member", "peak_gap", "kept",
    "enc_cut_gain", "enc_far_foe", "enc_multi_prov", "cap_objective", "cap_k", "enc_k",
)
N_FEATURES = len(FEATURE_NAMES)
_IDX = {n: i for i, n in enumerate(FEATURE_NAMES)}
#: How many teacher decisions were seen while recording and how many were among the candidates.
COVERAGE = {"decisions": 0, "covered": 0}
_DEFAULT_PLANNER = Planner()   # stateless helpers only: _odds, _value, _march, a fresh _choose_axes


@dataclass
class History:
    """What earlier pictures of this game showed: enemy counts per region, one step back and the
    running peak. Updated once per re-plan; the student's only memory."""

    prev: dict[str, int] = field(default_factory=dict)
    delta: dict[str, int] = field(default_factory=dict)
    ema: dict[str, float] = field(default_factory=dict)
    peak: dict[str, int] = field(default_factory=dict)

    def observe(self, pic: Picture) -> None:
        for rid, r in pic.regions.items():
            before = self.prev.get(rid, r.foe)
            self.delta[rid] = r.foe - before
            self.ema[rid] = 0.5 * self.ema.get(rid, 0.0) + (r.foe - before)
            self.peak[rid] = max(self.peak.get(rid, 0), r.foe)
            self.prev[rid] = r.foe


@dataclass
class Candidate:
    regions: tuple[str, ...]
    feats: list[float]


def _single_stats(pic: Picture, rid: str) -> dict[str, float] | None:
    """The planner's odds and value for one enemy region, from the aggregated picture; None when
    the region is not something we could attack from where we stand."""
    r = pic.regions[rid]
    if r.controller != pic.foe and not r.foe:
        return None
    ratio, _ = _DEFAULT_PLANNER._odds(pic, rid)
    if r.foe == 0 and r.controller == pic.foe:
        near = any(pic.regions[n].own and pic.regions[n].controller == pic.side for n in r.neighbors)
        near = near or any(
            pic.regions[m].own for n in r.neighbors for m in pic.regions[n].neighbors
            if pic.regions[n].controller == pic.side)
        if not near:
            return None
        ratio = max(ratio, 3.0)
    elif ratio <= 0:
        return None
    march = _DEFAULT_PLANNER._march(pic, rid)
    value = _DEFAULT_PLANNER._value(pic, [rid]) / (1.0 + 0.15 * march)
    base = value * min(ratio, 3.0) * (1.0 if r.foe else 1.3)
    return {"ratio": ratio, "value": value, "base": base, "march": march}


def default_axes(pic: Picture) -> list[str]:
    """What the hand-written aggregated planner would draw on this picture, with no memory."""
    return Planner()._choose_axes(pic)


def generate(pic: Picture) -> list[tuple[str, ...]]:
    """The candidate axis sets for an offensive re-plan, as sorted region tuples."""
    stats = {rid: s for rid in sorted(pic.regions) if (s := _single_stats(pic, rid)) is not None}
    ranked = sorted(stats, key=lambda rid: (-stats[rid]["base"], rid))
    sets: list[tuple[str, ...]] = [()]
    sets += [(rid,) for rid in ranked[:TOP_SINGLES]]
    top = ranked[:TOP_FOR_PAIRS]
    sets += [tuple(sorted(p)) for p in combinations(top, 2)]
    if len(ranked) >= 3 and pic.goal["kind"] == "capture":
        sets.append(tuple(sorted(ranked[:3])))
    default = tuple(sorted(default_axes(pic)))
    if default not in sets:
        sets.append(default)
    return sets


def candidate_features(
    pic: Picture, hist: History, regions: tuple[str, ...], default: tuple[str, ...],
    kept: tuple[str, ...] = (), cache: dict | None = None,
) -> list[float]:
    """The feature vector of one candidate. Reads only the aggregated picture and the history."""
    if pic.observe != "aggregated":
        raise ValueError("the learned plan chooser sees the aggregated picture only")
    cache = cache if cache is not None else {}
    f = [0.0] * N_FEATURES

    def put(name: str, value: float) -> None:
        f[_IDX[name]] = value

    k = len(regions)
    put(f"bias_k{min(k, 3)}", 1.0)
    put("is_default", 1.0 if regions == default else 0.0)
    if not k:
        return f
    foe = pic.foe
    if "hub_dist" not in cache:
        hubs = [rid for rid, r in pic.regions.items() if r.hub == foe]
        cache["hub_dist"] = pic.hops(hubs) if hubs else {}
        cache["stats"] = {}
    stats = cache["stats"]
    for rid in regions:
        if rid not in stats:
            stats[rid] = _single_stats(pic, rid) or {"ratio": 0.0, "value": 0.0, "base": 0.0, "march": 9.0}
    mem = [pic.regions[rid] for rid in regions]
    st = [stats[rid] for rid in regions]
    flips = dict.fromkeys(regions, pic.side)
    cut_joint, _ = pic.cut_off(foe, flips)
    _, freed = pic.cut_off(pic.side, flips)
    cut_sum = sum(pic.cut_off(foe, {rid: pic.side})[0] for rid in regions)
    gain = max(0, cut_joint - cut_sum)
    outside = {n for r in mem for n in r.neighbors} - set(regions)
    enemy_nb = [pic.regions[n] for n in outside if pic.regions[n].controller == foe]
    put("log_base", sum(math.log1p(max(s["base"], 0.0)) for s in st) / k / 4.0)
    put("ratio", sum(min(s["ratio"], 3.0) for s in st) / k / 3.0)
    put("ratio_min", min(min(s["ratio"], 3.0) for s in st) / 3.0)
    put("below_min_odds", sum(s["ratio"] < MIN_ODDS for s in st) / k)
    put("value_joint", _DEFAULT_PLANNER._value(pic, list(regions)) / 10.0)
    put("cut_joint", cut_joint / 3.0)
    put("cut_gain", gain / 3.0)
    put("freed_joint", freed / 3.0)
    put("objective", sum(r.objective for r in mem) / k)
    put("foe", sum(r.foe for r in mem) / k / 6.0)
    put("foe_weak_share", sum(r.foe_weak / r.foe for r in mem if r.foe) / k)
    put("empty_target", sum(1 for r in mem if not r.foe) / k)
    put("n_prov", sum(len(r.provinces) for r in mem) / k / 4.0)
    put("width", sum(r.width for r in mem) / k / 40.0)
    put("n_nbrs", sum(len(r.neighbors) for r in mem) / k / 6.0)
    put("foe_nbrs", sum(sum(pic.regions[n].controller == foe for n in r.neighbors) for r in mem) / k / 4.0)
    put("river", sum(max((rv for n, rv in pic.regions[m].neighbors.items()
                          if pic.regions[n].controller == pic.side), default=0)
                     for m in regions) / k / 2.0)
    put("attack_mod", sum(r.attack_mod for r in mem) / k)
    put("march", sum(s["march"] for s in st) / k / 4.0)
    put("hub_dist", sum(cache["hub_dist"].get(rid, 9) for rid in regions) / k / 6.0)
    put("own_adjacent", sum(pic.regions[n].own for r in mem for n in r.neighbors
                            if pic.regions[n].controller == pic.side) / 10.0)
    put("foe_around", sum(r.foe for r in enemy_nb) / 8.0)
    put("d_member", sum(hist.delta.get(rid, 0) for rid in regions) / 4.0)
    put("d_neigh", sum(hist.delta.get(n, 0) for n in outside) / 4.0)
    put("d_neigh_pos", sum(max(0, hist.delta.get(n, 0)) for n in outside) / 4.0)
    put("ema_member", sum(hist.ema.get(rid, 0.0) for rid in regions) / k / 4.0)
    put("peak_gap", sum(hist.peak.get(rid, 0) - pic.regions[rid].foe for rid in regions) / k / 6.0)
    put("kept", sum(rid in kept for rid in regions) / k)
    kind = pic.goal["kind"]
    enc, cap = kind == "encircle", kind == "capture"
    put("enc_cut_gain", f[_IDX["cut_gain"]] * enc)
    put("enc_far_foe", f[_IDX["hub_dist"]] * f[_IDX["foe"]] * enc)
    put("enc_multi_prov", f[_IDX["n_prov"]] * f[_IDX["foe"]] * enc)
    put("cap_objective", f[_IDX["objective"]] * cap)
    put("cap_k", k * cap / 3.0)
    put("enc_k", k * enc / 3.0)
    return f


def candidates(pic: Picture, hist: History, kept: tuple[str, ...] = ()) -> list[Candidate]:
    default = tuple(sorted(default_axes(pic)))
    cache: dict = {}
    return [Candidate(s, candidate_features(pic, hist, s, default, kept, cache)) for s in generate(pic)]


def teacher_choice(pic: Picture, cands: list[Candidate], axes: list[str]) -> int | None:
    """Index of the candidate that is the teacher's axis set, its provinces mapped to the aggregated
    picture's regions; None when the teacher's choice is not among the candidates."""
    want = tuple(sorted({pic.region_of[p] for p in axes}))
    for i, c in enumerate(cands):
        if c.regions == want:
            return i
    return None


class PlanPolicy:
    """Linear scores over the candidate features."""

    def __init__(self, weights: list[float] | None = None, meta: dict[str, Any] | None = None) -> None:
        self.weights = list(weights) if weights is not None else [0.0] * N_FEATURES
        if len(self.weights) != N_FEATURES:
            raise ValueError(f"expected {N_FEATURES} weights, got {len(self.weights)}")
        self.meta = meta or {}

    def score(self, feats: list[float]) -> float:
        w = self.weights
        return sum(w[i] * x for i, x in enumerate(feats) if x)

    def to_json(self) -> dict[str, Any]:
        return {"version": 1, "features": list(FEATURE_NAMES),
                "weights": [round(x, 6) for x in self.weights], "meta": self.meta}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> PlanPolicy:
        if list(raw.get("features", FEATURE_NAMES)) != list(FEATURE_NAMES):
            raise ValueError("parameter file was trained for a different feature set; retrain")
        return cls(raw["weights"], raw.get("meta"))

    def save(self, path: str | Path = PARAMS_PATH) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=1) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path = PARAMS_PATH) -> PlanPolicy:
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def default_policy() -> PlanPolicy:
    """The committed parameters; without the file, a policy that draws what the hand-written
    aggregated planner would."""
    if Path(PARAMS_PATH).exists():
        return PlanPolicy.load(PARAMS_PATH)
    w = [0.0] * N_FEATURES
    w[_IDX["is_default"]] = 5.0
    return PlanPolicy(w, {"stage": "fallback: the planner's own pick"})


class LearnedPlanner(Planner):
    """The planner with its axis choice replaced by a scorer over aggregated-picture candidates.

    ``recorder`` (training only) gets ``(candidate features, teacher index)`` whenever a
    full-information ``teacher`` (a `Planner`, queried on the same view) picks an axis set that is
    among the candidates; the student's own scores still decide what is played."""

    def __init__(self, policy: PlanPolicy | None = None, cadence: int = 24, patience: int | None = None,
                 teacher: Planner | None = None, recorder: list | None = None) -> None:
        super().__init__("aggregated", cadence, **({} if patience is None else {"patience": patience}))
        self.policy = policy if policy is not None else default_policy()
        self.history = History()
        self.teacher = teacher
        self.recorder = recorder
        self.last_scores: list[float] = []

    def plan(self, view: View, pic: Picture) -> list[BattlePlan]:
        self.history.observe(pic)
        self._teacher_axes: list[str] | None = None
        if self.teacher is not None:
            self.teacher.plan(view, build_picture(view, "full"))
            self._teacher_axes = list(getattr(self.teacher, "last_axes", None) or [])
        return super().plan(view, pic)

    def _choose_axes(self, pic: Picture) -> list[str]:
        kept = self._commit[0] if self._commit is not None else ()
        cands = candidates(pic, self.history, tuple(kept))
        if self.recorder is not None and self._teacher_axes is not None:
            idx = teacher_choice(pic, cands, self._teacher_axes)
            COVERAGE["decisions"] += 1
            COVERAGE["covered"] += idx is not None
            if idx is not None and len(cands) > 1:
                self.recorder.append(([c.feats for c in cands], idx))
        scores = [self.policy.score(c.feats) for c in cands]
        self.last_scores = scores
        best = max(range(len(cands)), key=lambda i: (scores[i], -i))
        if self._commit is not None:
            old, since, _ = self._commit
            alive = all(pic.regions[r].controller == pic.foe or pic.regions[r].foe for r in old)
            for i, c in enumerate(cands):
                if c.regions == tuple(old) and alive and pic.hour - since < self.patience \
                        and scores[best] - scores[i] < SWITCH_MARGIN:
                    best = i
        chosen = cands[best].regions
        self._commit = (chosen, pic.hour, scores[best]) if chosen else None
        return list(chosen)


class AxisTeacher(Planner):
    """`Planner` with ``observe="full"`` that remembers the axes it drew last (for cloning)."""

    def __init__(self, **kw: Any) -> None:
        super().__init__("full", **kw)
        self.last_axes: list[str] | None = None

    def _choose_axes(self, pic: Picture) -> list[str]:
        axes = super()._choose_axes(pic)
        self.last_axes = list(axes)
        return axes


class RecordingTeacher(AxisTeacher):
    """The full-information planner playing, while the aggregated candidates (what the student
    would see on the same view) and the teacher's choice among them are recorded."""

    def __init__(self, recorder: list, **kw: Any) -> None:
        super().__init__(**kw)
        self.recorder = recorder
        self.history = History()
        self._agg: Picture | None = None

    def plan(self, view: View, pic: Picture) -> list[BattlePlan]:
        self._agg = build_picture(view, "aggregated")
        self.history.observe(self._agg)
        return super().plan(view, pic)

    def _choose_axes(self, pic: Picture) -> list[str]:
        axes = super()._choose_axes(pic)
        agg = self._agg
        if agg is not None:
            cands = candidates(agg, self.history)
            idx = teacher_choice(agg, cands, axes)
            COVERAGE["decisions"] += 1
            COVERAGE["covered"] += idx is not None
            if idx is not None and len(cands) > 1:
                self.recorder.append(([c.feats for c in cands], idx))
        return axes


def learned_plans_controller(policy: PlanPolicy | None = None, **kw: Any) -> PlanController:
    return PlanController(LearnedPlanner(policy, **kw), name="learned_plans")


__all__ = [
    "AxisTeacher", "COVERAGE", "Candidate", "FEATURE_NAMES", "History", "LearnedPlanner", "PARAMS_PATH",
    "PlanPolicy", "RecordingTeacher", "candidate_features", "candidates", "default_policy",
    "default_axes", "generate", "learned_plans_controller", "teacher_choice",
]
