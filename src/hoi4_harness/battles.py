"""The battles a country can see: the save's ``combat`` block, read the way the attack bubbles show it.

A text save holds every battle in flight in one top-level block::

    combat={
        land_combat={ id location day duration terrain attacker={...} defender={...} }
        naval_combat={ id location duration terrain attacker={...} defender={...} progress ... }
        history={ ended battles }
    }

Checked on a real 1941 text save (43 land, 3 naval and 8 convoy combats, GER):

* ``duration`` is in hours; ``day`` repeats it. ``terrain`` is the combat's terrain (``plains``,
  ``urban`` ...). There is no river or other modifier field: the ``modifier_hours`` array in a log
  is indexed by modifiers the save does not name, so crossings and the like are not read.
* A land side lists ``unit={ id type }`` for every division in the fight, ``front`` for those on the
  line and ``reserves`` for those held back, and ``weighted_participants`` for the countries.
  ``tactic`` is the 1-based position of the tactic in ``common/combat_tactics.txt`` (verified
  against the pairs the game plays: 12 shock against 20 ambush, 32 rush against 35 skilful
  defence). A division is named by ``id`` *and* ``type`` (51 or 4713), see :func:`world._unit_key`.
* ``ground_damage_str`` / ``ground_damage_org`` on a side are the damage that side has *taken*: they
  equal the other side's ``log.total_damage`` in every combat checked. The damage a side dealt is
  therefore the other side's figure.
* ``org_loss_summary`` / ``str_loss_summary`` are the side's total loss in each of the last 24
  hours (a ring buffer; ``num_org_losses`` counts the hours recorded).
* ``air_plane`` (the planes flying support, with ``amount``), ``air_damage_*`` (damage taken from
  the air), ``anti_air_attack`` and ``has_flanked_opponent`` are written when they apply.
* A naval side has its ships under ``group.member`` (``cached_info`` gives the tag, the hull type
  and the ship's name; a convoy member has ``convoy`` instead), carrier wings under ``group.air``,
  and ``positioning``, ``total_damage_dealt``, ``total_initial_strength``, ``forces_compare``.
* There is no air-combat block: air battles are not kept in the save, only what an air wing did to a
  land or naval fight.

What the harness shows a player (fair play):

* a battle one of the country's divisions or ships is in: both sides in the detail the battle window
  gives, the country's own divisions with their organisation and strength, the enemy's divisions
  with what the window shows of them (template, organisation and strength as a share of the
  best-supplied copy of its template; never supply, entrenchment, experience or equipment);
* a battle in a province the country can see (its own land, the provinces next to it, where its
  divisions stand) and in which it has no part: the countries, how many divisions per side and the
  bar (who is ahead), nothing else.

The "who is winning" bar is an ESTIMATE, not the game's own number: the save does not hold the bubble's
bar. Per side, ``hours to break`` is the organisation the side's divisions have left divided by the
organisation it lost per hour over the last 24 hours; the attacker's share is
``T_att / (T_att + T_def)``. Where the organisation of the units is not shown to the observer, the
share is the defender's hourly organisation loss against the total (``rate_def / (rate_att +
rate_def)``), and where there is no rate, the damage race (``taken_def / (taken_att + taken_def)``).
Naval fights use the damage dealt, else ``forces_compare``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .adapters import savegame as sg

#: The combat tactics of 1.20, in the order of common/combat_tactics.txt (the save writes the index).
TACTICS = (
    "basic attack", "basic defend", "counterattack", "assault", "close-combat attack",
    "close-combat defend", "close-combat storm", "local strong point", "close-combat withdraw",
    "encirclement", "delay", "shock", "tactical withdrawal", "withdrawal attack",
    "withdrawal defend", "chase", "evade", "intercept", "breakthrough", "ambush", "blitz",
    "elastic defense", "backhand blow", "seize bridge", "bridge hold", "bridge skilful defence",
    "bridge assault", "bridge reckless assault", "retake bridge", "hold bridge", "bridge attack",
    "bridge rush", "bridge storm", "bridge hold-out", "bridge skilful defence (hold)",
    "guerrilla tactics", "human wave", "banzai charge", "grand banzai charge", "infantry charge",
    "planned attack", "relentless assault", "unexpected thrust", "overwhelming fire", "barrage",
    "masterful blitz", "masterful delay", "urban defence", "special forces storm",
    "special forces barrage", "armour-supported assault", "mouse-holing", "special forces defence",
    "fortify", "special forces ambush",
)

#: A side that has not lost organisation recently counts as lasting this long (hours).
MAX_HOURS = 999.0
#: Verdict bands on the attacker's share.
CLEAR, LEAN = 0.65, 0.55


def tactic_name(index) -> str | None:
    try:
        n = int(index)
    except (TypeError, ValueError):
        return None
    return TACTICS[n - 1] if 1 <= n <= len(TACTICS) else f"tactic {n}"


# --- reading the block ------------------------------------------------------------------------


@dataclass
class RawSide:
    keys: list[str] = field(default_factory=list)
    front: list[str] = field(default_factory=list)
    reserves: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    tactic: int | None = None
    #: Damage taken (the save's ground_damage_*), and hourly loss over the last 24 hours.
    taken_str: float = 0.0
    taken_org: float = 0.0
    rate_org: float | None = None
    rate_str: float | None = None
    planes: int = 0
    air_taken_str: float = 0.0
    air_taken_org: float = 0.0
    anti_air: float = 0.0
    flanked: bool = False
    #: Naval: ships ({"type", "tag", "name"}) and convoys, carrier wings, and the side's numbers.
    ships: list[dict] = field(default_factory=list)
    convoys: int = 0
    carrier_planes: int = 0
    dealt: float = 0.0
    initial_strength: float = 0.0
    positioning: float | None = None
    forces_compare: float | None = None
    disengaging: bool = False


@dataclass
class RawBattle:
    kind: str                  # land | naval
    ident: str
    province: int
    hours: int
    terrain: str
    attacker: RawSide
    defender: RawSide


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _items(block) -> list:
    if isinstance(block, dict):
        return block.get("_items", [])
    return []


def _participants(block) -> list[str]:
    """``weighted_participants={ { "GER" 75742 } }``: the countries, largest weight first."""
    pairs = []
    stack = list(sg._as_list(block))
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            items = node.get("_items")
            if isinstance(items, list) and len(items) == 2 and isinstance(items[0], str) \
                    and not isinstance(items[1], (dict, list)) \
                    and re.fullmatch(r"[A-Z][A-Z0-9]{2}", items[0]):
                pairs.append((items[0], _num(items[1])))
            else:
                stack.extend(items or [])
    return [t for t, _ in sorted(pairs, key=lambda p: -p[1])]


def _keys(value) -> list[str]:
    from .world import _unit_key

    return [k for k in (_unit_key(v) for v in sg._as_list(value)) if k]


def _mean_loss(block, recorded) -> float | None:
    values = [_num(v) for v in _items(block)]
    if not values:
        return None
    n = int(_num(recorded, len(values)))
    values = values[:n] if 0 < n < len(values) else values
    return sum(values) / len(values)


def _land_side(block: dict) -> RawSide:
    side = RawSide()
    side.keys = _keys(block.get("unit"))
    side.front = _keys(block.get("front"))
    side.reserves = _keys(block.get("reserves"))
    side.tags = _participants(block.get("weighted_participants"))
    side.tactic = int(_num(block.get("tactic"), 0)) or None
    side.taken_str = _num(block.get("ground_damage_str"))
    side.taken_org = _num(block.get("ground_damage_org"))
    recorded = block.get("num_org_losses")
    side.rate_org = _mean_loss(block.get("org_loss_summary"), recorded)
    side.rate_str = _mean_loss(block.get("str_loss_summary"), recorded)
    side.planes = sum(int(_num(p.get("amount"))) for p in sg._as_list(block.get("air_plane"))
                      if isinstance(p, dict))
    side.air_taken_str = _num(block.get("air_damage_str"))
    side.air_taken_org = _num(block.get("air_damage_org"))
    side.anti_air = _num(block.get("anti_air_attack"))
    side.flanked = block.get("has_flanked_opponent") == "yes"
    return side


def _naval_side(block: dict) -> RawSide:
    side = RawSide()
    group = block.get("group") if isinstance(block.get("group"), dict) else {}
    tags: dict[str, int] = {}
    for member in sg._as_list(group.get("member")):
        if not isinstance(member, dict):
            continue
        info = member.get("cached_info") if isinstance(member.get("cached_info"), dict) else {}
        tag = info.get("tag") if isinstance(info.get("tag"), str) else None
        if tag:
            tags[tag] = tags.get(tag, 0) + 1
        if "convoy" in member or info.get("convoy") == "yes":
            side.convoys += 1
        else:
            side.ships.append({"type": str(info.get("sprite", "ship")), "tag": tag,
                               "name": str(info.get("ship", ""))})
    for wing in sg._as_list(group.get("air")):
        if isinstance(wing, dict):
            side.carrier_planes += int(_num(wing.get("alive")))
            if isinstance(wing.get("tag"), str):
                tags.setdefault(wing["tag"], 0)
    side.tags = [t for t, _ in sorted(tags.items(), key=lambda kv: -kv[1])]
    side.keys = [k for k in (f"{u.get('id')}:{u.get('type')}" for u in sg._as_list(block.get("unit"))
                             if isinstance(u, dict))]
    side.anti_air = _num(block.get("anti_air"))
    side.positioning = _num(block.get("positioning"), None) if block.get("positioning") is not None else None
    side.dealt = _num(block.get("total_damage_dealt"))
    side.initial_strength = _num(block.get("total_initial_strength"))
    if isinstance(group.get("forces_compare"), str):
        side.forces_compare = _num(group.get("forces_compare"))
    side.disengaging = block.get("disengage") == "yes"
    return side


def read_combat(text: str) -> list[RawBattle]:
    """Every land and naval battle in the save's ``combat`` block (empty when it has none)."""
    block = sg.top_block(text, "combat")
    if not isinstance(block, dict):
        return []
    out: list[RawBattle] = []
    for kind, reader in (("land", _land_side), ("naval", _naval_side)):
        for raw in sg._as_list(block.get(f"{kind}_combat")):
            if not isinstance(raw, dict) or not isinstance(raw.get("attacker"), dict) \
                    or not isinstance(raw.get("defender"), dict):
                continue
            try:
                province = int(str(raw.get("location")))
            except ValueError:
                continue
            ident = raw.get("id")
            ident = str(ident.get("id")) if isinstance(ident, dict) else str(ident)
            out.append(RawBattle(kind, ident, province, int(_num(raw.get("duration"))),
                                 str(raw.get("terrain", "")), reader(raw["attacker"]),
                                 reader(raw["defender"])))
    return out


