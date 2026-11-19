"""Read-only state from HOI4 save files.

Maps the player's numbers from a text save onto :class:`GameState`; what each
field reads from, and what is still unknown, is in docs/adapters.md. Fields the
save does not hold stay in ``unknown_fields``.

Constraints worth knowing before you build on this:

* Ironman saves are binary and compressed, and so are ordinary saves unless
  settings.txt has ``save_as_binary=no``. This adapter reads **text saves** only;
  non-ironman is also the only mode where an autosave cadence you control is
  realistic.
* A save is a snapshot, not a stream. Pair it with an autosave-every-N-days
  setting, or with :mod:`hoi4_harness.adapters.screen` for anything live.
* It is read-only. Compose it with a write-capable sink (see
  :mod:`hoi4_harness.adapters.input_driver`) via :class:`CompositeAdapter`.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..paths import find_in_game_dir
from ..types import ActionCall, ActionResult, DivisionGroup, GameState, ProductionLine, ResearchSlot
from .base import AdapterInfo, GameAdapter

TOKEN = re.compile(r'"[^"]*"|[{}=]|[^\s{}=]+')




def find_save_dir(explicit: Path | None = None) -> Path | None:
    """Locate the save folder, including a Documents redirected into OneDrive."""
    return find_in_game_dir("save games", explicit=explicit)


def latest_save(save_dir: Path) -> Path | None:
    saves = sorted(save_dir.glob("*.hoi4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return saves[0] if saves else None


#: A key at the very start of a line: how a text save lays out its top level.
TOP_LEVEL = re.compile(r"^([A-Za-z0-9_\-]+)\s*=", re.M)


def top_level_spans(text: str) -> dict[str, list[tuple[int, int]]]:
    """Where each top-level entry of a save starts and ends, without parsing it.

    Saves on the machine this was checked on run 68-96 MB, and tokenizing the
    whole file to read a handful of numbers is most of the cost. A text save
    writes its top level at column 0 and everything nested indented, so a key
    at the start of a line opens an entry that runs to the next one. That is a
    layout assumption, not a grammar: when it finds nothing (a file on one
    line), callers fall back to a full parse.
    """
    starts = [(m.start(), m.group(1)) for m in TOP_LEVEL.finditer(text)]
    spans: dict[str, list[tuple[int, int]]] = {}
    for index, (start, key) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        spans.setdefault(key, []).append((start, end))
    return spans


def extract(text: str, keys: set[str]) -> dict:
    """Parse only the top-level entries named in ``keys``.

    Equivalent to ``{k: parse_clausewitz(text)[k] for k in keys}`` for a
    normally laid-out save, at the cost of the entries asked for rather than
    the whole file.
    """
    spans = top_level_spans(text)
    if not spans or not keys <= set(spans):
        # The layout did not show every key (a save on one line, or a key the
        # heuristic missed): parse it all rather than report a key absent.
        full = parse_clausewitz(text)
        return {k: v for k, v in full.items() if k in keys}
    parsed: dict = {}
    for key in keys:
        for start, end in spans.get(key, []):
            for k, v in parse_clausewitz(text[start:end]).items():
                if k in parsed:
                    if not isinstance(parsed[k], list):
                        parsed[k] = [parsed[k]]
                    parsed[k].append(v)
                else:
                    parsed[k] = v
    return parsed


def country_block(text: str, tag: str) -> dict | None:
    """The parsed block for one country, or None if this save does not lay it
    out as ``countries={ TAG={ ... } }``.

    The container name is the part most likely to have moved between patches,
    which is why ``hoi4-harness save-inspect`` exists: it shows what a real save
    calls it instead of this guessing harder.
    """
    match = re.search(r"^countries[ \t]*=[ \t]*\{", text, flags=re.M)
    if not match:
        return None
    start = match.end() - 1
    body = text[start:matching_brace(text, start)]
    # Countries sit at the indentation of the first key inside the container.
    # Anything deeper is nested in another country (GER={ SWE={opinion=...} }),
    # and [ \t] rather than \s keeps the match from running across a newline.
    first = re.compile(r"^([ \t]*)[A-Za-z0-9_\-]+[ \t]*=", re.M).search(body, 1)
    if not first:
        return None
    indent = first.group(1)
    found = re.search(rf"^{re.escape(indent)}{re.escape(tag)}[ \t]*=[ \t]*\{{", body, flags=re.M)
    if not found:
        return None
    chunk = body[found.start(): matching_brace(body, found.end() - 1)]
    return parse_clausewitz(chunk).get(tag)


def save_date(raw: str) -> str | None:
    """``1936.1.1.12`` -> ``1936-01-01``. The hour is noise here."""
    from .logtail import parse_date

    return parse_date(str(raw).strip('"'))


_BRACES = re.compile(r'[{}"]')


def matching_brace(text: str, open_at: int) -> int:
    """Index just past the ``}`` that closes the ``{`` at ``open_at``.

    Counts braces and skips quoted strings; this is what finds a block's end,
    because indentation cannot: a real save writes some nested blocks at column 0
    (``CAN={`` inside ENG's country block), so "the next line at the same indent"
    cuts a country short. Returns ``len(text)`` for a block that never closes,
    which is how a save being written looks.
    """
    depth = 0
    quoted = False
    for match in _BRACES.finditer(text, open_at):
        char = match.group()
        if char == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif char == "{":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return match.end()
    return len(text)


def top_block(text: str, key: str) -> dict | None:
    """Parse the one top-level ``key={ ... }`` block, whole, or None if absent."""
    match = re.search(rf"^{re.escape(key)}[ \t]*=[ \t]*\{{", text, flags=re.M)
    if not match:
        return None
    end = matching_brace(text, match.end() - 1)
    return parse_clausewitz(text[match.start():end]).get(key)


def parse_clausewitz(text: str) -> dict:
    """Parse Paradox's key=value / key={...} format into nested dicts.

    Repeated keys become lists. Bare values inside a block are collected under
    the ``"_items"`` key. This handles text saves; binary ironman is out of scope.
    """
    tokens = TOKEN.findall(text)
    pos = 0

    def parse_block() -> dict:
        nonlocal pos
        block: dict = {}
        while pos < len(tokens):
            tok = tokens[pos]
            if tok == "}":
                pos += 1
                return block
            pos += 1
            if tok == "{":
                # A block with no key: the members of a list of records, as in
                # `country_leader={ { ideology=a } { ideology=b } }`. Reading the
                # brace as a stray item used to close the enclosing block early.
                block.setdefault("_items", []).append(parse_block())
                continue
            if pos < len(tokens) and tokens[pos] == "=":
                pos += 1
                value_tok = tokens[pos]
                if value_tok == "{":
                    pos += 1
                    value: object = parse_block()
                else:
                    pos += 1
                    value = value_tok.strip('"')
                key = tok.strip('"')
                if key in block:
                    if not isinstance(block[key], list):
                        block[key] = [block[key]]
                    block[key].append(value)
                else:
                    block[key] = value
            else:
                block.setdefault("_items", []).append(tok.strip('"'))
        return block

    return parse_block()


#: The order unknown_fields is reported in (and its complete vocabulary).
_FIELD_ORDER = [
    "political_power", "stability", "war_support", "manpower",
    "civilian_factories", "military_factories", "dockyards", "fuel", "convoys",
    "research", "production", "construction", "divisions", "fronts", "wars",
    "completed_focuses", "national_focus", "delegated_armies", "ai_directives",
    "posture", "theater_postures",
]


def read_save(path: Path) -> dict:
    """Read the pieces of a text save the mapping needs, and nothing else.

    Returns ``{date, player, country, owned_states, division_templates,
    equipment_names}``. The save is ~60 MB, of which the player's country is
    ~0.3 MB; each block is cut out by brace matching and parsed alone. A binary
    save (the game's default) is refused with the setting that fixes it.
    """
    path = Path(path)
    with path.open("rb") as handle:
        magic = handle.read(8)
    if magic.startswith(b"HOI4bin") or bytes(1) in magic:
        raise ValueError(
            f"{path.name} is a binary save (starts {magic[:7]!r}). Set save_as_binary=no in "
            "settings.txt and save again: this adapter reads text saves only."
        )
    text = path.read_text(encoding="utf-8", errors="replace")
    raw = extract(text, {"date", "player"})
    player = raw.get("player")
    if not isinstance(player, str):
        return raw
    raw["country"] = country_block(text, player)
    states = top_block(text, "states") or {}
    raw["owned_states"] = {
        sid: state for sid, state in states.items()
        if isinstance(state, dict) and state.get("owner") == player
    }
    templates = top_block(text, "division_templates") or {}
    raw["division_templates"] = {
        _ident(t.get("id")): t.get("name", "") for t in _as_list(templates.get("division_template"))
        if isinstance(t, dict) and t.get("country") == player
    }
    raw["equipment_names"] = _equipment_names(top_block(text, "equipments") or {})
    return raw


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ident(value) -> str:
    """``{ id=21 type=52 }`` -> ``"21"``."""
    if isinstance(value, dict):
        value = value.get("id")
    return str(value) if value is not None else ""


def _equipment_names(equipments: dict) -> dict[str, str]:
    """Equipment variant id -> its name (``infantry_equipment_1``)."""
    names: dict[str, str] = {}
    for name, entries in equipments.items():
        for entry in _as_list(entries):
            if isinstance(entry, dict) and "id" in entry:
                names[_ident(entry["id"])] = name
    return names


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_country(state: GameState, country: dict, raw: dict, unknown: set[str]) -> None:
    politics = country.get("politics")
    if isinstance(politics, dict):
        pp = _number(politics.get("political_power"))
        if pp is not None:
            state.political_power = pp
            unknown.discard("political_power")
        ruling = politics.get("ruling_party")
        if isinstance(ruling, str):
            state.ideology = ruling

    # Stability and war support: the country block stores only the BASE value
    # (stability=0.47 where the game showed 82.6%); the modifiers are applied at
    # read time and are not in the save. The bridge mod writes the computed
    # figures into country variables (llmb_stab, llmb_ws, in percent). Without
    # the mod these stay unknown rather than report the base as the figure.
    variables = country.get("variables")
    if isinstance(variables, dict):
        stab, ws = _number(variables.get("llmb_stab")), _number(variables.get("llmb_ws"))
        if stab is not None:
            state.stability = stab / 100.0
            unknown.discard("stability")
        if ws is not None:
            state.war_support = ws / 100.0
            unknown.discard("war_support")

    _read_states(state, raw.get("owned_states"), unknown)
    _read_focus(state, country.get("focus"), unknown)
    _read_research(state, country.get("technology"), unknown)
    _read_production(state, country.get("production"), raw.get("equipment_names") or {}, unknown)
    _read_divisions(state, country.get("units"), raw.get("division_templates") or {}, unknown)


def _read_states(state: GameState, owned, unknown: set[str]) -> None:
    """Factories and manpower are not country fields: they are the sum over the
    states the country owns. Manpower is the sum of each state's
    ``manpower_pool.available`` (32,980 in the checked save, the top bar's
    "32.98K"); ``locked`` is population not yet recruitable."""
    if not isinstance(owned, dict) or not owned:
        return
    levels = {"industrial_complex": 0, "arms_factory": 0, "dockyard": 0}
    manpower = 0.0
    for entry in owned.values():
        buildings = entry.get("buildings")
        for name in levels:
            building = buildings.get(name) if isinstance(buildings, dict) else None
            level = _number(building.get("level")) if isinstance(building, dict) else None
            levels[name] += int(level or 0)
        pool = entry.get("manpower_pool")
        available = _number(pool.get("available")) if isinstance(pool, dict) else None
        manpower += available or 0
    state.civilian_factories = levels["industrial_complex"]
    state.military_factories = levels["arms_factory"]
    state.dockyards = levels["dockyard"]
    state.manpower = int(manpower)
    unknown.difference_update({"civilian_factories", "military_factories", "dockyards", "manpower"})


def _read_focus(state: GameState, focus, unknown: set[str]) -> None:
    # Only a running focus is read. A country block with no focus entry could
    # mean "none running" or "this save does not say", so that stays unknown;
    # progress is not read either (its unit is not established).
    current = focus.get("current") if isinstance(focus, dict) else None
    if isinstance(current, str) and current:
        state.national_focus = current
        unknown.discard("national_focus")


def _read_research(state: GameState, technology, unknown: set[str]) -> None:
    """``technology.slots`` holds one entry per busy slot, keyed by the
    technology, plus ``empty`` entries for the free ones. Days remaining is not
    stored (only points so far), so it stays None."""
    slots = technology.get("slots") if isinstance(technology, dict) else None
    if not isinstance(slots, dict):
        return
    busy = [name for name, value in slots.items() if name != "empty" and isinstance(value, dict)]
    free = len(_as_list(slots.get("empty")))
    state.research = [ResearchSlot(index=i + 1, technology=name) for i, name in enumerate(busy)]
    state.research += [ResearchSlot(index=len(busy) + i + 1) for i in range(free)]
    unknown.discard("research")


def _read_production(state: GameState, production, names: dict[str, str], unknown: set[str]) -> None:
    """One line per ``military_lines`` entry. Efficiency is the mean of the
    first ``active_factories`` of the line's per-factory ``factory_efficiencies``
    (percent); output per day is ``speed / cost``, the line's daily IC divided
    by one unit's IC cost."""
    if not isinstance(production, dict):
        return
    lines: list[ProductionLine] = []
    for line in _as_list(production.get("military_lines")):
        if not isinstance(line, dict):
            continue
        equipment = names.get(_ident(line.get("equipment_variant_index")))
        factories = _number(line.get("active_factories"))
        if equipment is None or factories is None:
            continue
        speed, cost = _number(line.get("speed")), _number(line.get("cost"))
        per_factory = line.get("factory_efficiencies")
        items = per_factory.get("_items") if isinstance(per_factory, dict) else None
        values = [_number(v) for v in _as_list(items)]
        active = [v for v in values[: int(factories)] if v is not None]
        lines.append(ProductionLine(
            equipment=equipment,
            factories=int(factories),
            efficiency=round(sum(active) / len(active) / 100.0, 4) if active else 0.0,
            output_per_day=round(speed / cost, 3) if speed is not None and cost else 0.0,
        ))
    state.production = lines
    unknown.discard("production")


def _read_divisions(state: GameState, units, templates: dict[str, str], unknown: set[str]) -> None:
    """Divisions counted by template name. Location and strength are not read:
    the save gives a province id and raw strength figures, neither in the units
    :class:`DivisionGroup` documents."""
    if not isinstance(units, dict):
        return
    counts: dict[str, int] = {}
    for division in _as_list(units.get("division")):
        if not isinstance(division, dict):
            continue
        name = templates.get(_ident(division.get("division_template_id")))
        if not name:
            return  # a template we cannot name: say nothing, not a partial count
        counts[name] = counts.get(name, 0) + 1
    state.divisions = [DivisionGroup(template=name, count=n) for name, n in counts.items()]
    unknown.discard("divisions")


class SaveGameAdapter(GameAdapter):
    """Observes the most recent autosave. Writes nothing."""

    supported_actions = frozenset()

    def __init__(self, save_dir: Path | None = None):
        self.save_dir = find_save_dir(save_dir)
        self._last_path: Path | None = None
        self._last_mtime: float = 0.0

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="savegame",
            readable=True,
            writable=False,
            clock_control=False,
            notes=f"save_dir={self.save_dir or 'not found'}; non-ironman text saves only",
        )

    def read_state(self) -> GameState:
        if not self.save_dir:
            raise FileNotFoundError(
                "No HOI4 save directory found. Set HOI4_SAVE_DIR to the folder holding *.hoi4."
            )
        path = latest_save(self.save_dir)
        if not path:
            raise FileNotFoundError(f"No *.hoi4 saves in {self.save_dir}.")
        self._last_path, self._last_mtime = path, path.stat().st_mtime
        return self._to_state(read_save(path))

    def _to_state(self, raw: dict) -> GameState:
        """Map what :func:`read_save` collected onto GameState.

        Every field read below was checked against a live game (docs/adapters.md,
        tests/test_savegame_mapping.py). What a save does not hold stays in
        ``unknown_fields`` rather than defaulting to a confident zero: the
        construction queue, completed focuses, wars and fronts, and the native
        AI's standing orders are not in the 1.19.5 country block.
        """
        state = GameState(raw=raw)
        # This used to keep only the year: '1936.1.1.12'.split('.')[0].
        state.date = save_date(raw.get("date", "")) or state.date
        player = raw.get("player")
        if isinstance(player, str):
            state.country = player
        # Everything is unknown until a read below says otherwise. This also
        # covers what is never read here: "construction", "fronts", "wars",
        # "completed_focuses", and the hybrid-control fields, which must not
        # pass for "no directive standing" or "no focus running".
        unknown = set(_FIELD_ORDER)
        country = raw.get("country")
        if isinstance(country, dict):
            _read_country(state, country, raw, unknown)
        state.unknown_fields = [name for name in _FIELD_ORDER if name in unknown]
        return state

    def apply(self, call: ActionCall) -> ActionResult:
        return self.unsupported(call)

    def advance(self, days: int) -> GameState:
        """Saves cannot be driven forward. Poll until a newer autosave appears."""
        raise NotImplementedError(
            "SaveGameAdapter is read-only. Wrap it in CompositeAdapter with a "
            "clock-capable sink, or poll wait_for_new_save()."
        )

    def wait_for_new_save(self, timeout_s: float = 300.0, poll_s: float = 5.0) -> GameState:
        """Block until the autosave file changes, then return the new snapshot."""
        import time

        deadline = time.monotonic() + timeout_s
        previous = self._last_mtime
        while time.monotonic() < deadline:
            if self.save_dir:
                path = latest_save(self.save_dir)
                if path and path.stat().st_mtime > previous:
                    return self.read_state()
            time.sleep(poll_s)
        raise TimeoutError(f"No new save within {timeout_s}s.")


def inspect(path: Path, limit: int = 60) -> str:
    """What a real save actually contains, for finishing the mapping (#6).

    The mapping from save blocks to GameState has to be checked against a real
    save, not written from a wiki: block names move between patches. This
    prints what there is to map -- the top-level entries by size, and the
    player country's own keys with a sample of each value -- so that work
    starts from the file rather than from memory.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    spans = top_level_spans(text)
    head = extract(text, {"date", "player"})
    lines = [
        f"{path}  ({len(text) / 1_000_000:.1f} MB)",
        f"date     {head.get('date', '?')}  -> {save_date(head.get('date', '')) or 'unparsed'}",
        f"player   {head.get('player', '?')}",
    ]
    if not spans:
        lines.append("no top-level layout found (one-line or binary save?); nothing more to show")
        return "\n".join(lines)
    sizes = sorted(
        ((key, sum(end - start for start, end in ranges), len(ranges)) for key, ranges in spans.items()),
        key=lambda item: -item[1],
    )
    lines.append("")
    lines.append(f"top-level entries ({len(sizes)}), largest first:")
    for key, size, count in sizes[:25]:
        lines.append(f"  {key:<32} {size / 1000:>10,.0f} kB" + (f"  x{count}" if count > 1 else ""))
    player = head.get("player")
    if not isinstance(player, str):
        return "\n".join(lines)
    block = country_block(text, player)
    lines.append("")
    if block is None:
        lines.append(f"no countries={{ {player}={{...}} }} block: the player country lives somewhere "
                     "else in this version. Look for it among the entries above.")
        return "\n".join(lines)
    lines.append(f"{player}'s block: {len(block)} keys")
    for key, value in list(block.items())[:limit]:
        lines.append(f"  {key:<32} {_sample(value)}")
    if len(block) > limit:
        lines.append(f"  ... and {len(block) - limit} more")
    return "\n".join(lines)


def _sample(value) -> str:
    if isinstance(value, dict):
        keys = [k for k in value if k != "_items"]
        return "{ " + " ".join(keys[:6]) + (" ..." if len(keys) > 6 else "") + " }"
    if isinstance(value, list):
        return f"[{len(value)} entries] first: {_sample(value[0])}"
    return str(value)[:60]
