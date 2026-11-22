"""Everything else a player of a country sees on screen, read from the same text save (#139 full).

``world.py`` reads the army, the map and the factories. This module reads the other panels a
human opens: politics, faction, diplomacy, intelligence agency, special projects, research,
navy, air, equipment stockpile, occupation and supply. Each reader takes the parsed block of one
country (the save lays everything under ``countries={ TAG={ ... } }``, key paths checked against
a real 1.20 autosave, see docs/observation.md) and returns plain facts; nothing here decides what
a country may *see* of another. :func:`view` does that, by the same intel bands the army uses:

* **own country**: everything;
* **public** for every country: its faction and members, its wars, guarantees, puppets and
  non-aggression pacts, its ruling party;
* **civilian intel**: party popularity, laws, national spirits (advisors only when exact);
* **army / navy / air intel**: equipment stockpile, fleets, wings (exact, rounded or unknown);
* **never**: another country's agency, operatives, projects, research queue, decisions.

Estimates are rounded, not randomised, so two looks at the same save agree.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from .adapters import savegame as sg
from .world import Intel, _about, band

#: The laws of the economy, trade and manpower screens (``politics.ideas`` lists them with the
#: national spirits and the hired advisors; the three kinds are told apart by token).
LAWS = frozenset({
    "civilian_economy", "early_mobilization", "partial_economic_mobilisation", "war_economy",
    "tot_economic_mobilisation", "total_economic_mobilisation", "export_focus", "limited_exports",
    "closed_economy", "free_trade", "disarmed_nation", "volunteer_only", "limited_conscription",
    "extensive_conscription", "service_by_requirement", "all_adults_serve",
    "scraping_the_barrel", "autarky", "autarkic_economy"})
#: ``mission`` of a task force and ``type`` of an air wing are enums the save stores as numbers.
#: Naval: 0 is no mission, 1 patrol and 3 convoy raiding were checked against a 1941 save (surface
#: fleets spotting; submarines with the convoy count); the rest are the game's order, UNVERIFIED.
NAVAL_MISSIONS = {0: "no mission", 1: "patrol", 2: "strike force", 3: "convoy raiding",
                  4: "convoy escort", 5: "mine laying", 6: "mine sweeping", 7: "training"}
_AIR_TYPES = {"fighter": "fighter", "heavy_fighter": "heavy fighter", "cas": "close air support",
              "tac_bomber": "tactical bomber", "strategic_bomber": "strategic bomber",
              "nav_bomber": "naval bomber", "scout_plane": "recon", "transport_plane": "transport",
              "interceptor": "interceptor", "maritime_patrol_plane": "maritime patrol"}


# --- small helpers ------------------------------------------------------------------------------


def _items(value) -> list:
    """A ``{ a b c }`` list as the parser returns it (under ``_items``), or a repeated key."""
    if isinstance(value, dict):
        return list(value["_items"]) if "_items" in value else ([value] if value else [])
    return sg._as_list(value)


def _strs(value) -> list[str]:
    """The strings of a ``{ a b c }`` list; an empty ``{ }`` and anything that is not text drop out."""
    return [x for x in _items(value) if isinstance(x, str)]


def _num(value, default: float = 0.0) -> float:
    found = sg._number(value)
    return default if found is None else found


def _int(value) -> int:
    return int(_num(value))


def pretty(token) -> str:
    """``GER_goebbels_reich_minister_of_propaganda`` -> ``Goebbels reich minister of propaganda``."""
    text = str(token or "")
    text = re.sub(r"^[A-Z][A-Z0-9]{2}_", "", text)
    text = text.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else "?"


def person(token) -> str:
    """A character's name from its token: ``GER_karl_heinrich_becker`` -> ``Karl Heinrich Becker``."""
    return pretty(token).title()


def _dig(value, *path):
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def top(text: str, spans: dict, key: str) -> dict | None:
    """One top-level block, parsed from the span the layout scan found (no brace matching over
    millions of lines); falls back to ``top_block`` when the scan did not see the key."""
    found = spans.get(key) if spans else None
    if not found:
        return sg.top_block(text, key)
    start, end = found[0]
    return sg.parse_clausewitz(text[start:end]).get(key)


# --- characters: advisors, scientists, leaders ----------------------------------------------------


_AFTER_CHARACTERS = re.compile(r"^(?:rail_way|power_balance|entity|equipment_market|raids|faction_system|"
                               r"doctrine|countries)[ \t]*=[ \t]*\{", re.M)


class Characters:
    """``character_manager`` of the save, indexed lazily: 370,000 lines in a 1936 save, of which a
    country needs a few dozen characters. The index maps a character id to where its block starts."""

    def __init__(self, text: str, spans: dict | None = None):
        self.text = text
        self._start = self._end = 0
        self._ids: list[int] = []
        self._offsets: list[int] = []
        self._at: dict[int, int] = {}
        self._tokens: dict[str, int] = {}
        self._cache: dict[int, dict] = {}
        match = re.search(r"^character_manager[ \t]*=[ \t]*\{", text, re.M)
        if not match:
            return
        # the block ends where the next known top-level key starts (see _AFTER_COUNTRIES for why the
        # layout scan's own spans are not trusted); brace matching is the fallback
        after = _AFTER_CHARACTERS.search(text, match.end())
        start = match.start()
        end = after.start() if after else sg.matching_brace(text, match.end() - 1)
        self._start, self._end = start, end
        for m in re.finditer(r"^\t+id=\{ id=(\d+) type=73 \}[ \t]*$", text[start:end], re.M):
            self._at[int(m.group(1))] = start + m.start()
        self._ids = sorted(self._at, key=self._at.get)
        self._offsets = [self._at[i] for i in self._ids]

    def get(self, ident) -> dict:
        try:
            key = int(ident)
        except (TypeError, ValueError):
            return {}
        if key in self._cache:
            return self._cache[key]
        at = self._at.get(key)
        block: dict = {}
        if at is not None:
            opener = self.text.rfind("character={", self._start, at)
            if opener >= 0:
                brace = opener + len("character=")
                chunk = self.text[opener: sg.matching_brace(self.text, brace)]
                block = sg.parse_clausewitz(chunk).get("character") or {}
        self._cache[key] = block if isinstance(block, dict) else {}
        return self._cache[key]

    def advisor_by_token(self, token: str) -> dict | None:
        """The character whose ``advisor.idea_token`` is ``token``, if any."""
        if not self._offsets:
            return None
        if not self._tokens:
            self._tokens = {"": 0}
            for m in re.finditer(r'idea_token="([^"]+)"', self.text[self._start:self._end]):
                at = self._start + m.start()
                slot = bisect.bisect_right(self._offsets, at) - 1
                if slot >= 0:
                    self._tokens.setdefault(m.group(1), self._ids[slot])
        ident = self._tokens.get(token)
        return self.get(ident) if ident else None


