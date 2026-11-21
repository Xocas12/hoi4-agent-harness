"""Which DLCs the player's game has on, and what that changes for the harness.

HOI4 gates a great deal of content on ``has_dlc = "Name"`` (1,790 times in
``common/national_focus`` alone). A harness that does not know which DLCs are on offers
content that does not exist in the player's game: the Swedish focus tree of Arms Against
Tyranny to a player who does not own it, a tank designer without No Step Back. This module
finds the active set; ``modcontext/script.py`` evaluates ``has_dlc`` against it, and
``identifiers.py`` / ``modcontext`` use that to filter what a country can see or do.
See ``docs/dlc.md`` for the evidence behind every row of ``FEATURES``.

The active set is, in order of precedence:

1. an explicit list (``Config.dlcs``, ``--dlc NAME`` repeated, ``--no-dlc`` for none): used as
   given, for tests and for asking "what if I had only these";
2. what is installed (``<game dir>/dlc/<folder>/<file>.dlc``, key ``name``) minus what the
   launcher disabled (``disabled_dlcs`` in ``<user dir>/dlc_load.json``);
3. unknown (``None``) when the game dir has no ``dlc/`` folder at all (a fixture, a partial
   copy): then nothing is filtered and every ``has_dlc`` stays "unknown", as before this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

#: Every name ``has_dlc`` is tested with in the 1.20 game files (``grep -rho has_dlc common events
#: history interface``), so a typo in ``--dlc`` can be told from a DLC this PC does not own.
KNOWN_DLCS = (
    "Poland: United and Ready", "Together for Victory", "Death or Dishonor", "Waking the Tiger",
    "Man the Guns", "La Resistance", "Battle for the Bosporus", "No Step Back",
    "By Blood Alone", "Arms Against Tyranny", "Graveyard of Empires", "Trial of Allegiance",
    "Peace For Our Time", "Thunder at Our Gates", "No Compromise, No Surrender",
    "Gotterdammerung", "To The Bitter End",
)


@dataclass(frozen=True)
class Installed:
    """One ``.dlc`` descriptor under ``<game dir>/dlc``."""

    name: str
    folder: str            # "dlc028_la_resistance"
    file: str              # "dlc028.dlc"
    pops_id: str = ""      # "hoi4_dlc028_la_resistance"

    def keys(self) -> set[str]:
        """Every spelling a ``disabled_dlcs`` entry may use for this DLC."""
        out = {self.name, self.folder, self.file, Path(self.file).stem, f"dlc/{self.folder}",
               f"dlc/{self.folder}/{self.file}", self.pops_id}
        return {_norm(k) for k in out if k}


@dataclass(frozen=True)
class DlcSet:
    """The DLC picture of one run: what is installed, what is disabled, what is active."""

    installed: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()
    #: None: unknown (no ``dlc/`` folder and no explicit list), so nothing is filtered.
    active: frozenset[str] | None = None
    source: str = "unknown"           # "explicit", "game log", "installed", "unknown"
    notes: tuple[str, ...] = field(default=())

    @property
    def known(self) -> bool:
        return self.active is not None

    def has(self, name: str) -> bool | None:
        """True or False when the active set is known, else None."""
        return None if self.active is None else name in self.active

    def describe(self) -> dict:
        return {"source": self.source, "installed": list(self.installed),
                "disabled": list(self.disabled),
                "active": sorted(self.active) if self.active is not None else None}


# --- detection --------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return text.strip().replace("\\", "/").strip("/").lower()


_NAME = re.compile(r'^\s*name\s*=\s*"([^"]*)"', re.M)
_POPS = re.compile(r'^\s*pops_id\s*=\s*"([^"]*)"', re.M)


def read_installed(game_dir: Path) -> list[Installed] | None:
    """The DLCs under ``<game dir>/dlc``, sorted by folder; None when there is no such folder."""
    root = Path(game_dir) / "dlc"
    if not root.is_dir():
        return None
    found = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        for descriptor in sorted(folder.glob("*.dlc")):
            text = descriptor.read_text(encoding="utf-8-sig", errors="replace")
            name = _NAME.search(text)
            if not name:
                continue
            pops = _POPS.search(text)
            found.append(Installed(name.group(1), folder.name, descriptor.name,
                                   pops.group(1) if pops else ""))
    return found


def read_disabled(user_dir: Path | None) -> tuple[list[str], str | None]:
    """The raw ``disabled_dlcs`` entries of ``<user dir>/dlc_load.json`` and a note when the file
    could not be used. An entry is a path (``dlc/dlc028_la_resistance/dlc028.dlc``), possibly in
    an object (``{"path": ...}``); the launcher's exact spelling is matched leniently (see
    ``Installed.keys``) because the file on the machine this was written on listed none."""
    if user_dir is None:
        return [], None
    path = Path(user_dir) / "dlc_load.json"
    if not path.is_file():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return [], f"dlc_load.json could not be read ({exc}): assuming no DLC is disabled"
    entries = data.get("disabled_dlcs", []) if isinstance(data, dict) else []
    out = []
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            for key in ("path", "name", "id", "pops_id"):
                if isinstance(entry.get(key), str):
                    out.append(entry[key])
                    break
    return out, None


def _canonical(name: str, pool: Iterable[str]) -> str | None:
    wanted = name.strip().lower()
    for known in pool:
        if known.lower() == wanted:
            return known
    return None


def explicit(names: Iterable[str]) -> DlcSet:
    """The set an explicit list names. A name that is not one of the game's gets a note, not an
    error: a mod may test its own ``has_dlc`` names."""
    pool = list(KNOWN_DLCS)
    chosen, notes = [], []
    for raw in names:
        found = _canonical(str(raw), pool)
        if found is None:
            notes.append(f"{raw!r} is not a DLC name the 1.20 game files test; kept as given")
            found = str(raw).strip()
        if found not in chosen:
            chosen.append(found)
    return DlcSet(installed=(), disabled=(), active=frozenset(chosen), source="explicit",
                  notes=tuple(notes))


def read_game_log(user_dir: Path | None) -> tuple[list[str], str] | None:
    """The DLCs the game itself reported active at its last start (``logs/system.log``: ``Active
    DLC Count: N`` then one ``Active DLC: <name>`` line each), and when. This is the game's own
    answer, so it beats the folder scan: a DLC can be owned with an empty folder (content shipped
    in the base game) or have a folder and not be owned or enabled. None when there is no list."""
    if user_dir is None:
        return None
    path = Path(user_dir) / "logs" / "system.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    start = text.rfind("Active DLC Count:")
    if start < 0:
        return None
    names = []
    for line in text[start:].splitlines()[1:]:
        if "Active DLC:" not in line:
            if names:
                break
            continue
        names.append(line.split("Active DLC:", 1)[1].strip())
    stamp = __import__("time").strftime("%Y-%m-%d %H:%M",
                                        __import__("time").localtime(path.stat().st_mtime))
    return names, stamp


def detect(game_dir: Path | None, user_dir: Path | None = None,
           override: Iterable[str] | None = None) -> DlcSet:
    """The active DLC set. ``override`` (an explicit list, empty for "none") wins; then the
    game's own list from its last start (``logs/system.log``); else the install minus the
    launcher's disabled list; else unknown."""
    if override is not None:
        return explicit(override)
    reported = read_game_log(user_dir)
    if reported is not None:
        names, stamp = reported
        installed = read_installed(Path(game_dir)) if game_dir is not None else None
        entries, note = read_disabled(user_dir)
        notes = [f"from the game's own list at its last start ({stamp}, logs/system.log); a "
                 "launcher change since takes effect at the next start"]
        if note:
            notes.append(note)
        return DlcSet(installed=tuple(d.name for d in installed) if installed else tuple(names),
                      disabled=tuple(entries), active=frozenset(names), source="game log",
                      notes=tuple(notes))
    if game_dir is None:
        return DlcSet()
    installed = read_installed(Path(game_dir))
    if installed is None:
        return DlcSet(notes=("no dlc/ folder in the game dir: the active DLC set is unknown, so "
                             "nothing is filtered by DLC",))
    entries, note = read_disabled(user_dir)
    wanted = {_norm(e) for e in entries}
    wanted |= {_norm(e).rsplit("/", 1)[-1] for e in entries}
    off = [d for d in installed if d.keys() & wanted]
    names = tuple(d.name for d in installed)
    gone = tuple(d.name for d in off)
    return DlcSet(installed=names, disabled=gone,
                  active=frozenset(n for n in names if n not in gone), source="installed",
                  notes=(note,) if note else ())