# --- what the observer sees of each battle ------------------------------------------------------


@dataclass
class Side:
    countries: list[str]
    divisions: int = 0
    front: int = 0
    reserves: int = 0
    #: Divisions with their numbers: the observer's own, and (own-involved battles only) the enemy's.
    units: list[dict] = field(default_factory=list)
    #: Template counts where the divisions are known; ``unknown`` counts those that are not.
    templates: dict[str, int] = field(default_factory=dict)
    unknown: int = 0
    tactic: str | None = None
    #: Damage the side took and dealt (str, org), and its organisation lost per hour (last 24 h).
    taken: tuple[float, float] = (0.0, 0.0)
    dealt: tuple[float, float] = (0.0, 0.0)
    rate_org: float | None = None
    rate_str: float | None = None
    #: Organisation and strength of the divisions on the line, summed, where shown to the observer.
    org_now: float | None = None
    str_now: float | None = None
    hours_to_break: float | None = None
    planes: int = 0
    anti_air: float = 0.0
    flanked: bool = False
    ships: dict[str, int] = field(default_factory=dict)
    convoys: int = 0
    carrier_planes: int = 0


@dataclass
class Battle:
    kind: str
    ident: str
    province: int
    state: int | None
    terrain: str
    hours: int
    attacker: Side
    defender: Side
    #: "attacker" / "defender" where one of the observer's divisions or ships is in it, else None.
    own_side: str | None
    #: The attacker's share of the fight, 0..1, how it was worked out, and the words for it.
    share: float | None = None
    basis: str = ""
    verdict: str = ""
    size: int = 0

    @property
    def own(self) -> bool:
        return self.own_side is not None


