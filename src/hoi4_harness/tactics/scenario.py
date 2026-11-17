"""Scenario files: a small map, forces, and a goal for each side.

A scenario is JSON (data/scenarios/*.json), so a map loader built elsewhere can emit the
same shape later. Fields:

    name, description, scored_side ("A"), max_hours, step_hours
    provinces: [{id, terrain, controller, hub?, x?, y?}]      hub = the side whose supply hub it is
    edges:     [[a, b, river]]                                 river 0/1/2 = none/small/large
    divisions: [{side, template, province, count?, org?}]      ids are generated: A1, A2, ... B1, ...
    goals:     {"A": {...}, "B": {...}}                        see GOAL_KINDS
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import DATA_DIR, Division, Province, load_constants

SCENARIO_DIR = DATA_DIR / "scenarios"

# hold:     keep every listed province controlled to the end (lost the moment one falls)
# capture:  control every listed province before the time limit
# encircle: get `min_cut_off` enemy divisions cut off from supply (or destroyed while cut off)
# escape:   end with at least `min_alive` of your divisions alive and back in supply
GOAL_KINDS = ("hold", "capture", "encircle", "escape")


@dataclass
class Scenario:
    name: str
    description: str
    scored_side: str
    max_hours: int
    step_hours: int
    provinces: list[dict[str, Any]]
    edges: list[list[Any]]
    divisions: list[dict[str, Any]]
    goals: dict[str, dict[str, Any]]
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Scenario:
        goals = raw["goals"]
        for side, goal in goals.items():
            if goal["kind"] not in GOAL_KINDS:
                raise ValueError(f"{raw['name']}: side {side} has unknown goal {goal['kind']!r}")
        ids = {p["id"] for p in raw["provinces"]}
        for a, b, *_ in raw["edges"]:
            if a not in ids or b not in ids:
                raise ValueError(f"{raw['name']}: edge {a}-{b} names an unknown province")
        for d in raw["divisions"]:
            if d["province"] not in ids:
                raise ValueError(f"{raw['name']}: division in unknown province {d['province']}")
        return cls(
            name=raw["name"],
            description=raw.get("description", ""),
            scored_side=raw.get("scored_side", "A"),
            max_hours=int(raw["max_hours"]),
            step_hours=int(raw.get("step_hours", 6)),
            provinces=raw["provinces"],
            edges=raw["edges"],
            divisions=raw["divisions"],
            goals=goals,
        )

    def build(self) -> tuple[dict[str, Province], dict[str, Division]]:
        consts = load_constants()
        provinces = {
            p["id"]: Province(
                id=p["id"], terrain=p["terrain"], controller=p["controller"], hub=p.get("hub"),
                x=p.get("x", 0.0), y=p.get("y", 0.0),
            )
            for p in self.provinces
        }
        for p in provinces.values():
            if p.terrain not in consts.terrain:
                raise ValueError(f"{self.name}: unknown terrain {p.terrain!r} in {p.id}")
        for a, b, *rest in self.edges:
            river = int(rest[0]) if rest else 0
            provinces[a].neighbors[b] = river
            provinces[b].neighbors[a] = river
        divisions: dict[str, Division] = {}
        counters: dict[str, int] = {}
        for spec in self.divisions:
            tpl = consts.templates[spec["template"]]
            for _ in range(int(spec.get("count", 1))):
                side = spec["side"]
                counters[side] = counters.get(side, 0) + 1
                did = f"{side}{counters[side]}"
                divisions[did] = Division(
                    id=did, side=side, template=tpl, province=spec["province"],
                    strength=tpl.hp * float(spec.get("strength", 1.0)),
                    org=tpl.org * float(spec.get("org", 1.0)),
                )
        return provinces, divisions


def list_scenarios() -> list[str]:
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.json"))


def load_scenario(name_or_path: str | Path) -> Scenario:
    path = Path(name_or_path)
    if not path.suffix:
        path = SCENARIO_DIR / f"{name_or_path}.json"
    if not path.exists():
        raise FileNotFoundError(f"no scenario {name_or_path!r}; known: {', '.join(list_scenarios())}")
    return Scenario.from_dict(json.loads(path.read_text(encoding="utf-8")))