def _advisor(block: dict) -> dict:
    for entry in sg._as_list(_dig(block, "advisors", "advisor")):
        if isinstance(entry, dict):
            return {"slot": entry.get("slot"), "traits": [pretty(t) for t in _strs(entry.get("traits"))]}
    return {}


def _scientist(block: dict) -> dict:
    sci = block.get("scientist") if isinstance(block, dict) else None
    if not isinstance(sci, dict):
        return {}
    skills = {}
    for name, entry in (sci.get("skills") or {}).items():
        level = _int(entry.get("level")) if isinstance(entry, dict) else 0
        if level:
            skills[name.replace("specialization_", "")] = level
    return {"traits": [pretty(t.replace("scientist_trait_", "")) for t in _strs(sci.get("traits"))],
            "skills": skills}


# --- one country --------------------------------------------------------------------------------


@dataclass
class FullCountry:
    """What the save holds of one country beyond its divisions. Raw facts, not yet filtered."""

    tag: str
    politics: dict = field(default_factory=dict)
    ideas: dict = field(default_factory=dict)
    relations: dict[str, dict] = field(default_factory=dict)
    wargoals: list[dict] = field(default_factory=list)
    fleets: list[dict] = field(default_factory=list)
    air: list[dict] = field(default_factory=list)
    stockpile: dict[str, int] = field(default_factory=dict)
    in_service: dict[str, int] = field(default_factory=dict)
    agency: dict = field(default_factory=dict)
    projects: dict = field(default_factory=dict)
    research: dict = field(default_factory=dict)
    doctrines: list[dict] = field(default_factory=list)
    decisions: dict = field(default_factory=dict)
    economy: dict = field(default_factory=dict)
    trade: list[dict] = field(default_factory=list)
    supply: dict = field(default_factory=dict)
    mios: list[str] = field(default_factory=list)
    commanders: list[dict] = field(default_factory=list)


def read_politics(block: dict, chars: Characters | None) -> tuple[dict, dict]:
    politics = block.get("politics") if isinstance(block.get("politics"), dict) else {}
    parties: dict[str, float] = {}
    leaders: dict[str, str] = {}
    for ideology, party in (politics.get("parties") or {}).items():
        if not isinstance(party, dict):
            continue
        parties[ideology] = _num(party.get("popularity"))
        for entry in _items(party.get("country_leader")):
            if isinstance(entry, dict):
                ref = entry.get("character")
                found = chars.get(sg._ident(ref)) if chars is not None else {}
                leaders[ideology] = (f"{person(found.get('token') or found.get('name') or sg._ident(ref))}"
                                     f" ({entry.get('ideology', ideology)})")
                break
    out = {"ruling_party": politics.get("ruling_party"), "parties": parties, "leaders": leaders,
           "political_power": round(_num(politics.get("political_power")), 1),
           "elections_allowed": politics.get("elections_allowed") == "yes",
           "last_election": politics.get("last_election"),
           "election_frequency_months": _int(politics.get("election_frequency")) or None}
    laws, spirits, advisors = [], [], []
    for token in _items(politics.get("ideas")):
        token = str(token)
        char = chars.advisor_by_token(token) if chars is not None and token not in LAWS else None
        if token in LAWS:
            laws.append(token)
        elif char is not None:
            info = _advisor(char)
            advisors.append({"name": person(char.get("token") or token), "slot": info.get("slot"),
                             "traits": info.get("traits", [])})
        else:
            spirits.append(token)
    return out, {"laws": laws, "advisors": advisors, "spirits": spirits}


def _owner_first(pair: dict, tag: str) -> bool:
    return isinstance(pair, dict) and pair.get("first") == tag


def read_relations(block: dict, tag: str) -> tuple[dict[str, dict], list[dict]]:
    """``diplomacy.active_relations``: one entry per other country; only those carrying a treaty, a
    war or a notable opinion are kept (the save lists all ~440)."""
    diplomacy = block.get("diplomacy") if isinstance(block.get("diplomacy"), dict) else {}
    out: dict[str, dict] = {}
    for other, rel in (diplomacy.get("active_relations") or {}).items():
        if not isinstance(rel, dict) or other == tag:
            continue
        item: dict = {}
        opinion = sg._number(rel.get("cached_sum"))
        if opinion is not None:
            item["opinion"] = round(opinion)
        if isinstance(rel.get("attitude"), str):
            item["attitude"] = rel["attitude"].replace("attitude_", "")
        for key, label in (("guarantee", "guarantee"), ("non_aggression_pact", "non_aggression_pact"),
                           ("military_access", "military_access"), ("docking_rights", "docking_rights"),
                           ("air_base_access", "air_base_access"), ("lend_lease", "lend_lease"),
                           ("puppet", "puppet")):
            pair = rel.get(key)
            if isinstance(pair, dict):
                item[label] = {"direction": "to" if _owner_first(pair, tag) else "from",
                               "since": pair.get("start_date"),
                               **({"autonomy": pair["autonomy_state"].replace("autonomy_", "")}
                                  if isinstance(pair.get("autonomy_state"), str) else {}),
                               **({"until": pair["end_date"]} if pair.get("end_date") else {})}
        for key in ("trade", "trade_equipment"):
            if isinstance(rel.get(key), str):
                item[key] = rel[key]
        if isinstance(rel.get("market_access_rights"), dict):
            item["market_access"] = True
        war = rel.get("war_relation")
        if isinstance(war, dict):
            mine = war.get("first") == tag
            ours = war.get("war_score_first_vs_second" if mine else "war_score_second_vs_first") or {}
            theirs = war.get("war_score_second_vs_first" if mine else "war_score_first_vs_second") or {}
            item["war"] = {
                "since": war.get("start_date"),
                "instigator": bool(war.get("first_was_instigator") == "yes") == mine,
                "casualties_ours": _int(war.get("first_casualties" if mine else "second_casualties")),
                "casualties_theirs": _int(war.get("second_casualties" if mine else "first_casualties")),
                "score_ours": _score(ours), "score_theirs": _score(theirs)}
        notable = (set(item) - {"opinion", "attitude"}) or abs(item.get("opinion", 0)) >= 50
        if notable:
            out[other] = item
    goals = []
    wg = diplomacy.get("wargoals") if isinstance(diplomacy.get("wargoals"), dict) else {}
    for kind, entries in wg.items():
        for entry in sg._as_list(entries):
            if isinstance(entry, dict):
                goals.append({"type": kind, "target": entry.get("wargoaldata_recipient"),
                              "states": [_int(s) for s in _items(_dig(entry, "states"))],
                              **({"expires": entry["expire"]} if entry.get("expire") else {})})
    return out, goals