def share_words(share: float | None, own_side: str | None = None) -> str:
    """The bar in words. Where the observer is in the battle it is put from their side."""
    if share is None:
        return "no reading"
    if own_side:
        mine = share if own_side == "attacker" else 1 - share
        if mine >= CLEAR:
            return "you are clearly winning"
        if mine >= LEAN:
            return "you are ahead"
        if mine > 1 - LEAN:
            return "even"
        if mine > 1 - CLEAR:
            return "you are behind"
        return "you are clearly losing"
    lead, who = (share, "attacker") if share >= 0.5 else (1 - share, "defender")
    if lead >= CLEAR:
        return f"{who} clearly ahead"
    return f"{who} ahead" if lead >= LEAN else "even"


def hours_to_break(org: float | None, rate: float | None) -> float | None:
    if org is None:
        return None
    if not rate or rate <= 0:
        return MAX_HOURS
    return min(MAX_HOURS, org / rate)


def estimate(att: Side, dfn: Side, naval: tuple[RawSide, RawSide] | None = None):
    """(attacker share, basis) per the module docstring; (None, "") with nothing to go on."""
    if naval is not None:
        a, d = naval
        if a.dealt + d.dealt > 0:
            return a.dealt / (a.dealt + d.dealt), "damage dealt"
        if a.forces_compare is not None and d.forces_compare is not None \
                and a.forces_compare + d.forces_compare > 0:
            return a.forces_compare / (a.forces_compare + d.forces_compare), "forces compared"
        return None, ""
    ta, td = att.hours_to_break, dfn.hours_to_break
    if ta is not None and td is not None and (ta < MAX_HOURS or td < MAX_HOURS):
        return ta / (ta + td), "hours to break (organisation left over organisation lost per hour)"
    ra, rd = att.rate_org, dfn.rate_org
    if ra is not None and rd is not None and ra + rd > 0:
        return rd / (ra + rd), "organisation lost per hour (last 24 h)"
    total = att.taken[1] + dfn.taken[1]
    if total > 0:
        return dfn.taken[1] / total, "damage taken so far"
    return None, ""


