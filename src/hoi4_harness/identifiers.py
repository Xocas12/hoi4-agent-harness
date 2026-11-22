"""What identifiers exist in this playset: focuses, technologies, states, tags.

``set_national_focus`` takes a free-text id and nothing in the harness knew which
ids are real. A live run produced ``SWE_industrialization``,
``SWE_industrialization_effort``, ``SWE_expand_industry`` and
``SWE_defense_industrialization`` across one campaign -- a model inventing
plausible ids because nothing told it otherwise. On a total conversion it cannot
even guess plausibly.

So the harness reads the vocabulary from the files the game itself loads: the
install, then each active mod in load order. A mod file at the same relative
path replaces the game's (or an earlier mod's), and a mod's ``replace_path``
drops everything loaded before it under that folder -- the same layering the
game applies. From that:

* an invented id is rejected locally, with the nearest real ones, instead of
  reaching the game and silently doing nothing;
* the brief can offer the *relevant slice* -- the focuses this country can take
  next, not the whole tree and never every technology;
* a transcript records which playset produced it.

The parser is the savegame adapter's Clausewitz tokenizer with comments
stripped. Script files use more syntax than it models (comparison operators,
inline math), but ids sit in plain ``key = value`` pairs, which is all this
reads.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .adapters.savegame import parse_clausewitz
from .dlc import DlcSet
from .techtree import Gridbox, parse_gridboxes
from .types import ActionCall, ActionResult, GameState

VANILLA = "vanilla"

#: Arguments that name a country, per action.
TAG_ARGUMENTS = {
    "set_ai_directive": "target",
    "set_ai_posture": "theater",
    "diplomacy": "target",
    "diplomacy_act": "target",
    "set_occupation_law": "occupied",
    "set_trade": "from_country",
}

#: Words the harness accepts in place of a real state, because its own reflexes
#: and the mock use them. A real adapter resolves them itself.
STATE_ALIASES = {"capital"}


# --- the playset -------------------------------------------------------------


@dataclass
class Mod:
    path: Path
    name: str
    version: str = ""
    replace_paths: list[str] = field(default_factory=list)
    #: The Steam Workshop file id (``remote_file_id`` in the descriptor, or the
    #: numeric folder name under ``workshop/content``); None for a local mod.
    remote_file_id: str | None = None

    @classmethod
    def load(cls, path: Path) -> Mod:
        path = Path(path)
        descriptor = path / "descriptor.mod"
        name, version, replace, remote = path.name, "", [], None
        if descriptor.is_file():
            text = descriptor.read_text(encoding="utf-8-sig", errors="replace")
            name = _descriptor_value(text, "name") or name
            version = _descriptor_value(text, "version") or ""
            replace = re.findall(r'^\s*replace_path\s*=\s*"([^"]+)"', text, flags=re.M)
            remote = _descriptor_value(text, "remote_file_id")
        if not remote and path.name.isdigit() and path.parent.name == "394360":
            remote = path.name
        return cls(path=path, name=name, version=version, remote_file_id=remote or None,
                   replace_paths=[p.replace("\\", "/").strip("/") for p in replace])


def _resolve_descriptor(path: Path) -> Path:
    """A launcher ``.mod`` file (``Documents/.../mod/ugc_123.mod``) stands for
    the folder its ``path=`` names, so an installed mod can be named the way the
    launcher names it."""
    if path.is_file() and path.suffix.lower() == ".mod":
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        target = _descriptor_value(text, "path") or _descriptor_value(text, "archive")
        if not target:
            raise FileNotFoundError(f"{path} names no mod folder (no path= line)")
        return Path(target)
    return path


def _descriptor_value(text: str, key: str) -> str | None:
    match = re.search(rf'^\s*{key}\s*=\s*"([^"]*)"', text, flags=re.M)
    return match.group(1) if match else None


@dataclass
class Playset:
    """The install plus the active mods, in load order (last wins)."""

    game_dir: Path
    mods: list[Mod] = field(default_factory=list)
    #: Which DLCs are on (dlc.py). ``active`` None means unknown: ``has_dlc`` stays unevaluated.
    dlc: DlcSet = field(default_factory=DlcSet)

    @property
    def dlcs(self) -> frozenset[str] | None:
        """The active DLC names, or None when they are not known."""
        return self.dlc.active

    @classmethod
    def from_paths(cls, game_dir: Path, mod_dirs: Iterable[Path] = (),
                   dlc: DlcSet | None = None) -> Playset:
        game_dir = Path(game_dir)
        if not (game_dir / "common").is_dir():
            raise FileNotFoundError(
                f"{game_dir} does not look like a HOI4 install: no common/ folder. Point "
                "HOI4_GAME_DIR at the folder holding hoi4.exe, not the Documents folder."
            )
        mods = []
        for mod_dir in mod_dirs:
            mod_dir = _resolve_descriptor(Path(mod_dir))
            if not Path(mod_dir).is_dir():
                raise FileNotFoundError(f"mod folder {mod_dir} does not exist")
            mods.append(Mod.load(Path(mod_dir)))
        return cls(game_dir=game_dir, mods=mods, dlc=dlc if dlc is not None else DlcSet())

    @property
    def name(self) -> str:
        """``vanilla``, or the mods' names in load order."""
        # The bridge mod is plumbing, not content: a vanilla game with only the
        # bridge loaded is still vanilla to every scenario and guidance pack.
        content = [mod.name for mod in self.mods if not _is_bridge(mod)]
        return " + ".join(content) if content else VANILLA

    @property
    def is_vanilla(self) -> bool:
        return self.name == VANILLA

    def layered(self, folder: str, pattern: str = "*.txt") -> list[Path]:
        """Every file under ``folder`` the game would load, overrides applied."""
        files: dict[str, Path] = {}
        for root, replace in [(self.game_dir, [])] + [(m.path, m.replace_paths) for m in self.mods]:
            for replaced in replace:
                if folder == replaced or folder.startswith(replaced + "/"):
                    files.clear()
            for key, path in _walk(root, folder, pattern):
                files[key] = path
        return [files[key] for key in sorted(files)]

    def load_order_warnings(self) -> list[str]:
        """The bridge listed before a mod that ``replace_path``s a folder the
        bridge has files in: the game drops the bridge's files there, so its
        hooks and decisions silently vanish. The bridge belongs last."""
        warnings = []
        for i, bridge in enumerate(self.mods):
            if not _is_bridge(bridge):
                continue
            used = {p.relative_to(bridge.path).parent.as_posix()
                    for p in bridge.path.rglob("*.txt")}
            for later in self.mods[i + 1:]:
                if _is_bridge(later):
                    continue
                hit = sorted(r for r in later.replace_paths
                             if any(u == r or u.startswith(r + "/") for u in used))
                if hit:
                    warnings.append(
                        f"{bridge.name} loads before {later.name}, which replaces "
                        f"{', '.join(hit)}: the bridge's files there are dropped. "
                        "Put LLM Bridge last in the load order."
                    )
        return warnings

    def describe(self) -> dict:
        out = {
            "name": self.name,
            "game_dir": str(self.game_dir),
            "mods": [{"name": m.name, "version": m.version, "path": str(m.path)} for m in self.mods],
        }
        if self.dlcs is not None:
            out["dlcs"] = sorted(self.dlcs)
        return out


