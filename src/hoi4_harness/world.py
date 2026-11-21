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


@dataclass
class StatePicture:
    id: int
    owner: str | None
    civ: int = 0
    mil: int = 0
    dock: int = 0
    infra: int = 0


@dataclass
class CountryPicture:
    tag: str
    divisions: list[Division] = field(default_factory=list)
    production: list[ProductionLine] = field(default_factory=list)


@dataclass
class World:
    date: str | None
    path: Path | None
    player: str | None
    states: dict[int, StatePicture] = field(default_factory=dict)
    #: Provinces held by someone other than their state's owner (occupation, a front).
    controllers: dict[int, str] = field(default_factory=dict)
    countries: dict[str, CountryPicture] = field(default_factory=dict)
    seconds: float = 0.0

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
        out[ident] = picture
    return out


def read_controllers(text: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for pid, block in (sg.top_block(text, "provinces") or {}).items():
        ident = _int(pid)
        if ident is not None and isinstance(block, dict) and isinstance(block.get("controller"), str):
            out[ident] = block["controller"]
    return out


def read_templates(text: str) -> dict[str, dict[str, str]]:
    """country -> template id -> name."""
    out: dict[str, dict[str, str]] = {}
    templates = sg.top_block(text, "division_templates") or {}
    for t in sg._as_list(templates.get("division_template")):
        if isinstance(t, dict) and isinstance(t.get("country"), str):
            out.setdefault(t["country"], {})[sg._ident(t.get("id"))] = str(t.get("name", ""))
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


def read_divisions(country: dict, tag: str, templates: dict[str, str], layer) -> list[Division]:
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
            supply=sg._number(d.get("army_current_supply_ratio"))))
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


def read_world(path: Path, me: str, *, layer=None, also: tuple[str, ...] = ()) -> World:
    """The save at ``path``, read for country ``me``: its own block, and the countries next to it
    or named in ``also`` (its enemies, the person's country), up to MAX_COUNTRIES."""
    started = time.time()
    path = Path(path)
    with path.open("rb") as handle:
        magic = handle.read(8)
    if magic.startswith(b"HOI4bin") or bytes(1) in magic:
        raise ValueError(f"{path.name} is a binary save: set save_as_binary=no in settings.txt")
    text = path.read_text(encoding="utf-8", errors="replace")
    head = sg._head(text)
    world = World(date=head.get("date") if isinstance(head.get("date"), str) else None,
                  path=path, player=head.get("player") if isinstance(head.get("player"), str) else None)
    world.date = sg.save_date(world.date) if world.date else None
    world.states = read_states(text)
    world.controllers = read_controllers(text)
    templates = read_templates(text)
    names = sg._equipment_names_fast(text) or sg._equipment_names(sg.top_block(text, "equipments") or {})
    wanted = [me] + [t for t in (*also, world.player) if t and t != me]
    wanted += [t for t in neighbours_of(me, world.states, world.controllers, layer) if t not in wanted]
    for tag in wanted[:MAX_COUNTRIES + 1]:
        block = sg.country_block(text, tag)
        if block is None:
            continue
        world.countries[tag] = CountryPicture(
            tag, read_divisions(block, tag, templates.get(tag, {}), layer),
            read_production(block, names))
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
    return Picture(me, world.date, list(mine.divisions), contact, foreign, borders,
                   world.factories(me), list(mine.production))


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
        lines.append(f"  {tag}: divisions {info['divisions']}" + (f" ({seen} in contact)" if seen else "")
                     + f"; factories {fac}; producing {prod}{note}")
    return lines


def _ranked(pic: Picture) -> dict[str, dict]:
    return dict(sorted(pic.foreign.items(),
                       key=lambda kv: (-len(pic.contact.get(kv[0], [])), kv[0])))


def query(pic: Picture, view: str, ident: str | None, index=None, layer=None, limit: int = 60) -> dict:
    view = (view or "divisions").lower()
    if view == "divisions":
        rows = sorted(pic.own, key=lambda d: (d.state or 0, d.template, d.id))
        return {"view": "divisions", "date": pic.date, "count": len(rows), "shown": min(limit, len(rows)),
                "divisions": [_division(d, index) for d in rows[:limit]]}
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
                "enemy_seen": [_division(d, index) for d in foe]}
    if view == "country":
        tag = (ident or "").upper()
        if tag == pic.me:
            return {"view": "country", "tag": tag, "note": "your own country: view=divisions"}
        if tag not in pic.foreign:
            return {"view": "country", "tag": tag, "known": False,
                    "note": "not a neighbour, an enemy or the person's country in this save"}
        return {"view": "country", **pic.foreign[tag],
                "in_contact": [_division(d, index) for d in pic.contact.get(tag, [])][:limit]}
    if view == "borders":
        return {"view": "borders", "borders": [
            {"state": s, "name": _state_name(index, s), "held_by": h, "terrain": t}
            for s, (h, t) in sorted(pic.borders.items(), key=lambda kv: (kv[1][0], kv[0]))][:limit]}
    return {"error": f"unknown view {view!r}: divisions, state, country or borders"}


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
            except (OSError, ValueError) as exc:
                self.error = str(exc)
            self._key = key
        return self.world

    def picture(self, state: GameState) -> Picture | None:
        world = self.refresh(state)
        if world is None:
            return None
        return picture(world, self.me, intel_from(state), self.layer)

    def brief_lines(self, state: GameState) -> list[str]:
        from .front_orders import Standing

        pic = self.picture(state)
        lines = brief_lines(pic, self.index, self.layer) if pic is not None else []
        standing = Standing(self.run_dir).line(lambda s: _state_name(self.index, s))
        return lines + ([standing] if standing else [])

    def query(self, state: GameState, view: str, ident: str | None, limit: int = 60) -> dict:
        pic = self.picture(state)
        if pic is None:
            return {"error": "no text save of this campaign yet: the game autosaves monthly "
                             "(save_as_binary=no); until then only the brief's own numbers exist"
                             + (f" ({self.error})" if self.error else "")}
        return query(pic, view, ident, self.index, self.layer, limit)


def intel_from(state: GameState) -> dict[str, Intel]:
    raw = getattr(state, "intel", None) or {}
    return {tag: Intel(**{k: v for k, v in values.items() if k in ("civilian", "army", "navy", "air")})
            for tag, values in raw.items()}
