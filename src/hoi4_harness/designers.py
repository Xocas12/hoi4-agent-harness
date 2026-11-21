"""The tank, ship and plane designers, read from the game's files and priced as the screen prices them.

A player designs equipment on three screens (tank: No Step Back, ship: Man the Guns, plane: By
Blood Alone; ``dlc.FEATURES``). Each one starts from a chassis (a tank chassis, a hull, an airframe),
fills its module slots from the modules the country's technologies unlocked, may raise upgrade levels,
and pays experience (army for tanks, navy for ships, air for planes). Nothing here is vanilla
knowledge written down by hand: the chassis, the slots, the modules, their stats, the technologies
that unlock them and the defines that price a change are all read from the playset, so a mod that
changes them is followed.

This module has three halves:

* ``DesignerData``: the files, parsed (``common/units/equipment``, ``.../modules``, ``.../upgrades``,
  ``common/technologies`` for ``enable_equipments`` / ``enable_equipment_modules``,
  ``common/defines``).
* ``read_variants``: the country's own designs from a text save (``equipments = { <type> = { ... } }``).
* ``plan_design``: whether a design is allowed (slots, categories, count limits, unlocks, upgrade
  levels) and what it costs, as ``Plan``; ``Plan.guard`` / ``Plan.body`` are the script the console
  channel wraps in one marked ``if`` (``adapters/console.py``, ``_plan_design``).

THE EXPERIENCE COST RULE (docs/designers.md has the evidence per line). Per module slot, against the
parent design (the default variant of the chassis when none is named):

* empty -> module:      ``EQUIPMENT_MODULE_ADD_XP_COST``      (5)
* module -> empty:      ``EQUIPMENT_MODULE_REMOVE_XP_COST``   (1)
* module -> related:    ``EQUIPMENT_MODULE_CONVERT_XP_COST``  (3); "related" is ``can_convert_from``
  on the new module naming the old one (``module =``) or its category (``module_category =``)
* module -> unrelated:  ``EQUIPMENT_MODULE_REPLACE_XP_COST``  (6)
* unchanged:            0

The files do not say what a module's own ``xp_cost`` does next to those four defines (61 plane and 93
tank modules carry one, no ship module does; the localisation only says the screen shows "Costs N to
add / replace / remove"). So the harness charges the define PLUS the new module's ``xp_cost``: that
is at least what the screen asks under every reading that can be named (define only, module only,
the larger of the two, the sum), and it is reported as an upper bound, never as exact. Upgrades are
priced the same way, from ``linear_cost`` or the ``*_EQUIPMENT_BASE_COST`` / ``RAMP_COST`` defines, and
also as an upper bound. ``DESIGN_TEAM_CHANGE_XP_COST`` (0) is added. An unknown part is refused or
listed under ``Plan.unchecked``; it is never guessed low.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import dlc as dlc_mod

KINDS = ("tank", "ship", "plane")
#: designer -> (the dlc.FEATURES id, the experience it spends, the trigger that tests it, the effect
#: that takes it).
KIND_FEATURE = {"tank": "tank_designer", "ship": "ship_designer", "plane": "plane_designer"}
KIND_XP = {"tank": ("army", "has_army_experience", "army_experience"),
           "ship": ("navy", "has_navy_experience", "navy_experience"),
           "plane": ("air", "has_air_experience", "air_experience")}
#: The module file each designer reads, in ``common/units/equipment/modules``.
MODULE_FILES = {"tank": "00_tank_modules.txt", "ship": "00_ship_modules.txt",
                "plane": "00_plane_modules.txt"}
#: ``cost =`` of an upgrade -> the kind of equipment it is for.
UPGRADE_COST_KIND = {"land": "tank", "naval": "ship", "air": "plane"}
EMPTY = "empty"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")


class DesignRefused(Exception):
    """A design the designer would not accept, or one the harness cannot price: the reason."""


# --- a small script reader ------------------------------------------------------------------------

_TOK = re.compile(r'"[^"]*"|[{}]|<=|>=|[<>=]|[^\s{}=<>"#]+|#[^\n]*')


@dataclass
class Node:
    key: str | None
    op: str
    val: Any            # str, or list[Node] for a block


def parse_script(text: str) -> list[Node]:
    """``key = value`` / ``key = { ... }`` / ``key < value`` / bare words, in order, duplicates kept.
    Comments are dropped. Quotes are stripped. A bare word is a node with ``key None``."""
    toks = [t for t in _TOK.findall(text) if not t.startswith("#")]
    pos = 0

    def block() -> list[Node]:
        nonlocal pos
        out: list[Node] = []
        while pos < len(toks):
            tok = toks[pos]
            if tok == "}":
                pos += 1
                return out
            if tok == "{":
                pos += 1
                out.append(Node(None, "", block()))
                continue
            nxt = toks[pos + 1] if pos + 1 < len(toks) else None
            if nxt in ("=", "<", ">", "<=", ">="):
                pos += 2
                if pos >= len(toks):
                    break
                value = toks[pos]
                pos += 1
                if value == "{":
                    out.append(Node(tok.strip('"'), nxt, block()))
                else:
                    out.append(Node(tok.strip('"'), nxt, value.strip('"')))
            else:
                pos += 1
                out.append(Node(None, "", tok.strip('"')))
        return out

    return block()


def _get(nodes: list[Node], key: str) -> Any | None:
    for n in nodes:
        if n.key == key:
            return n.val
    return None


def _all(nodes: list[Node], key: str) -> list[Any]:
    return [n.val for n in nodes if n.key == key]


def _words(val: Any) -> list[str]:
    """The bare words of a block (``{ a b c }``) and the keys of its ``k = yes`` entries."""
    if not isinstance(val, list):
        return [str(val)] if val is not None else []
    return [str(n.val) if n.key is None and not isinstance(n.val, list) else str(n.key)
            for n in val if n.key is not None or not isinstance(n.val, list)]


def _stats(val: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(val, list):
        for n in val:
            if n.key and not isinstance(n.val, list):
                try:
                    out[n.key] = float(n.val)
                except ValueError:
                    pass
    return out


def _num(val: Any) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def fmt(x: float) -> str:
    return f"{x:g}"


# --- what the files define ------------------------------------------------------------------------


@dataclass
class Slot:
    name: str
    required: bool
    categories: tuple[str, ...]


@dataclass
class Limit:
    modules: tuple[str, ...]
    categories: tuple[str, ...]
    op: str                 # "<", ">", "=", or "any"
    count: float
    title: str = ""

    def key(self) -> tuple:
        return (self.modules, self.categories)

    def describe(self) -> str:
        what = " or ".join([*self.modules, *(f"category {c}" for c in self.categories)]) or "any"
        return "no limit" if self.op == "any" else f"{what}: count {self.op} {fmt(self.count)}"


@dataclass
class Chassis:
    id: str
    kind: str
    archetype: str | None
    year: int | None
    abbreviation: str
    slots: dict[str, Slot]
    default_modules: dict[str, str]
    limits: list[Limit]
    upgrades: tuple[str, ...]
    stats: dict[str, float]
    resources: dict[str, float]
    is_archetype: bool
    unlock_techs: list[str] = field(default_factory=list)


@dataclass
class Module:
    id: str
    kind: str
    category: str
    abbreviation: str
    xp_cost: float | None
    add_stats: dict[str, float]
    multiply_stats: dict[str, float]
    average_stats: dict[str, float]
    resources: dict[str, float]
    dismantle_cost_ic: float | None
    converts_from: list[tuple[str, str, float | None]]     # ("module" | "module_category", id, IC)
    parent: str | None
    slot_categories: dict[str, set[str]]                   # slot -> categories this module lets in
    forbids_categories: tuple[str, ...]
    equipment_rules: dict[str, list[str]]                  # allow_/forbid_equipment_type* as written
    dlcs: tuple[str, ...]
    unlock_techs: list[str] = field(default_factory=list)


@dataclass
class Upgrade:
    id: str
    cost: str                       # land / naval / air
    max_level: int
    linear: float | None            # linear_cost.cost_by_level
    requirements: dict[int, list[str]]   # level -> technologies (all of them) needed at that level
    unparsed_requirements: set[int]      # levels whose requirement is not a plain has_tech list
    stats: dict[str, float]


@dataclass
class Defines:
    add: float
    replace: float
    convert: float
    remove: float
    team_flat: float
    base: dict[str, float]          # land / naval / air -> *_EQUIPMENT_BASE_COST
    ramp: dict[str, float]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


class DesignerData:
    """Everything the three designers read from a playset's files."""

    def __init__(self) -> None:
        self.chassis: dict[str, Chassis] = {}
        self.modules: dict[str, Module] = {}
        self.upgrades: dict[str, Upgrade] = {}
        self.defines: Defines | None = None
        self.defines_missing: list[str] = []
        self.category_kind: dict[str, str] = {}

    # --- loading ---

    @classmethod
    def load(cls, playset) -> DesignerData:
        data = cls()
        data._read_modules(playset)
        data._read_upgrades(playset)
        enablers = data._read_techs(playset)
        data._read_chassis(playset, enablers)
        data._read_defines(playset)
        for module in data.modules.values():
            module.unlock_techs = enablers["modules"].get(module.id, [])
        return data

    def _read_modules(self, playset) -> None:
        for kind, filename in MODULE_FILES.items():
            for path in playset.layered("common/units/equipment/modules"):
                if path.name != filename:
                    continue
                for top in parse_script(_read(path)):
                    if top.key != "equipment_modules" or not isinstance(top.val, list):
                        continue
                    dlcs = tuple(str(v) for v in _all(_get_block(top.val, "limit"), "has_dlc"))
                    for node in top.val:
                        if node.key and node.key != "limit" and isinstance(node.val, list):
                            module = _module(node.key, kind, node.val, dlcs)
                            if module is not None:
                                self.modules[module.id] = module
                                self.category_kind.setdefault(module.category, kind)

    def _read_upgrades(self, playset) -> None:
        for path in playset.layered("common/units/equipment/upgrades"):
            for top in parse_script(_read(path)):
                if top.key != "upgrades" or not isinstance(top.val, list):
                    continue
                for node in top.val:
                    if node.key and isinstance(node.val, list):
                        self.upgrades[node.key] = _upgrade(node.key, node.val)

    def _read_techs(self, playset) -> dict[str, dict[str, list[str]]]:
        chassis: dict[str, list[str]] = {}
        modules: dict[str, list[str]] = {}
        for path in playset.layered("common/technologies"):
            text = _read(path)
            if "enable_equipment" not in text:
                continue
            for top in parse_script(text):
                if top.key != "technologies" or not isinstance(top.val, list):
                    continue
                for tech in top.val:
                    if not (tech.key and isinstance(tech.val, list)):
                        continue
                    for group in _all(tech.val, "enable_equipments"):
                        for item in _words(group):
                            chassis.setdefault(item, []).append(tech.key)
                    for group in _all(tech.val, "enable_equipment_modules"):
                        for item in _words(group):
                            modules.setdefault(item, []).append(tech.key)
        return {"chassis": chassis, "modules": modules}

    def _read_chassis(self, playset, enablers) -> None:
        raw: dict[str, list[Node]] = {}
        for path in playset.layered("common/units/equipment"):
            if "/modules/" in path.as_posix() or "/upgrades/" in path.as_posix():
                continue
            text = _read(path)
            if "module_slots" not in text:
                continue
            for top in parse_script(text):
                if top.key == "equipments" and isinstance(top.val, list):
                    for node in top.val:
                        if node.key and isinstance(node.val, list):
                            raw[node.key] = node.val
        memo: dict[str, dict] = {}

        def resolve(ident: str, depth: int = 0) -> dict:
            if ident in memo:
                return memo[ident]
            body = raw.get(ident)
            out: dict = {"slots": None, "defaults": {}, "limits": {}, "upgrades": (), "stats": {},
                         "resources": {}}
            if body is None or depth > 12:
                return out
            parents = [p for p in (_get(body, "archetype"), _get(body, "parent")) if isinstance(p, str)]
            for p in parents:
                inherited = resolve(p, depth + 1)
                if inherited["slots"] is not None:
                    out["slots"] = inherited["slots"]
                out["defaults"].update(inherited["defaults"])
                out["limits"].update(inherited["limits"])
                if inherited["upgrades"]:
                    out["upgrades"] = inherited["upgrades"]
                out["stats"].update(inherited["stats"])
                out["resources"].update(inherited["resources"])
            slots_val = _get(body, "module_slots")
            if isinstance(slots_val, list):
                out["slots"] = _slots(slots_val, out["slots"])
            defaults = _get(body, "default_modules")
            if isinstance(defaults, list):
                out["defaults"].update({n.key: str(n.val) for n in defaults
                                        if n.key and not isinstance(n.val, list)})
            for lim in _all(body, "module_count_limit"):
                limit = _limit(lim)
                if limit is not None:
                    out["limits"][limit.key()] = limit
            ups = _get(body, "upgrades")
            if isinstance(ups, list):
                out["upgrades"] = tuple(_words(ups))
            for n in body:
                if n.key and not isinstance(n.val, list) and n.key not in _TEXT_KEYS:
                    value = _num(n.val)
                    if value is not None:
                        out["stats"][n.key] = value
            res = _get(body, "resources")
            if isinstance(res, list):
                out["resources"].update(_stats(res))
            memo[ident] = out
            return out

        for ident, body in raw.items():
            info = resolve(ident)
            if not info["slots"] or _get(body, "module_slots") is None:
                continue        # a legacy type (destroyer_1, fighter_equipment_1) has no modules
            categories = {c for s in info["slots"].values() for c in s.categories}
            kinds = [self.category_kind[c] for c in categories if c in self.category_kind]
            if not kinds:
                continue
            archetype = _get(body, "archetype")
            self.chassis[ident] = Chassis(
                id=ident, kind=max(set(kinds), key=kinds.count),
                archetype=archetype if isinstance(archetype, str) else None,
                year=int(_num(_get(body, "year")) or 0) or None,
                abbreviation=str(_get(body, "abbreviation") or ""),
                slots=info["slots"], default_modules=info["defaults"],
                limits=list(info["limits"].values()), upgrades=info["upgrades"],
                stats=info["stats"], resources=info["resources"],
                is_archetype=str(_get(body, "is_archetype")) == "yes",
                unlock_techs=enablers["chassis"].get(ident, []))

    def _read_defines(self, playset) -> None:
        values: dict[str, float] = {}
        for path in playset.layered("common/defines", "*.lua"):
            for name, number in re.findall(r"^\s*([A-Z0-9_]+)\s*=\s*(-?[0-9.]+)", _read(path), re.M):
                values[name] = float(number)
        need = ["EQUIPMENT_MODULE_ADD_XP_COST", "EQUIPMENT_MODULE_REPLACE_XP_COST",
                "EQUIPMENT_MODULE_CONVERT_XP_COST", "EQUIPMENT_MODULE_REMOVE_XP_COST",
                "DESIGN_TEAM_CHANGE_XP_COST"]
        for k in ("LAND", "NAVAL", "AIR"):
            need += [f"{k}_EQUIPMENT_BASE_COST", f"{k}_EQUIPMENT_RAMP_COST"]
        self.defines_missing = [n for n in need if n not in values]
        if self.defines_missing:
            self.defines = None
            return
        self.defines = Defines(
            add=values["EQUIPMENT_MODULE_ADD_XP_COST"], replace=values["EQUIPMENT_MODULE_REPLACE_XP_COST"],
            convert=values["EQUIPMENT_MODULE_CONVERT_XP_COST"],
            remove=values["EQUIPMENT_MODULE_REMOVE_XP_COST"],
            team_flat=values["DESIGN_TEAM_CHANGE_XP_COST"],
            base={k.lower(): values[f"{k}_EQUIPMENT_BASE_COST"] for k in ("LAND", "NAVAL", "AIR")},
            ramp={k.lower(): values[f"{k}_EQUIPMENT_RAMP_COST"] for k in ("LAND", "NAVAL", "AIR")})

    # --- queries ---

    def types(self, kind: str) -> list[Chassis]:
        """The chassis a design can be made on: types, not archetypes."""
        return sorted((c for c in self.chassis.values() if c.kind == kind and not c.is_archetype),
                      key=lambda c: (c.archetype or "", c.year or 0, c.id))

    def category_modules(self, kind: str, category: str) -> list[Module]:
        return sorted((m for m in self.modules.values() if m.kind == kind and m.category == category),
                      key=lambda m: m.id)

    def categories(self, kind: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for module in sorted(self.modules.values(), key=lambda m: m.id):
            if module.kind == kind:
                out.setdefault(module.category, []).append(module.id)
        return out


_TEXT_KEYS = {"year", "priority", "visual_level", "abbreviation", "archetype", "parent", "picture", "type",
              "group_by", "model", "sprite", "derived_variant_name", "alias", "substitute",
              "interface_category"}


def _get_block(nodes: list[Node], key: str) -> list[Node]:
    val = _get(nodes, key)
    return val if isinstance(val, list) else []


def _slots(nodes: list[Node], inherited: dict[str, Slot] | None = None) -> dict[str, Slot]:
    slots: dict[str, Slot] = {}
    for n in nodes:
        if not n.key:
            continue
        if isinstance(n.val, list):
            cats = tuple(_words(_get(n.val, "allowed_module_categories")))
            slots[n.key] = Slot(n.key, str(_get(n.val, "required")) == "yes", cats)
        elif n.val == "inherit" and inherited and n.key in inherited:
            slots[n.key] = inherited[n.key]
        elif n.val in slots:
            slots[n.key] = Slot(n.key, slots[n.val].required, slots[n.val].categories)
    return slots


def _limit(nodes: list[Node]) -> Limit | None:
    modules = tuple(str(v) for v in _all(nodes, "module"))
    categories = tuple(str(v) for v in _all(nodes, "category"))
    for n in nodes:
        if n.key == "count":
            if n.val == "any":
                return Limit(modules, categories, "any", 0)
            number = _num(n.val)
            if number is None:
                return None
            return Limit(modules, categories, n.op, number, str(_get(nodes, "title") or ""))
    return None


def _module(ident: str, kind: str, body: list[Node], dlcs: tuple[str, ...]) -> Module | None:
    category = _get(body, "category")
    if not isinstance(category, str):
        return None
    converts = []
    for conv in _all(body, "can_convert_from"):
        if not isinstance(conv, list):
            continue
        cost = _num(_get(conv, "convert_cost_ic"))
        for n in conv:
            if n.key in ("module", "module_category") and not isinstance(n.val, list):
                converts.append((n.key, str(n.val), cost))
    slot_cats: dict[str, set[str]] = {}
    for block in _all(body, "allowed_module_categories"):
        if isinstance(block, list):
            for n in block:
                if n.key and isinstance(n.val, list):
                    slot_cats.setdefault(n.key, set()).update(_words(n.val))
    rules = {}
    for key in ("allow_equipment_type", "forbid_equipment_type", "forbid_equipment_type_exact_match",
                "forbid_equipment_type_exact_match_for_category", "add_equipment_type"):
        values = [w for v in _all(body, key) for w in (_words(v) if isinstance(v, list) else [str(v)])]
        if key.endswith("for_category"):
            values = [f"{n.key}={n.val}" for v in _all(body, key) if isinstance(v, list) for n in v if n.key]
        if values:
            rules[key] = values
    res = _get(body, "build_cost_resources")
    return Module(
        id=ident, kind=kind, category=category, abbreviation=str(_get(body, "abbreviation") or ""),
        xp_cost=_num(_get(body, "xp_cost")), add_stats=_stats(_get(body, "add_stats")),
        multiply_stats=_stats(_get(body, "multiply_stats")),
        average_stats=_stats(_get(body, "add_average_stats")),
        resources=_stats(res) if isinstance(res, list) else {},
        dismantle_cost_ic=_num(_get(body, "dismantle_cost_ic")), converts_from=converts,
        parent=_get(body, "parent") if isinstance(_get(body, "parent"), str) else None,
        slot_categories=slot_cats,
        forbids_categories=tuple(w for v in _all(body, "forbid_module_categories") for w in _words(v)),
        equipment_rules=rules, dlcs=dlcs)


def _upgrade(ident: str, body: list[Node]) -> Upgrade:
    linear = _get(body, "linear_cost")
    requirements: dict[int, list[str]] = {}
    unparsed: set[int] = set()
    reqs = _get(body, "level_requirements")
    if isinstance(reqs, list):
        for n in reqs:
            level = _num(n.key)
            if level is None or not isinstance(n.val, list):
                continue
            techs = [str(x.val) for x in n.val if x.key == "has_tech"]
            if len(techs) != len(n.val):
                unparsed.add(int(level))
            requirements[int(level)] = techs
    return Upgrade(
        id=ident, cost=str(_get(body, "cost") or ""), max_level=int(_num(_get(body, "max_level")) or 0),
        linear=_num(_get(linear, "cost_by_level")) if isinstance(linear, list) else None,
        requirements=requirements, unparsed_requirements=unparsed, stats=_stats(body))


# --- the country's own designs, from a save -------------------------------------------------------

_ENTRY = re.compile(r"^\t([A-Za-z0-9_]+)=\{[ \t\r]*\n(.*?)^\t\}", re.M | re.S)
_TOP = re.compile(r"^equipments[ \t]*=[ \t]*\{[ \t\r]*$", re.M)


@dataclass
class Variant:
    type: str
    name: str | None
    version: int
    parent_id: int | None
    obsolete: bool
    is_frame: bool
    modules: dict[str, str]
    upgrades: dict[str, int]
    id: int | None = None

    def describe(self) -> dict:
        return {"name": self.name, "type": self.type, "version": self.version,
                "obsolete": self.obsolete, "default_design": self.is_frame,
                "modules": dict(self.modules), "upgrades": dict(self.upgrades)}


def read_variants(text: str, tag: str) -> list[Variant]:
    """The designs ``tag`` owns (``creator="TAG"`` entries of the save's ``equipments`` block that
    have a ``modules`` block: a modular chassis and its variants)."""
    start = _TOP.search(text)
    if start is None:
        return []
    end = re.compile(r"^\}", re.M).search(text, start.end())
    chunk = text[start.end(): end.start() if end else len(text)]
    out: list[Variant] = []
    mark = f'creator="{tag}"'
    for match in _ENTRY.finditer(chunk):
        body = match.group(2)
        if mark not in body or "modules=" not in body:
            continue
        nodes = parse_script(body.replace("=", " = ").replace("{", " { ").replace("}", " } "))
        modules = {n.key: str(n.val) for n in _get_block(nodes, "modules")
                   if n.key and not isinstance(n.val, list)}
        upgrades: dict[str, int] = {}
        outer = _get_block(nodes, "upgrades")
        for inner in _all(outer, "upgrades") or [outer]:
            if isinstance(inner, list):
                # `{ tank_nsb_engine_upgrade 2}` records: a block of bare words (name, level).
                for rec in inner:
                    if rec.key is None and isinstance(rec.val, list) and len(rec.val) == 2:
                        level = _num(rec.val[1].val)
                        if level is not None:
                            upgrades[str(rec.val[0].val)] = int(level)
        ident = _get(nodes, "id")
        parent = _get(nodes, "parent_id")
        out.append(Variant(
            type=match.group(1), name=_get(nodes, "name") if isinstance(_get(nodes, "name"), str) else None,
            version=int(_num(_get(nodes, "version")) or 0),
            parent_id=int(_num(_get(parent, "id")) or 0) if isinstance(parent, list) else None,
            obsolete=str(_get(nodes, "obsolete")) == "yes", is_frame=str(_get(nodes, "is_frame")) == "yes",
            modules=modules, upgrades=upgrades,
            id=int(_num(_get(ident, "id")) or 0) if isinstance(ident, list) else None))
    return out


def designs_view(data: DesignerData, variants: list[Variant], kind: str) -> dict:
    """``list_designs``: the country's own designs of one kind."""
    rows = []
    for v in variants:
        chassis = data.chassis.get(v.type)
        if chassis is None or chassis.kind != kind:
            continue
        row = v.describe()
        parent = next((p for p in variants if p.id is not None and p.id == v.parent_id), None)
        if parent is not None:
            row["parent"] = parent.name or f"{parent.type} v{parent.version}"
            row["parent_version"] = parent.version
        rows.append(row)
    rows.sort(key=lambda r: (r["type"], r["version"]))
    live = [r for r in rows if not r["obsolete"]]
    return {"kind": kind, "count": len(rows), "current": len(live),
            "note": "version is the number create_equipment_variant takes as parent_version (within "
                    "the same type); 'default_design' is the chassis' own first design; obsolete "
                    "designs are kept, the production screen hides them. Read from the newest text "
                    "save: a design made since is not listed until the next autosave.",
            "designs": rows}


# --- describing an option -------------------------------------------------------------------------


def _unlock(techs: list[str], dlcs: tuple[str, ...] | list[str], kind: str) -> dict:
    return {"technologies": list(techs) or None,
            "dlc": list(dlcs) or None,
            "note": None if techs else "no technology of this playset unlocks it (script or the "
                                       "start of the game does): the harness cannot test it, so "
                                       "design_equipment refuses a change that adds it"}


def describe_option(data: DesignerData, kind: str, ident: str | None, active: frozenset[str] | None
                    ) -> dict:
    """``describe_design_option``: the overview of a designer, or one chassis, module, module
    category or upgrade with what the files say about it."""
    gap = dlc_mod.feature_refusal(KIND_FEATURE[kind], active)
    if gap:
        return {"kind": kind, "exists": False, "note": gap + ": there is no such designer in this game"}
    xp = KIND_XP[kind][0]
    if not ident:
        return {
            "kind": kind, "experience": f"{xp}_experience",
            "chassis": [{"id": c.id, "archetype": c.archetype, "year": c.year,
                         "unlocked_by": c.unlock_techs or None, "slots": len(c.slots)}
                        for c in data.types(kind)],
            "module_categories": {cat: len(ids) for cat, ids in data.categories(kind).items()},
            "upgrades": sorted(u.id for u in data.upgrades.values() if UPGRADE_COST_KIND.get(u.cost) == kind),
            "note": "describe_design_option kind=<kind> id=<chassis | module | category | upgrade> "
                    "for the detail; the cost rule is in design_equipment"}
    chassis = data.chassis.get(ident)
    if chassis is not None and chassis.kind == kind:
        return {
            "kind": kind, "type": "chassis", "id": chassis.id, "archetype": chassis.archetype,
            "year": chassis.year, "buildable_type": not chassis.is_archetype,
            "unlock": _unlock(chassis.unlock_techs, [], kind),
            "slots": [{"slot": s.name, "required": s.required, "categories": list(s.categories),
                       "default": chassis.default_modules.get(s.name, EMPTY)}
                      for s in chassis.slots.values()],
            "limits": [lim.describe() for lim in chassis.limits],
            "base_stats": chassis.stats, "resources": chassis.resources,
            "upgrades": [{"id": u, "max_level": data.upgrades[u].max_level} for u in chassis.upgrades
                         if u in data.upgrades]}
    module = data.modules.get(ident)
    if module is not None and module.kind == kind:
        return {
            "kind": kind, "type": "module", "id": module.id, "category": module.category,
            "unlock": _unlock(module.unlock_techs, module.dlcs, kind),
            "xp_cost": module.xp_cost, "add_stats": module.add_stats,
            "multiply_stats": module.multiply_stats, "add_average_stats": module.average_stats,
            "resources": module.resources, "dismantle_cost_ic": module.dismantle_cost_ic,
            "converts_from": [{"by": by, "id": ref, "convert_cost_ic": ic}
                              for by, ref, ic in module.converts_from],
            "lets_in": {slot: sorted(cats) for slot, cats in module.slot_categories.items()} or None,
            "forbids_categories": list(module.forbids_categories) or None,
            "equipment_rules": module.equipment_rules or None,
            "fits": sorted(c.id for c in data.types(kind)
                           if any(module.category in s.categories for s in c.slots.values())),
            "design_cost": "see design_equipment: the experience a change costs is the define for "
                           "the kind of change plus this xp_cost"}
    if ident in data.category_kind and data.category_kind[ident] == kind:
        return {"kind": kind, "type": "category", "id": ident,
                "modules": [{"id": m.id, "xp_cost": m.xp_cost, "unlocked_by": m.unlock_techs or None,
                             "add_stats": m.add_stats} for m in data.category_modules(kind, ident)]}
    upgrade = data.upgrades.get(ident)
    if upgrade is not None and UPGRADE_COST_KIND.get(upgrade.cost) == kind:
        return {"kind": kind, "type": "upgrade", "id": upgrade.id, "max_level": upgrade.max_level,
                "cost_kind": upgrade.cost, "linear_cost_per_level": upgrade.linear,
                "level_requirements": {str(k): v for k, v in sorted(upgrade.requirements.items())},
                "per_level_stats": upgrade.stats}
    pool = [c.id for c in data.types(kind)] + [m.id for m in data.modules.values() if m.kind == kind] \
        + list(data.categories(kind)) + [u.id for u in data.upgrades.values()]
    return {"error": f"{ident!r} is not a {kind} chassis, module, module category or upgrade",
            "nearest": difflib.get_close_matches(ident, pool, n=6)}


# --- planning a design ----------------------------------------------------------------------------


@dataclass
class Change:
    slot: str
    old: str
    new: str
    op: str                 # add / remove / replace / convert
    define: float
    module_xp: float

    @property
    def cost(self) -> float:
        return self.define + self.module_xp


@dataclass
class UpgradeStep:
    upgrade: str
    old: int
    new: int
    cost: float


@dataclass
class Plan:
    kind: str
    chassis: Chassis
    modules: dict[str, str]
    upgrades: dict[str, int]
    parent_version: int | None
    parent_label: str
    changes: list[Change]
    steps: list[UpgradeStep]
    cost: float
    dlcs: list[str]
    tech_groups: list[list[str]]
    unchecked: list[str]

    @property
    def experience(self) -> str:
        return KIND_XP[self.kind][0]

    def guard(self, dlc_test: bool = True) -> str:
        """The ``limit`` body. ``dlc_test`` False leaves out the ``has_dlc`` tests: a DLC name has
        spaces, and the typed console splits quoted text on them (the inbox does not)."""
        parts = [f'has_dlc = "{d}"' for d in self.dlcs] if dlc_test else []
        for group in self.tech_groups:
            if len(group) == 1:
                parts.append(f"has_tech = {group[0]}")
            else:
                parts.append("OR = { " + " ".join(f"has_tech = {t}" for t in group) + " }")
        trigger = KIND_XP[self.kind][1]
        if self.cost > 0:
            parts.append(f"{trigger} > {fmt(self.cost)}")
        return " ".join(parts)

    def body(self, name: str, obsolete_older: bool = False) -> str:
        pieces = [f'name = "{name}"', f"type = {self.chassis.id}"]
        if self.parent_version is not None:
            pieces.append(f"parent_version = {self.parent_version}")
        pieces.append("obsolete = no")
        if obsolete_older:
            pieces.append("mark_older_equipment_obsolete = yes")
        mods = {s: m for s, m in self.modules.items() if self.changed(s)}
        if mods:
            pieces.append("modules = { " + " ".join(f"{s} = {m}" for s, m in mods.items()) + " }")
        if self.steps:
            pieces.append("upgrades = { " + " ".join(
                f"{u} = {lvl}" for u, lvl in self.upgrades.items()) + " }")
        effect = f"create_equipment_variant = {{ {' '.join(pieces)} }}"
        if self.cost > 0:
            effect += f" {KIND_XP[self.kind][2]} = -{fmt(self.cost)}"
        return effect

    def changed(self, slot: str) -> bool:
        return any(c.slot == slot for c in self.changes)

    def breakdown(self) -> dict:
        return {"experience": f"{self.experience}_experience", "cost": self.cost,
                "exact": False,
                "modules": [{"slot": c.slot, "from": c.old, "to": c.new, "change": c.op,
                             "define": c.define, "module_xp_cost": c.module_xp} for c in self.changes],
                "upgrades": [{"upgrade": s.upgrade, "from": s.old, "to": s.new, "upper_bound": s.cost}
                             for s in self.steps]}


def _level_cost(data: DesignerData, up: Upgrade, old: int, new: int, total_after: int) -> float:
    d = data.defines
    assert d is not None
    kind = up.cost if up.cost in d.base else "land"
    if up.linear is not None:
        # linear_cost: read as level n costing n * cost_by_level, the dearer of the two readings
        # (flat cost_by_level per level is the other); an upper bound.
        return sum(n * up.linear for n in range(old + 1, new + 1))
    return (new - old) * (d.base[kind] + total_after * d.ramp[kind])


def _count_ok(limit: Limit, modules: dict[str, str], data: DesignerData) -> bool:
    if limit.op == "any":
        return True
    matched = 0
    for module_id in modules.values():
        if module_id == EMPTY:
            hit = "empty" in limit.modules
        else:
            m = data.modules.get(module_id)
            hit = ("any" in limit.modules or "any" in limit.categories or module_id in limit.modules
                   or (m is not None and m.category in limit.categories))
        matched += 1 if hit else 0
    if limit.op == "<":
        return matched < limit.count
    if limit.op == ">":
        return matched > limit.count
    if limit.op == "<=":
        return matched <= limit.count
    if limit.op == ">=":
        return matched >= limit.count
    return matched == limit.count


def plan_design(data: DesignerData, kind: str, base: str, modules: dict[str, str] | None,
                upgrades: dict[str, int] | None, *, parent: Variant | None = None,
                default_modules: dict[str, str] | None = None,
                active: frozenset[str] | None = None) -> Plan:
    """The checks and the price of one design. ``parent`` is the saved design it is made from (None:
    the chassis' default design, whose modules are ``default_modules`` or the chassis' own defaults).
    Raises ``DesignRefused`` with the reason when the designer would not take it or the harness
    cannot price it."""
    if kind not in KINDS:
        raise DesignRefused(f"kind is one of {', '.join(KINDS)}")
    gap = dlc_mod.feature_refusal(KIND_FEATURE[kind], active)
    if gap:
        raise DesignRefused(gap)
    chassis = data.chassis.get(base)
    if chassis is None or chassis.kind != kind:
        near = difflib.get_close_matches(base, [c.id for c in data.types(kind)], n=5)
        raise DesignRefused(f"{base!r} is not a {kind} chassis of this playset"
                            + (f" (nearest: {', '.join(near)})" if near else "")
                            + "; describe_design_option lists them")
    if chassis.is_archetype:
        raise DesignRefused(f"{base} is an archetype (a family name), not a chassis type: pick one "
                            f"of its types (describe_design_option kind={kind})")
    if data.defines is None:
        raise DesignRefused("the experience a design costs cannot be established: this playset's "
                            "defines do not give " + ", ".join(data.defines_missing))
    d = data.defines
    if not chassis.unlock_techs:
        raise DesignRefused(f"nothing in this playset unlocks {chassis.id}, so the harness cannot "
                            "tell that the country may use it")
    if parent is not None and parent.type != chassis.id:
        raise DesignRefused(f"the parent design is a {parent.type}, not a {chassis.id}")
    start = dict(parent.modules) if parent is not None else dict(
        default_modules if default_modules is not None else chassis.default_modules)
    start_upgrades = dict(parent.upgrades) if parent is not None else {}
    final = {s: start.get(s, EMPTY) for s in chassis.slots}
    asked = dict(modules or {})
    for slot, module_id in asked.items():
        if slot not in chassis.slots:
            raise DesignRefused(f"{chassis.id} has no slot {slot!r}: {', '.join(chassis.slots)}")
        if module_id != EMPTY:
            module = data.modules.get(module_id)
            if module is None or module.kind != kind:
                near = difflib.get_close_matches(module_id, [m for m, x in data.modules.items()
                                                             if x.kind == kind], n=5)
                raise DesignRefused(f"{module_id!r} is not a {kind} module"
                                    + (f" (nearest: {', '.join(near)})" if near else ""))
        final[slot] = module_id
    # What a slot takes: its own categories, plus the ones a module elsewhere in the design lets in
    # (a medium turret opens the main armament slot to medium guns: ``allowed_module_categories``
    # inside the module).
    opened: dict[str, set[str]] = {}
    for module_id in final.values():
        module = data.modules.get(module_id)
        for target, cats in (module.slot_categories.items() if module else ()):
            opened.setdefault(target, set()).update(cats)
    for slot, module_id in asked.items():
        if module_id == EMPTY:
            continue
        module = data.modules[module_id]
        takes = set(chassis.slots[slot].categories) | opened.get(slot, set())
        if module.category not in takes:
            raise DesignRefused(
                f"{module_id} (category {module.category}) does not go in slot {slot} of "
                f"{chassis.id}: it takes {', '.join(sorted(takes))}"
                + ("; a module in another slot (a turret) may open more" if slot_openers(data, chassis, slot)
                   else ""))
    # a module whose own slot has lost its category (the turret changed under the gun)
    for slot, module_id in final.items():
        module = data.modules.get(module_id)
        if module is None or module_id == start.get(slot, EMPTY) and not asked:
            continue
        takes = set(chassis.slots[slot].categories) | opened.get(slot, set())
        if module.category not in takes:
            raise DesignRefused(
                f"{module_id} in {slot} (category {module.category}) is no longer allowed once the "
                f"other modules change: the slot takes {', '.join(sorted(takes))}")
    for module_id in final.values():
        module = data.modules.get(module_id)
        if module is None:
            continue
        for cat in module.forbids_categories:
            clash = [m for m in final.values() if data.modules.get(m) and data.modules[m].category == cat]
            if clash:
                raise DesignRefused(f"{module_id} cannot sit beside {clash[0]} (category {cat})")
    for slot, slot_def in chassis.slots.items():
        if slot_def.required and final[slot] == EMPTY:
            raise DesignRefused(f"slot {slot} is required: the designer will not save a design "
                                "with it empty")
    for limit in chassis.limits:
        if not _count_ok(limit, final, data):
            raise DesignRefused(f"the chassis limits modules ({limit.describe()}): this design breaks it")
    changes: list[Change] = []
    for slot, new in final.items():
        old = start.get(slot, EMPTY)
        if old == new:
            continue
        if old == EMPTY:
            op, define = "add", d.add
        elif new == EMPTY:
            op, define = "remove", d.remove
        else:
            new_mod, old_mod = data.modules[new], data.modules.get(old)
            related = old_mod is not None and any(
                (by == "module" and ref == old) or (by == "module_category" and ref == old_mod.category)
                for by, ref, _ic in new_mod.converts_from)
            op, define = ("convert", d.convert) if related else ("replace", d.replace)
        module_xp = 0.0
        if new != EMPTY:
            module_xp = data.modules[new].xp_cost or 0.0
        changes.append(Change(slot, old, new, op, define, module_xp))
    tech_groups: list[list[str]] = [list(dict.fromkeys(chassis.unlock_techs))]
    dlcs = [dlc_mod.feature(KIND_FEATURE[kind]).any_of[0]]
    for c in changes:
        if c.new == EMPTY:
            continue
        module = data.modules[c.new]
        for extra in module.dlcs:
            if extra not in dlcs:
                dlcs.append(extra)
        if module.unlock_techs:
            group = list(dict.fromkeys(module.unlock_techs))
            if group not in tech_groups:
                tech_groups.append(group)
        elif c.new != chassis.default_modules.get(c.slot):
            raise DesignRefused(f"nothing in this playset unlocks {c.new}, so the harness cannot tell "
                                "that the country may use it")
    final_upgrades = dict(start_upgrades)
    steps: list[UpgradeStep] = []
    for name, level in (upgrades or {}).items():
        up = data.upgrades.get(name)
        if up is None or name not in chassis.upgrades:
            raise DesignRefused(f"{chassis.id} has no upgrade {name!r}: "
                                f"{', '.join(chassis.upgrades) or 'none'}")
        if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= up.max_level:
            raise DesignRefused(f"{name} goes from 0 to {up.max_level}")
        have = start_upgrades.get(name, 0)
        if level < have:
            raise DesignRefused(f"{name} is at level {have} on the parent: the designer only raises")
        if level == have:
            continue
        for needed in sorted(up.requirements):
            if have < needed <= level:
                if needed in up.unparsed_requirements:
                    raise DesignRefused(f"{name} level {needed} has a requirement the harness cannot "
                                        "test (not a plain has_tech)")
                for tech in up.requirements[needed]:
                    if [tech] not in tech_groups:
                        tech_groups.append([tech])
        final_upgrades[name] = level
        steps.append(UpgradeStep(name, have, level, 0.0))
    total_after = sum(final_upgrades.values())
    for step in steps:
        step.cost = _level_cost(data, data.upgrades[step.upgrade], step.old, step.new, total_after)
    if not changes and not steps:
        raise DesignRefused("this is the parent design unchanged: nothing to create")
    cost = sum(c.cost for c in changes) + sum(s.cost for s in steps) + d.team_flat
    unchecked = [
        "the experience is an UPPER BOUND: for each changed slot the harness charges the define for "
        "the kind of change plus the new module's own xp_cost, because the files do not say how "
        "the two combine; the screen may ask less, never more (docs/designers.md)",
        "cost modifiers on the screen's price (land/naval/air_equipment_upgrade_xp_cost from "
        "ideas and advisors) cannot be read by script",
        "which equipment type a plane or an anti-air tank becomes, and the modules that depend on "
        "that role (allow_/forbid_equipment_type*), is derived by the game from the modules and is "
        "not tested here",
        "that a design of this name does not already exist (the game allows duplicates)",
    ]
    if parent is not None:
        unchecked.append("the parent design's modules and upgrades are read from the newest save: a "
                         "change made since then shifts the real price")
    elif not asked and not upgrades:
        pass
    else:
        unchecked.append("the default design's modules are the chassis' default_modules")
    return Plan(kind=kind, chassis=chassis, modules=final, upgrades=final_upgrades,
                parent_version=parent.version if parent is not None else None,
                parent_label=(parent.name or f"{parent.type} v{parent.version}") if parent is not None
                else "the chassis' default design",
                changes=changes, steps=steps, cost=cost, dlcs=dlcs, tech_groups=tech_groups,
                unchecked=unchecked)


def slot_openers(data: DesignerData, chassis: Chassis, slot: str) -> list[str]:
    """The modules that open ``slot`` to more categories than the chassis lists for it."""
    return sorted(m.id for m in data.modules.values()
                  if m.kind == chassis.kind and slot in m.slot_categories)


def find_parent(variants: list[Variant], chassis_id: str, parent: Any) -> Variant:
    """The saved design ``parent`` names (a version number, or a name) among ``chassis_id``'s."""
    pool = [v for v in variants if v.type == chassis_id]
    if isinstance(parent, int) and not isinstance(parent, bool) or str(parent).isdigit():
        found = [v for v in pool if v.version == int(parent)]
    else:
        found = [v for v in pool if (v.name or "").lower() == str(parent).lower()]
    if not found:
        names = ", ".join(f"{v.name or 'default'} (v{v.version})" for v in pool) or "none saved"
        raise DesignRefused(f"no {chassis_id} design {parent!r} in the newest save: {names}")
    return found[-1]


def default_design(variants: list[Variant], chassis_id: str) -> Variant | None:
    """The chassis' own first design (``is_frame``), when the save has it."""
    frames = [v for v in variants if v.type == chassis_id and v.is_frame]
    return frames[0] if frames else None
