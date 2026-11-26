"""A gradient-boosted contextual bandit: the ML model class for the micro controller (#85).

Needs the optional ``ml`` extra (``pip install hoi4-agent-harness[ml]``: numpy and scikit-learn) to
TRAIN. A trained model is stored as plain JSON (the boosted trees as arrays) and predicts in pure
Python, so loading and running one needs no extra: a campaign can use ``micro_controller: learned``
on a machine that never trained anything, and there is no pickle to trust.

Training uses the logged propensities, in one of two ways (``mode``):

* ``dr`` (default), the doubly robust learner. A cheap ridge reward model ``q`` (learn.LinearBandit),
  cross-fitted by episode so a row is never scored by a model that saw it, gives every row and every
  order the pseudo-outcome ``q(x, a) + 1[A = a] / p(a | x) * (r - q(x, a))``. One boosted regressor
  per order is fitted on all rows where that order was possible. The pseudo-outcome is unbiased for
  the order's value whenever the propensities are right, and quieter than plain weighting when ``q``
  is any good.
* ``ipw``, importance-weighted regression: one boosted regressor per order, fitted only on the rows
  where it was taken, each weighted by ``1 / p`` (clipped at ``clip``).

Rows without a propensity count as ``p = 1`` (a log that never explored), which makes both modes
plain regression on what the logger did: they cannot then say anything about orders it never took.

The object has the same surface as ``LinearBandit`` (``fit``, ``predict``, ``act``, ``trusted``,
``counts``, ``save``/``load``, plus ``q`` for ope.py), so the evaluation, the CLI and
``LearnedController`` take either.
"""

from __future__ import annotations

import json
from pathlib import Path

from .baseline import baseline_from_features
from .features import FEATURES, vector
from .interface import ORDERS

#: An order with fewer logged decisions than this is not trusted (same rule as the linear model).
MIN_COUNT = 5
MODES = ("dr", "ipw")
INSTALL_HINT = "pip install 'hoi4-agent-harness[ml]'"


class MissingExtra(ImportError):
    """Training a boosted model needs numpy and scikit-learn, which the core install does not have."""

    def __init__(self, what: str = "numpy and scikit-learn"):
        super().__init__(f"training a boosted micro model needs {what}, which is not installed; "
                         f"install the extra with: {INSTALL_HINT}  (the linear model, "
                         f"`--model-type linear`, needs nothing)")


def _need_sklearn():
    try:
        import numpy as np
        from sklearn.ensemble import GradientBoostingRegressor
    except ImportError as exc:                
        raise MissingExtra() from exc
    return np, GradientBoostingRegressor


def ml_available() -> bool:
    try:
        _need_sklearn()
    except MissingExtra:
        return False
    return True


# -- trees as plain data ------------------------------------------------------------------------------


def _tree_predict(tree: dict, x: list[float]) -> float:
    node = 0
    left, right, feat, thr = tree["left"], tree["right"], tree["feature"], tree["threshold"]
    while left[node] != -1:
        node = left[node] if x[feat[node]] <= thr[node] else right[node]
    return tree["value"][node]


def _export(est) -> dict:
    """A fitted GradientBoostingRegressor as arrays, predicting init + rate * sum of tree leaves."""
    import numpy as np

    zero = np.zeros((1, est.n_features_in_))
    trees = []
    for stage in est.estimators_:
        t = stage[0].tree_
        trees.append({"left": t.children_left.tolist(), "right": t.children_right.tolist(),
                      "feature": t.feature.tolist(), "threshold": [float(v) for v in t.threshold],
                      "value": [float(v) for v in t.value[:, 0, 0]]})
    return {"init": float(est.init_.predict(zero)[0]), "rate": float(est.learning_rate), "trees": trees}


def _predict(model: dict, x: list[float]) -> float:
    return model["init"] + model["rate"] * sum(_tree_predict(t, x) for t in model["trees"])


# -- the model ----------------------------------------------------------------------------------------


