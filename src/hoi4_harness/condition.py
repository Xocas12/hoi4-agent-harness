"""How fit each of the country's divisions is: organisation, strength, supply, entrenchment, cut off.

What the text save holds per division (checked on a real 1941 save, 946 divisions of four countries):

* ``organisation`` and ``strength`` as absolute numbers. **No maximum is written**: the cap depends on
  the template, doctrine, leader and terrain. The share shown here is an ESTIMATE, the division's
  value over the highest value among the country's divisions of the same template (needs two of
  them), so a country whose every copy of a template is damaged reads high.
* ``army_current_supply_ratio`` and ``max_supply``: the first runs from 0 to the second (108 or 156
  in the save), so the supply share is the ratio over the ceiling. ``out_of_supply_days`` is
  written only while the division is out of supply.
* ``dig_in`` and ``dig_in_cap`` (entrenchment and what it can grow to), ``experience`` (raw points,
  not the percentage bar), ``last_combat_date``, ``str_damage`` / ``org_damage`` (what recent fights
  cost it), ``acclimatization`` (climate adaptation against its maximum).
* Attrition is not saved: the game computes it from supply, climate and terrain. What the save
  holds are its causes (days out of supply, acclimatisation), and those are what is shown.
* Equipment against need: the template's need is in the game's unit files, not the save. The
  division's pending reinforcement requests list what it is short of, so the fill is
  ``held / (held + still requested)``; it is exact about the shortage, and it does not count a
  gap the division has not yet asked to fill.

Encirclement is worked out from the map: a division out of supply is encircled when the land
provinces reachable from its own, without crossing a province an enemy holds, form a pocket (no
more than POCKET provinces) that touches enemy-held land. A division whose every land neighbour is
enemy-held is the pocket of one province. The limit is deliberate: a colony across the sea (Libya,
35 provinces on the 1941 save) is out of supply and bordered by the enemy, and is not encircled, it
is at the end of a sea line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: A pocket larger than this many provinces is taken to reach home (the search stops there).
POCKET = 12
#: Thresholds for what the brief calls a problem.
LOW_ORG_SHARE = 0.30
#: Used when a division has no same-template copy to compare with: 30% of a typical 60.
LOW_ORG_ABSOLUTE = 18.0
LOW_SUPPLY_SHARE = 0.25
LONG_OUT_OF_SUPPLY = 6
LOW_STRENGTH_SHARE = 0.40


@dataclass
class Condition:
    id: str
    template: str
    province: int
    state: int | None
    terrain: str
    org: float | None
    org_share: float | None
    strength: float | None
    strength_share: float | None
    manpower: float | None
    supply: float | None
    supply_share: float | None
    out_of_supply_days: int
    dig_in: float | None
    dig_in_cap: float | None
    experience: float | None
    equipment_fill: float | None
    acclimatisation: float | None
    recent_damage: tuple[float, float] | None
    last_combat: str | None
    #: "line" / "reserve" when the division is in a battle in the save, else None.
    in_battle: str | None = None
    encircled: bool | None = None
    pocket: int | None = None
    problems: list[str] = field(default_factory=list)


def _share(value, reference):
    if value is None or not reference:
        return None
    return max(0.0, min(1.0, value / reference))


def _reference(divisions) -> tuple[dict[str, float], dict[str, float]]:
    org: dict[str, list[float]] = {}
    strength: dict[str, list[float]] = {}
    for d in divisions:
        if d.organisation is not None:
            org.setdefault(d.template, []).append(d.organisation)
        if d.strength is not None:
            strength.setdefault(d.template, []).append(d.strength)
    # a maximum needs a second copy to be more than the value itself
    return ({t: max(v) for t, v in org.items() if len(v) > 1},
            {t: max(v) for t, v in strength.items() if len(v) > 1})


def pocket_of(province: int, layer, controller_of, enemies: set[str], limit: int | None = None):
    """(size, touches an enemy) of the pocket of land the province sits in, or (None, False) when it
    reaches more than ``limit`` provinces, which is taken to mean it is connected to home."""
    limit = POCKET if limit is None else limit
    seen = {province}
    stack = [province]
    touches = False
    while stack:
        here = stack.pop()
        for n in layer.neighbours(here, straits=False):
            info = layer.provinces.get(n)
            if info is None or info.kind != "land" or n in seen:
                continue
            if controller_of(n) in enemies:
                touches = True
                continue
            seen.add(n)
            if len(seen) > limit:
                return None, touches
            stack.append(n)
    return len(seen), touches


def assess(world, me: str, layer, in_battle: dict[str, str] | None = None) -> list[Condition]:
    """One :class:`Condition` per division of ``me`` in the save, worst first."""
    country = world.countries.get(me)
    if country is None:
        return []
    divisions = country.divisions
    ref_org, ref_str = _reference(divisions)
    in_battle = in_battle or {}
    out = []
    for d in divisions:
        supply_share = None
        if d.supply is not None and d.max_supply:
            supply_share = max(0.0, min(1.0, d.supply / d.max_supply))
        fill = None
        if d.equipment_held is not None and d.equipment_missing is not None:
            total = d.equipment_held + d.equipment_missing
            fill = d.equipment_held / total if total else None
        recent = None
        if d.str_damage is not None or d.org_damage is not None:
            recent = (d.str_damage or 0.0, d.org_damage or 0.0)
        cond = Condition(
            id=d.id, template=d.template, province=d.province, state=d.state, terrain=d.terrain,
            org=d.organisation, org_share=_share(d.organisation, ref_org.get(d.template)),
            strength=d.strength, strength_share=_share(d.strength, ref_str.get(d.template)),
            manpower=d.manpower, supply=d.supply, supply_share=supply_share,
            out_of_supply_days=d.out_of_supply_days or 0, dig_in=d.dig_in, dig_in_cap=d.dig_in_cap,
            experience=d.experience, equipment_fill=fill, acclimatisation=d.acclimatisation,
            recent_damage=recent, last_combat=d.last_combat, in_battle=in_battle.get(d.key))
        if cond.out_of_supply_days and layer is not None:
            size, touches = pocket_of(d.province, layer,
                                      lambda p: world.controller_of(p, layer), world.enemies)
            cond.pocket = size
            cond.encircled = bool(size is not None and touches)
        elif layer is not None:
            cond.encircled = False
        cond.problems = problems_of(cond)
        out.append(cond)
    out.sort(key=lambda c: (-len(c.problems), -c.out_of_supply_days, c.org_share
                            if c.org_share is not None else 1.0, c.id))
    return out


def low_org(c: Condition) -> bool:
    if c.org_share is not None:
        return c.org_share < LOW_ORG_SHARE
    return c.org is not None and c.org < LOW_ORG_ABSOLUTE


def problems_of(c: Condition) -> list[str]:
    out = []
    if c.encircled:
        out.append("encircled")
    if c.out_of_supply_days >= LONG_OUT_OF_SUPPLY:
        out.append(f"out of supply {c.out_of_supply_days}d")
    elif c.out_of_supply_days:
        out.append("out of supply")
    elif c.supply_share is not None and c.supply_share < LOW_SUPPLY_SHARE:
        out.append("low supply")
    if low_org(c):
        out.append("low organisation")
    if c.strength_share is not None and c.strength_share < LOW_STRENGTH_SHARE:
        out.append("low strength")
    return out


# --- words -------------------------------------------------------------------------------------


def _top_places(items, name_of, n: int = 2) -> str:
    counts: dict[str, int] = {}
    for c in items:
        label = name_of(c.state)
        counts[label] = counts.get(label, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return ", ".join(label for label, _ in ranked)


def brief_lines(conds: list[Condition], name_of) -> list[str]:
    """One line of what is wrong with the army, or nothing when nothing is."""
    if not conds:
        return []
    bits = []
    out = [c for c in conds if c.out_of_supply_days >= LONG_OUT_OF_SUPPLY]
    if out:
        bits.append(f"{len(out)} out of supply {LONG_OUT_OF_SUPPLY}+ days in {_top_places(out, name_of)}")
    cut = [c for c in conds if c.encircled]
    if cut:
        bits.append(f"{len(cut)} encircled in {_top_places(cut, name_of)}")
    low = [c for c in conds if low_org(c)]
    if low:
        bits.append(f"{len(low)} under {LOW_ORG_SHARE:.0%} org")
    thin = [c for c in conds if c.strength_share is not None and c.strength_share < LOW_STRENGTH_SHARE]
    if thin:
        bits.append(f"{len(thin)} under {LOW_STRENGTH_SHARE:.0%} strength")
    fighting = sum(1 for c in conds if c.in_battle)
    head = "  Army condition: " + (", ".join(bits) if bits
                                  else "no division out of supply, cut off or broken")
    if fighting:
        head += f"; {fighting} in battle"
    return [head + " (inspect_world view=divisions)"]


def row(c: Condition, name_of) -> dict:
    out: dict = {"id": c.id, "template": c.template, "state": name_of(c.state), "province": c.province,
                 "terrain": c.terrain}
    if c.org is not None:
        out["org"] = round(c.org, 1)
    if c.org_share is not None:
        out["org_share_est"] = round(c.org_share, 2)
    if c.strength is not None:
        out["strength"] = round(c.strength, 1)
    if c.strength_share is not None:
        out["strength_share_est"] = round(c.strength_share, 2)
    if c.manpower is not None:
        out["manpower"] = f"{c.manpower:.0%}"
    if c.supply_share is not None:
        out["supply"] = f"{c.supply_share:.0%}"
    out["out_of_supply_days"] = c.out_of_supply_days
    if c.dig_in is not None:
        out["dug_in"] = round(c.dig_in, 1)
        if c.dig_in_cap:
            out["dig_in_cap"] = round(c.dig_in_cap, 1)
    if c.experience is not None:
        out["experience_points"] = round(c.experience)
    if c.equipment_fill is not None:
        out["equipment_fill"] = f"{c.equipment_fill:.0%}"
    if c.acclimatisation is not None:
        out["acclimatised"] = f"{c.acclimatisation:.0%}"
    if c.recent_damage:
        out["recent_damage"] = {"str": round(c.recent_damage[0], 1), "org": round(c.recent_damage[1], 1)}
    if c.last_combat:
        out["last_combat"] = c.last_combat
    out["in_battle"] = c.in_battle or False
    if c.encircled is not None:
        out["encircled"] = c.encircled
        if c.encircled and c.pocket:
            out["pocket_provinces"] = c.pocket
    if c.problems:
        out["problems"] = c.problems
    return out