def _walk(root: Path, folder: str, pattern: str) -> Iterable[tuple[str, Path]]:
    """(relative posix path, absolute path) for every file under root/folder
    matching ``pattern``. os.walk and string joins: pathlib's rglob and
    relative_to cost seconds on a total conversion's thousands of files."""
    base = os.path.join(root, folder)
    suffix = pattern.lstrip("*")  # only "*.txt" / "*.yml" are used
    for current, _dirs, names in os.walk(base):
        rel = os.path.relpath(current, root).replace("\\", "/")
        for name in names:
            if name.endswith(suffix):
                yield f"{rel}/{name}", Path(current) / name


def _is_bridge(mod: Mod) -> bool:
    return mod.name.strip().lower() in {"llm bridge", "llm_bridge"}


# --- the index ---------------------------------------------------------------


@dataclass
class Focus:
    id: str
    #: All groups must be met; any focus within a group meets it.
    prerequisites: list[list[str]] = field(default_factory=list)


@dataclass
class FocusTree:
    id: str
    tags: set[str] = field(default_factory=set)
    default: bool = False
    focuses: dict[str, Focus] = field(default_factory=dict)
    shared: list[str] = field(default_factory=list)


@dataclass
class StateInfo:
    id: str
    key: str
    name: str
    owner: str | None
    #: The province ids of the state (``history/states``' ``provinces = { ... }``).
    provinces: frozenset[str] = frozenset()


@dataclass(frozen=True)
class SubUnit:
    """A battalion or support company of ``common/units``: its group and whether it is available
    from the start (``active = yes``); the rest is unlocked by a technology."""

    id: str
    group: str
    active: bool


#: ``group`` values of the land sub-units that go in a template's battalion grid, and of the
#: support companies that go in its support column.
BATTALION_GROUPS = frozenset({"infantry", "armor", "mobile", "combat_support", "mobile_combat_support",
                              "armor_combat_support"})
SUPPORT_GROUPS = frozenset({"support"})


