"""Scenarios: fixed starts with checkable objectives.

A scenario is deliberately narrow. "Play Germany well" is unscoreable; "by
1937-01-01, hold 20 civilian factories with stability above 50%" is a number two
agents can be compared on. Each scenario runs against the mock adapter by
default so it costs nothing but model tokens.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..types import GameState


@dataclass
class Objective:
    name: str
    check: Callable[[GameState], bool]
    weight: float = 1.0
    description: str = ""


@dataclass
class Scenario:
    key: str
    title: str
    country: str
    start: str
    turns: int
    briefing: str
    objectives: list[Objective] = field(default_factory=list)
    seed: int = 1936


ECONOMY_RAMP = Scenario(
    key="economy_ramp",
    title="Peacetime economy ramp",
    country="SWE",
    start="1936-01-01",
    turns=40,
    briefing=(
        "Neutral start, no war expected for years. Grow industry without wrecking "
        "stability, and keep a focus and all research slots busy."
    ),
    objectives=[
        Objective(
            "civ_factories_20",
            lambda s: s.civilian_factories >= 20,
            description="20+ civilian factories",
        ),
        Objective(
            "stability_held",
            lambda s: s.stability >= 0.50,
            description="stability at or above 50%",
        ),
        Objective(
            "focus_running",
            lambda s: s.national_focus is not None,
            description="a national focus is running at the end",
        ),
        Objective(
            "research_busy",
            lambda s: all(slot.technology for slot in s.research),
            description="no idle research slot at the end",
        ),
    ],
)

WAR_READINESS = Scenario(
    key="war_readiness",
    title="Ready by September 1939",
    country="SWE",
    start="1936-01-01",
    turns=180,
    briefing=(
        "War arrives on 1939-09-01. Be able to equip and field a real army by then "
        "without having bankrupted the economy to do it."
    ),
    objectives=[
        Objective("mil_factories_15", lambda s: s.military_factories >= 15,
                  description="15+ military factories"),
        Objective("divisions_36", lambda s: sum(g.count for g in s.divisions) >= 36,
                  description="36+ divisions fielded"),
        Objective("equipment_buffer",
                  lambda s: s.stockpiles.get("infantry_equipment_1", 0) >= 10_000,
                  description="10k+ infantry equipment in reserve"),
    ],
)

SCENARIOS = {s.key: s for s in (ECONOMY_RAMP, WAR_READINESS)}