def _score(parts: dict) -> dict:
    keep = ("province_capture", "equipment_damage", "air_damage_str", "sunk_ship", "convoy_attack",
            "casualties", "strategic_air")
    return {k: round(_num(parts.get(k)), 1) for k in keep if _num(parts.get(k))}


def read_agency(block: dict, chars: Characters | None, scientists_by_id=None) -> dict:
    agency = block.get("intelligence_agency")
    if not isinstance(agency, dict):
        return {}
    operatives = []
    for op in sg._as_list(agency.get("operative")):
        if not isinstance(op, dict):
            continue
        mission = {}
        for kind, detail in (op.get("mission") or {}).items():
            mission = {"type": kind, **(detail if isinstance(detail, dict) else {})}
        operatives.append({"name": pretty(op.get("name")), "skill": _int(op.get("skill")),
                           "traits": [pretty(t.replace("operative_", "")) for t in _strs(op.get("traits"))],
                           "state": op.get("state") or "?", "mission": mission or None,
                           "nationalities": _strs(op.get("nationalities"))})
    pool = []
    for op in _items(_dig(agency, "recruitment", "recruitable_operatives")):
        if isinstance(op, dict):
            pool.append({"name": pretty(op.get("name")), "skill": _int(op.get("skill")),
                         "traits": [pretty(t.replace("operative_", "")) for t in _strs(op.get("traits"))]})
    upgrades = {k.replace("upgrade_", ""): _int(v) for k, v in (agency.get("upgrades") or {}).items()
                if _int(v)}
    captured = [{"country": c.get("country"), "intel_gained": {
        k: round(_num(v), 1) for k, v in (c.get("intel") or {}).items()}}
        for c in _items(agency.get("captured")) if isinstance(c, dict)]
    targets = [{"target": t.get("target"), "amount": _int(t.get("amount"))}
               for t in _items(_dig(agency, "cryptology", "targets")) if isinstance(t, dict)]
    return {"name": agency.get("name"), "created": agency.get("is_created") == "yes",
            "operative_slots": _int(agency.get("max_operative_count")),
            "usable_slots": _int(agency.get("usable_operative_slots")),
            "counter_intelligence": round(_num(agency.get("defense")), 2),
            "upgrades": upgrades, "upgrade_in_progress": agency.get("upgrade") if isinstance(
                agency.get("upgrade"), str) else None,
            "upgrade_progress": _int(agency.get("upgrade_progress")) or None,
            "operatives": operatives, "recruitable": pool, "captured": captured,
            "decrypting": targets,
            "operations_done": {pretty(k.replace("operation_", "")): len(_items(v)) // 2
                                for k, v in (_dig(block, "operations", "finished") or {}).items()}}


def read_projects(block: dict, chars: Characters | None, province_state=None) -> dict:
    status = block.get("program_status")
    if not isinstance(status, dict):
        return {}
    active, done, idle = [], [], 0
    for name, project in ((_dig(status, "project_pool", "project")) or {}).items():
        if not isinstance(project, dict):
            continue
        if project.get("completed") == "yes":
            done.append(name)
        elif project.get("start") == "yes":
            progress = project.get("progress") if isinstance(project.get("progress"), dict) else {}
            proto = progress.get("prototype_state")
            proto = proto if isinstance(proto, dict) else {}
            active.append({"project": name, "phase": progress.get("current_state"),
                           "progress": round(_num(proto.get("project_progress")), 2),
                           "iterations": _int(proto.get("iterations"))})
        else:
            idle += 1
    facilities = []
    for prog in sg._as_list(status.get("program")):
        if not isinstance(prog, dict):
            continue
        sci = prog.get("scientist")
        who = {}
        if isinstance(sci, dict) and chars is not None:
            char = chars.get(sg._ident(sci.get("character") or sci.get("id")))
            who = {"name": person(char.get("token")), **_scientist(char)} if char else {}
        province = _int(prog.get("province"))
        facilities.append({"type": prog.get("building"), "province": province,
                           "state": province_state(province) if province_state else None,
                           **({"scientist": who} if who else {})})
    return {"active": active, "completed": done, "not_started": idle, "facilities": facilities,
            "breakthrough": {k.replace("specialization_", ""): _int(v)
                             for k, v in (status.get("breakthrough") or {}).items()}}


_RANKS = ("field_marshal", "corps_commander", "navy_leader", "admiral", "general")


def read_commanders(block: dict, chars: Characters | None) -> list[dict]:
    """The country's unit leaders: ``characters.character_status`` marks them, and the character
    holds the rank block (``field_marshal``, ``corps_commander``, ``navy_leader``) with skills."""
    out = []
    if chars is None:
        return out
    for entry in sg._as_list(_dig(block, "characters", "character_status")):
        if not isinstance(entry, dict) or entry.get("unit_leader") != "yes":
            continue
        char = chars.get(sg._ident(entry.get("character")))
        for rank in _RANKS:
            leader = char.get(rank) if char else None
            if isinstance(leader, dict):
                out.append({"name": person(char.get("token")), "rank": rank.replace("_", " "),
                            "skill": _int(leader.get("skill")), "attack": _int(leader.get("attack_skill")),
                            "defense": _int(leader.get("defense_skill")),
                            "planning": _int(leader.get("planning_skill")),
                            "logistics": _int(leader.get("logistics_skill")),
                            "traits": [pretty(t) for t in _strs(leader.get("traits"))]})
                break
    return sorted(out, key=lambda c: (-c["skill"], c["name"]))


def read_scientists(block: dict, chars: Characters | None) -> list[dict]:
    out = []
    if chars is None:
        return out
    for entry in sg._as_list(_dig(block, "characters", "character_status")):
        if isinstance(entry, dict) and entry.get("scientist") == "yes":
            char = chars.get(sg._ident(entry.get("character")))
            if char:
                out.append({"name": person(char.get("token")), **_scientist(char)})
    return out


