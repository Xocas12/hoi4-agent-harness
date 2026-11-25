"""The offline learner: one ridge-regression reward model per order (a linear contextual bandit,
pure Python, no dependencies). The policy picks the order whose predicted outcome is highest.

This is the smallest thing that is honest about what the data can support. It is a reward model
fitted to logged decisions, not a strong player; docs/micro-controller.md says what a strong one needs.
"""

from __future__ import annotations

import json
from pathlib import Path

from .baseline import RuleController, baseline_from_features
from .features import FEATURES, sector_features, vector
from .interface import ORDERS, SectorState

#: An order with fewer logged decisions than this is not trusted: the policy cannot pick it.
MIN_COUNT = 5


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting for a small symmetric positive-definite system."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            raise ValueError("singular system")
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))) / m[i][i]
    return x


def fit_ridge(xs: list[list[float]], ys: list[float], l2: float) -> list[float]:
    n = len(xs[0])
    a = [[sum(x[i] * x[j] for x in xs) for j in range(n)] for i in range(n)]
    for i in range(1, n):          # the bias (index 0) is not shrunk
        a[i][i] += l2
    a[0][0] += 1e-9
    b = [sum(x[i] * y for x, y in zip(xs, ys, strict=True)) for i in range(n)]
    return solve(a, b)


class LinearBandit:
    def __init__(self, weights: dict[str, list[float]] | None = None, counts: dict[str, int] | None = None,
                 l2: float = 1.0, features: tuple[str, ...] = FEATURES):
        self.weights = weights or {}
        self.counts = counts or {}
        self.l2 = l2
        self.features = tuple(features)

    def fit(self, rows: list[dict], l2: float | None = None) -> LinearBandit:
        if l2 is not None:
            self.l2 = l2
        self.weights, self.counts = {}, {}
        for order in ORDERS:
            mine = [r for r in rows if r["action"] == order and r.get("reward") is not None]
            self.counts[order] = len(mine)
            if mine:
                self.weights[order] = fit_ridge([vector(r["features"]) for r in mine],
                                                [float(r["reward"]) for r in mine], self.l2)
        return self

    def trusted(self) -> list[str]:
        return [o for o in ORDERS if o in self.weights and self.counts.get(o, 0) >= MIN_COUNT]

    def predict(self, features: dict[str, float]) -> dict[str, float]:
        x = vector(features)
        return {o: sum(w * v for w, v in zip(self.weights[o], x, strict=True)) for o in self.trusted()}

    def act(self, features: dict[str, float]) -> str:
        """The order with the best predicted outcome; the rule baseline's when nothing is trusted yet."""
        pred = self.predict(features)
        if not pred:
            return baseline_from_features(features)
        return max(pred, key=lambda o: (pred[o], -ORDERS.index(o)))

    # -- storage --------------------------------------------------------------------------

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "features": list(self.features), "l2": self.l2,
                                    "weights": self.weights, "counts": self.counts}, indent=1),
                        encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> LinearBandit:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if tuple(data.get("features", ())) != FEATURES:
            raise ValueError("the model was trained on a different feature set; retrain it")
        return cls(data["weights"], data["counts"], data.get("l2", 1.0), FEATURES)


class LearnedController(RuleController):
    """The rule controller's structure (intent, hold elsewhere, rotation) with the main effort's
    order chosen by the learned model."""

    name = "learned"

    def __init__(self, model: LinearBandit, max_orders: int = 6):
        super().__init__(max_orders)
        self.model = model

    def main_effort(self, sec: SectorState) -> str:
        return self.model.act(sector_features(sec))


def split_episodes(rows: list[dict], test_fraction: float = 0.3) -> tuple[list[dict], list[dict]]:
    """Held-out split by episode (run): the last runs by name go to the test set. A single run is split
    in time instead, the later decisions held out, so the test never trains on its own future."""
    from ..frontreport import ordinal

    episodes = sorted({r["episode"] for r in rows})
    if len(episodes) >= 2:
        n_test = max(1, round(len(episodes) * test_fraction))
        test_eps = set(episodes[-n_test:])
        return ([r for r in rows if r["episode"] not in test_eps],
                [r for r in rows if r["episode"] in test_eps])
    ordered = sorted(rows, key=lambda r: ordinal(r["date"]) or 0)
    cut = int(len(ordered) * (1 - test_fraction))
    return ordered[:cut], ordered[cut:]
