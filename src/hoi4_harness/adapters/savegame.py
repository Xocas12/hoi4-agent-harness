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

import math
import re
from pathlib import Path

from ..paths import find_in_game_dir
from ..types import ActionCall, ActionResult, DivisionGroup, GameState, ProductionLine, ResearchSlot
from .base import AdapterInfo, GameAdapter

# Strings and comments must be consumed together so their braces are inert.
_QUOTED = r'"(?:\\.|[^"\\])*(?:"|\\?$)'
TOKEN = re.compile(rf'{_QUOTED}|\#[^\r\n]*|[{{}}=]|[^\s{{}}="\#]+')
# Scan structure without allocating a token for every number in the world save.
_STRUCTURE = re.compile(rf'(?P<key>{_QUOTED}|[^\s{{}}="\#]+)\s*=|{_QUOTED}|\#[^\r\n]*|[{{}}]')


def _unquote(token: str) -> str:
    if token.startswith('"') and token.endswith('"'):
        return re.sub(r'\\(["\\])', r'\1', token[1:-1])
    return token


def find_save_dir(explicit: Path | None = None) -> Path | None:
    """Locate the save folder, including a Documents redirected into OneDrive."""
    return find_in_game_dir("save games", explicit=explicit)


def latest_save(save_dir: Path) -> Path | None:
    saves = sorted(save_dir.glob("*.hoi4"), key=lambda p: p.stat().st_mtime, reverse=True)
    return saves[0] if saves else None


def _entry_spans(text: str, start: int = 0, end: int | None = None) -> dict[str, list[tuple[int, int]]]:
    """Index direct assignments in a container, skipping nested data in one pass."""
    end = len(text) if end is None else end
    depth = 0
    spans: dict[str, list[tuple[int, int]]] = {}
    pending: tuple[str, int] | None = None

    def finish(stop: int) -> None:
        if pending is not None:
            key, begin = pending
            spans.setdefault(key, []).append((begin, stop))

    for match in _STRUCTURE.finditer(text, start, end):
        token = match.group()
        if token == "{":
            depth += 1
        elif token == "}":
            if depth == 0:
                finish(match.start())
                return spans
            depth -= 1
        elif depth == 0 and match.group("key") is not None:
            finish(match.start())
            pending = (_unquote(match.group("key")), match.start())
    if depth == 0:
        finish(end)
    return spans


def top_level_spans(text: str) -> dict[str, list[tuple[int, int]]]:
    """Index top-level entries by brace depth, independent of indentation.

    Only assignments, braces, strings and comments are scanned. Nested blocks
    are never materialized; numeric arrays do not become Python token lists.
    """
    return _entry_spans(text)


def extract(text: str, keys: set[str], *, spans: dict | None = None) -> dict:
    """Parse selected entries only. Missing keys never trigger a whole-save parse."""
    if spans is None:
        spans = top_level_spans(text)
    parsed: dict = {}
    for key in keys:
        values = []
        for start, end in spans.get(key, []):
            chunk = parse_clausewitz(text[start:end])
            if key in chunk:
                values.append(chunk[key])
        if values:
            parsed[key] = values[0] if len(values) == 1 else values
    return parsed


def _children(text: str, span: tuple[int, int]) -> dict[str, list[tuple[int, int]]]:
    start, end = span
    tokens = (m for m in TOKEN.finditer(text, start, end) if not m.group().startswith("#"))
    prefix = [next(tokens, None) for _ in range(3)]
    if prefix[2] is None or prefix[2].group() != "{":
        return {}
    return _entry_spans(text, prefix[2].end(), end)


def country_block(text: str, tag: str, *, spans: dict | None = None) -> dict | None:
    """Parse only countries' direct TAG child, never another country's reference."""
    if spans is None:
        spans = top_level_spans(text)
    ranges = []
    for container in spans.get("countries", []):
        ranges.extend(_children(text, container).get(tag, []))
    # Duplicates anywhere in the countries containers are ambiguous.
    if len(ranges) != 1:
        return None
    start, end = ranges[0]
    block = parse_clausewitz(text[start:end]).get(tag)
    return block if isinstance(block, dict) else None


