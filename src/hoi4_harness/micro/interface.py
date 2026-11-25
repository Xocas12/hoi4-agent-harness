"""The controller interface.

The LLM states an *intent* (which front, which sector, how hard, how concentrated). A micro
controller turns that and the observed state into low-level orders through the front tools the
game already has (``set_front_order``: push / attack / probe / hold / reserve). It never needs the
game: it reads an ``Observation`` and returns ``MicroOrder`` records, so it can be tested, replayed
and trained offline.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from ..types import ActionCall

#: The orders a controller may emit (the subset of set_front_order that steers a sector).
ORDERS = ("push", "attack", "probe", "hold", "reserve")
POSTURES = ("offensive", "defensive", "hold")
#: condition.py's problem text for a division low on organisation.
LOW_ORG = "low organisation"


@dataclass
class Intent:
    """What the LLM wants on one front. ``sector`` None leaves the choice of sector to the controller."""

    front: str                      # enemy tag
    sector: int | None = None       # state id the main effort goes at
    posture: str = "offensive"      # offensive | defensive | hold
    concentration: float = 0.7      # 0..1: how much of the effort goes at the sector (>= 0.6: push)

    def __post_init__(self) -> None:
        if self.posture not in POSTURES:
            raise ValueError(f"posture must be one of {POSTURES}")
        self.concentration = min(1.0, max(0.0, float(self.concentration)))


@dataclass
class SectorState:
    """One stretch of front: an enemy-held state touching the line, with the forces in contact."""

    state: int
    enemy: str
    own: int = 0
    foe: int = 0
    own_low_org: int = 0
    own_short_supply: int = 0
    own_armoured: int = 0
    org_average: float | None = None
    terrain: str = ""
    #: Province movement of this enemy's front over the report window (frontreport.py).
    gained: int = 0
    lost: int = 0
    unchanged_days: int | None = None
    #: The own state where most of the sector's divisions stand: where a reserve order goes.
    rear_state: int | None = None


@dataclass
class Observation:
    date: str | None
    sectors: list[SectorState] = field(default_factory=list)
    #: The orders standing now: state id -> order.
    current: dict[int, str] = field(default_factory=dict)

    def sector(self, state: int) -> SectorState | None:
        return next((s for s in self.sectors if s.state == state), None)


@dataclass
class MicroOrder:
    state: int
    order: str
    reason: str = ""

    def __post_init__(self) -> None:
        if self.order not in ORDERS:
            raise ValueError(f"order must be one of {ORDERS}, not {self.order!r}")


class MicroController(Protocol):
    name: str

    def decide(self, intents: list[Intent], obs: Observation) -> list[MicroOrder]: ...


def orders_to_calls(orders: list[MicroOrder]) -> list[ActionCall]:
    """The set_front_order calls that carry the orders out."""
    return [ActionCall("set_front_order", {"state": str(o.state), "order": o.order},
                       rationale=f"micro: {o.reason}" if o.reason else "micro")
            for o in orders]


def build_observation(pic, layer=None, history=None, current: dict[int, str] | None = None) -> Observation:
    """The sectors of the line from a world picture (world.Picture) and, when there is one, the
    front history (frontreport.FrontHistory). Duck-typed so tests can pass plain objects."""
    from .. import frontreport as FR

    world = pic.world
    enemies = set(getattr(world, "enemies", None) or ()) if world is not None else set()
    if world is not None:
        enemies -= {t for t, c in world.countries.items() if getattr(c, "capitulated", False)}
    found: dict[int, SectorState] = {}
    for tag, divs in pic.contact.items():
        if tag not in enemies:
            continue
        for d in divs:
            sid = d.state if d.state is not None else (layer.state_of(d.province) if layer else None)
            if sid is None:
                continue
            sec = found.setdefault(sid, SectorState(sid, tag))
            sec.foe += 1
    for sid, (holder, terrain) in pic.borders.items():
        if holder in enemies:
            sec = found.setdefault(sid, SectorState(sid, holder))
            sec.terrain = terrain
    by_id = {c.id: c for c in pic.conditions}
    states = getattr(layer, "states", {}) if layer is not None else {}
    for sid, sec in found.items():
        near: set[int] = set()
        if sid in states:
            near = set(states[sid].provinces)
            for p in list(near):
                near.update(layer.neighbours(p))
        mine = [d for d in pic.own if d.province in near or d.state == sid]
        sec.own = len(mine)
        orgs = [d.organisation for d in mine if d.organisation is not None]
        sec.org_average = sum(orgs) / len(orgs) if orgs else None
        sec.own_armoured = sum(1 for d in mine if FR._is_armoured(d))
        for d in mine:
            cond = by_id.get(d.id)
            if cond is None:
                continue
            sec.own_low_org += LOW_ORG in cond.problems
            sec.own_short_supply += any("supply" in p for p in cond.problems)
        rear = Counter(d.state for d in mine if d.state is not None and d.state != sid)
        sec.rear_state = rear.most_common(1)[0][0] if rear else None
        if history is not None:
            try:
                move = FR._movement(history, sec.enemy, pic.date)
            except Exception:  # noqa: BLE001 - an unreadable history costs the feature, not the turn
                move = {}
            if move.get("known"):
                sec.gained = move["window"]["gained"]
                sec.lost = move["window"]["lost"]
                sec.unchanged_days = move.get("unchanged_days")
    return Observation(pic.date, [found[k] for k in sorted(found)], dict(current or {}))