def read_research(block: dict) -> dict:
    """Busy slots are keys of ``technology.slots``; the points so far sit on the technology's own
    entry (``research_points``, no ``level`` yet). Finished ones carry ``level`` and a date."""
    tech = block.get("technology") if isinstance(block.get("technology"), dict) else {}
    slots = tech.get("slots") if isinstance(tech.get("slots"), dict) else {}
    entries = tech.get("technologies") if isinstance(tech.get("technologies"), dict) else {}
    busy = []
    for name, entry in slots.items():
        if name != "empty" and isinstance(entry, dict):
            done = entries.get(name) if isinstance(entries.get(name), dict) else {}
            busy.append({"technology": name, "points": round(_num(done.get("research_points")), 1),
                         **({"design_team": pretty(done["locked_design_team"])}
                            if str(done.get("locked_design_team", "none")).lower() != "none" else {})})
    done = [(name, entry.get("date") or "") for name, entry in entries.items()
            if isinstance(entry, dict) and "level" in entry]
    recent = sorted(done, key=lambda kv: tuple(int(x) for x in re.findall(r"\d+", kv[1])[:3]) or (0,),
                    reverse=True)[:8]
    idle = len(sg._as_list(slots.get("empty")))
    return {"slots_total": _int(block.get("research_slot")) or len(busy) + idle,
            "researching": busy, "idle_slots": idle,
            "researched": len(done), "latest": [n for n, _ in recent]}


def read_doctrines(doctrine_top: dict | None, index: int | None) -> list[dict]:
    """The top-level ``doctrine={ countries={ {folder={...}} {...} } }`` list: one entry per country
    in the country order, 1-based (entry 0 is a placeholder; GER is 1, ENG 2 as everywhere)."""
    if not isinstance(doctrine_top, dict) or index is None:
        return []
    countries = _items(_dig(doctrine_top, "countries"))
    if index >= len(countries) or not isinstance(countries[index], dict):
        return []
    out = []
    for folder in _items(countries[index].get("folder")):
        if not isinstance(folder, dict) or not folder.get("grand_doctrine"):
            continue
        out.append({"folder": folder.get("folder"), "doctrine": folder.get("grand_doctrine"),
                    "tracks": [{"sub_doctrine": t.get("sub_doctrine"),
                                "mastery": round(_num(t.get("mastery"))), "rewards": _int(t.get("rewards"))}
                               for t in _items(folder.get("tracks")) if isinstance(t, dict)
                               and t.get("sub_doctrine")]})
    return out


def read_decisions(block: dict) -> dict:
    status = block.get("decision_status") if isinstance(block.get("decision_status"), dict) else {}
    timed = [{"decision": e.get("decision"), "days_left": _int(e.get("days"))}
             for e in sg._as_list(status.get("active_timed_decision")) if isinstance(e, dict)]
    targeted = [e for e in sg._as_list(status.get("active_targeted_decision")) if isinstance(e, dict)]
    focus = block.get("focus") if isinstance(block.get("focus"), dict) else {}
    return {"timed_active": timed,
            "targeted_available": sorted({e.get("decision") for e in targeted
                                          if e.get("state") == "available" and e.get("decision")}),
            "focus_tree": block.get("focus_tree"), "focus_current": focus.get("current"),
            "focus_progress": round(_num(focus.get("progress")), 1) if focus.get("progress") else None,
            "focuses_completed": len(sg._as_list(focus.get("completed")))}


def _by_amount(amounts: dict[str, int]) -> dict[str, int]:
    return dict(sorted(amounts.items(), key=lambda kv: -kv[1]))


def read_equipment(block: dict, names: dict[str, str]) -> tuple[dict[str, int], dict[str, int]]:
    """The stockpile is ``production.equipments.equipment`` (id, amount): the key path nothing else
    in the country block uses for amounts of equipment. What divisions hold is on each division."""
    stock: dict[str, int] = {}
    for entry in sg._as_list(_dig(block, "production", "equipments", "equipment")):
        if isinstance(entry, dict):
            name = names.get(sg._ident(entry.get("id")))
            if name:
                stock[name] = stock.get(name, 0) + _int(entry.get("amount"))
    held: dict[str, int] = {}
    for d in sg._as_list(_dig(block, "units", "division")):
        for entry in sg._as_list(_dig(d, "equipment", "equipment")):
            if isinstance(entry, dict):
                name = names.get(sg._ident(entry.get("id")))
                if name:
                    held[name] = held.get(name, 0) + _int(entry.get("amount"))
    return _by_amount(stock), _by_amount(held)


def read_fleets(block: dict, layer=None) -> list[dict]:
    out = []
    for fleet in sg._as_list(_dig(block, "units", "fleet")):
        if not isinstance(fleet, dict):
            continue
        for tf in sg._as_list(fleet.get("task_force")):
            if not isinstance(tf, dict):
                continue
            ships: dict[str, int] = {}
            org, strength, count = 0.0, 0.0, 0
            for ship in sg._as_list(tf.get("ship")):
                if isinstance(ship, dict):
                    kind = str(ship.get("definition") or "?")
                    ships[kind] = ships.get(kind, 0) + 1
                    org += _num(ship.get("organisation"))
                    strength += _num(ship.get("strength"))
                    count += 1
            mission = _int(_dig(tf, "mission", "mission"))
            location = _int(tf.get("location"))
            out.append({"fleet": fleet.get("name"), "task_force": tf.get("name"), "ships": ships,
                        "ship_count": count, "location": location,
                        "state": layer.state_of(location) if layer is not None else None,
                        "mission": NAVAL_MISSIONS.get(mission, f"mission {mission}"),
                        "in_combat": bool(tf.get("last_combat_date") and not str(
                            tf.get("last_combat_date")).startswith("1.1.1")),
                        "avg_org": round(org / count, 1) if count else None,
                        "avg_strength": round(strength / count, 1) if count else None,
                        "home_base": _int(fleet.get("home_base")) or None})
    return out


def read_air(air_top: dict | None, tag: str, names: dict[str, str], base_state: dict[int, int]) -> list[dict]:
    pools = sg._as_list(air_top.get(tag).get("air_wing_pool")) if isinstance(
        air_top, dict) and isinstance(air_top.get(tag), dict) else []
    out = []
    for pool in pools:
        if not isinstance(pool, dict):
            continue
        base = _int(sg._ident(pool.get("air_base")))
        for wing in sg._as_list(pool.get("air_wings")):
            if not isinstance(wing, dict):
                continue
            mission = wing.get("mission") if isinstance(wing.get("mission"), dict) else {}
            active = mission.get("active") == "yes"
            kind = str(pool.get("definition") or "?")
            out.append({"wing": wing.get("name"), "type": _AIR_TYPES.get(kind, kind.replace("_", " ")),
                        "planes": _int(wing.get("count")), "air_base": base, "state": base_state.get(base),
                        "mission": (mission.get("executing_mission") or "idle") if active else "idle",
                        "region": _int(mission.get("strategic_region")) or None,
                        "experience": round(_num(wing.get("experience")), 1)})
    return out