@dataclass
class TechPlace:
    """Where a technology sits in its folder: ``common/technologies`` gives
    ``position = { x y }`` where ``y`` is a year column (``@1936`` names one) and
    ``x`` the row. The tree is drawn left to right, so ``column`` runs across the
    screen and ``row`` down it. Both are relative to the technology's sub-tree, not
    the tab: ``roots`` are the technologies at the top of the chains
    (``path = { leads_to_tech }``) that reach this one, and the interface file places
    each root's sub-tree (see ``techtree.py``)."""

    folder: str
    column: int
    row: int
    roots: tuple[str, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class AdvisorRole:
    """One advisor block of a character: the slot it fills and what it costs.

    ``cost`` is the base political power from the definition; None when the
    block names none (the game then applies a default this index does not know),
    which callers must treat as unknown rather than free."""

    slot: str | None
    cost: float | None
    #: The advisor's ``idea_token``: the idea the character holds while hired in this role.
    token: str | None = None
    #: False when the role says ``can_be_fired = no`` (script can change it later; not read).
    can_be_fired: bool = True


@dataclass
class IdentifierIndex:
    playset: Playset
    tags: set[str] = field(default_factory=set)
    trees: list[FocusTree] = field(default_factory=list)
    shared_focuses: dict[str, Focus] = field(default_factory=dict)
    technologies: set[str] = field(default_factory=set)
    states: dict[str, StateInfo] = field(default_factory=dict)
    tech_places: dict[str, TechPlace] = field(default_factory=dict)
    #: ``(folder, "<root>_tree")`` -> the sub-tree's box in countrytechtreeview.gui.
    tech_gridboxes: dict[tuple[str, str], Gridbox] = field(default_factory=dict)
    #: technology -> the technologies its ``path = { leads_to_tech }`` blocks name.
    tech_leads: dict[str, list[str]] = field(default_factory=dict)
    #: equipment id -> the technologies whose ``enable_equipments`` unlock it.
    equipment_enablers: dict[str, list[str]] = field(default_factory=dict)
    #: sub-unit (battalion or support company) -> the technologies whose ``enable_subunits`` unlock it.
    subunit_enablers: dict[str, list[str]] = field(default_factory=dict)
    _sub_units: dict[str, SubUnit] | None = field(default=None, repr=False)
    _focus_names: dict[str, str] | None = field(default=None, repr=False)
    _research_layout: object = field(default=None, repr=False)
    _advisors: dict[str, list[AdvisorRole]] | None = field(default=None, repr=False)
    _equipment: set[str] | None = field(default=None, repr=False)
    _equipment_active: set[str] = field(default_factory=set, repr=False)
    _archetypes: set[str] = field(default_factory=set, repr=False)
    _advisor_tokens: dict[str, str] = field(default_factory=dict, repr=False)
    _mod_context: object | None = field(default=None, repr=False)
    _designers: object | None = field(default=None, repr=False)
    #: The focus files in load order, re-read for the DLC conditions on first use (dlc.py).
    _focus_sources: list[Path] = field(default_factory=list, repr=False)
    _focus_cond: tuple[dict, dict] | None = field(default=None, repr=False)
    _hidden: dict[str, list[str]] | None = field(default=None, repr=False)

    # --- building -------------------------------------------------------------

    @classmethod
    def build(cls, playset: Playset) -> IdentifierIndex:
        index = cls(playset=playset)
        for path in playset.layered("common/country_tags"):
            for line in _uncommented(path).splitlines():
                match = re.match(r"\s*([A-Z0-9]{3})\s*=", line)
                if match:
                    index.tags.add(match.group(1))
        for path in playset.layered("common/national_focus"):
            index._focus_sources.append(path)
            index._read_focus_file(_parse(path))
        leads: dict[str, list[str]] = {}
        for path in playset.layered("common/technologies"):
            for block in _as_list(_parse(path).get("technologies")):
                variables = {k: v for k, v in block.items() if k.startswith("@")}
                for key, value in block.items():
                    if isinstance(value, dict) and not key.startswith("@") and key != "_items":
                        index.technologies.add(key)
                        place = _tech_place(value, variables)
                        if place:
                            index.tech_places[key] = place
                        for group in _as_list(value.get("enable_equipments")):
                            for equipment in (group.get("_items", []) if isinstance(group, dict) else []):
                                index.equipment_enablers.setdefault(str(equipment), []).append(key)
                        for group in _as_list(value.get("enable_subunits")):
                            for unit in (group.get("_items", []) if isinstance(group, dict) else []):
                                index.subunit_enablers.setdefault(str(unit), []).append(key)
                        leads[key] = [str(t) for p in _as_list(value.get("path"))
                                      if isinstance(p, dict)
                                      for t in _as_list(p.get("leads_to_tech")) if isinstance(t, str)]
        index.tech_leads = leads
        _attach_roots(index.tech_places, leads)
        for path in playset.layered("interface", "countrytechtreeview.gui"):
            index.tech_gridboxes = parse_gridboxes(path.read_text(encoding="utf-8-sig", errors="replace"))
        names = index._state_names()
        for path in playset.layered("history/states"):
            for block in _as_list(_parse(path).get("state")):
                state_id = str(block.get("id", "")).strip()
                if not state_id:
                    continue
                key = str(block.get("name", f"STATE_{state_id}"))
                history = _first(block.get("history")) or {}
                owner = history.get("owner")
                provinces = frozenset(
                    str(p) for group in _as_list(block.get("provinces")) if isinstance(group, dict)
                    for p in group.get("_items", []))
                index.states[state_id] = StateInfo(
                    id=state_id, key=key, name=names.get(key, key),
                    owner=str(owner) if isinstance(owner, str) else None,
                    provinces=provinces,
                )
        return index

    def research_layout(self):
        """The research panel and tab layout of the playset's interface files
        (uilayout.py), read on first use. Raises uilayout.LayoutRefused."""
        if self._research_layout is None:
            from .uilayout import ResearchLayout

            self._research_layout = ResearchLayout.from_playset(self.playset)
        return self._research_layout

    def _read_focus_file(self, parsed: dict) -> None:
        for block in _as_list(parsed.get("shared_focus")):
            focus = _focus(block)
            if focus:
                self.shared_focuses[focus.id] = focus
        for block in _as_list(parsed.get("focus_tree")):
            if not isinstance(block, dict):
                continue
            tree = FocusTree(id=str(block.get("id", "")))
            # original_tag is how a tree keeps to a country through a civil war
            # or a tag switch, and how many total conversions key their trees.
            country = block.get("country")
            tree.tags = set(_all_values(country, "tag")) | set(_all_values(country, "original_tag"))
            tree.default = str(block.get("default", "no")) == "yes"
            for entry in _as_list(block.get("focus")):
                focus = _focus(entry)
                if focus:
                    tree.focuses[focus.id] = focus
            tree.shared = [str(s) for s in _as_list(block.get("shared_focus")) if isinstance(s, str)]
            # A mod redefining a tree by id replaces it, as the game does.
            self.trees = [t for t in self.trees if t.id != tree.id or not tree.id]
            self.trees.append(tree)

    def _state_names(self) -> dict[str, str]:
        names: dict[str, str] = {}
        files = [
            path for path in self.playset.layered("localisation", "*.yml")
            if "state" in path.name.lower() and "english" in path.as_posix().lower()
        ]
        # A key defined twice keeps its first definition, unless the later one
        # sits in a replace/ folder -- which is how a mod renames a state.
        files.sort(key=lambda path: "replace" in path.parent.parts)
        for path in files:
            replacing = "replace" in path.parent.parts
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            for key, value in re.findall(r'^\s*(STATE_\w+):\d*\s*"([^"]*)"', text, flags=re.M):
                if replacing or key not in names:
                    names[key] = value
        return names

    def focus_name(self, focus_id: str) -> str | None:
        """The English display name of a focus, or None when none is defined.

        Read on first use and kept for the focus ids this index knows: the
        English localisation is large and only the input driver needs it.
        """
        if self._focus_names is None:
            wanted = set(self.shared_focuses) | {f for t in self.trees for f in t.focuses}
            names: dict[str, str] = {}
            files = [p for p in self.playset.layered("localisation", "*.yml")
                     if "english" in p.as_posix().lower()]
            files.sort(key=lambda path: "replace" in path.parent.parts)
            for path in files:
                replacing = "replace" in path.parent.parts
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                for key, value in re.findall(r'^\s*([\w.]+):\d*\s*"(.*)"', text, flags=re.M):
                    if key in wanted and (replacing or key not in names):
                        names[key] = value
            self._focus_names = names
        return self._focus_names.get(focus_id)

    def advisor_roles(self, advisor_id: str) -> list[AdvisorRole] | None:
        """The roles of the character ``advisor_id`` names (a character id or an
        advisor's ``idea_token``), or None when no such advisor is defined.

        ``activate_advisor`` takes the character id; an idea token is accepted
        here because the brief and the UI show both. Read on first use: a total
        conversion has hundreds of character files."""
        if self._advisors is None:
            roles: dict[str, list[AdvisorRole]] = {}
            tokens: dict[str, str] = {}
            for path in self.playset.layered("common/characters"):
                for block in _as_list(_parse(path).get("characters")):
                    for char_id, body in (block.items() if isinstance(block, dict) else []):
                        found = _advisor_blocks(body)
                        if not found:
                            continue
                        # A mod redefining a character replaces it, as the game does.
                        roles[char_id] = []
                        for advisor in found:
                            cost = _number(_first(advisor.get("cost")))
                            slot = _first(advisor.get("slot"))
                            token = _first(advisor.get("idea_token"))
                            fired = _first(advisor.get("can_be_fired"))
                            roles[char_id].append(
                                AdvisorRole(slot if isinstance(slot, str) else None, cost,
                                            token if isinstance(token, str) else None,
                                            fired != "no"))
                            if isinstance(token, str):
                                tokens.setdefault(token, char_id)
            self._advisors = roles
            self._advisor_tokens = tokens
        if advisor_id in self._advisors:
            return self._advisors[advisor_id]
        char_id = self._advisor_tokens.get(advisor_id)
        return self._advisors.get(char_id) if char_id else None

    def advisor_character(self, advisor_id: str) -> str | None:
        """The character id ``activate_advisor`` wants for ``advisor_id``."""
        if self.advisor_roles(advisor_id) is None:
            return None
        return advisor_id if advisor_id in self._advisors else self._advisor_tokens[advisor_id]

    def equipment_types(self) -> set[str]:
        """Equipment a production line can be made for: every entry of
        ``common/units/equipment`` that is not an archetype (an archetype is a
        family name, such as ``infantry_equipment``, not something that is built)."""
        if self._equipment is None:
            found: set[str] = set()
            active: set[str] = set()
            archetypes: set[str] = set()
            for path in self.playset.layered("common/units/equipment"):
                for block in _as_list(_parse(path).get("equipments")):
                    for key, body in (block.items() if isinstance(block, dict) else []):
                        if isinstance(body, dict) and str(_first(body.get("is_archetype"))) == "yes":
                            archetypes.add(key)
                        if isinstance(body, dict) and str(_first(body.get("is_archetype"))) != "yes":
                            found.add(key)
                            if str(_first(body.get("active"))) == "yes":
                                active.add(key)
            self._equipment = found
            self._equipment_active = active
            self._archetypes = archetypes
        return self._equipment

    def equipment_archetypes(self) -> set[str]:
        """The equipment archetypes (families such as ``infantry_equipment``) of the playset."""
        self.equipment_types()
        return set(self._archetypes)

    def sub_units(self) -> dict[str, SubUnit]:
        """The land battalions and support companies of ``common/units``, by id."""
        if self._sub_units is None:
            found: dict[str, SubUnit] = {}
            for path in self.playset.layered("common/units"):
                if "/equipment/" in path.as_posix():
                    continue
                for block in _as_list(_parse(path).get("sub_units")):
                    for key, body in (block.items() if isinstance(block, dict) else []):
                        if not isinstance(body, dict):
                            continue
                        group = _first(body.get("group"))
                        if isinstance(group, str):
                            found[key] = SubUnit(key, group, str(_first(body.get("active"))) == "yes")
            self._sub_units = found
        return self._sub_units

    def subunit_unlock(self, unit: str) -> tuple[bool, list[str]]:
        """``(always, techs)`` for a sub-unit, as ``equipment_unlock`` is for equipment."""
        found = self.sub_units().get(unit)
        return bool(found and found.active), list(self.subunit_enablers.get(unit, []))

    def equipment_unlock(self, equipment: str) -> tuple[bool, list[str]]:
        """``(always, techs)``: whether the equipment is buildable from the start (``active = yes``
        in its definition) and the technologies whose ``enable_equipments`` unlock it. An equipment
        with neither is unlocked some way the files do not say (script), which no guard can test."""
        self.equipment_types()
        return equipment in self._equipment_active, list(self.equipment_enablers.get(equipment, []))

    def designers(self):
        """The tank, ship and plane designers' chassis, modules, upgrades and prices
        (``designers.DesignerData``), read once from the playset's files."""
        if self._designers is None:
            from .designers import DesignerData

            self._designers = DesignerData.load(self.playset)
        return self._designers

    def mod_context(self):
        """The action surface of this playset (``modcontext``), read on demand."""
        if self._mod_context is None:
            from .modcontext import ModContext

            self._mod_context = ModContext(self)
        return self._mod_context

    # --- lookups --------------------------------------------------------------

    def trees_for(self, tag: str) -> list[FocusTree]:
        """The focus trees ``tag`` can have. The game gives a country the tree whose
        ``country = { factor .. modifier = { add = N <conditions> } }`` scores highest; with the
        DLC set known those scores are worked out (a tree tied to a DLC that is off scores 0 and
        the generic tree wins), else every tree that names the tag is returned."""
        own = [tree for tree in self.trees if tag in tree.tags]
        dlcs = self.playset.dlcs
        if dlcs is None:
            return own or [tree for tree in self.trees if tree.default]
        return self._scored_trees(tag, dlcs, own)

    def _conditions(self) -> tuple[dict, dict]:
        """``(trees, focuses)``: tree id -> its ``country`` block; focus id -> ``(allow_branch,
        available)`` blocks, as ``modcontext.script`` nodes. Read on first use."""
        if self._focus_cond is None:
            from .modcontext import script as S

            countries: dict[str, list] = {}
            focuses: dict[str, tuple[list, list]] = {}

            def add(body: list) -> None:
                fid = S.text(body, "id")
                if fid:
                    focuses[fid] = (S.block(body, "allow_branch") or [], S.block(body, "available") or [])

            for path in self._focus_sources:
                for key, _op, body in S.parse(path.read_text(encoding="utf-8-sig", errors="replace")):
                    if not isinstance(body, list):
                        continue
                    if key == "focus_tree":
                        tid = S.text(body, "id")
                        if tid:
                            countries[tid] = S.block(body, "country") or []
                        for entry in S.blocks(body, "focus"):
                            add(entry)
                    elif key == "shared_focus":
                        add(body)
            self._focus_cond = (countries, focuses)
        return self._focus_cond

    def _scored_trees(self, tag: str, dlcs: frozenset[str], own: list[FocusTree]) -> list[FocusTree]:
        """The trees of ``own`` that the active DLCs leave standing. A tree is dropped when its
        ``country`` score is 0 because a ``has_dlc`` is off (Poland without Poland: United and
        Ready or No Step Back), or when another tree beats it by a score that only DLC raised
        (the sea trees of No Compromise, No Surrender over the Waking the Tiger ones). Trees that
        merely score lower stay: a script can swap a country onto them (Free France)."""
        from .modcontext import script as S

        countries, _focuses = self._conditions()

        def score(tree: FocusTree, known) -> tuple[float, float]:
            block = countries.get(tree.id)
            if block is None:
                return (10.0, 10.0) if tag in tree.tags else (0.0, 0.0)
            base = S.number(S.text(block, "factor") or S.text(block, "base") or "0") or 0.0
            low = high = base
            for modifier in S.blocks(block, "modifier"):
                add = S.number(S.text(modifier, "add") or "")
                if add is None:
                    continue
                conditions = [n for n in modifier if n[0] not in ("add", "factor", "base", "multiply")]
                verdict = S.holds(conditions, tag, known)
                if verdict is True:
                    low += add
                if verdict is not False:
                    high += add
            return low, high

        rows = []
        for tree in own:
            low, high = score(tree, dlcs)
            blind_low, blind_high = score(tree, None)
            if high <= 0 < blind_high:
                continue                                   # a has_dlc kills its only score
            rows.append((tree, low, high, blind_low))
        final = [tree for tree, _low, high, _b in rows
                 if not any(other_low > high and other_low > other_blind
                            for other, other_low, _oh, other_blind in rows if other is not tree)]
        return final or [t for t in self.trees if t.default]

    def hidden_focuses(self) -> dict[str, list[str]]:
        """Focus id -> the DLCs its ``allow_branch`` needs, for the focuses the active DLC set
        hides (``allow_branch`` is False), plus every focus that can then never be reached: one of
        whose prerequisite groups is made only of hidden focuses. Empty when the set is unknown."""
        dlcs = self.playset.dlcs
        if dlcs is None:
            return {}
        if self._hidden is None:
            from .modcontext import script as S

            _c, conditions = self._conditions()
            hidden = {fid: S.dlc_names(allow) for fid, (allow, _a) in conditions.items()
                      if S.dlc_truth(allow, dlcs) is False}
            every = {**self.shared_focuses}
            for tree in self.trees:
                every.update(tree.focuses)
            changed = True
            while changed:
                changed = False
                for focus in every.values():
                    if focus.id in hidden:
                        continue
                    for group in focus.prerequisites:
                        if group and all(f in hidden for f in group):
                            hidden[focus.id] = hidden[group[0]]
                            changed = True
                            break
            self._hidden = hidden
        return self._hidden

    def focus_needs(self, focus_id: str) -> list[str]:
        """The DLCs that would make ``focus_id`` takeable, when the active set keeps it from being:
        hidden by an ``allow_branch``, or its own ``available`` test of ``has_dlc`` is False."""
        dlcs = self.playset.dlcs
        if dlcs is None:
            return []
        hidden = self.hidden_focuses()
        if focus_id in hidden:
            return hidden[focus_id]
        from .modcontext import script as S

        _c, conditions = self._conditions()
        available = conditions.get(focus_id, ([], []))[1]
        return S.dlc_names(available) if S.dlc_truth(available, dlcs) is False else []

    def focuses_for(self, tag: str) -> dict[str, Focus]:
        """The focuses ``tag`` can ever take: its trees, plus the shared focuses
        those trees pull in and everything that descends from them. With the DLC set known,
        focuses the active DLCs hide (``allow_branch``) are left out."""
        focuses: dict[str, Focus] = {}
        roots: list[str] = []
        for tree in self.trees_for(tag):
            focuses.update(tree.focuses)
            roots += tree.shared
        included = set(roots)
        changed = True
        while changed:
            changed = False
            for focus in self.shared_focuses.values():
                if focus.id in included:
                    continue
                needed = {f for group in focus.prerequisites for f in group}
                if needed & included:
                    included.add(focus.id)
                    changed = True
        focuses.update({k: self.shared_focuses[k] for k in included if k in self.shared_focuses})
        hidden = self.hidden_focuses()
        if hidden:
            focuses = {k: v for k, v in focuses.items() if k not in hidden}
        return focuses

    def available_focuses(self, tag: str, completed: Iterable[str]) -> list[str]:
        """Focuses whose every prerequisite group has at least one completed focus."""
        done = set(completed)
        return [
            focus.id
            for focus in self.focuses_for(tag).values()
            if focus.id not in done and all(set(group) & done for group in focus.prerequisites)
            and not self.focus_needs(focus.id)
        ]

    def resolve_state(self, value: str) -> StateInfo | None:
        text = str(value).strip()
        if text in self.states:
            return self.states[text]
        lowered = text.lower()
        for state in self.states.values():
            if lowered in (state.key.lower(), state.name.lower()):
                return state
        return None

    # --- validation -----------------------------------------------------------

    def check(self, call: ActionCall, country: str) -> ActionResult | None:
        """None when every identifier in the call is real; otherwise a rejection
        naming the nearest real ones."""
        args = call.arguments
        if call.name == "set_national_focus" and self.trees:
            focus = str(args.get("focus_id", ""))
            mine = self.focuses_for(country)
            needs = self.focus_needs(focus)
            in_tree = focus in mine or focus in self.shared_focuses or any(
                focus in t.focuses for t in self.trees_for(country))
            if needs and in_tree:
                from .dlc import needs_message

                return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                    message=needs_message(f"Focus '{focus}'", needs) + ".",
                                    error_kind="invalid_args")
            if focus not in mine:
                anywhere = focus in self.shared_focuses or any(focus in t.focuses for t in self.trees)
                why = (f"'{focus}' is not in {country}'s focus tree" if anywhere
                       else f"There is no focus '{focus}' in this playset ({self.playset.name})")
                return self._reject(call, why, focus, mine)
        if call.name in ("start_research", "steer_research") and self.technologies:
            tech = str(args.get("technology", ""))
            if tech not in self.technologies:
                # A unique prefix is the id the model meant ("synth_oil" for
                # "synth_oil_experiments", live 2026-11-22: Haiku guessed it four times).
                starts = [t for t in self.technologies if tech and t.startswith(tech)]
                if len(starts) == 1:
                    args["technology"] = tech = starts[0]
            if tech not in self.technologies:
                return self._reject(
                    call, f"There is no technology '{tech}' in this playset", tech, self.technologies
                )
        if call.name == "queue_construction" and self.states:
            state = str(args.get("state", ""))
            if state.lower() not in STATE_ALIASES and self.resolve_state(state) is None:
                names = [s.name for s in self.states.values()] + list(self.states)
                return self._reject(call, f"There is no state '{state}' in this playset", state, names)
        if call.name == "take_decision":
            return self.mod_context().check_take(call, country)
        if call.name == "click_gui_button":
            return self.mod_context().check_click(call, country)
        if call.name == "change_law":
            ctx = self.mod_context()
            law = str(args.get("law", ""))
            laws = [o["id"] for options in ctx.ideas()["laws"].values() for o in options]
            if laws and ctx.find_law(law) is None:
                return self._reject(call, f"There is no law '{law}' in this playset", law, laws)
        if call.name == "set_occupation_law":
            law = str(args.get("law", ""))
            known = self.mod_context().occupation_laws()
            if known and law not in known:
                # A unique name inside a real one ("civilian_oversight" for
                # "foreign_civilian_oversight", live 2026-11-22).
                inside = [k for k in known if law and law in k]
                if len(inside) == 1:
                    args["law"] = law = inside[0]
            if known and law not in known:
                return self._reject(call, f"There is no occupation law '{law}' in this playset",
                                    law, known)
        if call.name in ("steer_production", "steer_intelligence", "steer_navy", "steer_air",
                         "steer_spending", "steer_market", "steer_army"):
            from . import steering

            area = call.name.removeprefix("steer_")
            kind, ident = (("importance", args.get("region")) if call.name == "steer_air"
                           else (str(args.get("kind", "")), args.get("id")))
            if call.name != "steer_air" and ident:
                # The alias step ran only in the console's planner, after this check had
                # already refused "motorized_equipment" (overnight campaign, 2026-11-22).
                fixed = steering.normalise(area, kind, str(ident))
                if fixed != ident:
                    args["id"] = ident = fixed
            why = steering.refusal(area, kind, ident, str(args.get("level", "")),
                                   steering.known_ids(self))
            if why:
                return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                    message=why + ".", error_kind="invalid_args")
        if call.name == "add_production_line":
            equipment = str(args.get("equipment", ""))
            known = self.equipment_types()
            if known and equipment not in known:
                return self._reject(call, f"There is no buildable equipment '{equipment}' in this "
                                    "playset", equipment, sorted(known))
        if call.name == "design_equipment":
            from .designers import EMPTY, KINDS

            kind = str(args.get("kind", ""))
            data = self.designers()
            if kind in KINDS and data.chassis:
                base = str(args.get("base", ""))
                chassis = data.chassis.get(base)
                if chassis is None or chassis.kind != kind:
                    return self._reject(call, f"There is no {kind} chassis '{base}' in this playset",
                                        base, [c.id for c in data.types(kind)])
                for slot, module in (args.get("modules") or {}).items():
                    if slot not in chassis.slots:
                        return self._reject(call, f"{base} has no slot '{slot}'", str(slot),
                                            chassis.slots)
                    if module != EMPTY and (module not in data.modules
                                            or data.modules[module].kind != kind):
                        return self._reject(call, f"There is no {kind} module '{module}' in this "
                                            "playset", str(module),
                                            [m for m, x in data.modules.items() if x.kind == kind])
                for name in (args.get("upgrades") or {}):
                    if name not in chassis.upgrades:
                        return self._reject(call, f"{base} has no upgrade '{name}'", str(name),
                                            chassis.upgrades)
        if call.name == "queue_province_building":
            province = str(args.get("province", ""))
            if self.states and not any(province in info.provinces for info in self.states.values()):
                return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                    message=f"There is no province '{province}' in this playset.",
                                    error_kind="invalid_args")
        if call.name == "set_front_order" and self.states:
            state = str(args.get("state", ""))
            if self.resolve_state(state) is None:
                names = [s.name for s in self.states.values()] + list(self.states)
                return self._reject(call, f"There is no state '{state}' in this playset", state, names)
        argument = TAG_ARGUMENTS.get(call.name)
        if argument and self.tags:
            tag = str(args.get(argument, ""))
            if tag and tag.upper() not in self.tags:
                return self._reject(call, f"There is no country '{tag}' in this playset", tag,
                                    self.tags)
        return None

    def _reject(self, call: ActionCall, why: str, given: str, choices: Iterable[str]) -> ActionResult:
        near = difflib.get_close_matches(given, sorted(set(choices)), n=5, cutoff=0.5)
        hint = f" Nearest real ones: {', '.join(near)}." if near else ""
        return ActionResult(
            ok=False, action=call.name, call_id=call.call_id,
            message=f"{why}.{hint}", error_kind="invalid_args",
        )

    # --- the slice that goes in the brief ------------------------------------

    def brief_lines(self, state: GameState, limit: int = 12) -> list[str]:
        """Balance of power bars, every brief; the focuses this country can start now,
        only when it needs one."""
        from .dlc import brief_line

        lines: list[str] = [line for line in (brief_line(self.playset.dlcs),) if line]
        if state.power_balances:
            lines += self.mod_context().balance_lines(state.power_balances)
        return lines + self._focus_lines(state, limit)

    def _focus_lines(self, state: GameState, limit: int) -> list[str]:
        if state.national_focus or not state.known("national_focus") or not self.trees:
            return []
        focuses = self.focuses_for(state.country)
        if not focuses:
            return []
        if state.known("completed_focuses"):
            options = self.available_focuses(state.country, state.completed_focuses)
            head = "Focuses available now"
        else:
            options = [f.id for f in focuses.values() if not f.prerequisites]
            head = "Focus tree roots (completed focuses not observable)"
        shown = ", ".join(options[:limit])
        more = f" (+{len(options) - limit} more)" if len(options) > limit else ""
        return [f"{head}: {shown}{more}" if options else f"{head}: none"]

    def summary(self) -> dict:
        return {
            "playset": self.playset.name,
            "tags": len(self.tags),
            "focus_trees": len(self.trees),
            "focuses": sum(len(t.focuses) for t in self.trees) + len(self.shared_focuses),
            "technologies": len(self.technologies),
            "states": len(self.states),
            "fingerprint": self.fingerprint(),
        }

    def fingerprint(self) -> str:
        """Short, stable hash of what was indexed: two runs with the same one
        saw the same vocabulary."""
        digest = hashlib.sha1()
        for items in (sorted(self.tags), sorted(self.technologies), sorted(self.states),
                      sorted(f"{t.id}:{','.join(sorted(t.focuses))}" for t in self.trees),
                      sorted(self.shared_focuses)):
            digest.update("\n".join(items).encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()[:12]


# --- helpers -----------------------------------------------------------------


def _uncommented(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return "\n".join(_strip_comment(line) for line in text.splitlines())


def _strip_comment(line: str) -> str:
    """Cut a line at its first '#' outside a quoted string.

    Scanned left to right: a quote inside the comment itself (``# the "Sverige
    class``) must not decide whether the '#' was in a string, or the tokenizer
    reads from that quote across lines to the next one.
    """
    if "#" not in line:  # nearly every line: skip the character scan
        return line
    quoted = False
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == "#" and not quoted:
            return line[:i]
    return line


def _parse(path: Path) -> dict:
    return parse_clausewitz(_uncommented(path))


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _first(value):
    items = _as_list(value)
    return items[0] if items else None


def _all_values(block, key: str) -> list[str]:
    """Every value of ``key`` anywhere inside ``block``, however deeply nested."""
    found: list[str] = []
    for item in _as_list(block):
        if isinstance(item, dict):
            for k, v in item.items():
                if k == key:
                    found += [str(x) for x in _as_list(v) if isinstance(x, str)]
                else:
                    found += _all_values(v, key)
    return found


def _number(raw) -> float | None:
    try:
        return float(str(raw).strip())
    except ValueError:
        return None


def _advisor_blocks(body) -> list[dict]:
    """Every ``advisor = { ... }`` block in a character, including those inside
    the ``instance`` wrappers a character uses to vary by DLC."""
    found: list[dict] = []
    for item in _as_list(body):
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if key == "advisor":
                found += [v for v in _as_list(value) if isinstance(v, dict)]
            elif key == "instance":
                for inner in _as_list(value):
                    found += _advisor_blocks(inner)
    return found


def _tech_place(block: dict, variables: dict) -> TechPlace | None:
    folder = _first(block.get("folder"))
    if not isinstance(folder, dict) or not folder.get("name"):
        return None
    position = _first(folder.get("position"))
    if not isinstance(position, dict):
        return None

    def number(raw) -> int | None:
        text = str(raw).strip()
        text = str(variables.get(text, text)).strip() if text.startswith("@") else text
        try:
            return int(float(text))
        except ValueError:
            return None

    row, column = number(position.get("x")), number(position.get("y"))
    if row is None or column is None:
        return None
    return TechPlace(folder=str(folder["name"]), column=column, row=row)


def _attach_roots(places: dict[str, TechPlace], leads: dict[str, list[str]]) -> None:
    """Fill ``roots``: per folder, the technologies nothing else in the folder leads
    to, and for each technology the roots whose chains reach it (file order)."""
    for place in places.values():
        place.roots = ()
    led_to = {t for src, targets in leads.items() if src in places
              for t in targets if t in places and places[t].folder == places[src].folder}
    for root in (t for t in places if t not in led_to):
        seen, queue = {root}, [root]
        while queue:
            tech = queue.pop()
            places[tech].roots += (root,)
            for nxt in leads.get(tech, ()):
                if nxt in places and nxt not in seen and places[nxt].folder == places[root].folder:
                    seen.add(nxt)
                    queue.append(nxt)


def _focus(block) -> Focus | None:
    if not isinstance(block, dict) or "id" not in block:
        return None
    groups = []
    for prerequisite in _as_list(block.get("prerequisite")):
        if isinstance(prerequisite, dict):
            members = [str(f) for f in _as_list(prerequisite.get("focus")) if isinstance(f, str)]
            if members:
                groups.append(members)
    return Focus(id=str(block["id"]), prerequisites=groups)

