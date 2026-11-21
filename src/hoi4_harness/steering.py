"""Steering the game's AI where only steering is faithful: production, intelligence, research.

A human's production line, research slot or agency upgrade is a thing the game makes the AI
choose for a country it runs; script has no faithful way to do those for the player (an effect
that adds a research or an agency upgrade skips the time and cost a player pays). What the game
does expose is the AI's own knobs, ``ai_strategy`` blocks (``common/ai_strategy/_documentation.md``),
and the same trick the front orders use works for them: a block in the mod gated on a country
flag, and the inbox sets the flag. One country's flags are its own, so several LLM countries
can steer at once without reading each other's.

Three actions use this module:

* ``steer_production``: how many factories the AI gives an equipment category
  (``equipment_production_factor``), how much of a plane or ship type it builds (``unit_ratio``),
  which building it prefers (``build_building``), how many garrison divisions it keeps
  (``garrison``);
* ``steer_intelligence``: which agency branch it upgrades first
  (``intelligence_agency_branch_desire_factor``), how many factories it spends on the agency
  (``intelligence_agency_usable_factories``), which untargeted operative mission it prefers
  (``operative_mission``). Missions and operations that name a country are directives
  (``spy_on``, ``sabotage``, ``steal_tech``) in ``modgen``;
* ``steer_navy`` and ``steer_air``: where the AI's navy seeks sea control
  (``naval_dominance``, a sea region), whom its raiders hunt (``convoy_raiding_target``), and
  which region's air war it weighs most (``strategic_air_importance``). Region ids are the
  playset's (``map/strategicregions``), so those blocks are generated per playset, like research;
* ``steer_research``: research a technology first (``research_tech``), one block per technology
  of the playset (``llm_bridge_research.txt``, written by ``hoi4-harness mod-probe``).

Not here, with the reason: ``decrypt_target`` and ``activate_crypto`` are named in the
documentation's token list and nowhere else (no syntax, no vanilla use), and ``occupation_policy``
is the same: a block written from a guess would be ignored silently, so they are not offered.
``build_army``, ``build_airplane`` and ``build_ship`` have no documented value scale either
(vanilla only uses ``build_army``, with small integers), so production steering goes through
``equipment_production_factor`` and ``unit_ratio``, which are documented as percentages.

The values are a starting point, not measured: unverified against the live game.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

FILE = "common/ai_strategy/llm_bridge_steering.txt"
REGIONS_FILE = "common/ai_strategy/llm_bridge_steer_regions.txt"
RESEARCH_FILE = "common/ai_strategy/llm_bridge_research.txt"
STANDING = "steering.json"
CLEAR = "clear"

#: Equipment categories (``script_enum_equipment_category``) the AI's production factor takes.
EQUIPMENT = ("infantry", "artillery", "anti_tank", "anti_air", "support", "motorized", "mechanized",
             "armor", "fighter", "heavy_fighter", "interceptor", "cas", "tactical_bomber",
             "strategic_bomber", "naval_bomber", "scout_plane", "maritime_patrol_plane", "convoy",
             "submarine", "screen_ship", "capital_ship", "carrier")
#: ``unit_ratio`` ids: the ship and plane types the game's own AI files weigh.
UNITS = ("screen_ship", "capital_ship", "submarine", "convoy", "carrier", "fighter", "heavy_fighter",
         "interceptor", "cas", "tactical_bomber", "strategic_bomber", "naval_bomber", "scout_plane",
         "cv_fighter", "cv_naval_bomber", "maritime_patrol_plane", "marines")
#: ``build_building`` ids: the buildings the AI can be told to prefer (``common/buildings``).
BUILDINGS = ("industrial_complex", "arms_factory", "dockyard", "infrastructure", "air_base",
             "radar_station", "anti_air_building", "synthetic_refinery", "fuel_silo", "naval_base",
             "supply_node", "air_facility", "land_facility", "naval_facility")
#: ``common/intelligence_agency_upgrades``' branches.
BRANCHES = ("branch_intelligence", "branch_defense", "branch_operation", "branch_operative",
            "branch_crypto")
#: ``script_enum_operative_mission_type``: the missions that name no target.
MISSIONS = ("build_intel_network", "counter_intelligence", "quiet_network", "root_out_resistance",
            "control_trade")


@dataclass(frozen=True)
class Spec:
    strategy: str
    ids: tuple[str, ...]            # () means the strategy takes no id
    levels: dict[str, int]
    field: str = "id"               # the ai_strategy field the id goes in
    what: str = ""
    #: Where the ids come from when the playset supplies them: ``region`` (any strategic region),
    #: ``naval_region`` (a sea region), ``tag`` (a country).
    source: str = ""


AREAS: dict[str, dict[str, Spec]] = {
    "navy": {
        "dominance": Spec("naval_dominance", (), {"less": 20, "more": 60, "much_more": 100},
                          source="naval_region",
                          what="the sea control the AI's navy seeks in that region, in percent"),
        "raiding": Spec("convoy_raiding_target", (), {"more": 50, "much_more": 100}, source="tag",
                        what="how hard the AI's raiders hunt that country's convoys, in percent"),
    },
    "air": {
        "importance": Spec("strategic_air_importance", (), {"more": 10000, "much_more": 40000},
                           source="region",
                           what="how much the AI's air force weighs that region (a held front in "
                                "combat is about 35000)"),
    },
    "production": {
        "equipment": Spec("equipment_production_factor", EQUIPMENT,
                          {"less": -50, "more": 50, "much_more": 150},
                          what="share of the factories the AI gives the category, in percent of what it "
                               "computes it needs (+50: half as many again)"),
        "units": Spec("unit_ratio", UNITS, {"less": -40, "more": 40, "much_more": 100},
                      what="how much of that ship or plane type the AI wants against the others"),
        "building": Spec("build_building", BUILDINGS, {"more": 100, "much_more": 400},
                         what="how strongly the AI picks that building when it chooses what to build"),
        "garrison": Spec("garrison", (), {"less": -50, "more": 50, "much_more": 200},
                         what="how many divisions the AI keeps as garrison in occupied land"),
    },
    "intelligence": {
        "branch": Spec("intelligence_agency_branch_desire_factor", BRANCHES,
                       {"less": -50, "more": 100, "much_more": 500},
                       what="how much the AI wants the next agency upgrade from that branch"),
        "factories": Spec("intelligence_agency_usable_factories", (),
                          {"less": -20, "more": 20, "much_more": 50},
                          what="how many civilian factories the AI spends on the agency"),
        "mission": Spec("operative_mission", MISSIONS, {"more": 500, "much_more": 1000},
                        field="mission",
                        what="how much the AI sends operatives on that mission (no target)"),
    },
}
LEVELS = ("less", "more", "much_more", CLEAR)
FIRST = "first"


def flag(area: str, kind: str, ident: str | None, level: str) -> str:
    return f"llmb_steer_{area}_{kind}_{ident or 'any'}_{level}"


def research_flag(tech: str) -> str:
    return f"llmb_research_{tech}"


def specs(dynamic: bool = False) -> list[tuple[str, str, Spec]]:
    """The steers with a fixed id list (``dynamic=False``) or the ones the playset supplies ids for."""
    return [(area, kind, spec) for area, kinds in AREAS.items() for kind, spec in kinds.items()
            if bool(spec.source) == dynamic]


def regions(playset) -> tuple[set[str], set[str]]:
    """``(every strategic region id, the sea ones)`` of the playset's ``map/strategicregions``."""
    every: set[str] = set()
    sea: set[str] = set()
    for path in playset.layered("map/strategicregions"):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        found = re.search(r"\bid\s*=\s*(\d+)", text)
        if found:
            every.add(found.group(1))
            if "naval_terrain" in text:
                sea.add(found.group(1))
    return every, sea