class BoostedBandit:
    kind = "boosted"

    def __init__(self, models: dict[str, dict] | None = None, counts: dict[str, int] | None = None,
                 mode: str = "dr", params: dict | None = None, clip: float = 10.0, seed: int = 0):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
        self.models = models or {}
        self.counts = counts or {}
        self.mode = mode
        self.clip = clip
        self.seed = seed
        self.params = {"n_estimators": 60, "max_depth": 2, "learning_rate": 0.1, "min_samples_leaf": 5,
                       "subsample": 1.0, **(params or {})}

    # training ---------------------------------------------------------------------------------

    def fit(self, rows: list[dict], l2: float | None = None) -> BoostedBandit:
        """``l2`` is accepted for the CLI's sake and only shapes the ridge model of the ``dr`` mode."""
        np, regressor = _need_sklearn()
        rows = [r for r in rows if r.get("reward") is not None]
        self.models, self.counts = {}, {o: sum(1 for r in rows if r["action"] == o) for o in ORDERS}
        if not rows:
            return self
        x_all = np.array([vector(r["features"]) for r in rows])
        y = np.array([float(r["reward"]) for r in rows])
        p = np.array([float(r.get("propensity") or 1.0) for r in rows])
        acted = [r["action"] for r in rows]
        valid = [set(r.get("valid") or ORDERS) | {r["action"]} for r in rows]
        q = self._cross_fitted_q(rows, l2) if self.mode == "dr" else None
        for order in ORDERS:
            if self.counts[order] < MIN_COUNT:
                continue
            took = np.array([a == order for a in acted])
            if self.mode == "ipw":
                idx = np.flatnonzero(took)
                target, weight = y[idx], np.minimum(1.0 / p[idx], self.clip)
            else:
                idx = np.array([i for i in range(len(rows)) if order in valid[i]])
                qa = q[:, ORDERS.index(order)][idx]
                target = qa + np.where(took[idx], np.minimum(1.0 / p[idx], self.clip), 0.0) * (y[idx] - qa)
                weight = None
            est = regressor(random_state=self.seed, **self.params)
            est.fit(x_all[idx], target, sample_weight=weight)
            self.models[order] = _export(est)
        return self

    @staticmethod
    def _cross_fitted_q(rows: list[dict], l2: float | None):
        """The ridge reward model's prediction of every order for every row, from folds of whole
        episodes that exclude the row's own (a single episode falls back to in-sample)."""
        import numpy as np

        from .learn import LinearBandit

        eps = sorted({r["episode"] for r in rows})
        k = min(4, len(eps))
        fold_of = {e: i % k for i, e in enumerate(eps)}
        fallback = sum(float(r["reward"]) for r in rows) / len(rows)
        q = np.full((len(rows), len(ORDERS)), fallback)
        for f in range(k):
            held = [i for i, r in enumerate(rows) if fold_of[r["episode"]] == f]
            fit_rows = [r for r in rows if fold_of[r["episode"]] != f] if k > 1 else rows
            lin = LinearBandit().fit(fit_rows, l2)
            for i in held:
                for j, order in enumerate(ORDERS):
                    if order in lin.weights:
                        q[i, j] = sum(a * b for a, b in zip(lin.weights[order], vector(rows[i]["features"]),
                                                            strict=True))
        return q

    # prediction -------------------------------------------------------------------------------

    def trusted(self) -> list[str]:
        return [o for o in ORDERS if o in self.models and self.counts.get(o, 0) >= MIN_COUNT]

    def q(self, features: dict[str, float], order: str, fallback: float = 0.0) -> float:
        m = self.models.get(order)
        return _predict(m, vector(features)) if m is not None else fallback

    def predict(self, features: dict[str, float]) -> dict[str, float]:
        x = vector(features)
        return {o: _predict(self.models[o], x) for o in self.trusted()}

    def act(self, features: dict[str, float]) -> str:
        pred = self.predict(features)
        if not pred:
            return baseline_from_features(features)
        return max(pred, key=lambda o: (pred[o], -ORDERS.index(o)))

    # storage ----------------------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "kind": self.kind, "features": list(FEATURES),
                                    "mode": self.mode, "clip": self.clip, "params": self.params,
                                    "counts": self.counts, "models": self.models}), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> BoostedBandit:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("kind") != cls.kind:
            raise ValueError("not a boosted micro model file")
        if tuple(data.get("features", ())) != FEATURES:
            raise ValueError("the model was trained on a different feature set; retrain it")
        return cls(data["models"], data["counts"], data.get("mode", "dr"), data.get("params"),
                   data.get("clip", 10.0))
