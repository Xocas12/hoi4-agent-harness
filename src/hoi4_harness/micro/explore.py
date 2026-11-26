"""Exploration for the micro controller (#85): epsilon-greedy over the orders of one sector, with the
propensity of what was chosen logged, so a policy that was never run can be scored from the log
(ope.py).

The wrapped controller (any with a ``plan`` method: the rule controller, the learned one) says what it
would order on each sector. For each sector the wrapper then draws, independently and from a seed:

* with probability ``1 - epsilon`` the controller's own order (the "base" order);
* with probability ``epsilon``, one of the other orders, uniformly (``epsilon / (K - 1)`` each).

So the propensity of the logged order is known exactly: ``1 - epsilon`` for the base order,
``epsilon / (K - 1)`` for an alternative. Every order has a nonzero chance on every explored sector,
which is what off-policy estimates need ("positivity").

What is explored and what is not:

* Sectors only: states that are in the observation (an enemy state with our divisions against it).
  A rotation order on a rear state is not a choice among the sector's orders; it is logged with
  propensity 1 and ``explored: false``.
* Orders that follow from the LLM's intent are the LLM's, not the controller's, and are neither
  perturbed nor logged here.
* A sector with none of our divisions is not explored: any order there does nothing.

The draw is seeded by (seed, date, state), not by a running generator, so a restarted run draws the
same coin for the same sector and day, and two sectors never share a coin.
"""

from __future__ import annotations

import random

from .baseline import _RANK
from .interface import ORDERS, Intent, MicroOrder, Observation

DEFAULT_EPSILON = 0.2


def valid_orders(sec) -> tuple[str, ...]:
    """The orders worth choosing among on a sector (all of them when we have divisions there)."""
    return ORDERS if sec.own > 0 else ("hold",)


def propensity(order: str, base: str, valid: tuple[str, ...], epsilon: float) -> float:
    """Probability that the exploring policy picks ``order`` when the base policy picks ``base``."""
    if order not in valid:
        return 0.0
    if len(valid) == 1 or base not in valid:
        return 1.0
    return 1.0 - epsilon if order == base else epsilon / (len(valid) - 1)


class ExploringController:
    def __init__(self, inner, epsilon: float = DEFAULT_EPSILON, seed: int = 0):
        if not hasattr(inner, "plan"):
            raise ValueError("exploration wraps a controller that has a plan() method")
        if not 0.0 <= epsilon <= 1.0:
            raise ValueError("epsilon must be between 0 and 1")
        self.inner = inner
        self.epsilon = float(epsilon)
        self.seed = seed
        self.name = f"{inner.name}+explore"
        #: One record per sector decision of the last ``decide``: what was drawn and why.
        self.decisions: list[dict] = []

    def draw(self, date, sec, base: str) -> tuple[str, bool]:
        valid = valid_orders(sec)
        if self.epsilon <= 0 or len(valid) == 1 or base not in valid:
            return base, False
        rng = random.Random(f"{self.seed}|{date}|{sec.state}")
        if rng.random() >= self.epsilon:
            return base, False
        return rng.choice([o for o in valid if o != base]), True

    def decide(self, intents: list[Intent], obs: Observation) -> list[MicroOrder]:
        wanted = self.inner.plan(intents, obs)
        self.decisions = []
        drawn: dict[int, MicroOrder] = {}
        for state, order in wanted.items():
            sec = obs.sector(state)
            if sec is None:
                drawn[state] = order
                self.decisions.append({"state": state, "order": order.order, "base_order": order.order,
                                       "explored": False, "propensity": 1.0, "valid": [order.order],
                                       "enemy": None, "is_sector": False})
                continue
            if order.reason.startswith("intent"):
                drawn[state] = order
                continue
            valid = valid_orders(sec)
            chosen, explored = self.draw(obs.date, sec, order.order)
            drawn[state] = MicroOrder(state, chosen, order.reason if not explored
                                      else f"explore: {chosen} instead of {order.order}")
            self.decisions.append({
                "state": state, "order": chosen, "base_order": order.order, "explored": explored,
                "epsilon": self.epsilon, "seed": self.seed, "enemy": sec.enemy, "is_sector": True,
                "valid": list(valid), "alternatives": [o for o in valid if o != chosen],
                "propensity": propensity(chosen, order.order, valid, self.epsilon)})
        todo = [o for o in drawn.values() if obs.current.get(o.state) != o.order]
        todo.sort(key=lambda o: (_RANK[o.order], o.state))
        kept = todo[: self.inner.max_orders]
        sent = {(o.state, o.order) for o in kept}
        for d in self.decisions:
            d["applied"] = (d["state"], d["order"]) in sent or obs.current.get(d["state"]) == d["order"]
        return kept