def save_date(raw: str) -> str | None:
    """``1936.1.1.12`` -> ``1936-01-01``. The hour is noise here."""
    from .logtail import parse_date

    return parse_date(str(raw).strip('"'))


def matching_brace(text: str, open_at: int) -> int:
    """Find a closing brace, ignoring strings/comments; return EOF if incomplete."""
    depth = 0
    for match in _STRUCTURE.finditer(text, open_at):
        if match.group() == "{":
            depth += 1
        elif match.group() == "}":
            depth -= 1
            if depth == 0:
                return match.end()
    return len(text)


def top_block(text: str, key: str, *, spans: dict | None = None) -> dict | None:
    """Parse one complete root block, withholding absent or duplicate entries."""
    value = extract(text, {key}, spans=spans).get(key)
    return value if isinstance(value, dict) else None


def parse_clausewitz(text: str) -> dict:
    """Parse Paradox's key=value / key={...} format into nested dicts.

    Repeated keys become lists. Bare values inside a block are collected under
    the ``"_items"`` key. This handles text saves; binary ironman is out of scope.
    """
    tokens = [m.group() for m in TOKEN.finditer(text) if not m.group().startswith("#")]
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
                block.setdefault("_items", []).append(parse_block())
                continue
            if pos < len(tokens) and tokens[pos] == "=":
                pos += 1
                if pos >= len(tokens) or tokens[pos] == "}":
                    return block
                value_tok = tokens[pos]
                if value_tok == "{":
                    pos += 1
                    value: object = parse_block()
                else:
                    pos += 1
                    value = _unquote(value_tok)
                key = _unquote(tok)
                if key in block:
                    if not isinstance(block[key], list):
                        block[key] = [block[key]]
                    block[key].append(value)
                else:
                    block[key] = value
            else:
                block.setdefault("_items", []).append(_unquote(tok))
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
    spans = top_level_spans(text)
    raw = extract(text, {"date", "player"}, spans=spans)
    player = raw.get("player")
    if not isinstance(player, str):
        return raw
    raw["country"] = country_block(text, player, spans=spans)
    states = top_block(text, "states", spans=spans) or {}
    raw["owned_states"] = {
        sid: state for sid, state in states.items()
        if isinstance(state, dict) and state.get("owner") == player
    }
    templates = top_block(text, "division_templates", spans=spans) or {}
    raw["division_templates"] = _reference_names([
        t for t in _as_list(templates.get("division_template"))
        if isinstance(t, dict) and t.get("country") == player
    ], "name")
    raw["equipment_names"] = _equipment_names(top_block(text, "equipments", spans=spans) or {})
    return raw


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ident(value) -> tuple[str, str] | None:
    """Keep both parts of a typed save reference, never just its numeric id."""
    if isinstance(value, dict) and all(isinstance(value.get(k), str) for k in ("id", "type")):
        return value["id"], value["type"]
    return None


def _reference_names(records: list, name_key: str) -> dict | None:
    names: dict = {}
    for record in records:
        if not isinstance(record, dict):
            return None
        ref, name = _ident(record.get("id")), record.get(name_key)
        if ref is None or ref in names or not isinstance(name, str) or not name:
            return None
        names[ref] = name
    return names


