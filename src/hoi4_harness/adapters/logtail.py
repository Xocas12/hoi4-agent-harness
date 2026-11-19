"""Read state from the LLM Bridge mod's telemetry in ``game.log``.

This is the best read path the harness has. A save file is exact but stale and
expensive to parse; a screenshot is live but needs a vision call. The mod makes
the game itself print a structured line every tick, and tailing a log file is
free and immediate.

Pair it with a writer (``--adapter logtail+input``). It observes; it does not act.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from ..paths import find_in_game_dir
from ..types import ActionCall, ActionResult, GameEvent, GameState, ResearchSlot
from .base import AdapterInfo, GameAdapter
from .clock import wait_for_days

#: Must match LLMB_SCHEMA_VERSION in the mod's telemetry effect.
SCHEMA_VERSION = 3

PREFIX = "LLMB|"
LINE = re.compile(r"LLMB\|v(?P<version>\d+)\|(?P<kind>\w+)\|(?P<fields>.*)$")
#: The engine stamps every line with the in-game date: [hh:mm:ss][Y.M.D.H][file:line].
ENGINE_DATE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\[(?P<date>\d{4}\.\d{1,2}\.\d{1,2})\.\d{1,2}\]")



# Numeric event codes the mod emits, mapped to harness event kinds and severity.
EVENT_CODES = {
    "1": ("war_declared", "critical"),
    "2": ("capitulation", "critical"),
    "3": ("focus_complete", "notable"),
    "4": ("research_done", "notable"),
    "10": ("posture_defensive", "info"),
    "11": ("posture_offensive", "info"),
    "12": ("directives_cleared", "info"),
    # Mirrored from modgen.EVENT_TARGET_SELECTED / EVENT_DIRECTIVE_RAISED.
    "5": ("construction_complete", "notable"),
    "13": ("directive_target_selected", "info"),
    "14": ("directive_raised", "info"),
    # Mirrored from modgen.EVENT_THEATER_POSTURE; detail is "<posture> <TAG>".
    "15": ("theater_posture", "info"),
}

#: Counters on the state line that only ever go up. The game has no on_action
#: for a finished tech or building (1.19.5.0), so a rise between two state
#: lines IS the completion. Field -> what the brief calls one.
COUNTERS = {
    "techs": "technology",
    "built_civ": "civilian factory",
    "built_mil": "military factory",
    "built_dock": "dockyard",
    "built_infra": "infrastructure level",
}
#: Which counters are construction (the rest is research).
CONSTRUCTION_COUNTERS = ("built_civ", "built_mil", "built_dock", "built_infra")

# Telemetry field -> (GameState attribute, converter)
FIELDS = {
    "pp": ("political_power", float),
    "stab": ("stability", lambda v: float(v) / 100.0),
    "ws": ("war_support", lambda v: float(v) / 100.0),
    "civ": ("civilian_factories", int),
    "mil": ("military_factories", int),
    "doc": ("dockyards", int),
    "mp": ("manpower", int),
}

#: How much of the log's start identifies it: a session's first line carries
#: its own timestamp, so a new game.log differs here even on a reused inode.
HEAD_BYTES = 256

# Fields the mod does not emit yet. Reported as unknown rather than defaulted.
NOT_EMITTED = [
    "research", "production", "construction", "fronts", "wars", "national_focus",
    "completed_focuses",
    # Hybrid control: the mod raises flags but reports only posture changes, as
    # events. Posture becomes known the first time one arrives.
    "posture", "ai_directives", "delegated_armies",
    # Per-theater posture is learned from theater_posture events, as posture is.
    "theater_postures",
]

#: Events that say what the AI's posture now is.
POSTURE_EVENTS = {"posture_defensive": "defensive", "posture_offensive": "offensive"}


def find_log(explicit: Path | None = None) -> Path | None:
    """Locate game.log, including a Documents folder redirected into OneDrive."""
    return find_in_game_dir("logs", "game.log", explicit=explicit)


def parse_date(raw: str) -> str | None:
    """Accept the shapes the game's date token can produce.

    Which one you get depends on the token used in the mod and on locale, so the
    parser is permissive and returns ``None`` rather than guessing.
    """
    raw = raw.strip()
    # The game writes Y.M.D and, in log prefixes, Y.M.D.H -- the hour is noise here.
    match = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.\d{1,2})?", raw)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return date(year, month, day).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    # The game's date text: "12:00, 1 January, 1936" (hour prefix, commas).
    match = re.fullmatch(r"(?:\d{1,2}:\d{2},?\s*)?(\d{1,2})\s+(\w+),?\s+(\d{4})", raw)
    if match:
        months = ["january", "february", "march", "april", "may", "june", "july",
                  "august", "september", "october", "november", "december"]
        name = match.group(2).lower()
        if name in months:
            return date(int(match.group(3)), months.index(name) + 1, int(match.group(1))).isoformat()
    return None


def _event_text(event_kind: str, detail: str) -> str:
    """What the brief says for an event.

    ``detail`` is free text only where the mod fills it in (a target, a raised
    directive). Elsewhere it is the unset variable's ``0``, which must never
    reach the brief as a bare ``-  0``.
    """
    detail = detail.strip()
    label = event_kind.replace("_", " ")
    if detail in ("", "0"):
        return label
    if event_kind == "directive_target_selected":
        return f"target selected: {detail}"
    if event_kind == "directive_raised":
        return f"directive raised: {detail}"
    if event_kind == "theater_posture":
        return f"theater posture: {detail}"
    return detail


def engine_date(line: str) -> str | None:
    """The in-game date from a log line's own prefix, if it has one.

    Worth preferring over anything the mod prints: the engine stamps it on every
    line, so it cannot be broken by a script token that did not expand.
    """
    match = ENGINE_DATE.match(line.strip())
    return parse_date(match.group("date")) if match else None


def parse_line(line: str) -> tuple[str, dict[str, str]] | None:
    """One ``LLMB|`` line -> (kind, fields). ``None`` if it is not ours."""
    match = LINE.search(line)
    if not match:
        return None
    if int(match.group("version")) != SCHEMA_VERSION:
        raise ValueError(
            f"LLM Bridge schema v{match.group('version')} but this harness speaks "
            f"v{SCHEMA_VERSION}. Update the mod or the harness -- do not run mismatched."
        )
    fields: dict[str, str] = {}
    for chunk in match.group("fields").split("|"):
        key, _, value = chunk.partition("=")
        if _:
            fields[key.strip()] = value.strip()
    return match.group("kind"), fields


class LogTailAdapter(GameAdapter):
    """Follows game.log and rebuilds GameState from the mod's telemetry."""

    supported_actions = frozenset()

    def __init__(
        self,
        log_path: Path | None = None,
        country: str | None = None,
        poll_seconds: float = 0.5,
    ):
        self.log_path = find_log(log_path)
        self.poll_seconds = poll_seconds
        self.country = country
        self._offset = 0
        #: The file the offset belongs to: (device, inode) and its first bytes.
        #: Both, because a deleted and recreated file can get the same inode.
        self._identity: tuple[int, int] | None = None
        self._head = b""
        self._state = GameState(unknown_fields=list(NOT_EMITTED))
        self._pending: list[GameEvent] = []
        self._seen_any = False
        #: tag -> last counter values seen, for completion events.
        self._counters: dict[str, dict[str, int]] = {}
        #: tag -> the tick being read / the last tick that was complete. A tick
        #: is a state line plus the focus/research probe lines after it; it is
        #: complete once the next state line for the same tag arrives.
        self._ticks: dict[str, dict] = {}
        self._closed: dict[str, dict] = {}
        self._research_event: dict[str, GameEvent] = {}

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="logtail",
            readable=True,
            writable=False,
            clock_control=False,
            notes=(
                f"log={self.log_path or 'not found'}; requires the LLM Bridge mod "
                f"(schema v{SCHEMA_VERSION})"
            ),
        )

    # --- reading -------------------------------------------------------------

    def poll(self) -> int:
        """Consume whatever has been appended since the last poll.

        Returns the number of telemetry lines applied. A truncated log (size
        shrank) or a replaced one (a new file, as a game restart writes) is a
        new session: it is read from the start, and nothing the old session
        reported carries over. A last line
        the game has not finished writing is left for the next poll: read early,
        ``mp=214000`` is ``mp=21`` and ``kind=12`` is ``kind=1``, a war.
        """
        if not self.log_path or not self.log_path.exists():
            raise FileNotFoundError(
                "No game.log found. Set HOI4_LOG_PATH, and check the LLM Bridge mod is enabled."
            )
        stat = self.log_path.stat()
        identity = (stat.st_dev, stat.st_ino)
        with self.log_path.open("rb") as raw:
            head = raw.read(HEAD_BYTES)
        replaced = self._identity not in (None, identity) or (
            self._head and not head.startswith(self._head)
        )
        if stat.st_size < self._offset or replaced:
            # Size alone misses a new log that has already grown past the old
            # offset before this poll: it would be read from the middle.
            self._offset = 0
            self._state = GameState(unknown_fields=list(NOT_EMITTED))
            self._pending = []
            self._counters = {}
            self._ticks, self._closed, self._research_event = {}, {}, {}
        self._identity = identity
        self._head = head
        applied = 0
        with self.log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(self._offset)
            while True:
                start = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    handle.seek(start)
                    break
                if PREFIX not in line:
                    continue
                parsed = parse_line(line)
                if parsed:
                    kind, fields = parsed
                    stamped = engine_date(line)
                    if stamped:
                        fields.setdefault("_engine_date", stamped)
                    self._apply(kind, fields)
                    applied += 1
            self._offset = handle.tell()
        return applied

    def _concerns_player(self, kind: str, fields: dict[str, str]) -> bool:
        """Whether a line is about the country this reader keeps to.

        A state line is the country's own. An event line names the scope the
        on_action ran in (``tag``) and, where there is one, the other side
        (``other``): a war is logged in the declarer's scope, so a war declared
        *on* the player only matches through ``other``.
        """
        tags = [fields.get("tag")]
        if kind == "evt":
            tags.append(fields.get("other"))
        tags = [t.strip().upper() for t in tags if t]
        return not tags or self.country.upper() in tags

    def _apply(self, kind: str, fields: dict[str, str]) -> None:
        state = self._state
        if self.country and not self._concerns_player(kind, fields):
            return

        if kind in {"state", "boot"}:
            self._seen_any = True
            if "tag" in fields:
                state.country = fields["tag"]
            iso = fields.get("_engine_date") or parse_date(
                fields.get("date", "") or fields.get("start", "")
            )
            if iso:
                state.date = iso
            for key, (attribute, convert) in FIELDS.items():
                if key in fields:
                    try:
                        setattr(state, attribute, convert(fields[key]))
                    except ValueError:
                        # A token the game did not expand prints literally. Treat
                        # it as missing, never as zero.
                        if attribute not in state.unknown_fields:
                            state.unknown_fields.append(attribute)
                        continue
                    if attribute in state.unknown_fields:
                        state.unknown_fields.remove(attribute)
            if kind == "state":
                self._split_trade(fields)
                self._derive_completions(fields)
                self._open_tick(fields)
        elif kind in {"focus", "research"}:
            self._probe(kind, fields)
        elif kind == "evt":
            code = fields.get("kind", "")
            event_kind, severity = EVENT_CODES.get(code, (code or "unknown", "info"))
            if event_kind in POSTURE_EVENTS or event_kind == "directives_cleared":
                state.posture = POSTURE_EVENTS.get(event_kind)
                if "posture" in state.unknown_fields:
                    state.unknown_fields.remove("posture")
            if event_kind == "theater_posture":
                posture, _, tag = fields.get("detail", "").strip().partition(" ")
                if posture and tag:
                    state.theater_postures[tag.strip().upper()] = posture
                    if "theater_postures" in state.unknown_fields:
                        state.unknown_fields.remove("theater_postures")
            if event_kind == "directives_cleared":
                state.theater_postures = {}
                if "theater_postures" in state.unknown_fields:
                    state.unknown_fields.remove("theater_postures")
            if event_kind == "directives_cleared":
                # Standing down clears every directive: from here on the list is
                # complete, not just what has been seen.
                state.ai_directives = []
                if "ai_directives" in state.unknown_fields:
                    state.unknown_fields.remove("ai_directives")
            elif event_kind == "directive_raised":
                # Each raise logs "<directive> <TAG>" as a literal. Directives
                # raised before the harness started reading are not in this list
                # until a stand-down resets it; a raise it did see always is.
                entry = fields.get("detail", "").strip()
                if entry and entry not in state.ai_directives:
                    state.ai_directives.append(entry)
                if "ai_directives" in state.unknown_fields:
                    state.unknown_fields.remove("ai_directives")
            self._pending.append(
                GameEvent(
                    kind=event_kind,
                    text=_event_text(event_kind, fields.get("detail", "")),
                    severity=severity,
                    date=state.date,
                )
            )

    def _open_tick(self, fields: dict[str, str]) -> None:
        """A state line starts a tick: the probe lines after it say what is running.

        The previous tick for this country is complete now, so it is compared
        with the one before it. A day with no ``focus`` line means no focus is
        running; waiting for the next state line is what makes that safe, since
        a poll can land between a state line and the probe lines behind it.
        """
        tag = fields.get("tag", "").strip().upper()
        done = self._ticks.get(tag)
        if done is not None:
            self._close_tick(tag, done)
        # The brief keeps showing the last complete day until this one closes.
        # Clearing here would report "no focus" between a state line and the
        # probe lines behind it -- and that wakes the planner for nothing.
        self._ticks[tag] = {"date": fields.get("_engine_date"), "focus": set(), "research": set()}

    def _probe(self, kind: str, fields: dict[str, str]) -> None:
        tag = fields.get("tag", "").strip().upper()
        ident = fields.get("id", "").strip()
        tick = self._ticks.get(tag)
        if tick is None or not ident:
            return
        stamp = fields.get("_engine_date")
        if stamp and tick["date"] and stamp != tick["date"]:
            return        # another day's tick; lines for two days can interleave
        state = self._state
        tick[kind].add(ident)
        # A probe line is positive evidence, so it shows at once; only absence
        # waits for the day to close.
        if kind == "focus":
            state.national_focus = ident
        elif ident not in [slot.technology for slot in state.research]:
            state.research.append(ResearchSlot(index=len(state.research), technology=ident))
        if ("national_focus" if kind == "focus" else "research") in state.unknown_fields:
            state.unknown_fields.remove("national_focus" if kind == "focus" else "research")

    def _close_tick(self, tag: str, tick: dict) -> None:
        last = self._closed.get(tag)
        self._closed[tag] = tick
        self._state.telemetry_ticks = (self._state.telemetry_ticks or 0) + 1
        self._show_tick(tick)
        if last is None:
            return
        date_ = self._state.date
        for focus in sorted(last["focus"] - tick["focus"]):
            self._pending.append(GameEvent(
                kind="focus_complete", text=f"focus complete: {focus}",
                severity="notable", date=date_))
        gone = sorted(last["research"] - tick["research"])
        if not gone:
            return
        names = ", ".join(gone)
        event = self._research_event.pop(tag, None)
        if event is not None and any(e is event for e in self._pending):
            # The techs counter already reported this one, a day ahead and
            # without a name; say which it was rather than report it twice.
            event.text += f": {names}"
        elif tag not in self._counters or "techs" not in self._counters[tag]:
            self._pending.append(GameEvent(
                kind="research_done", text=f"research complete: {names}",
                severity="notable", date=date_))

    def _show_tick(self, tick: dict) -> None:
        """Make a complete day's probe lines the state: what ran that day, and nothing else."""
        state = self._state
        state.national_focus = min(tick["focus"]) if tick["focus"] else None
        state.research = [ResearchSlot(index=i, technology=t) for i, t in enumerate(sorted(tick["research"]))]
        for name in ("national_focus", "research"):
            if name in state.unknown_fields:
                state.unknown_fields.remove(name)

    def _split_trade(self, fields: dict[str, str]) -> None:
        """Separate owned civilian factories from ones on loan through trade.

        The game's civilian count includes trade (seen live: 20, of which 9
        owned and 11 from trade). ``owned`` is ``num_of_factories``, which the
        game documents as excluding trade and lend-lease; what it leaves after
        military factories and dockyards is the owned civilian count. A split
        that does not add up is not used.
        """
        state = self._state
        try:
            owned = int(fields["owned"])
            civ, mil, doc = int(fields["civ"]), int(fields["mil"]), int(fields["doc"])
        except (KeyError, ValueError):
            state.trade_civilian_factories = None
            return
        owned_civ = owned - mil - doc
        if 0 <= owned_civ <= civ:
            state.civilian_factories = owned_civ
            state.trade_civilian_factories = civ - owned_civ
        else:
            state.trade_civilian_factories = None

    def _derive_completions(self, fields: dict[str, str]) -> None:
        """Turn a rise in a completion counter into the event the game never fires.

        Compared against the previous state line of the same country. The first
        line only sets the baseline, and a counter that fell (a new game in the
        same log) resets it rather than reporting anything. Focus completion
        has no counter: 1.19.5.0 exposes no variable for it.
        """
        now: dict[str, int] = {}
        for key in COUNTERS:
            try:
                now[key] = int(fields[key])
            except (KeyError, ValueError):
                continue
        tag = fields.get("tag", "").strip().upper()
        before = self._counters.get(tag)
        self._counters[tag] = {**(before or {}), **now}
        if before is None:
            return
        date_ = self._state.date

        def rise(key: str) -> int:
            return now[key] - before[key] if key in now and key in before else 0

        if rise("techs") > 0:
            n = rise("techs")
            event = GameEvent(
                kind="research_done",
                text=f"research complete: {n} new technolog{'y' if n == 1 else 'ies'} "
                     f"({now['techs']} researched)",
                severity="notable", date=date_)
            self._pending.append(event)
            self._research_event[tag] = event
        built = [f"+{rise(k)} {COUNTERS[k]}" for k in CONSTRUCTION_COUNTERS if rise(k) > 0]
        if built:
            self._pending.append(GameEvent(
                kind="construction_complete", text="construction complete: " + ", ".join(built),
                severity="notable", date=date_))

    def read_state(self) -> GameState:
        """The state now, with every event the loop has not acknowledged yet.

        Reading never drains the queue. It used to, and the clock wait polls
        through this read: the poll that saw a war declaration ended the wait
        early and took the event with it, so the loop's next read found nothing
        and the planner was never woken for the war (#62). Events now leave
        only through ``acknowledge_events``.
        """
        self.poll()
        if not self._seen_any:
            raise RuntimeError(
                "game.log has no LLM Bridge telemetry. Enable the mod, or use "
                "the 'Emit telemetry now' decision to force a line."
            )
        self._state.events = list(self._pending)
        return self._state

    def peek_state(self) -> GameState:
        """Same as ``read_state``, which no longer drains anything.

        Kept for callers that ask for a non-draining read by name, such as the
        input driver's verification polling.
        """
        return self.read_state()

    def acknowledge_events(self, events: Iterable[GameEvent]) -> None:
        """Drop exactly these events, matched by identity.

        By identity, not by value: two directive raises on the same day can be
        equal field for field, and acknowledging one must not drop the other.
        Whatever arrived after the read that returned ``events`` stays queued.
        """
        answered = {id(event) for event in events}
        self._pending = [event for event in self._pending if id(event) not in answered]

    def apply(self, call: ActionCall) -> ActionResult:
        return self.unsupported(call)

    def advance(self, days: int) -> GameState:
        """Watch the clock until the date has moved, or something critical lands.

        This adapter never touches the clock -- it has no write channel and, in
        co-op, no business taking it. The game runs at whatever speed the player
        or the writer set.
        """
        return wait_for_days(self.read_state, days, self.poll_seconds)