def known_ids(index) -> dict[str, set[str]]:
    """What the playset has, by steer source, cached on the index."""
    cached = getattr(index, "_steer_known", None)
    if cached is None:
        from .modgen import DEFAULT_TAGS

        every, sea = regions(index.playset)
        # The tags the mod has blocks for (modgen.DEFAULT_TAGS), as for directives.
        cached = {"region": every, "naval_region": sea,
                  "tag": {tag for tag in index.tags if tag in DEFAULT_TAGS}}
        index._steer_known = cached
    return cached


def spec_for(area: str, kind: str) -> Spec | None:
    return AREAS.get(area, {}).get(kind)


def refusal(area: str, kind: str, ident: str | None, level: str,
            known: dict[str, set[str]] | None = None) -> str | None:
    """Why this steer cannot be sent, or None. ``known`` maps a source (``region``,
    ``naval_region``, ``tag``) to the ids the playset has; without it only the shape is checked."""
    spec = spec_for(area, kind)
    if spec is None:
        return f"{kind!r} is not a {area} steer: one of {', '.join(AREAS[area])}"
    if spec.source:
        pattern = r"[A-Z][A-Z0-9]{2}" if spec.source == "tag" else r"\d+"
        what = {"tag": "a country tag", "region": "a strategic region id",
                "naval_region": "a sea region id"}[spec.source]
        if not ident or not re.fullmatch(pattern, str(ident)):
            return f"{kind} needs an id: {what}"
        have = (known or {}).get(spec.source)
        if have and str(ident) not in have:
            return f"{ident!r} is not {what} in this playset"
    elif spec.ids:
        if not ident:
            return f"{kind} needs an id: one of {', '.join(spec.ids)}"
        if ident not in spec.ids:
            return f"{ident!r} is not a {kind} id: one of {', '.join(spec.ids)}"
    elif ident:
        return f"{kind} takes no id"
    if level != CLEAR and level not in spec.levels:
        return f"{kind} has no level {level!r}: one of {', '.join([*spec.levels, CLEAR])}"
    return None


