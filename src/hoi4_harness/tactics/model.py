"""Data model: constants, templates, provinces, divisions, orders."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"

ORDER_KINDS = ("hold", "move", "attack", "retreat")


@dataclass(frozen=True)
class Order:
    """What a player tells one division. `move` may name any province (the sim finds the
    way around enemy units); `attack` and `retreat` name an adjacent one."""

    division: str
    kind: str
    target: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ORDER_KINDS:
            raise ValueError(f"unknown order kind {self.kind!r}; expected one of {ORDER_KINDS}")


class Constants:
    """The committed fixture, flattened: `c.combat('hit_rate')`, `c.terrain['hills']`."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self._groups = {
            g: {k: v["value"] for k, v in raw[g].items()} for g in ("combat", "movement", "recovery")
        }
        self.terrain: dict[str, dict[str, Any]] = raw["terrain"]
        self.unit_terrain: dict[str, dict[str, Any]] = raw["unit_terrain"]
        self.templates: dict[str, Template] = {
            name: Template.build(name, spec["battalions"], raw["battalions"])
            for name, spec in raw["templates"].items()
        }

    def combat(self, key: str) -> float:
        return self._groups["combat"][key]

    def movement(self, key: str) -> float:
        return self._groups["movement"][key]

    def recovery(self, key: str) -> float:
        return self._groups["recovery"][key]


@lru_cache(maxsize=1)
def load_constants() -> Constants:
    return Constants(json.loads((DATA_DIR / "constants.json").read_text(encoding="utf-8")))


@dataclass(frozen=True)
class Template:
    """A division design, flattened to the totals combat uses."""

    name: str
    width: float
    hp: float
    org: float
    speed: float
    soft_attack: float
    hard_attack: float
    defense: float
    breakthrough: float
    armor: float
    piercing: float
    hardness: float

    @property
    def kind(self) -> str:
        return "armor" if self.hardness >= 0.5 else "infantry"

    @classmethod
    def build(cls, name: str, battalions: dict[str, int], table: dict[str, dict]) -> Template:
        n = sum(battalions.values())
        tot = lambda key: sum(table[b][key] * k for b, k in battalions.items())  # noqa: E731
        avg = lambda key: tot(key) / n  # noqa: E731
        return cls(
            name=name,
            width=tot("width"),
            hp=tot("hp"),
            org=avg("org"),
            speed=min(table[b]["speed"] for b in battalions),
            soft_attack=tot("soft_attack"),
            hard_attack=tot("hard_attack"),
            defense=tot("defense"),
            breakthrough=tot("breakthrough"),
            armor=avg("armor"),
            piercing=avg("piercing"),
            hardness=avg("hardness"),
        )


@dataclass
class Province:
    id: str
    terrain: str
    controller: str
    hub: str | None = None  # a supply hub belonging to this side
    neighbors: dict[str, int] = field(default_factory=dict)  # id -> river (0 none, 1 small, 2 large)
    x: float = 0.0
    y: float = 0.0


@dataclass
class Division:
    id: str
    side: str
    template: Template
    province: str
    strength: float
    org: float
    path: list[str] = field(default_factory=list)  # provinces still to enter, nearest first
    attack_target: str | None = None
    move_left: int = 0  # hours until the next hop completes; 0 = not started
    alive: bool = True
    engaged: bool = False
    encircled: bool = False
    encircled_hours: int = 0
    supply: float = 1.0

    @property
    def strength_frac(self) -> float:
        return max(self.strength, 0.0) / self.template.hp

    @property
    def org_frac(self) -> float:
        return max(self.org, 0.0) / self.template.org