# --- what the DLCs are for ----------------------------------------------------------------------


@dataclass(frozen=True)
class Feature:
    """A game feature and the DLC its files gate it on (``any_of``: one of them is enough)."""

    id: str
    label: str
    any_of: tuple[str, ...]
    evidence: str
    #: The harness actions or views that go through this feature, for the ``dlc`` command.
    used_by: tuple[str, ...] = ()


#: Whole features the game files tie to a DLC. Everything else the DLCs add is gated item by
#: item (a focus, a decision, a law, a project) with ``has_dlc`` in that item's own conditions,
#: which the harness evaluates (``script.tag_truth``); docs/dlc.md has the counts per folder.
FEATURES: tuple[Feature, ...] = (
    Feature("tank_designer", "Tank designer (modules, variants)", ("No Step Back",),
            'common/units/equipment/modules/00_tank_modules.txt opens with equipment_modules = '
            '{ limit = { has_dlc = "No Step Back" } }; common/defines/00_defines.lua '
            '(DEFAULT_MODULE_VARIANT_CREATION_XP_CUTOFF_LAND): "the tank designer DLC feature"',
            used_by=("design_equipment kind=tank", "list_designs", "describe_design_option")),
    Feature("ship_designer", "Ship designer (modules, variants)", ("Man the Guns",),
            'common/units/equipment/modules/00_ship_modules.txt opens with equipment_modules = '
            '{ limit = { has_dlc = "Man the Guns" } }',
            used_by=("design_equipment kind=ship", "list_designs", "describe_design_option")),
    Feature("plane_designer", "Plane designer (modules, variants)", ("By Blood Alone",),
            'common/units/equipment/modules/00_plane_modules.txt opens with equipment_modules = '
            '{ limit = { has_dlc = "By Blood Alone" } }',
            used_by=("design_equipment kind=plane", "list_designs", "describe_design_option")),
)

