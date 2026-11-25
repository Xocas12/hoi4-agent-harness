"""Sector features: one vector shape for the live controller, the dataset and the learner."""

from __future__ import annotations

import math

FEATURES = ("bias", "log_own", "log_foe", "log_ratio", "low_org_share", "short_supply_share",
            "armour_share", "net_gain", "stuck")


def feature_dict(own: int, foe: int, low_org: int = 0, short_supply: int = 0, armoured: int = 0,
                 gained: int = 0, lost: int = 0, unchanged_days: int | None = None) -> dict[str, float]:
    def share(n: int) -> float:
        return min(1.0, n / own) if own else 0.0

    return {
        "bias": 1.0,
        "log_own": math.log1p(own),
        "log_foe": math.log1p(foe),
        "log_ratio": math.log((own + 1) / (foe + 1)),
        "low_org_share": share(low_org),
        "short_supply_share": share(short_supply),
        "armour_share": share(armoured),
        "net_gain": max(-1.0, min(1.0, (gained - lost) / 10)),
        "stuck": min(1.0, (unchanged_days or 0) / 90),
    }


def sector_features(sec) -> dict[str, float]:
    return feature_dict(sec.own, sec.foe, sec.own_low_org, sec.own_short_supply, sec.own_armoured,
                        sec.gained, sec.lost, sec.unchanged_days)


def vector(features: dict[str, float]) -> list[float]:
    return [float(features.get(name, 0.0)) for name in FEATURES]