def read_economy(block: dict, states_owned: list) -> dict:
    fuel = block.get("fuel_status") if isinstance(block.get("fuel_status"), dict) else {}
    convoys = sum(_int(e.get("amount")) for e in sg._as_list(_dig(block, "convoys", "equipment", "equipment"))
                  if isinstance(e, dict))
    nukes = sum(_int(e.get("amount")) for e in _items(_dig(block, "nukes")) if isinstance(e, dict))
    mp_free = sum(s.manpower for s in states_owned)
    mp_locked = sum(s.locked for s in states_owned)
    out = {"political_power": round(_num(_dig(block, "politics", "political_power")), 1),
           "command_power": round(_num(block.get("command_power")), 1),
           "fuel": {"stock": round(_num(fuel.get("fuel"))), "max": round(_num(fuel.get("max_fuel"))),
                    "gain": _num(fuel.get("fuel_gain")), "cost": _num(fuel.get("fuel_cost"))},
           "manpower": {"available": mp_free, "population_not_yet_recruitable": mp_locked},
           "convoys": convoys, "nukes": nukes, "ships": _int(block.get("num_ships")),
           "occupation_law": _dig(block, "occupation_status", "default_law"),
           "stability_base": round(_num(block.get("stability")) * 100, 1),
           "war_support_base": round(_num(block.get("war_support")) * 100, 1)}
    return out


def read_trade(block: dict) -> list[dict]:
    routes = _dig(block, "resources", "delivery_routes") or {}
    out = []
    for other, route in routes.items():
        if isinstance(route, dict) and route.get("sender") != route.get("receiver"):
            out.append({"partner": other if route.get("receiver") == other else route.get("receiver"),
                        "direction": "export" if route.get("sender") == block.get("_tag") else "route",
                        "sea": route.get("type") == "2", "from_state": _int(route.get("from_state")) or None,
                        "to_state": _int(route.get("to_state")) or None})
    return out


def read_mios(block: dict) -> list[str]:
    orgs = _dig(block, "production", "industrial_organisations") or {}
    return sorted(pretty(k) for k, v in orgs.items() if isinstance(v, dict))


def read_country(block: dict, tag: str, *, names: dict[str, str], chars: Characters | None,
                 air_top: dict | None, base_state: dict[int, int], states_owned: list,
                 layer=None, doctrine_top=None, index: int | None = None, province_state=None,
                 own: bool = False) -> FullCountry:
    """All facts of one country. ``own`` also reads what only the country itself can see."""
    block = dict(block)
    block["_tag"] = tag
    politics, ideas = read_politics(block, chars)
    relations, wargoals = read_relations(block, tag)
    stock, held = read_equipment(block, names)
    full = FullCountry(tag, politics, ideas, relations, wargoals, read_fleets(block, layer),
                       read_air(air_top, tag, names, base_state), stock, held)
    full.economy = read_economy(block, states_owned)
    if own:
        full.agency = read_agency(block, chars)
        full.projects = read_projects(block, chars, province_state)
        full.projects["scientists"] = read_scientists(block, chars)
        full.research = read_research(block)
        full.doctrines = read_doctrines(doctrine_top, index)
        full.decisions = read_decisions(block)
        full.trade = read_trade(block)
        full.mios = read_mios(block)
        full.commanders = read_commanders(block, chars)
    return full


# --- the world's shared parts -----------------------------------------------------------------------


#: What follows ``countries={ ... }`` at the top level of a save. The layout scan cannot find the
#: end of ``countries`` by itself: a real save writes some nested blocks at column 0 (``CAN={``
#: inside ENG's), which that scan reads as top-level keys and cuts the block short at.
_AFTER_COUNTRIES = re.compile(r"^(?:faction|strategic_air|peace_conference|unit_leader|weather|"
                              r"supply_system_2|threat|all_playthrough_data)[ \t]*=", re.M)


def country_order(text: str, spans: dict | None = None) -> list[str]:
    """Tags in the order the save lists them, which is the country index every per-country array
    (doctrines, intelligence networks, faction influence) uses: GER is 1, ENG 2, SOV 3 ..."""
    match = re.search(r"^countries[ \t]*=[ \t]*\{", text, re.M)
    if not match:
        return []
    begin = match.end()
    after = _AFTER_COUNTRIES.search(text, begin)
    end = after.start() if after else sg.matching_brace(text, begin - 1)
    first = re.compile(r"^([ \t]*)[A-Za-z0-9_\-]+[ \t]*=", re.M).search(text, begin)
    if not first:
        return []
    pattern = re.compile(rf"^{re.escape(first.group(1))}([A-Za-z0-9_]{{3}})[ \t]*=[ \t]*\{{", re.M)
    return [m.group(1) for m in pattern.finditer(text, begin, end)]


def read_factions(text: str, spans: dict | None = None) -> list[dict]:
    """The top-level ``faction={ ... }`` blocks (a handful, small)."""
    out = []
    for start, end in (spans if spans is not None else sg.top_level_spans(text)).get("faction", []):
        block = sg.parse_clausewitz(text[start:end]).get("faction")
        if not isinstance(block, dict):
            continue
        members = [str(m) for m in _strs(block.get("members"))]
        rules = _strs(_dig(block, "rule_status", "rules"))
        goals = [{"goal": pretty(g.get("goal").replace("faction_goal_", "")), "status": g.get("status")}
                 for g in _items(_dig(block, "goal_status", "goals")) if isinstance(g, dict)
                 and g.get("goal") and g.get("goal") != _dig(block, "goal_status", "manifest")]
        slots = [re.sub(r"[\x00-\x1f]", "", str(_dig(s, "spymaster", "name") or
                                                 _dig(s, "modifier", "name") or ""))
                 for s in _items(_dig(block, "upgrades", "slots")) if isinstance(s, dict)]
        slots = [("Spymaster" if n == "HSpymaster!" else n) for n in slots]
        out.append({"template": str(block.get("template", "")).replace("faction_template_", ""),
                    "ideology": block.get("ideology"), "leader": members[0] if members else None,
                    "members": members, "since_leader_change": block.get("faction_leader_change_date"),
                    "manifest": pretty(str(_dig(block, "goal_status", "manifest") or "").replace(
                        "faction_manifest_", "")),
                    "goals": goals, "rules": [pretty(r) for r in rules if isinstance(r, str)],
                    "upgrade_slots": [s for s in slots if s],
                    "research_sharing": block.get("research") == "yes",
                    "manpower_sharing": block.get("manpower") == "yes",
                    "tech_sharing_group": [str(c) for c in _items(
                        _dig(block, "upgrades", "tech_sharing_group", "countries"))]})
    return out


