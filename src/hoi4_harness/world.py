"""The world as one country's player sees it: the map, every division, others through intel (#139).

Two sources, each for what it alone has:

* **The text save** (``save_as_binary=no``; the game autosaves monthly, and the newest save of
  this campaign is used). Script cannot address a single division, but the save holds each one:
  its province, template, organisation, manpower against need, entrenchment and supply. It also
  holds every country's production lines, every state's owner and buildings, and the provinces
  a war has changed hands (``provinces={ N={ controller=TAG } }``).
* **The mod's intel line** (``LLMB|v5|intel``, weekly): the country's civilian, army, navy and air
  intel against each country that matters to it, from the 1.20 dynamic variables.

The save is omniscient, a player is not, so :func:`picture` filters it the way the game does for a
human:

* its own divisions, all of them;
* foreign divisions exactly where they stand next to its units or its territory (the map shows
  those), and beyond that only as its army intel allows: exact, an estimate, or unknown;
* foreign factories and production as its civilian intel allows, the way the intelligence ledger
  shows estimates.

Estimates are rounded, not randomised, so two looks at the same save agree.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import savegame as sg
from .types import GameState, ProductionLine

#: Intel at or above this is shown exactly; at or above ESTIMATE as an estimate; below, unknown.
EXACT, ESTIMATE = 0.66, 0.33
#: The band assumed for a country the mod has logged no intel line for (an older mod, or the
#: first week): an estimate, and the brief says it is assumed.
DEFAULT_INTEL = 0.4
#: How many foreign countries the reader parses (each is ~0.25 s on a 60 MB save).
MAX_COUNTRIES = 16
#: A save older than this many days before the game's date is another moment, not this one.
STALE_DAYS = 62
_BUILDINGS = {"industrial_complex": "civ", "arms_factory": "mil", "dockyard": "dock",
              "infrastructure": "infra"}


@dataclass
class Division:
    id: str
    country: str
    template: str
    province: int
    state: int | None = None
    terrain: str = ""
    organisation: float | None = None
    #: Manpower held against what the template needs, 0..1: how full the division is.
    manpower: float | None = None
    dig_in: float | None = None
    supply: float | None = None
    #: ``"<id>:<type>"``, how a combat block names a division (``unit={ id=1 type=51 }``): the id
    #: alone is not unique, the type is part of the key.
    key: str = ""
    #: Absolute strength, the supply ceiling (``max_supply``) and the cap on entrenchment: the
    #: save writes no maximum organisation or strength for a division.
    strength: float | None = None
    max_supply: float | None = None
    dig_in_cap: float | None = None
    #: Days out of supply (the key is only written while the division is cut off), the raw
    #: experience points, the last combat's date, and the damage it took recently.
    out_of_supply_days: int | None = None
    experience: float | None = None
    last_combat: str | None = None
    str_damage: float | None = None
    org_damage: float | None = None
    #: Equipment held (items) and still requested by the reinforcement queue: the shortage.
    equipment_held: int | None = None
    equipment_missing: int | None = None
    #: How far the division has adapted to the climate it stands in, 0..1 (None: no block).
    acclimatisation: float | None = None
    #: Share of the template's combat battalions that are tanks (None: composition not in the save).
    armour: float | None = None


@dataclass
class StatePicture:
    id: int
    owner: str | None
    civ: int = 0
    mil: int = 0
    dock: int = 0
    infra: int = 0
    #: Manpower available and locked in the state's pool, and the state's occupation block
    #: (resistance, compliance) when it has one.
    manpower: int = 0
    locked: int = 0
    resistance: dict | None = None
    #: Who holds the state when it is not its owner (the state's own ``controller``).
    controller: str | None = None


@dataclass
class CountryPicture:
    tag: str
    divisions: list[Division] = field(default_factory=list)
    production: list[ProductionLine] = field(default_factory=list)
    #: Everything but the army (world_full.FullCountry); None where it was not read.
    full: object | None = None
    #: The country has surrendered (its ``diplomacy`` block says ``capitulated=yes``; an exile
    #: keeps a few divisions and is still "at war").
    capitulated: bool = False


@dataclass
class World:
    date: str | None
    path: Path | None
    player: str | None
    states: dict[int, StatePicture] = field(default_factory=dict)
    #: Provinces held by someone other than their state's owner (occupation, a front).
    controllers: dict[int, str] = field(default_factory=dict)
    countries: dict[str, CountryPicture] = field(default_factory=dict)
    #: The countries the observed country is at war with (its own ``war_relation`` entries) and
    #: every battle in the save's ``combat`` block (battles.RawBattle), for the battle picture.
    enemies: set[str] = field(default_factory=set)
    combat: list = field(default_factory=list)
    seconds: float = 0.0
    #: Victory points by province, and the shared parts of the other panels (factions, the
    #: country's intelligence networks): world_full.FullWorld.
    vp: dict[int, int] = field(default_factory=dict)
    #: Supply hub and railway levels by province (the logistics screen): province -> (hub, rail).
    logistics: dict[int, tuple[int, int]] = field(default_factory=dict)
    full: object | None = None
    full_error: str | None = None
    #: The observed country's own tank, ship and plane designs (designers.Variant).
    variants: list = field(default_factory=list)

    def owner_of(self, province: int, layer) -> str | None:
        state = layer.state_of(province) if layer is not None else None
        return self.states[state].owner if state in self.states else None

    def controller_of(self, province: int, layer) -> str | None:
        return self.controllers.get(province) or self.owner_of(province, layer)

    def factories(self, tag: str) -> dict[str, int]:
        out = {"civ": 0, "mil": 0, "dock": 0}
        for s in self.states.values():
            if s.owner == tag:
                out["civ"] += s.civ
                out["mil"] += s.mil
                out["dock"] += s.dock
        return out


@dataclass
class Intel:
    civilian: float | None = None
    army: float | None = None
    navy: float | None = None
    air: float | None = None
    assumed: bool = False


def band(value: float | None) -> str:
    if value is None:
        return "unknown"
    return "exact" if value >= EXACT else "estimate" if value >= ESTIMATE else "unknown"


# --- reading the save -------------------------------------------------------------------------


def _int(value) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _level(building) -> int:
    if isinstance(building, dict):
        return _int(building.get("level")) or 0
    return _int(building) or 0


def read_states(text: str) -> dict[int, StatePicture]:
    out: dict[int, StatePicture] = {}
    for sid, block in (sg.top_block(text, "states") or {}).items():
        ident = _int(sid)
        if ident is None or not isinstance(block, dict):
            continue
        picture = StatePicture(ident, block.get("owner") if isinstance(block.get("owner"), str) else None)
        buildings = block.get("buildings")
        if isinstance(buildings, dict):
            for name, attribute in _BUILDINGS.items():
                setattr(picture, attribute, _level(buildings.get(name)))
        pool = block.get("manpower_pool")
        if isinstance(pool, dict):
            picture.manpower = _int(pool.get("available")) or 0
            picture.locked = _int(pool.get("locked")) or 0
        if isinstance(block.get("resistance"), dict) and block["resistance"]:
            picture.resistance = block["resistance"]
        if isinstance(block.get("controller"), str):
            picture.controller = block["controller"]
        out[ident] = picture
    return out


def read_provinces(text: str) -> tuple[dict[int, str], dict[int, int], dict[int, tuple[int, int]]]:
    """``provinces={ N={ controller=TAG victory_points=V buildings={ supply_node={ level } } } }``:
    who holds a province that is not its state's owner's, the victory points (the map's numbers),
    and the supply hubs and railways (province buildings, not state ones)."""
    controllers: dict[int, str] = {}
    points: dict[int, int] = {}
    logistics: dict[int, tuple[int, int]] = {}
    for pid, block in (sg.top_block(text, "provinces") or {}).items():
        ident = _int(pid)
        if ident is None or not isinstance(block, dict):
            continue
        if isinstance(block.get("controller"), str):
            controllers[ident] = block["controller"]
        value = block.get("victory_points")
        value = value.get("_items", [None])[0] if isinstance(value, dict) else value
        if _int(value):
            points[ident] = _int(value)
        buildings = block.get("buildings")
        if isinstance(buildings, dict):
            hub, rail = _level(buildings.get("supply_node")), _level(buildings.get("rail_way"))
            if hub or rail:
                logistics[ident] = (hub, rail)
    return controllers, points, logistics


def read_controllers(text: str) -> dict[int, str]:
    return read_provinces(text)[0]


def read_templates(text: str) -> dict[str, dict[str, str]]:
    """country -> template id -> name."""
    out: dict[str, dict[str, str]] = {}
    templates = sg.top_block(text, "division_templates") or {}
    for t in sg._as_list(templates.get("division_template")):
        if isinstance(t, dict) and isinstance(t.get("country"), str):
            out.setdefault(t["country"], {})[sg._ident(t.get("id"))] = str(t.get("name", ""))
    return out


def _capitulated(block: dict) -> bool:
    """``diplomacy={ capitulated=yes }`` in a country's block (checked on a 1943 save: NOR, HOL,
    FRA and POL have it, SOV and GER do not)."""
    diplomacy = block.get("diplomacy") if isinstance(block, dict) else None
    value = diplomacy.get("capitulated") if isinstance(diplomacy, dict) else None
    return str(value).lower() in ("yes", "true")


def _is_armour(kind: str) -> bool:
    kind = kind.lower()
    return (("armor" in kind or "armour" in kind or "tank" in kind) and "destroyer" not in kind
            and "anti" not in kind)


def read_template_armour(text: str) -> dict[str, dict[str, float]]:
    """country -> template id -> share of the template's combat battalions that are tanks."""
    out: dict[str, dict[str, float]] = {}
    templates = sg.top_block(text, "division_templates") or {}
    for t in sg._as_list(templates.get("division_template")):
        if not isinstance(t, dict) or not isinstance(t.get("country"), str):
            continue
        regiments = t.get("regiments")
        if not isinstance(regiments, dict):
            continue
        total = armour = 0
        for kind, value in regiments.items():
            if kind.startswith("_"):
                continue
            n = len(sg._as_list(value))
            total += n
            if _is_armour(kind):
                armour += n
        if total:
            out.setdefault(t["country"], {})[sg._ident(t.get("id"))] = armour / total
    return out