#: Balance of power bars (``common/bop``), by the DLC the script that sets each one up is tied
#: to. The bar files themselves carry no ``has_dlc``; the DLC is read from who uses the bar id:
#: events/AAT_*.txt, common/on_actions/09_aat_on_actions.txt (Arms Against Tyranny),
#: 08_bba_on_actions.txt and events/BBA_*.txt (By Blood Alone), common/national_focus/
#: china_communist_sea.txt (No Compromise, No Surrender: country block has_dlc), and
#: czechoslovakia_mu.txt (Peace For Our Time). BRA_political_military_balance is defined and
#: referenced nowhere else in 1.20, so it is not mapped.
BOP_DLC = {
    "DEN_balance_of_power": "Arms Against Tyranny",
    "SWE_riksdag_political_balance": "Arms Against Tyranny",
    "FIN_power_balance": "Arms Against Tyranny",
    "ETH_centralization_balance": "By Blood Alone",
    "ITA_power_balance": "By Blood Alone",
    "SWI_centralization_bop": "By Blood Alone",
    "PRC_communist_power_struggle": "No Compromise, No Surrender",
    "CZE_bop_army_readiness": "Peace For Our Time",
}


def missing(active: frozenset[str] | None, any_of: Iterable[str]) -> list[str]:
    """The DLCs of ``any_of`` that are not active; empty when one is (or the set is unknown)."""
    if active is None:
        return []
    options = list(any_of)
    return [] if any(o in active for o in options) else options


def needs_message(what: str, any_of: Iterable[str]) -> str:
    """``<what> needs the <DLC> DLC, which is not active`` (``one of`` when several would do)."""
    names = list(any_of)
    which = f"the {names[0]} DLC" if len(names) == 1 else "one of the " + ", ".join(names) + " DLCs"
    return f"{what} needs {which}, which is not active"


def feature(feature_id: str) -> Feature:
    for item in FEATURES:
        if item.id == feature_id:
            return item
    raise KeyError(feature_id)


def feature_refusal(feature_id: str, active: frozenset[str] | None) -> str:
    """Why the feature is off, or an empty string when it is on or the set is unknown."""
    item = feature(feature_id)
    gone = missing(active, item.any_of)
    return needs_message(item.label, gone) if gone else ""


def feature_table(active: frozenset[str] | None) -> list[dict]:
    return [{"id": f.id, "label": f.label, "needs": list(f.any_of),
             "on": None if active is None else not missing(active, f.any_of),
             "used_by": list(f.used_by), "evidence": f.evidence} for f in FEATURES]


# --- what the model is told ---------------------------------------------------------------------


def brief_line(active: frozenset[str] | None) -> str:
    """The one line of the brief: how many DLCs are active and that DLC-gated content is
    filtered. Empty when the set is unknown (nothing is filtered then, so there is nothing to say)."""
    if active is None:
        return ""
    story = [n for n in KNOWN_DLCS if n in active]
    return (f"DLC: {len(active)} active ({len(story)} of the {len(KNOWN_DLCS)} that add content); "
            "focuses, decisions, events, laws, advisors and windows that need an inactive DLC "
            "are filtered out.")


def prompt_lines(active: frozenset[str] | None) -> list[str]:
    """For the system prompt's playset section: the active content DLCs by name, and the whole
    features that are off, so the model does not plan around a designer it does not have."""
    if active is None:
        return []
    story = [n for n in KNOWN_DLCS if n in active]
    lines = ["Active DLC that add content: " + (", ".join(story) if story else "none")
             + ". Content that needs an inactive DLC is filtered out of everything listed here."]
    off = [f"{f.label} (needs {' or '.join(f.any_of)})" for f in FEATURES if missing(active, f.any_of)]
    if off:
        lines.append("Not in this game: " + "; ".join(off) + ".")
    return lines