def read_networks(operatives_top: dict | None, index: int | None) -> list[dict]:
    """``strategic_operatives.strategic_operative.<country index>.intel_network``: per target the
    nodes (states) of the network and the strength of each, 0-100."""
    entry = _dig(operatives_top, "strategic_operative", str(index)) if index is not None else None
    out = []
    for net in sg._as_list(entry.get("intel_network")) if isinstance(entry, dict) else []:
        inner = net.get("intel_network") if isinstance(net, dict) else None
        if not isinstance(inner, dict):
            continue
        strengths = [_num(v) for v in _items(inner.get("strength"))]
        states = [_int(v) for v in _items(inner.get("state_id"))]
        if not strengths:
            continue
        out.append({"target": net.get("target"), "nodes": len(strengths),
                    "nodes_active": sum(1 for s in strengths if s > 0),
                    "strength": round(sum(strengths) / len(strengths), 1),
                    "states_covered": [s for s, v in zip(states, strengths, strict=False) if v > 0][:12]})
    return sorted(out, key=lambda n: -n["strength"])


def read_supply(top: dict | None, tag: str) -> dict:
    """``supply_system_2.countries.<tag>``: trucks, the supply buffer and what attrition and raids
    have cost the railways. Per-province supply flow is not stored; only these totals are."""
    block = _dig(top, "countries", tag)
    if not isinstance(block, dict):
        return {}

    def total(key: str) -> int:
        return int(sum(_num(v) for v in _items(block.get(key))))

    return {"trucks": sum(_int(t.get("count")) for t in _items(block.get("truck")) if isinstance(t, dict)),
            "trucks_wanted": _int(block.get("wanted_supply_trucks")), "buffer": _num(block.get("buffer")),
            "railways_lost_recent": total("lost_railways"), "trains_lost_recent": total("lost_trains"),
            "trucks_lost_attrition_recent": total("lost_trucks_attrition"),
            "trucks_lost_killed_recent": total("lost_trucks_killed")}


def read_air_bases(air_top: dict | None) -> dict[int, int]:
    return {_int(sg._ident(b.get("id"))): _int(b.get("state")) for b in sg._as_list(
        air_top.get("air_base") if isinstance(air_top, dict) else None)
        if isinstance(b, dict) and b.get("state")}


@dataclass
class FullWorld:
    order: list[str] = field(default_factory=list)
    factions: list[dict] = field(default_factory=list)
    countries: dict[str, FullCountry] = field(default_factory=dict)
    networks: list[dict] = field(default_factory=list)
    #: Parsed pieces of the save the per-country readers share (not for the model).
    chars: Characters | None = None
    air_top: dict | None = None
    base_state: dict[int, int] = field(default_factory=dict)
    doctrine_top: dict | None = None
    #: The own country's supply system (trucks, trains lost), from ``supply_system_2``.
    supply: dict = field(default_factory=dict)

    def faction_of(self, tag: str) -> dict | None:
        return next((f for f in self.factions if tag in f["members"]), None)


# --- what the country can see --------------------------------------------------------------------------


def _band(intel: dict[str, Intel], tag: str, kind: str) -> tuple[str, bool]:
    got = intel.get(tag)
    assumed = got is None
    value = getattr(got, kind, None) if got else None
    if value is None:
        from .world import DEFAULT_INTEL

        value = DEFAULT_INTEL
        assumed = True
    return band(value), assumed


def _round5(value: float) -> int:
    return int(round(value / 5.0) * 5)


def view_faction(full: FullWorld, me: str, tag: str | None) -> dict:
    tag = (tag or me).upper()
    faction = full.faction_of(tag)
    if faction is None:
        return {"view": "faction", "tag": tag, "faction": None, "note": f"{tag} is in no faction"}
    out = {"view": "faction", "tag": tag, **faction}
    if tag != me and me not in faction["members"]:
        # a faction's goals, rules and perks are on its page for anyone; the research and
        # manpower sharing and the agency slots are for members
        for key in ("upgrade_slots", "tech_sharing_group", "research_sharing", "manpower_sharing"):
            out.pop(key, None)
    return out


def view_diplomacy(full: FullWorld, me: str, tag: str | None, intel, limit: int) -> dict:
    tag = (tag or me).upper()
    country = full.countries.get(tag)
    if country is None:
        return {"view": "diplomacy", "tag": tag, "known": False,
                "note": "not a neighbour, an enemy, a faction partner or the person's country in this save"}
    wars = {other: rel["war"] for other, rel in country.relations.items() if "war" in rel}
    treaties = {other: {k: v for k, v in rel.items() if k not in ("opinion", "attitude", "war")}
                for other, rel in country.relations.items()
                if set(rel) - {"opinion", "attitude", "war"}}
    out = {"view": "diplomacy", "tag": tag, "wars": wars,
           "treaties": dict(list(treaties.items())[:limit]),
           "faction": (full.faction_of(tag) or {}).get("members")}
    if tag == me:
        out["opinions"] = {other: {"opinion": rel.get("opinion"), "attitude": rel.get("attitude")}
                           for other, rel in country.relations.items() if "opinion" in rel
                           and abs(rel["opinion"]) >= 50}
        out["war_goals"] = country.wargoals
    else:
        mine = (full.countries.get(me).relations if me in full.countries else {}).get(tag, {})
        out["towards_you"] = {k: mine[k] for k in ("opinion", "attitude") if k in mine}
    return out


def view_politics(full: FullWorld, me: str, tag: str | None, intel) -> dict:
    tag = (tag or me).upper()
    country = full.countries.get(tag)
    if country is None:
        return {"view": "politics", "tag": tag, "known": False}
    pol, ideas = country.politics, country.ideas
    if tag == me:
        return {"view": "politics", "tag": tag, **pol, **ideas,
                "economy_law": [law for law in ideas["laws"]]}
    civ, assumed = _band(intel, tag, "civilian")
    out = {"view": "politics", "tag": tag, "ruling_party": pol.get("ruling_party"),
           "intel": civ + (" (assumed)" if assumed else "")}
    if civ == "exact":
        out.update(parties=pol["parties"], leaders=pol["leaders"], laws=ideas["laws"],
                   spirits=ideas["spirits"], advisors=ideas["advisors"])
    elif civ == "estimate":
        out.update(parties={k: _round5(v) for k, v in pol["parties"].items()}, laws=ideas["laws"])
    return out