def _ratio(block, value_key: str, need_key: str) -> float | None:
    if not isinstance(block, dict):
        return None

    def total(node) -> float | None:
        if not isinstance(node, dict):
            return None
        values = [sg._number(v.get("value")) for v in sg._as_list(node.get("value")) if isinstance(v, dict)]
        values = [v for v in values if v is not None]
        return sum(values) if values else None

    have, need = total(block.get(value_key)), total(block.get(need_key))
    if have is None or not need:
        return None
    return max(0.0, min(1.0, have / need))


def _unit_key(value) -> str:
    """``{ id=31387 type=4713 }`` -> ``"31387:4713"``: the key a combat block names a unit by."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict) and value.get("id") is not None:
        return f"{value.get('id')}:{value.get('type', '')}"
    return ""


def _equipment_held(block) -> int | None:
    """Items a division holds: the ``amount`` of each entry of its ``equipment`` block."""
    equipment = block.get("equipment") if isinstance(block, dict) else None
    if not isinstance(equipment, dict):
        return None
    return sum(_int(item.get("amount")) or 0
               for item in sg._as_list(equipment.get("equipment")) if isinstance(item, dict))


def _equipment_missing(requests) -> int:
    """Items the reinforcement queue still asks for; a division with no requests block asks for none."""
    if not isinstance(requests, dict):
        return 0
    reinforcement = requests.get("reinforcement")
    if not isinstance(reinforcement, dict):
        return 0
    missing = 0
    for request in sg._as_list(reinforcement.get("request")):
        need = request.get("need") if isinstance(request, dict) else None
        if isinstance(need, dict):
            missing += sum(_int(v) or 0 for v in need.values() if not isinstance(v, (dict, list)))
    return missing


def _acclimatisation(block) -> float | None:
    """The adapted climate's value against ``max_acclimatization``: 1.0 is fully adapted."""
    if not isinstance(block, dict):
        return None
    limit = sg._number(block.get("max_acclimatization"))
    if not limit:
        return None
    active = block.get("actively_gaining")
    kinds = [str(active)] if isinstance(active, str) else [k for k in block if k.endswith("_climate")]
    values = [sg._number(block.get(k)) for k in kinds]
    values = [v for v in values if v is not None]
    return max(0.0, min(1.0, max(values) / limit)) if values else None


