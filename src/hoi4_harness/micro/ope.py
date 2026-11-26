"""Off-policy evaluation: score a policy that was never run from decisions logged by one that was.

The exploration controller (explore.py) logs, for every sector decision, the order it took and the
probability (propensity) it had of taking it. With that, the value of a different policy ``pi`` (the
mean outcome it would have got) can be estimated from the log:

* ``ipw``   inverse propensity weighting: mean of ``1[a == pi(x)] / p(a|x) * r``. Unbiased when every
  order ``pi`` might pick had a nonzero propensity, but noisy: only the rows where the logged order
  equals ``pi``'s count, each weighted up by ``1/p``.
* ``snips`` the self-normalised version: weighted mean ``sum(w r) / sum(w)``. Slightly biased, much
  less noisy, never outside the range of the rewards.
* ``dr``    doubly robust: ``q(x, pi(x)) + 1[a == pi(x)] / p * (r - q(x, a))`` with ``q`` the reward model
  (learn.LinearBandit). Unbiased when either the propensities or the reward model are right, and with
  a decent model much less noisy than ``ipw``.

What it cannot do, and says so in the numbers it returns:

* A policy that picks an order the logger never could (not in the row's ``valid`` set, or a propensity
  of zero) has no support there. ``off_support`` is the share of rows where that happens; an estimate
  with a large share is not an estimate. Logs from a controller that never explores have propensity 1
  on one order, so every other policy is mostly off support.
* The effective sample size ``ess`` is what the weights leave: ``(sum w)^2 / sum w^2``.
* The outcome is one decision's, over the horizon. Orders on neighbouring sectors and on the next turn
  share it, so rows are not independent; the interval resamples whole episodes (runs) when there are
  at least ``MIN_CLUSTERS`` of them and single rows otherwise, and the latter is too narrow.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable

from .features import vector
from .interface import ORDERS

Policy = Callable[[dict], str]          # features -> order
MIN_CLUSTERS = 5
N_BOOT = 400


def usable(rows: list[dict]) -> list[dict]:
    """Sector decisions with an outcome and a propensity (rotation orders on rear states and old
    rows without propensities are not part of the estimate)."""
    return [r for r in rows if r.get("reward") is not None and (r.get("propensity") or 0) > 0
            and r.get("is_sector", True) is not False]


def q_hat(model, features: dict, order: str, fallback: float = 0.0) -> float:
    """The reward model's prediction for any order, trusted or not (a model with no data for an order
    falls back to ``fallback``, which the caller sets to the training mean)."""
    if model is not None and hasattr(model, "q"):         # a model class with its own q (boosted.py)
        return model.q(features, order, fallback)
    w = getattr(model, "weights", {}).get(order) if model is not None else None
    if w is None:
        return fallback
    return sum(a * b for a, b in zip(w, vector(features), strict=True))


def _terms(row: dict, policy: Policy, model, fallback: float, clip: float | None) -> dict:
    pi = policy(row["features"])
    valid = row.get("valid") or list(ORDERS)
    p = float(row["propensity"])
    match = pi == row["action"]
    w = (1.0 / p) if match else 0.0
    if clip is not None:
        w = min(w, clip)
    r = float(row["reward"])
    out = {"w": w, "r": r, "match": match, "off": pi not in valid, "pi": pi}
    out["ipw"] = w * r
    if model is not None:
        out["dr"] = q_hat(model, row["features"], pi, fallback) + w * (r - q_hat(model, row["features"],
                                                                                 row["action"], fallback))
    return out


def _summarise(terms: list[dict]) -> dict:
    n = len(terms)
    if not n:
        return {"n": 0}
    sw = sum(t["w"] for t in terms)
    sw2 = sum(t["w"] ** 2 for t in terms)
    out = {"n": n, "matched": sum(1 for t in terms if t["match"]),
           "off_support": sum(1 for t in terms if t["off"]) / n,
           "ipw": sum(t["ipw"] for t in terms) / n,
           "snips": (sum(t["ipw"] for t in terms) / sw) if sw else None,
           "ess": (sw * sw / sw2) if sw2 else 0.0,
           "max_weight": max(t["w"] for t in terms)}
    if "dr" in terms[0]:
        out["dr"] = sum(t["dr"] for t in terms) / n
    return out


def _resample(rows: list[dict], rng: random.Random) -> list[int]:
    eps: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        eps.setdefault(str(r.get("episode")), []).append(i)
    if len(eps) < MIN_CLUSTERS:
        return [rng.randrange(len(rows)) for _ in rows]
    keys = sorted(eps)
    picks: list[int] = []
    for _ in keys:
        picks += eps[rng.choice(keys)]
    return picks


def _interval(values: list[float]) -> list[float] | None:
    vals = sorted(v for v in values if v is not None and not math.isnan(v))
    if len(vals) < 20:
        return None
    return [vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))]]


def estimate(rows: list[dict], policy: Policy, model=None, fallback: float = 0.0,
             clip: float | None = None, seed: int = 0, n_boot: int = N_BOOT) -> dict:
    """Estimated value of ``policy`` (mean outcome per decision) from the logged ``rows``, with a
    bootstrap 95% interval on each estimator. Pass ``model`` for the doubly robust one."""
    rows = usable(rows)
    terms = [_terms(r, policy, model, fallback, clip) for r in rows]
    out = _summarise(terms)
    if not rows:
        return out
    rng = random.Random(seed)
    boots = [_summarise([terms[i] for i in _resample(rows, rng)]) for _ in range(n_boot)]
    for key in ("ipw", "snips") + (("dr",) if model is not None else ()):
        out[f"{key}_ci"] = _interval([b.get(key) for b in boots])
    return out


def compare(rows: list[dict], policy_a: Policy, policy_b: Policy, model=None, fallback: float = 0.0,
            clip: float | None = None, seed: int = 0, n_boot: int = N_BOOT) -> dict:
    """The paired difference ``a - b`` in estimated value, on the same rows, with a bootstrap interval.
    Uses the doubly robust estimator when a model is given, else ipw."""
    key = "dr" if model is not None else "ipw"
    rows = usable(rows)
    ta = [_terms(r, policy_a, model, fallback, clip) for r in rows]
    tb = [_terms(r, policy_b, model, fallback, clip) for r in rows]
    diffs = [a[key] - b[key] for a, b in zip(ta, tb, strict=True)]
    if not diffs:
        return {"n": 0, "estimator": key, "difference": None, "ci": None}
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        idx = _resample(rows, rng)
        boots.append(sum(diffs[i] for i in idx) / len(idx))
    return {"n": len(diffs), "estimator": key, "difference": sum(diffs) / len(diffs),
            "ci": _interval(boots)}