def _count_types(items: list[dict], key: str, weight: str | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        values = item[key] if isinstance(item[key], dict) else {item[key]: item[weight] if weight else 1}
        for name, n in values.items():
            if n:
                out[name] = out.get(name, 0) + n
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def view_navy(full: FullWorld, me: str, tag: str | None, intel, limit: int) -> dict:
    tag = (tag or me).upper()
    country = full.countries.get(tag)
    if country is None:
        return {"view": "navy", "tag": tag, "known": False}
    fleets = country.fleets
    ships = _count_types(fleets, "ships")
    total = sum(ships.values())
    if tag == me:
        return {"view": "navy", "tag": tag, "ships": total, "by_type": ships, "task_forces": fleets[:limit]}
    level, assumed = _band(intel, tag, "navy")
    note = level + (" (assumed)" if assumed else "")
    if level == "exact":
        return {"view": "navy", "tag": tag, "intel": note, "ships": total, "by_type": ships,
                "task_forces": [{k: v for k, v in f.items() if k in (
                    "task_force", "ships", "location", "state", "mission")} for f in fleets[:limit]]}
    if level == "estimate":
        return {"view": "navy", "tag": tag, "intel": note, "ships": _about(total),
                "by_type": {k: _about(v) for k, v in ships.items()}}
    return {"view": "navy", "tag": tag, "intel": note, "ships": "unknown"}


def view_air(full: FullWorld, me: str, tag: str | None, intel, limit: int) -> dict:
    tag = (tag or me).upper()
    country = full.countries.get(tag)
    if country is None:
        return {"view": "air", "tag": tag, "known": False}
    wings = country.air
    by_type = _count_types(wings, "type", "planes")
    total = sum(by_type.values())
    if tag == me:
        return {"view": "air", "tag": tag, "planes": total, "by_type": by_type, "wings": wings[:limit]}
    level, assumed = _band(intel, tag, "air")
    note = level + (" (assumed)" if assumed else "")
    if level == "exact":
        return {"view": "air", "tag": tag, "intel": note, "planes": total, "by_type": by_type,
                "wings": [{k: v for k, v in w.items() if k in ("type", "planes", "state", "mission")}
                          for w in wings[:limit]]}
    if level == "estimate":
        return {"view": "air", "tag": tag, "intel": note, "planes": _about(total),
                "by_type": {k: _about(v) for k, v in by_type.items()}}
    return {"view": "air", "tag": tag, "intel": note, "planes": "unknown"}


def view_economy(full: FullWorld, me: str, tag: str | None, intel, production) -> dict:
    tag = (tag or me).upper()
    country = full.countries.get(tag)
    if country is None:
        return {"view": "economy", "tag": tag, "known": False}
    if tag == me:
        return {"view": "economy", "tag": tag, **country.economy, "stockpile": country.stockpile,
                "in_service": country.in_service,
                "industrial_organisations": country.mios, "trade_routes": country.trade,
                "note": "equipment need per template is not in the save, so deficits are read as "
                        "stockpile against what the lines make; resource income is the top bar's"}
    level, assumed = _band(intel, tag, "army")
    note = level + (" (assumed)" if assumed else "")
    if level == "exact":
        return {"view": "economy", "tag": tag, "intel": note, "stockpile": country.stockpile}
    if level == "estimate":
        return {"view": "economy", "tag": tag, "intel": note,
                "stockpile": {k: _about(v) for k, v in list(country.stockpile.items())[:6]}}
    return {"view": "economy", "tag": tag, "intel": note, "stockpile": "unknown"}


def view_intel(full: FullWorld, me: str, intel: dict[str, Intel]) -> dict:
    own = full.countries.get(me)
    ledger = {tag: {k: round(v * 100) for k, v in (("civilian", i.civilian), ("army", i.army),
                                                    ("navy", i.navy), ("air", i.air)) if v is not None}
              for tag, i in sorted(intel.items())}
    out = {"view": "intel", "intel_levels_percent": ledger,
           "networks": full.networks,
           "agency": own.agency if own else {}}
    if not ledger:
        out["note"] = "no intel line from the mod yet: estimates are assumed (~40%)"
    return out


def view_projects(full: FullWorld, me: str) -> dict:
    own = full.countries.get(me)
    return {"view": "projects", **(own.projects if own else {})}


def view_research(full: FullWorld, me: str) -> dict:
    own = full.countries.get(me)
    if own is None:
        return {"view": "research", "known": False}
    return {"view": "research", **own.research, "doctrines": own.doctrines, **own.decisions}


# --- the brief ------------------------------------------------------------------------------------------


def brief_lines(full: FullWorld, me: str, intel: dict[str, Intel]) -> list[str]:
    """Two to four compact lines: faction and wars, agency, navy and air, stockpile."""
    own = full.countries.get(me)
    if own is None:
        return []
    out = []
    faction = full.faction_of(me)
    wars = sorted(t for t, r in own.relations.items() if "war" in r)
    bits = []
    if faction:
        bits.append(f"faction {faction['template']} (leader {faction['leader']}, "
                    f"{len(faction['members'])} members)")
    if wars:
        bits.append("at war with " + ", ".join(wars[:6])
                    + (f" (+{len(wars) - 6} more)" if len(wars) > 6 else ""))
    guaranteed = sorted(t for t, r in own.relations.items()
                        if r.get("guarantee", {}).get("direction") == "to")
    if guaranteed:
        bits.append("guarantees " + ", ".join(guaranteed[:5]))
    subjects = sorted(t for t, r in own.relations.items() if r.get("puppet", {}).get("direction") == "to")
    if subjects:
        bits.append("overlord of " + ", ".join(subjects[:5]))
    if bits:
        out.append("  Diplomacy: " + "; ".join(bits))
    agency = own.agency
    nets = ", ".join(f"{n['target']} {n['strength']:.0f}%" for n in full.networks[:4])
    ops = agency.get("operatives") or []
    if agency.get("created") or ops or nets:
        out.append(f"  Intelligence: agency {agency.get('name') or '-'}, {len(ops)} operatives"
                   f" ({sum(1 for o in ops if o['state'] == 'on_mission')} on missions)"
                   + (f"; networks {nets}" if nets else ""))
    ships = sum(f["ship_count"] for f in own.fleets)
    planes = sum(w["planes"] for w in own.air)
    stock = ", ".join(f"{k} {v}" for k, v in list(own.stockpile.items())[:3])
    out.append(f"  Fleet and air: {ships} ships in {len(own.fleets)} task forces, {planes} planes in "
               f"{len(own.air)} wings" + (f"; stockpile {stock}" if stock else ""))
    # The log carries neither the fuel stockpile nor the convoys; the save does (the top bar's
    # numbers). The brief's header said "fuel 0" before this line existed (2026-11-22).
    econ = own.economy or {}
    fuel = econ.get("fuel") or {}
    if fuel.get("max") or econ.get("convoys") is not None:
        out.append(f"  Fuel {fuel.get('stock', 0):,} of {fuel.get('max', 0):,}; "
                   f"convoys {econ.get('convoys', 0)}")
    return out


# --- occupation and supply (the map's own part) --------------------------------------------------------


def _state_provinces(layer, sid: int) -> list[int]:
    found = layer.states.get(sid) if layer is not None else None
    return list(getattr(found, "provinces", []) or [])


def view_occupation(world, me: str, layer, limit: int) -> dict:
    """Resistance and compliance of the states the country holds that are another nation's land, the
    provinces other countries hold inside its borders, and victory points. Garrison strength is not
    stored (the game computes it), so the resistance *target* stands in for how hard it will push."""
    def entry(sid: int, st) -> dict:
        r = st.resistance or {}
        return {"state": sid, "owner": st.owner, "resistance": round(_num(r.get("resistance")), 1),
                "target": round(_num(r.get("resistance_target")), 1),
                "speed": round(_num(r.get("resistance_speed")), 2),
                "compliance": round(_num(r.get("compliance")), 1),
                "of_nation": r.get("occupied_country_tag"), "active": r.get("operational_status") == "yes"}

    resisting, your_states_held = [], []
    for sid, st in sorted(world.states.items()):
        if st.controller == me and st.owner != me:
            resisting.append(entry(sid, st))
        elif st.owner == me and st.controller and st.controller != me:
            your_states_held.append({**entry(sid, st), "held_by": st.controller})
        elif st.owner == me and st.resistance and st.resistance.get("occupied_country_tag"):
            resisting.append(entry(sid, st))
    resisting.sort(key=lambda x: -x["resistance"])
    lost: dict[int, dict[str, int]] = {}
    taken: dict[int, dict] = {}
    if layer is not None:
        for sid, st in world.states.items():
            provinces = _state_provinces(layer, sid)
            if st.owner == me:
                for p in provinces:
                    holder = world.controllers.get(p)
                    if holder and holder != me:
                        lost.setdefault(sid, {}).setdefault(holder, 0)
                        lost[sid][holder] += 1
            elif st.owner:
                mine = [p for p in provinces if world.controllers.get(p) == me]
                if mine:
                    taken[sid] = {"owner": st.owner, "provinces_held": len(mine), "of": len(provinces)}
    own = world.countries.get(me)
    law = own.full.economy.get("occupation_law") if own is not None and own.full is not None else None
    points: dict[str, list] = {"held": [], "lost": []}
    for p, value in sorted(world.vp.items(), key=lambda kv: -kv[1]):
        holder = world.controller_of(p, layer)
        owner = world.owner_of(p, layer)
        if holder == me and owner and owner != me:
            points["held"].append({"province": p, "points": value, "of": owner})
        elif owner == me and holder and holder != me:
            points["lost"].append({"province": p, "points": value, "held_by": holder})
    return {"view": "occupation", "occupation_law": law,
            "occupied_by_you_resisting": resisting[:limit],
            "your_states_held_by_others": your_states_held[:limit],
            "provinces_held_by_others_in_your_states": {str(k): v for k, v in list(lost.items())[:limit]},
            "foreign_states_you_hold": {str(k): v for k, v in list(taken.items())[:limit]},
            "victory_points": {"held": points["held"][:limit], "lost": points["lost"][:limit]},
            "note": "garrison needs and strength are computed by the game, not saved"}


def view_supply(world, me: str, pic, layer, limit: int) -> dict:
    hubs, rails = [], []
    for province, (hub, rail) in sorted(world.logistics.items()):
        if world.controller_of(province, layer) != me:
            continue
        state = layer.state_of(province) if layer is not None else None
        if hub:
            hubs.append({"province": province, "state": state, "hub_level": hub})
        if rail:
            rails.append(rail)
    low = sorted((d for d in pic.own if d.supply is not None and d.supply < 70), key=lambda d: d.supply)
    values = [d.supply for d in pic.own if d.supply is not None]
    return {"view": "supply", "system": getattr(world.full, "supply", {}),
            "hubs": hubs[:limit], "provinces_with_railway": len(rails),
            "average_railway_level": round(sum(rails) / len(rails), 2) if rails else 0,
            "divisions": {"count": len(values),
                          "average_supply_percent": round(sum(values) / len(values)) if values else None,
                          "below_70_percent": [{"id": d.id, "state": d.state, "province": d.province,
                                                "supply": round(d.supply)} for d in low[:limit]]}}


NEW_VIEWS = ("faction", "intel", "projects", "navy", "air", "politics", "economy", "diplomacy",
             "research", "occupation", "supply", "commanders")


def query(pic, view: str, ident: str | None, layer=None, limit: int = 60) -> dict | None:
    """Answer one of the NEW_VIEWS from a filtered picture, or None for any other view."""
    if view not in NEW_VIEWS:
        return None
    world = pic.world
    full = getattr(world, "full", None)
    if full is None:
        return {"error": "the save's other panels could not be read"
                         + (f" ({world.full_error})" if getattr(world, "full_error", None) else "")}
    me, intel = pic.me, pic.intel
    if view == "faction":
        return view_faction(full, me, ident)
    if view == "intel":
        return view_intel(full, me, intel)
    if view == "projects":
        return view_projects(full, me)
    if view == "navy":
        return view_navy(full, me, ident, intel, limit)
    if view == "air":
        return view_air(full, me, ident, intel, limit)
    if view == "politics":
        return view_politics(full, me, ident, intel)
    if view == "economy":
        return view_economy(full, me, ident, intel, pic.production)
    if view == "diplomacy":
        return view_diplomacy(full, me, ident, intel, limit)
    if view == "research":
        return view_research(full, me)
    if view == "commanders":
        own = full.countries.get(me)
        found = own.commanders if own else []
        by_rank: dict[str, int] = {}
        for c in found:
            by_rank[c["rank"]] = by_rank.get(c["rank"], 0) + 1
        return {"view": "commanders", "count": len(found), "by_rank": by_rank, "commanders": found[:limit]}
    if view == "occupation":
        return view_occupation(world, me, layer, limit)
    return view_supply(world, me, pic, layer, limit)