def read_divisions(country: dict, tag: str, templates: dict[str, str], layer,
                   armour: dict[str, float] | None = None) -> list[Division]:
    units = country.get("units") if isinstance(country, dict) else None
    out = []
    if not isinstance(units, dict):
        return out
    for d in sg._as_list(units.get("division")):
        if not isinstance(d, dict):
            continue
        province = _int(d.get("location"))
        if province is None:
            continue
        raw_id = d.get("id")
        ident = sg._ident(raw_id[0] if isinstance(raw_id, list) and raw_id else raw_id)
        state = layer.state_of(province) if layer is not None else None
        terrain = ""
        if layer is not None and province in layer.provinces:
            terrain = layer.provinces[province].terrain
        out.append(Division(
            id=ident, country=tag,
            template=templates.get(sg._ident(d.get("division_template_id")), "") or "?",
            province=province, state=state, terrain=terrain,
            organisation=sg._number(d.get("organisation")),
            manpower=_ratio(d.get("army_manpower"), "army_manpower_value", "army_manpower_need"),
            dig_in=sg._number(d.get("dig_in")),
            supply=sg._number(d.get("army_current_supply_ratio")),
            key=_unit_key(raw_id), strength=sg._number(d.get("strength")),
            max_supply=sg._number(d.get("max_supply")), dig_in_cap=sg._number(d.get("dig_in_cap")),
            out_of_supply_days=_int(d.get("out_of_supply_days")),
            experience=sg._number(d.get("experience")),
            last_combat=d.get("last_combat_date") if isinstance(d.get("last_combat_date"), str) else None,
            str_damage=sg._number(d.get("str_damage")), org_damage=sg._number(d.get("org_damage")),
            equipment_held=_equipment_held(d), equipment_missing=_equipment_missing(d.get("requests")),
            acclimatisation=_acclimatisation(d.get("acclimatization")),
            armour=(armour or {}).get(sg._ident(d.get("division_template_id")))))
    return out


def read_production(country: dict, names: dict[str, str]) -> list[ProductionLine]:
    holder = GameState()
    sg._read_production(holder, country.get("production") if isinstance(country, dict) else None,
                        names, set())
    return holder.production