def reference_maxima(world) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    """Per (country, template): the highest organisation and strength among its divisions, the
    stand-in for a maximum the save does not write (see condition.py)."""
    org: dict[tuple[str, str], float] = {}
    strength: dict[tuple[str, str], float] = {}
    for tag, country in world.countries.items():
        for d in country.divisions:
            k = (tag, d.template)
            if d.organisation is not None:
                org[k] = max(org.get(k, 0.0), d.organisation)
            if d.strength is not None:
                strength[k] = max(strength.get(k, 0.0), d.strength)
    return org, strength


def unit_row(d, ref_org, ref_str, own: bool) -> dict:
    """One division as the battle window lists it. Own: organisation and strength with the numbers.
    Enemy: the same two bars (as a share of its template's best copy) and nothing more."""
    row: dict = {"id": d.id, "country": d.country, "template": d.template}
    k = (d.country, d.template)
    org_pct = (d.organisation / ref_org[k]) if d.organisation is not None and ref_org.get(k) else None
    str_pct = (d.strength / ref_str[k]) if d.strength is not None and ref_str.get(k) else None
    if own:
        if d.organisation is not None:
            row["org"] = round(d.organisation, 1)
        if d.strength is not None:
            row["strength"] = round(d.strength, 1)
    if org_pct is not None:
        row["org_pct"] = round(min(1.0, org_pct), 2)
    if str_pct is not None:
        row["strength_pct"] = round(min(1.0, str_pct), 2)
    return row


def resolve(world, me: str, layer, seen: set[int] | None = None) -> list[Battle]:
    """The battles ``me`` can see, own-involved first, largest first. ``seen`` is the provinces
    the country sees (its land, its divisions' provinces, their neighbours); None sees all."""
    by_key = {d.key: d for c in world.countries.values() for d in c.divisions if d.key}
    own_keys = {d.key for d in world.countries.get(me, _EMPTY).divisions if d.key}
    ref_org, ref_str = reference_maxima(world)
    out: list[Battle] = []
    for raw in getattr(world, "combat", []) or []:
        sides = (raw.attacker, raw.defender)
        own_side = None
        for name, side in zip(("attacker", "defender"), sides, strict=True):
            if me in side.tags or any(k in own_keys for k in side.keys):
                own_side = name
        if own_side is None and seen is not None and raw.province not in seen:
            continue
        built = []
        for name, side, other in (("attacker", raw.attacker, raw.defender),
                                  ("defender", raw.defender, raw.attacker)):
            built.append(_side(raw, name, side, other, by_key, own_keys, ref_org, ref_str,
                               detail=own_side is not None, own=name == own_side))
        att, dfn = built
        share, basis = estimate(att, dfn, sides if raw.kind == "naval" else None)
        state = layer.state_of(raw.province) if layer is not None else None
        terrain = raw.terrain
        battle = Battle(raw.kind, raw.ident, raw.province, state, terrain, raw.hours, att, dfn,
                        own_side, share, basis, share_words(share, own_side),
                        size=att.divisions + dfn.divisions + len(raw.attacker.ships)
                        + len(raw.defender.ships))
        out.append(battle)
    out.sort(key=lambda b: (not b.own, -b.size, b.ident))
    return out