def _equipment_names(equipments: dict) -> dict | None:
    records = []
    for name, entries in equipments.items():
        for entry in _as_list(entries):
            if not isinstance(entry, dict):
                return None
            records.append({"id": entry.get("id"), "name": name})
    return _reference_names(records, "name")


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


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
    if not all(isinstance(entry, dict) for entry in owned.values()):
        return
    amounts = []
    for entry in owned.values():
        pool = entry.get("manpower_pool")
        amount = _integer(pool.get("available")) if isinstance(pool, dict) else None
        if amount is None or amount < 0:
            break
        amounts.append(amount)
    if len(amounts) == len(owned):
        state.manpower = sum(amounts)
        unknown.discard("manpower")
    for building, field in (
        ("industrial_complex", "civilian_factories"),
        ("arms_factory", "military_factories"),
        ("dockyard", "dockyards"),
    ):
        levels = []
        for entry in owned.values():
            buildings = entry.get("buildings")
            if not isinstance(buildings, dict):
                break
            if building not in buildings:
                # A complete buildings object enumerates present buildings.
                levels.append(0)
            else:
                data = buildings[building]
                level = _integer(data.get("level")) if isinstance(data, dict) else None
                if level is None or level < 0:
                    break
                levels.append(level)
        if len(levels) == len(owned):
            setattr(state, field, sum(levels))
            unknown.discard(field)


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
    busy = []
    free = 0
    for name, value in slots.items():
        records = _as_list(value)
        if not records or not all(isinstance(record, dict) for record in records):
            return
        if name == "empty":
            free += len(records)
        else:
            busy.extend([name] * len(records))
    state.research = [ResearchSlot(index=i + 1, technology=name) for i, name in enumerate(busy)]
    state.research += [ResearchSlot(index=len(busy) + i + 1) for i in range(free)]
    unknown.discard("research")


def _read_production(state: GameState, production, names: dict, unknown: set[str]) -> None:
    """Read military and naval lines without inventing missing assignments.

    Preserve #84's military efficiency / output derivation when every input is
    available. Those metrics have not been checked for naval lines.
    """
    if not isinstance(production, dict) or not {"military_lines", "naval_lines"} & production.keys():
        return
    lines: list[ProductionLine] = []
    for key, kind in (("military_lines", "military"), ("naval_lines", "naval")):
        for line in _as_list(production.get(key)):
            if not isinstance(line, dict):
                return
            equipment = names.get(_ident(line.get("equipment_variant_index")))
            factories = _integer(line.get("active_factories"))
            if equipment is None or (
                "active_factories" in line and (factories is None or factories < 0)
            ):
                return  # do not report an incomplete set of production lines
            efficiency = output = None
            if kind == "military":
                speed, cost = _number(line.get("speed")), _number(line.get("cost"))
                if speed is not None and speed >= 0 and cost is not None and cost > 0:
                    output = round(speed / cost, 3)
                per_factory = line.get("factory_efficiencies")
                items = per_factory.get("_items") if isinstance(per_factory, dict) else None
                values = [_number(v) for v in _as_list(items)]
                if (factories is not None and factories > 0 and len(values) >= factories
                        and all(v is not None and 0 <= v <= 100 for v in values[:factories])):
                    efficiency = round(sum(values[:factories]) / factories / 100.0, 4)
            lines.append(ProductionLine(
                equipment=equipment, factories=factories, efficiency=efficiency,
                output_per_day=output, kind=kind,
            ))
    state.production = lines
    unknown.discard("production")


def _read_divisions(state: GameState, units, templates: dict, unknown: set[str]) -> None:
    """Divisions counted by template name. Location and strength are not read:
    the save gives a province id and raw strength figures, neither in the units
    :class:`DivisionGroup` documents."""
    if not isinstance(units, dict) or "division" not in units:
        return
    counts: dict[str, int] = {}
    for division in _as_list(units.get("division")):
        if not isinstance(division, dict):
            return
        name = templates.get(_ident(division.get("division_template_id")))
        if not name:
            return  # a template we cannot name: say nothing, not a partial count
        counts[name] = counts.get(name, 0) + 1
    state.divisions = [
        DivisionGroup(template=name, count=n, location="unknown", strength=None)
        for name, n in counts.items()
    ]
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


def inspect(path: Path, limit: int = 100) -> str:
    """What a real save actually contains, for finishing the mapping (#6).

    The mapping from save blocks to GameState has to be checked against a real
    save, not written from a wiki: block names move between patches. This
    prints what there is to map -- the top-level entries by size, and the
    player country's own keys with a sample of each value -- so that work
    starts from the file rather than from memory.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    spans = top_level_spans(text)
    head = extract(text, {"date", "player"}, spans=spans)
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
    block = country_block(text, player, spans=spans)
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