def strategy_text(spec: Spec, ident: str | None, value: int) -> str:
    parts = [f"type = {spec.strategy}"]
    if ident:
        parts.append(f"{spec.field} = {ident}")
    parts.append(f"value = {value}")
    return " ".join(parts)


def _block(name: str, strategies: list[str]) -> list[str]:
    return [
        f"{name} = {{",
        f"\tenable = {{ llm_bridge_is_harness = yes has_country_flag = {name} }}",
        f"\tabort = {{ OR = {{ llm_bridge_is_harness = no NOT = {{ has_country_flag = {name} }} }} }}",
        *(f"\tai_strategy = {{ {s} }}" for s in strategies),
        "}",
        "",
    ]


def strategies() -> str:
    out = [
        "# GENERATED by src/hoi4_harness/steering.py (via modgen). Do not edit by hand.",
        "#",
        "# steer_production / steer_intelligence: one block per (kind, id, level), on while the",
        "# country carries its flag; the harness sets the flag through the inbox. The strategy types",
        "# and their value scales are those of ai_strategy/_documentation.md (1.20).",
        "",
    ]
    for area, kind, spec in specs():
        for ident in spec.ids or (None,):
            for level, value in spec.levels.items():
                name = flag(area, kind, ident, level)
                out += _block(name, [strategy_text(spec, ident, value)])
    return "\n".join(out)


def region_strategies(every: set[str], sea: set[str], tags) -> str:
    """The steers whose ids the playset supplies: strategic regions, sea regions, countries."""
    out = [
        "# GENERATED by src/hoi4_harness/steering.py from the playset's strategic regions and country",
        "# tags (hoi4-harness mod-probe). Do not edit by hand. steer_navy / steer_air: one block per",
        "# (kind, id, level), on while the country carries its flag.",
        "",
    ]
    pools = {"region": sorted(every, key=int), "naval_region": sorted(sea, key=int),
             "tag": sorted(str(t) for t in tags if re.fullmatch(r"[A-Z][A-Z0-9]{2}", str(t)))}
    for area, kind, spec in specs(dynamic=True):
        for ident in pools[spec.source]:
            for level, value in spec.levels.items():
                out += _block(flag(area, kind, ident, level), [strategy_text(spec, ident, value)])
    return "\n".join(out)


def research_strategies(techs) -> str:
    """One research_tech block per technology: on while the country carries the tech's flag."""
    out = [
        "# GENERATED by src/hoi4_harness/steering.py from the playset's technologies (hoi4-harness",
        "# mod-probe). Do not edit by hand. steer_research level=first: the AI researches the",
        "# technology as soon as it can (research_tech, ai_strategy/_documentation.md).",
        "",
    ]
    for tech in sorted({str(t) for t in techs if str(t).replace("_", "").isalnum()}):
        out += _block(research_flag(tech), [f"type = research_tech id = {tech} value = 100"])
    return "\n".join(out)


def effect(area: str, kind: str, ident: str | None, level: str) -> str:
    """The script, in the country's scope, that leaves (kind, id) at ``level`` and nothing else."""
    spec = AREAS[area][kind]
    body = [f"clr_country_flag = {flag(area, kind, ident, lv)}" for lv in spec.levels]
    if level != CLEAR:
        body.append(f"set_country_flag = {flag(area, kind, ident, level)}")
    return " ".join(body)


def research_effect(tech: str, level: str) -> str:
    return (f"clr_country_flag = {research_flag(tech)}"
            + ("" if level == CLEAR else f" set_country_flag = {research_flag(tech)}"))


class Standing:
    """The steers this run has given and the game confirmed, kept in the run folder."""

    def __init__(self, run_dir: Path | None):
        self.path = Path(run_dir) / STANDING if run_dir else None
        self.orders: dict[str, str] = {}
        if self.path and self.path.exists():
            try:
                self.orders = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.orders = {}

    def record(self, key: str, level: str) -> None:
        if level == CLEAR:
            self.orders.pop(key, None)
        else:
            self.orders[key] = level
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.orders, sort_keys=True), encoding="utf-8")

    def line(self) -> str | None:
        if not self.orders:
            return None
        return "  AI steering standing: " + "; ".join(
            f"{key} {level}" for key, level in sorted(self.orders.items()))