def neighbours_of(tag: str, states: dict[int, StatePicture], controllers: dict[int, str],
                  layer) -> list[str]:
    """Countries holding a province that borders one ``tag`` holds, most shared borders first."""
    if layer is None:
        return []
    mine = {p for sid, s in states.items() if s.owner == tag for p in _provinces(layer, sid)}
    mine = {p for p in mine if controllers.get(p, tag) == tag} | {
        p for p, c in controllers.items() if c == tag}
    counts: dict[str, int] = {}
    for p in mine:
        for n in layer.neighbours(p):
            if n in mine:
                continue
            state = layer.state_of(n)
            holder = controllers.get(n) or (states[state].owner if state in states else None)
            if holder and holder != tag:
                counts[holder] = counts.get(holder, 0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _provinces(layer, state: int) -> list[int]:
    found = layer.states.get(state) if layer is not None else None
    return list(getattr(found, "provinces", []) or [])


def _read_shared(text: str, world: World, me: str, layer, names):
    """The parts of the other panels that belong to no one country: factions, the country order,
    the air bases, the characters index, and the own country's intelligence networks."""
    from . import world_full as F

    try:
        shared = F.FullWorld()
        spans = sg.top_level_spans(text)
        shared.order = F.country_order(text, spans)
        shared.factions = F.read_factions(text, spans)
        shared.air_top = F.top(text, spans, "strategic_air")
        shared.base_state = F.read_air_bases(shared.air_top)
        shared.chars = F.Characters(text, spans)
        shared.doctrine_top = F.top(text, spans, "doctrine")
        index = shared.order.index(me) + 1 if me in shared.order else None
        shared.networks = F.read_networks(F.top(text, spans, "strategic_operatives"), index)
        shared.supply = F.read_supply(F.top(text, spans, "supply_system_2"), me)
        return shared
    except Exception as exc:  # noqa: BLE001
        world.full_error = f"shared: {type(exc).__name__}: {exc}"
        return None


def read_world(path: Path, me: str, *, layer=None, also: tuple[str, ...] = ()) -> World:
    """The save at ``path``, read for country ``me``: its own block, and the countries next to it
    or named in ``also`` (its enemies, the person's country), up to MAX_COUNTRIES."""
    started = time.time()
    path = Path(path)
    with path.open("rb") as handle:
        magic = handle.read(8)
    if magic.startswith(b"HOI4bin") or bytes(1) in magic:
        raise ValueError(f"{path.name} is a binary save: set save_as_binary=no in settings.txt")
    text = sg.read_text(path)
    head = sg._head(text)
    world = World(date=head.get("date") if isinstance(head.get("date"), str) else None,
                  path=path, player=head.get("player") if isinstance(head.get("player"), str) else None)
    world.date = sg.save_date(world.date) if world.date else None
    world.states = read_states(text)
    world.controllers, world.vp, world.logistics = read_provinces(text)
    templates = read_templates(text)
    try:
        armour = read_template_armour(text)
    except Exception:  # noqa: BLE001 - template composition is an extra; the army reads without it
        armour = {}
    names = sg._equipment_names_fast(text) or sg._equipment_ids(sg.top_block(text, "equipments") or {})
    from . import world_full as F

    shared = _read_shared(text, world, me, layer, names)
    mine = sg.country_block(text, me)
    members = []
    enemies: list[str] = []
    if mine is not None and shared is not None:
        enemies = [t for t, r in F.read_relations(mine, me)[0].items() if "war" in r]
        found = shared.faction_of(me)
        members = [m for m in (found["members"] if found else []) if m != me]
    world.enemies = set(enemies)
    try:
        from .designers import read_variants

        world.variants = read_variants(text, me)
    except Exception as exc:  # noqa: BLE001 - designs that cannot be read must not cost the army
        world.full_error = world.full_error or f"designs: {type(exc).__name__}: {exc}"
    try:
        from .battles import read_combat

        world.combat = read_combat(text)
    except Exception as exc:  # noqa: BLE001 - battles that cannot be read must not cost the army
        world.full_error = world.full_error or f"combat: {type(exc).__name__}: {exc}"
    wanted = [me] + [t for t in (*also, world.player, *enemies) if t and t != me]
    wanted += [t for t in neighbours_of(me, world.states, world.controllers, layer) if t not in wanted]
    wanted += [t for t in members if t not in wanted]
    for tag in wanted[:MAX_COUNTRIES + 1]:
        block = mine if tag == me else sg.country_block(text, tag)
        if block is None:
            continue
        country = CountryPicture(tag, read_divisions(block, tag, templates.get(tag, {}), layer,
                                                   armour.get(tag)),
                                 read_production(block, names),
                                 capitulated=_capitulated(block))
        world.countries[tag] = country
        if shared is not None:
            try:
                owned = [st for st in world.states.values() if st.owner == tag]
                country.full = shared.countries[tag] = F.read_country(
                    block, tag, names=names, chars=shared.chars, air_top=shared.air_top,
                    base_state=shared.base_state, states_owned=owned, layer=layer,
                    doctrine_top=shared.doctrine_top if tag == me else None,
                    index=(shared.order.index(tag) + 1 if tag in shared.order else None),
                    province_state=(layer.state_of if layer is not None else None), own=tag == me)
            except Exception as exc:  # noqa: BLE001 - a panel that cannot be read must not cost the army
                world.full_error = f"{tag}: {type(exc).__name__}: {exc}"
    world.full = shared
    world.seconds = round(time.time() - started, 2)
    return world


# --- what the country can see -------------------------------------------------------------------


def _about(n: int) -> str:
    if n < 10:
        return f"~{n}"
    step = 5 if n < 100 else 10
    return f"~{int(round(n / step) * step)}"


@dataclass
class Picture:
    """``World`` filtered to what ``me`` can see."""

    me: str
    date: str | None
    own: list[Division]
    #: Foreign divisions next to ``me``'s units or land, by country.
    contact: dict[str, list[Division]]
    foreign: dict[str, dict]
    #: Border states on the other side: state id -> (holder, terrain).
    borders: dict[int, tuple[str, str]]
    #: The country's own factories and production lines, from the same save.
    industry: dict[str, int] = field(default_factory=dict)
    production: list[ProductionLine] = field(default_factory=list)
    #: The save and the intel readings this picture was filtered from, for the other panels.
    world: World | None = None
    intel: dict[str, Intel] = field(default_factory=dict)
    #: The provinces the country sees (its land, where its divisions stand, their neighbours).
    seen: set[int] = field(default_factory=set)
    #: The battles it can see and how fit each of its divisions is (battles.py, condition.py).
    battles: list = field(default_factory=list)
    conditions: list = field(default_factory=list)
    #: Unit keys of the enemy divisions that are in a battle with one of the country's own.
    engaged: set[str] = field(default_factory=set)
    #: Unit keys of the country's own divisions in a battle.
    fighting: set[str] = field(default_factory=set)


def picture(world: World, me: str, intel: dict[str, Intel], layer) -> Picture:
    mine = world.countries.get(me, CountryPicture(me))
    held = set()
    if layer is not None:
        for sid, s in world.states.items():
            if s.owner == me:
                held |= {p for p in _provinces(layer, sid) if world.controllers.get(p, me) == me}
        held |= {p for p, c in world.controllers.items() if c == me}
    contact_provinces = held | {d.province for d in mine.divisions}
    seen = set(contact_provinces)
    borders: dict[int, tuple[str, str]] = {}
    if layer is not None:
        for p in contact_provinces:
            for n in layer.neighbours(p):
                seen.add(n)
                holder = world.controller_of(n, layer)
                state = layer.state_of(n)
                if holder and holder != me and state is not None and state not in borders:
                    terrain = layer.provinces[n].terrain if n in layer.provinces else ""
                    borders[state] = (holder, terrain)
    contact: dict[str, list[Division]] = {}
    foreign: dict[str, dict] = {}
    for tag, country in world.countries.items():
        if tag == me:
            continue
        near = [d for d in country.divisions if d.province in seen]
        if near:
            contact[tag] = near
        foreign[tag] = _foreign(world, country, near, intel.get(tag) or Intel(
            DEFAULT_INTEL, DEFAULT_INTEL, DEFAULT_INTEL, DEFAULT_INTEL, assumed=True))
    pic = Picture(me, world.date, list(mine.divisions), contact, foreign, borders,
                  world.factories(me), list(mine.production), world, dict(intel), seen)
    from . import battles as B
    from . import condition as C

    pic.battles = B.resolve(world, me, layer, seen)
    fighting = {}
    for b in pic.battles:
        if b.own_side is None or b.kind != "land":
            continue
        raw = next((r for r in world.combat if r.ident == b.ident and r.kind == b.kind), None)
        if raw is not None:
            side = raw.attacker if b.own_side == "attacker" else raw.defender
            other = raw.defender if b.own_side == "attacker" else raw.attacker
            own_keys = {d.key for d in mine.divisions}
            for k in side.keys:
                if k in own_keys:
                    fighting[k] = "reserve" if k in side.reserves else "line"
            pic.engaged |= {k for k in other.keys if k not in own_keys}
    pic.conditions = C.assess(world, me, layer, fighting)
    pic.fighting = set(fighting)
    return pic


def _foreign(world: World, country: CountryPicture, near: list[Division], intel: Intel) -> dict:
    out: dict = {"tag": country.tag, "intel": {
        "army": band(intel.army), "civilian": band(intel.civilian),
        **({"assumed": True} if intel.assumed else {})}}
    total = len(country.divisions)
    army = band(intel.army)
    if army == "exact":
        out["divisions"] = str(total)
        out["templates"] = _templates(country.divisions)
    elif army == "estimate":
        out["divisions"] = _about(total)
    else:
        out["divisions"] = f"at least {len(near)} seen" if near else "unknown"
    factories = world.factories(country.tag)
    civ = band(intel.civilian)
    if civ == "exact":
        out["factories"] = factories
        out["production"] = [{"equipment": line.equipment, "factories": line.factories}
                             for line in sorted(country.production, key=lambda x: -x.factories)]
    elif civ == "estimate":
        out["factories"] = {k: _about(v) for k, v in factories.items()}
        out["production"] = sorted({line.equipment for line in sorted(
            country.production, key=lambda x: -x.factories)[:4]})
    else:
        out["factories"] = "unknown"
        out["production"] = "unknown"
    return out


def _templates(divisions: list[Division]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in divisions:
        counts[d.template] = counts.get(d.template, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


# --- words for the brief and the query tool -------------------------------------------------------


def _avg(values) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _state_name(index, state: int | None) -> str:
    if state is None:
        return "?"
    if index is not None:
        info = index.states.get(str(state))
        if info is not None and info.name:
            return info.name
    return f"state {state}"


def _group_line(index, state, divisions: list[Division], foe: list[Division] | None = None) -> str:
    org = _avg(d.organisation for d in divisions)
    mp = _avg(d.manpower for d in divisions)
    dig = _avg(d.dig_in for d in divisions)
    terrain = divisions[0].terrain if divisions else ""
    kinds = ", ".join(f"{n} {t}" for t, n in list(_templates(divisions).items())[:3])
    if state is None and terrain == "ocean":
        # A sea province has no state: these are divisions on transports (the 1940 Norway
        # landing read "? [ocean]" in the overnight campaign, 2026-11-22).
        text = f"At sea, on transports: {len(divisions)} ({kinds})"
    else:
        text = (f"{_state_name(index, state)}" + (f" [{terrain}]" if terrain else "")
                + f": {len(divisions)} ({kinds})")
    bits = []
    if org is not None:
        bits.append(f"org {org:.0f}")
    if mp is not None:
        bits.append(f"manpower {mp:.0%}")
    if dig:
        bits.append(f"dug in {dig:.0f}")
    if bits:
        text += " " + ", ".join(bits)
    if foe:
        kinds = ", ".join(f"{n} {t}" for t, n in list(_templates(foe).items())[:2])
        text += f" | facing {len(foe)} ({kinds})"
    return text


def brief_lines(pic: Picture, index=None, layer=None, *, states: int = 6, countries: int = 4) -> list[str]:
    lines = [f"Forces and map (save of {pic.date or '?'}; own army in full, others as your intel "
             "and the map show them; inspect_world for more):"]
    by_state: dict[int | None, list[Division]] = {}
    for d in pic.own:
        by_state.setdefault(d.state, []).append(d)
    foe_by_neighbour: dict[int | None, list[Division]] = {}
    if layer is not None:
        contact_states = {}
        for divs in pic.contact.values():
            for d in divs:
                contact_states.setdefault(d.state, []).append(d)
        for s in by_state:
            near = []
            for p in {d.province for d in by_state[s]}:
                for n in layer.neighbours(p):
                    near += [d for divs in pic.contact.values() for d in divs if d.province == n]
            foe_by_neighbour[s] = list({id(d): d for d in near}.values())
    org = _avg(d.organisation for d in pic.own)
    mp = _avg(d.manpower for d in pic.own)
    lines.append(f"  Army: {len(pic.own)} divisions"
                 + (f", org {org:.0f}" if org is not None else "")
                 + (f", manpower {mp:.0%}" if mp is not None else ""))
    from . import battles as B
    from . import condition as C

    name_of = lambda state: _state_name(index, state)  # noqa: E731
    lines += C.brief_lines(pic.conditions, name_of)
    lines += B.brief_lines(pic.battles, name_of)
    if pic.industry or pic.production:
        lines_ = sorted(pic.production, key=lambda x: -x.factories)
        making = ", ".join(f"{p.equipment} x{p.factories}" for p in lines_[:5]) or "nothing"
        fac = pic.industry or {}
        lines.append(f"  Industry: {fac.get('civ', 0)} civ / {fac.get('mil', 0)} mil / "
                     f"{fac.get('dock', 0)} dock; producing {making}"
                     + (f" (+{len(lines_) - 5} more lines)" if len(lines_) > 5 else ""))
    order = sorted(by_state, key=lambda s: (-len(foe_by_neighbour.get(s, [])), -len(by_state[s])))
    for s in order[:states]:
        lines.append("  " + _group_line(index, s, by_state[s], foe_by_neighbour.get(s)))
    if len(order) > states:
        lines.append(f"  (+{len(order) - states} more states: inspect_world view=divisions)")
    for tag, info in list(_ranked(pic).items())[:countries]:
        seen = len(pic.contact.get(tag, []))
        intel = info["intel"]
        note = " (intel assumed)" if intel.get("assumed") else ""
        prod = info["production"]
        if isinstance(prod, list) and prod and isinstance(prod[0], dict):
            prod = ", ".join(f"{p['equipment']} x{p['factories']}" for p in prod[:3])
        elif isinstance(prod, list):
            prod = ", ".join(prod) or "nothing seen"
        fac = info["factories"]
        if isinstance(fac, dict):
            fac = f"{fac['civ']} civ / {fac['mil']} mil / {fac['dock']} dock"
        # "In contact" read as a war to the model: a peacetime Germany "fought" CZE, POL and FRA
        # for eight months in its notes (overnight campaign, 2026-11-22). Say which it is.
        enemies = getattr(pic.world, "enemies", None) or set()
        where = "in contact, at war" if tag in enemies else "at the border, at peace"
        lines.append(f"  {tag}: divisions {info['divisions']}" + (f" ({seen} {where})" if seen else "")
                     + f"; factories {fac}; producing {prod}{note}")
    full = getattr(pic.world, "full", None)
    if full is not None:
        from . import world_full

        lines += world_full.brief_lines(full, pic.me, pic.intel)
    return lines


def _ranked(pic: Picture) -> dict[str, dict]:
    return dict(sorted(pic.foreign.items(),
                       key=lambda kv: (-len(pic.contact.get(kv[0], [])), kv[0])))


_VIEW_FEATURES = {"projects": "special_projects", "economy": "mios"}


def _feature_gap(index, view: str, me: str) -> str:
    """Why a view's feature does not exist for ``me`` with the DLCs that are on (docs/dlc.md);
    empty when it does, or when there is no playset to tell."""
    key = _VIEW_FEATURES.get(view)
    if index is None or key is None:
        return ""
    return index.mod_context().feature_gap(key, me)


def query(pic: Picture, view: str, ident: str | None, index=None, layer=None, limit: int = 60,
          history=None) -> dict:
    view = (view or "divisions").lower()
    from . import world_full

    if view in world_full.NEW_VIEWS:
        gap = _feature_gap(index, view, pic.me)
        if gap and view == "projects":
            return {"view": view, "exists": False, "note": gap + ": there are no special projects "
                    "in this game for your country, so there is nothing to show"}
        data = world_full.query(pic, view, ident, layer, limit)
        if gap and view == "economy" and isinstance(data, dict) and "industrial_organisations" in data:
            data["industrial_organisations"] = "none exist in this game"
            data["industrial_organisations_note"] = gap
        return data
    if view == "battles":
        from . import battles as B

        return B.view(pic.battles, lambda state: _state_name(index, state), limit)
    if view == "fronts":
        from . import frontreport as FR

        name_of = lambda state: _state_name(index, state)  # noqa: E731
        return FR.view(FR.report(history, pic, layer, name_of), history, limit)
    if view == "divisions":
        from . import condition as C

        name_of = lambda state: _state_name(index, state)  # noqa: E731
        rows = pic.conditions or []
        return {"view": "divisions", "date": pic.date, "count": len(rows), "shown": min(limit, len(rows)),
                "note": "worst first; organisation and strength shares are estimates against the "
                        "best copy of the same template (the save writes no maximum); supply is "
                        "the ratio over max_supply; attrition is not saved, its causes are shown",
                "divisions": [C.row(c, name_of) for c in rows[:limit]]}
    if view == "state":
        sid = _int(ident)
        if sid is None:
            return {"error": "view=state needs id=<state id>"}
        own = [d for d in pic.own if d.state == sid]
        foe = [d for divs in pic.contact.values() for d in divs if d.state == sid]
        holder = pic.borders.get(sid, (None, ""))
        return {"view": "state", "state": sid, "name": _state_name(index, sid),
                "held_by": holder[0] or ("you" if own else "?"), "terrain": holder[1] or
                (own[0].terrain if own else ""),
                "own": [_division(d, index) for d in own],
                "enemy_seen": [_foreign_division(d, index, pic) for d in foe]}
    if view == "country":
        tag = (ident or "").upper()
        if tag == pic.me:
            return {"view": "country", "tag": tag, "note": "your own country: view=divisions"}
        if tag not in pic.foreign:
            return {"view": "country", "tag": tag, "known": False,
                    "note": "not a neighbour, an enemy or the person's country in this save"}
        return {"view": "country", **pic.foreign[tag],
                "in_contact": [_foreign_division(d, index, pic) for d in pic.contact.get(tag, [])][:limit]}
    if view == "borders":
        return {"view": "borders", "borders": [
            {"state": s, "name": _state_name(index, s), "held_by": h, "terrain": t}
            for s, (h, t) in sorted(pic.borders.items(), key=lambda kv: (kv[1][0], kv[0]))][:limit]}
    return {"error": f"unknown view {view!r}: divisions, battles, fronts, state, country, borders, "
                     + ", ".join(world_full.NEW_VIEWS)}


def _foreign_division(d: Division, index, pic: Picture) -> dict:
    """A foreign division in contact, as a player sees it: where it stands and what it is. Its
    organisation and strength bars only when it is in a battle with one of the country's own (the
    battle window lists them); never its supply, entrenchment, experience or equipment."""
    out = {"id": d.id, "country": d.country, "template": d.template,
           "state": _state_name(index, d.state), "province": d.province, "terrain": d.terrain}
    if d.key in pic.engaged and pic.world is not None:
        from . import battles as B

        ref_org, ref_str = B.reference_maxima(pic.world)
        row = B.unit_row(d, ref_org, ref_str, own=False)
        out["in_battle_with_yours"] = True
        out.update({k: v for k, v in row.items() if k in ("org_pct", "strength_pct")})
    return out


def _division(d: Division, index) -> dict:
    out = {"id": d.id, "template": d.template, "state": _state_name(index, d.state),
           "province": d.province, "terrain": d.terrain}
    if d.organisation is not None:
        out["org"] = round(d.organisation, 1)
    if d.manpower is not None:
        out["manpower"] = f"{d.manpower:.0%}"
    if d.dig_in:
        out["dug_in"] = round(d.dig_in, 1)
    if d.supply is not None:
        out["supply"] = round(d.supply, 1)
    return out


# --- finding this campaign's newest save ----------------------------------------------------------


def _date_key(date: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", date or "")[:3]) or (0,)


def newest_save(save_dir: Path, game_date: str | None, player: str | None = None) -> Path | None:
    """The newest text save that belongs to this moment of this campaign: written last, not after
    the game's date, not more than STALE_DAYS before it, and (when known) the same player."""
    if save_dir is None or not Path(save_dir).is_dir():
        return None
    candidates = sorted(Path(save_dir).glob("*.hoi4"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates[:6]:
        try:
            with path.open("rb") as handle:
                head = handle.read(4096).decode("utf-8", errors="replace")
        except OSError:
            continue
        if not head.startswith("HOI4txt"):
            continue
        match = re.search(r'\bdate="?([\d.]+)', head)
        saved = sg.save_date(match.group(1)) if match else None
        if player:
            who = re.search(r'\bplayer="?(\w+)', head)
            if who and who.group(1) != player:
                continue
        if game_date and saved:
            gap = _days(saved, game_date)
            if gap is None or gap < 0 or gap > STALE_DAYS:
                continue
        return path
    return None


def _days(a: str, b: str) -> int | None:
    from datetime import date

    try:
        ya, ma, da = _date_key(a)
        yb, mb, db = _date_key(b)
        return (date(yb, mb, db) - date(ya, ma, da)).days
    except (ValueError, TypeError):
        return None


def build_observer(config, index, fake=None) -> WorldObserver:
    """The observer for a run: the fake game's save folder, the configured one, or the game's."""
    from .adapters.savegame import find_save_dir
    from .mapdata import MapLayer

    save_dir = config.save_dir or (getattr(fake, "save_dir", None) if fake is not None
                                   else find_save_dir())
    layer = None
    if index is not None:
        try:
            layer = MapLayer.load(index.playset)
        except Exception:  # noqa: BLE001 - no map files: divisions without states, no contact
            layer = None
    me = (getattr(config, "rival", None) or getattr(config, "country", None) or "").upper()
    return WorldObserver(me, Path(save_dir) if save_dir else None, index=index, layer=layer,
                         run_dir=Path(config.run_dir))


class WorldObserver:
    """Keeps the picture current for one country: rereads the newest save when it changes, and
    answers the brief and ``inspect_world`` from it."""

    def __init__(self, me: str, save_dir: Path | None, *, index=None, layer=None,
                 others: tuple[str, ...] = (), player: str | None = None,
                 run_dir: Path | None = None):
        self.run_dir = run_dir
        self.me = me
        self.save_dir = save_dir
        self.index = index
        self.layer = layer
        self.others = others
        self.player = player
        self._key: tuple | None = None
        self.world: World | None = None
        self.error: str | None = None
        self._history = None

    def _me(self, state: GameState) -> str:
        tag = (state.country or "").upper()
        return tag if len(tag) == 3 and tag != "___" else self.me

    def refresh(self, state: GameState) -> World | None:
        self.me = self._me(state)
        path = newest_save(self.save_dir, state.date if state.date != "1936-01-01" else None,
                           self.player)
        if path is None:
            return self.world
        key = (str(path), path.stat().st_mtime)
        if key != self._key:
            try:
                enemies = tuple(w.against for w in state.wars if getattr(w, "against", None))
                self.world = read_world(path, self.me, layer=self.layer,
                                        also=enemies + tuple(self.others))
                self.error = None
                self._record_fronts(self.world)
            except (OSError, ValueError) as exc:
                self.error = str(exc)
            self._key = key
        return self.world

    @property
    def history(self):
        """What the observer has seen of the fronts across saves (frontreport.py)."""
        if self._history is None:
            from .frontreport import FrontHistory

            self._history = FrontHistory.for_run(self.run_dir)
        return self._history

    def _record_fronts(self, world: World) -> None:
        try:
            self.history.record(world, self.me, self.layer)
        except Exception as exc:  # noqa: BLE001 - a history that cannot be kept must not cost the brief
            world.full_error = world.full_error or f"fronts: {type(exc).__name__}: {exc}"

    def picture(self, state: GameState) -> Picture | None:
        world = self.refresh(state)
        if world is None:
            return None
        return picture(world, self.me, intel_from(state), self.layer)

    def brief_lines(self, state: GameState) -> list[str]:
        from .front_orders import Standing

        pic = self.picture(state)
        lines = brief_lines(pic, self.index, self.layer) if pic is not None else []
        if pic is not None:
            from . import frontreport as FR

            lines += FR.brief_lines(FR.report(self.history, pic, self.layer))
        from .steering import Standing as SteerStanding

        standing = Standing(self.run_dir).line(lambda s: _state_name(self.index, s))
        steers = SteerStanding(self.run_dir).line()
        return lines + [line for line in (standing, steers) if line]

    def variants(self, state: GameState) -> list | None:
        """The country's own designs from the newest text save; None when there is no save yet."""
        world = self.refresh(state)
        return None if world is None else list(world.variants)

    def query(self, state: GameState, view: str, ident: str | None, limit: int = 60) -> dict:
        pic = self.picture(state)
        if pic is None:
            return {"error": "no text save of this campaign yet: the game autosaves monthly "
                             "(save_as_binary=no); until then only the brief's own numbers exist"
                             + (f" ({self.error})" if self.error else "")}
        data = query(pic, view, ident, self.index, self.layer, limit, history=self.history)
        if "error" not in data and (not ident or ident.upper() == self.me):
            from .observation.mechanics import view_extras

            data.update(view_extras(state, (view or "").lower()))
        return data


def intel_from(state: GameState) -> dict[str, Intel]:
    raw = getattr(state, "intel", None) or {}
    return {tag: Intel(**{k: v for k, v in values.items() if k in ("civilian", "army", "navy", "air")})
            for tag, values in raw.items()}
