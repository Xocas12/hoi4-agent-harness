"""The rule-based baseline: concentrate on the weakest enemy sector, rotate tired divisions,
hold elsewhere. It is the thing a learned policy has to beat."""

from __future__ import annotations

import math

from .features import sector_features
from .interface import Intent, MicroOrder, Observation, SectorState

#: A sector with this share of its divisions low on organisation is rotated out.
ROTATE_SHARE = 0.25
MAX_ORDERS = 6
_RANK = {"push": 0, "attack": 1, "probe": 2, "reserve": 3, "hold": 4}


def baseline_action(own: int, foe: int, low_org_share: float = 0.0) -> str:
    """The order for a sector chosen as the main effort, from the forces alone."""
    if own == 0:
        return "hold"
    if low_org_share >= ROTATE_SHARE:
        return "reserve"
    ratio = own / max(foe, 1)
    if ratio >= 2:
        return "push"
    if ratio >= 1:
        return "attack"
    if ratio >= 0.5:
        return "probe"
    return "hold"


def baseline_from_features(f: dict[str, float]) -> str:
    """The same rule from a feature dict (own and foe recovered from the logs), for offline comparison."""
    own = round(max(0.0, math.expm1(f.get("log_own", 0.0))))
    foe = round(max(0.0, math.expm1(f.get("log_foe", 0.0))))
    return baseline_action(own, foe, f.get("low_org_share", 0.0))


def intent_order(intent: Intent) -> str:
    if intent.posture != "offensive":
        return "hold"
    return "push" if intent.concentration >= 0.6 else "attack" if intent.concentration >= 0.3 else "probe"


class RuleController:
    name = "rule"

    def __init__(self, max_orders: int = MAX_ORDERS):
        self.max_orders = max_orders

    def main_effort(self, sec: SectorState) -> str:
        """The order for the sector the controller picked on its own; a learned one overrides this."""
        return baseline_action(sec.own, sec.foe, sector_features(sec)["low_org_share"])

    def decide(self, intents: list[Intent], obs: Observation) -> list[MicroOrder]:
        wanted: dict[int, MicroOrder] = {}
        by_front: dict[str, list[SectorState]] = {}
        for sec in obs.sectors:
            by_front.setdefault(sec.enemy, []).append(sec)
        for enemy, sectors in sorted(by_front.items()):
            said = [i for i in intents if i.front == enemy]
            target = None
            for intent in said:
                sec = obs.sector(intent.sector) if intent.sector is not None else None
                if sec is not None and sec.enemy == enemy:
                    target = target or sec
                    wanted[sec.state] = MicroOrder(
                        sec.state, intent_order(intent),
                        f"intent {intent.posture} at {intent.concentration:.1f}")
            # a front the LLM put on hold or defence gets no offensive sector of the controller's own
            standing_down = any(i.posture != "offensive" and i.sector is None for i in said)
            if target is None and not standing_down:
                cands = [s for s in sectors if s.foe and s.own]
                if cands:
                    target = max(cands, key=lambda s: ((s.own + 1) / (s.foe + 1), -s.foe, -s.state))
                    wanted[target.state] = MicroOrder(
                        target.state, self.main_effort(target),
                        f"weakest sector: {target.own} against {target.foe}")
            for sec in sectors:
                if sec.state not in wanted:
                    wanted[sec.state] = MicroOrder(sec.state, "hold", "hold elsewhere")
            for sec in sectors:
                if sector_features(sec)["low_org_share"] >= ROTATE_SHARE and sec.rear_state is not None:
                    wanted.setdefault(sec.rear_state, MicroOrder(
                        sec.rear_state, "reserve",
                        f"rotate: {sec.own_low_org} of {sec.own} low on organisation"))
        orders = [o for o in wanted.values() if obs.current.get(o.state) != o.order]
        orders.sort(key=lambda o: (_RANK[o.order], o.state))
        return orders[: self.max_orders]