class _Empty:
    divisions: list = []


_EMPTY = _Empty()


def _side(raw: RawBattle, name: str, side: RawSide, other: RawSide, by_key, own_keys, ref_org,
          ref_str, *, detail: bool, own: bool) -> Side:
    out = Side(countries=list(side.tags))
    out.tactic = tactic_name(side.tactic) if detail else None
    if raw.kind == "naval":
        kinds: dict[str, int] = {}
        for ship in side.ships:
            kinds[ship["type"]] = kinds.get(ship["type"], 0) + 1
        out.ships, out.convoys, out.carrier_planes = kinds, side.convoys, side.carrier_planes
        out.divisions = 0
        out.dealt = (side.dealt, 0.0)
        out.taken = (other.dealt, 0.0)
        out.anti_air = side.anti_air
        return out
    out.divisions = len(side.keys)
    front = set(side.front)
    out.front = sum(1 for k in side.keys if k in front) if front else len(side.keys)
    out.reserves = len(side.reserves)
    out.planes = side.planes
    out.anti_air = side.anti_air
    out.flanked = side.flanked
    # An own-involved battle shows both sides' divisions as the window does; elsewhere only counts.
    orgs, strs = [], []
    for k in side.keys:
        d = by_key.get(k)
        if d is None:
            out.unknown += 1
            continue
        if detail:
            out.templates[d.template] = out.templates.get(d.template, 0) + 1
            out.units.append(unit_row(d, ref_org, ref_str, k in own_keys)
                             | {"role": "reserve" if k in side.reserves else "line"})
            if k in front or not front:
                if d.organisation is not None:
                    orgs.append(d.organisation)
                if d.strength is not None:
                    strs.append(d.strength)
    if detail:
        out.taken = (side.taken_str, side.taken_org)
        out.dealt = (other.taken_str, other.taken_org)
        out.rate_org, out.rate_str = side.rate_org, side.rate_str
        # Organisation and strength sums only when every division on the line was found.
        if orgs and out.unknown == 0:
            out.org_now = sum(orgs)
            out.str_now = sum(strs) if strs else None
            out.hours_to_break = hours_to_break(out.org_now, out.rate_org)
    else:
        # Not the observer's fight: the bar needs the rates and the damage race, and nothing of it
        # is shown but the verdict. They are kept here only to compute that.
        out.rate_org, out.rate_str = side.rate_org, side.rate_str
        out.taken = (side.taken_str, side.taken_org)
    return out


# --- words ------------------------------------------------------------------------------------------


def _names(side: Side, limit: int = 2) -> str:
    return "/".join(side.countries[:limit]) or "?"


def _forces(side: Side) -> str:
    if side.ships or side.convoys:
        bits = [f"{n} {t}" for t, n in sorted(side.ships.items(), key=lambda kv: -kv[1])[:3]]
        if side.convoys:
            bits.append(f"{side.convoys} convoys")
        return ", ".join(bits) or "ships"
    kinds = ", ".join(f"{n} {t}" for t, n in sorted(side.templates.items(), key=lambda kv: -kv[1])[:2])
    return f"{side.divisions} div" + (f" ({kinds})" if kinds else "")


def _days(hours: int) -> str:
    return f"{hours}h" if hours < 48 else f"{hours / 24:.0f}d"


def battle_line(b: Battle, name_of) -> str:
    """One battle in a line: where, who, how long, damage, the bar."""
    place = f"{name_of(b.state)}" if b.state is not None else f"province {b.province}"
    head = f"{place} [{b.terrain}]" if b.terrain else place
    kind = "naval, " if b.kind == "naval" else ""
    text = (f"{head}: {kind}{_names(b.attacker)} attack {_forces(b.attacker)} vs "
            f"{_names(b.defender)} {_forces(b.defender)}, {_days(b.hours)}")
    if b.own:
        text += (f"; damage dealt {b.attacker.dealt[0]:.0f} vs {b.defender.dealt[0]:.0f}"
                 if b.kind == "land" else "")
        tactics = "/".join(t for t in (b.attacker.tactic, b.defender.tactic) if t)
        if tactics and b.kind == "land":
            text += f"; tactics {tactics}"
    if b.share is not None:
        text += f"; {b.verdict} (attacker {b.share:.0%})"
    return text


def brief_lines(battles: list[Battle], name_of, limit: int = 5) -> list[str]:
    if not battles:
        return []
    own = sum(1 for b in battles if b.own)
    lines = [f"  Battles ({own} yours, {len(battles) - own} seen; the bar is an estimate from "
             "organisation left and lost per hour; inspect_world view=battles):"]
    for b in battles[:limit]:
        lines.append(("  * " if b.own else "  - ") + battle_line(b, name_of))
    if len(battles) > limit:
        lines.append(f"  (+{len(battles) - limit} more: inspect_world view=battles)")
    return lines


def _side_row(side: Side, naval: bool) -> dict:
    row: dict = {"countries": side.countries}
    if naval:
        row.update(ships=side.ships, convoys=side.convoys)
        if side.carrier_planes:
            row["carrier_planes"] = side.carrier_planes
        row["damage_dealt"] = round(side.dealt[0], 1)
        return row
    row.update(divisions=side.divisions, on_the_line=side.front, reserves=side.reserves)
    if side.templates:
        row["templates"] = dict(sorted(side.templates.items(), key=lambda kv: -kv[1]))
    if side.unknown:
        row["not_identified"] = side.unknown
    if side.tactic:
        row["tactic"] = side.tactic
    if side.units:
        row["units"] = side.units
    if side.rate_org is not None:
        row["org_lost_per_hour"] = round(side.rate_org, 2)
    if side.rate_str is not None:
        row["str_lost_per_hour"] = round(side.rate_str, 2)
    row["damage_dealt"] = {"str": round(side.dealt[0], 1), "org": round(side.dealt[1], 1)}
    row["damage_taken"] = {"str": round(side.taken[0], 1), "org": round(side.taken[1], 1)}
    if side.org_now is not None:
        row["org_on_the_line"] = round(side.org_now, 1)
    if side.hours_to_break is not None:
        row["hours_to_break"] = (None if side.hours_to_break >= MAX_HOURS
                                 else round(side.hours_to_break, 1))
    if side.planes:
        row["air_support_planes"] = side.planes
    if side.anti_air:
        row["anti_air"] = round(side.anti_air, 2)
    if side.flanked:
        row["flanked_the_enemy"] = True
    return row


def battle_row(b: Battle, name_of) -> dict:
    naval = b.kind == "naval"
    row: dict = {"kind": b.kind, "id": b.ident, "province": b.province,
                 "state": name_of(b.state) if b.state is not None else None, "terrain": b.terrain,
                 "duration_hours": b.hours, "duration_days": round(b.hours / 24, 1),
                 "yours": b.own_side or False}
    if b.own:
        row["attacker"] = _side_row(b.attacker, naval)
        row["defender"] = _side_row(b.defender, naval)
    else:
        # Not your fight: countries and how many on each side, as the map shows it.
        for name, side in (("attacker", b.attacker), ("defender", b.defender)):
            row[name] = ({"countries": side.countries, "ships": side.ships, "convoys": side.convoys}
                         if naval else {"countries": side.countries, "divisions": side.divisions})
    row["who_is_winning"] = {"attacker_share": None if b.share is None else round(b.share, 2),
                             "verdict": b.verdict, "basis": b.basis, "estimate": True}
    return row


def view(battles: list[Battle], name_of, limit: int = 60) -> dict:
    return {"view": "battles", "count": len(battles),
            "yours": sum(1 for b in battles if b.own), "shown": min(limit, len(battles)),
            "note": "who_is_winning is an estimate (battles.py): hours to break = organisation on the "
                    "line / organisation lost per hour, attacker_share = T_att/(T_att+T_def); "
                    "falls back to the loss rates, then the damage race. Enemy divisions show "
                    "organisation and strength as a share of their template's best copy.",
            "battles": [battle_row(b, name_of) for b in battles[:limit]]}
