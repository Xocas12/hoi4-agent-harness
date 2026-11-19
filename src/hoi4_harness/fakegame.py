"""A fake HOI4 that speaks the same files, for a harness run with no game at all.

The real stack reads the mod's telemetry out of ``game.log``, writes console
commands into ``llmb_turn.txt`` and presses a fixed key sequence (the console
key, ``run llmb_turn.txt``, enter, the console key), plus a keyboard script for
national focuses. This module is the other end of all three:

* it appends schema-v5 ``LLMB|`` lines to ``<user dir>/logs/game.log`` with the
  engine's date prefix, one ``state`` line (and its ``army``, ``focus`` and
  ``research`` lines) per in-game day, a ``war`` line per enemy while the player
  is at war, ``evt`` lines for what the mod logs;
* it receives hardware scan codes through ``send(scan, up)`` -- the very
  function the real key layer calls with SendInput -- and plays them into a
  small model of the screen: the console, the focus panel and its search box,
  the pause and speed keys, the menu that Escape opens and that swallows the
  next key. Typed text that lands nowhere triggers hotkeys, as it would;
* when a ``run`` arrives it reads the file from the user directory, parses the
  console commands the channel writes (``eval_effect TAG = { ... }``,
  ``effect TAG name``, ``gamespeed N``) and applies them to the game model,
  refusing what the real game would refuse: not owned, no free building slot,
  no free factory, not enough political power, an advisor slot already taken;
* ``savegame`` writes a text save into ``<user dir>/save games`` a couple of
  seconds later (``fakesave.py``: the layout the real game wrote on 1.20.0), or a
  binary one, or none, when a test asks for those failures.

The game model is the mock adapter's (``adapters/mock.py``), so the same seeded
world drives the daily numbers. Nothing here is a HOI4 simulator and it says so:
it exists so the *harness* -- log reader, console channel, key layer, clock
logic, agent loop -- can be run end to end and tested with no game, no window
and no input. What it cannot tell you is whether the real game accepts a
command; ``docs/live-session.md`` is the plan for that.

Time moves only when someone sleeps: ``FakeGame.sleep(seconds)`` advances the
in-game clock by ``seconds / seconds_per_day`` while the game is running, and
never really waits. Hand it to every injectable ``sleep`` in the stack and a run
is deterministic and instant. As a process (``hoi4-harness fakegame``) it is
driven by the wall clock instead, and takes its keys from a file.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import fakesave, modgen
from .actions.catalog import AI_DIRECTIVES
from .adapters.keys import key_name
from .adapters.mock import MockAdapter
from .adapters.window import FocusCheck
from .fakearmy import FakeArmy, FakeGui
from .fakecampaign import FakeAI
from .identifiers import IdentifierIndex, Playset
from .types import ActionCall, ConstructionItem, ProductionLine, ResearchSlot, ScenarioStart

SCHEMA_VERSION = 5
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
#: Wall-clock seconds one in-game day takes at each game speed.
SECONDS_PER_DAY = {1: 2.0, 2: 1.0, 3: 0.5, 4: 0.25, 5: 0.1}
#: The hour the daily on_action runs at, as in a real log.
TICK_HOUR = 21
#: The game's own building ids -> the harness's names (console.BUILDING_IDS, inverted).
BUILDING_NAMES = {
    "industrial_complex": "civilian_factory", "arms_factory": "military_factory",
    "dockyard": "dockyard", "infrastructure": "infrastructure", "air_base": "air_base",
    "anti_air_building": "anti_air", "radar_station": "radar",
    "synthetic_refinery": "synthetic_refinery",
}
#: The ones that take a slot in a state's shared pool; the rest have their own cap.
SLOT_BUILDINGS = {"industrial_complex", "arms_factory", "dockyard", "synthetic_refinery"}
OTHER_BUILDING_CAP = 5
#: Advisor slots with more than one seat; every other slot holds one.
ADVISOR_SEATS = {"high_command": 3}
#: Counters the state line carries, by what finishing a building adds to.
BUILT_COUNTER = {"civilian_factory": "built_civ", "military_factory": "built_mil",
                 "dockyard": "built_dock", "infrastructure": "built_infra"}


class ScriptError(Exception):
    """What the console would print for a line it cannot run."""


# --- the script the console runs ----------------------------------------------

_TOKEN = re.compile(r'"[^"]*"|<=|>=|[{}=<>]|[^\s{}=<>"]+')


@dataclass
class Entry:
    key: str
    op: str
    value: str | list[Entry]


def parse_script(text: str) -> list[Entry]:
    """``key = value`` entries, with ``{ ... }`` blocks nested. Small on purpose:
    the effects the harness writes and nothing more."""
    tokens = _TOKEN.findall(text)
    entries, pos = _parse_block(tokens, 0, top=True)
    if pos != len(tokens):
        raise ScriptError("unbalanced '}'")
    return entries


def _parse_block(tokens: list[str], pos: int, top: bool = False) -> tuple[list[Entry], int]:
    entries: list[Entry] = []
    while pos < len(tokens):
        token = tokens[pos]
        if token == "}":
            if top:
                raise ScriptError("unbalanced '}'")
            return entries, pos + 1
        if token in "{=<>" or token in ("<=", ">="):
            raise ScriptError(f"unexpected '{token}'")
        op = tokens[pos + 1] if pos + 1 < len(tokens) else ""
        if op not in ("=", "<", ">", "<=", ">="):
            raise ScriptError(f"expected '=' after '{token}'")
        if pos + 2 >= len(tokens):
            raise ScriptError(f"'{token}' has no value")
        if tokens[pos + 2] == "{":
            value, pos = _parse_block(tokens, pos + 3)
            entries.append(Entry(token, op, value))
        else:
            entries.append(Entry(token, op, tokens[pos + 2].strip('"')))
            pos += 3
    if not top:
        raise ScriptError("missing '}'")
    return entries, pos


def _number(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        raise ScriptError(f"{raw!r} is not a number") from None


def _compare(left: float, op: str, right: float) -> bool:
    return {"=": left == right, "<": left < right, ">": left > right,
            "<=": left <= right, ">=": left >= right}[op]


def _get(entries: list[Entry], key: str) -> Entry | None:
    return next((e for e in entries if e.key == key), None)


@dataclass
class FakeState:
    """A state's building slots: ``used`` are finished buildings, queued ones are
    counted from the construction queue."""

    id: str
    name: str
    owner: str | None
    slots: int
    used: int
    #: Finished buildings outside the shared slot pool, by game building id.
    others: dict[str, int] = field(default_factory=dict)


# --- a playset to run against when there is no install ------------------------

DEMO_FILES = {
    "common/country_tags/00_countries.txt":
        'SWE = "countries/Sweden.txt"\nGER = "countries/Germany.txt"\n'
        'POL = "countries/Poland.txt"\nFIN = "countries/Finland.txt"\n'
        'ENG = "countries/England.txt"\nSOV = "countries/Soviet.txt"\n',
    "common/national_focus/swe.txt": """focus_tree = {
	id = swe_demo_tree
	country = { factor = 0 modifier = { add = 10 tag = SWE } }
	focus = { id = SWE_defense_act x = 0 y = 0 }
	focus = { id = industrial_effort x = 2 y = 0 }
	focus = { id = SWE_expand_the_army x = 0 y = 1 prerequisite = { focus = SWE_defense_act } }
	focus = { id = SWE_rearmament x = 1 y = 2
		prerequisite = { focus = SWE_expand_the_army } prerequisite = { focus = industrial_effort } }
}
""",
    "common/characters/swe.txt": """characters = {
	SWE_demo_minister = {
		advisor = { slot = political_advisor idea_token = SWE_demo_minister_tok cost = 25 }
	}
	SWE_demo_second_minister = {
		advisor = { slot = political_advisor idea_token = SWE_demo_second_tok cost = 25 }
	}
	SWE_demo_theorist = {
		advisor = { slot = theorist idea_token = SWE_demo_theorist_tok cost = 100 }
	}
	SWE_demo_general = {
		advisor = { slot = high_command idea_token = SWE_demo_general_tok cost = 150 }
	}
}
""",
    "common/units/equipment/infantry.txt": """equipments = {
	infantry_equipment = { is_archetype = yes is_buildable = no }
	infantry_equipment_1 = { archetype = infantry_equipment }
	artillery_equipment = { is_archetype = yes is_buildable = no }
	artillery_equipment_1 = { archetype = artillery_equipment }
}
""",
    "history/states/10-Svealand.txt":
        'state = { id = 10 name = "STATE_10" history = { owner = SWE } }',
    "history/states/11-Norrland.txt":
        'state = { id = 11 name = "STATE_11" history = { owner = SWE } }',
    "history/states/12-Gotaland.txt":
        'state = { id = 12 name = "STATE_12" history = { owner = SWE } }',
    "history/states/20-Pomerania.txt":
        'state = { id = 20 name = "STATE_20" history = { owner = GER } }',
    "localisation/english/state_names_l_english.yml": (
        '﻿l_english:\n STATE_10:0 "Svealand"\n STATE_11:0 "Norrland"\n'
        ' STATE_12:0 "Gotaland"\n STATE_20:0 "Pomerania"\n'),
    "localisation/english/focus_l_english.yml": (
        '﻿l_english:\n SWE_defense_act:0 "The Defense Act"\n'
        ' industrial_effort:0 "Industrial Effort"\n'
        ' SWE_expand_the_army:0 "Expand the Army"\n SWE_rearmament:0 "Rearmament Programme"\n'),
}


def write_demo_playset(directory: Path) -> Path:
    """A tiny playset (one focus tree for SWE, four states, four advisors, two
    equipment lines) that ``FakeGame`` runs on when no install is configured. Not
    the game's content: ids here exist only in the fake."""
    for relative, text in DEMO_FILES.items():
        path = Path(directory) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    return Path(directory)


def demo_index(directory: Path) -> IdentifierIndex:
    return IdentifierIndex.build(Playset.from_paths(write_demo_playset(directory)))


def demo_start() -> ScenarioStart:
    """Enough political power for advisors, and one technology about to finish."""
    return ScenarioStart(
        political_power=120.0,
        research=[ResearchSlot(index=0, technology="infantry_weapons2", days_remaining=45),
                  ResearchSlot(index=1), ResearchSlot(index=2)],
    )


# --- the world ----------------------------------------------------------------


class _World(MockAdapter):
    """The mock, with the two counters the state line carries and a hook for the
    events the mod would log."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.techs = 32
        self.built = {name: 0 for name in BUILT_COUNTER.values()}
        self.on_event = None
        self.on_built = None
        self.focus_total: int | None = None

    def _emit(self, kind: str, text: str, severity: str = "info") -> None:
        # The fake keeps no event queue: the log is the only channel, as in the game.
        if kind == "research_done":
            self.techs += 1
        if self.on_event is not None:
            self.on_event(kind, text, severity)

    def _finish_building(self, item: ConstructionItem) -> None:
        super()._finish_building(item)
        counter = BUILT_COUNTER.get(item.building)
        if counter:
            self.built[counter] += 1
        if self.on_built is not None:
            self.on_built(item)

    def _do_set_national_focus(self, call):
        result = super()._do_set_national_focus(call)
        if result.ok:
            self.focus_total = self.state.focus_days_remaining
        return result


class FakeGame:
    """The game, as far as the harness can tell from its files and its keys."""

    def __init__(
        self,
        user_dir: Path,
        country: str = "SWE",
        seed: int = 1936,
        start: str = "1936-01-01",
        index: IdentifierIndex | None = None,
        start_state: ScenarioStart | None = None,
        variance: float = 1.0,
        slots: int = 6,
        slots_used: int = 4,
        target_tags: tuple[str, ...] = modgen.DEFAULT_TAGS,
        speed: int = 3,
        save_delay: float = 2.0,
        army: FakeArmy | None = None,
    ):
        self.user_dir = Path(user_dir)
        self.user_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.user_dir / "logs" / "game.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.country = country
        self.index = index
        self.target_tags = target_tags
        self.world = _World(seed=seed, country=country, start=start,
                            start_state=start_state if start_state is not None else demo_start(),
                            variance=variance)
        self.world.on_event = self._world_event
        self.world.on_built = self._built
        #: Divisions on a map (fakearmy.py); None: no front, the mod prints n=0.
        self.army = army
        # A game loads paused.
        self.paused = True
        #: The console's ``human_ai`` toggle: the game's AI runs the player's country.
        self.human_ai = False
        #: Country flags the console's effects set (``llmb_harness`` is the one that matters).
        self.flags: set[str] = set()
        #: A game that is not running writes nothing and ticks no day (``kill``).
        self.alive = True
        #: Whether the game is the focused window, and the wall-clock second it comes
        #: back at if a test took the focus away for a while (``lose_focus``).
        self.focused = True
        self._focus_back_at: float | None = None
        self.speed = max(1, min(5, speed))
        #: Game hours left before ``pause_in_hours`` stops the game; None when unset.
        self.pause_in: float | None = None
        self._acc = 0.0
        # --- the screen
        self._held: set[str] = set()
        self.console_open = False
        self.line = ""
        self.panel: str | None = None
        self.search: str | None = None        # the text field that has the keyboard, if any
        self.menu_open = False
        # --- what the mod's flags hold
        self.target: str | None = None
        self.directives: list[str] = []
        self.global_posture: str | None = None
        self.theater_postures: dict[str, str] = {}
        self.advisors: dict[str, list[str]] = {}
        # --- the record, for tests and for a person reading what happened
        self.keylog: list[str] = []
        #: Keys that arrived with no console open: nothing the harness sends in a console-only
        #: mode (rival) should ever be here, and the pause key would be.
        self.stray_keys: list[str] = []
        #: The wall-clock second each ``run`` file was read at, parallel to ``runs``.
        self.run_walls: list[float] = []
        self.runs: list[list[str]] = []
        self.errors: list[str] = []
        self.speed_log: list[int] = []
        # --- saves: the console's savegame command writes one a few seconds later
        self.save_dir = self.user_dir / "save games"
        #: Wall-clock seconds from the command to the file appearing.
        self.save_delay = save_delay
        #: A test's way to make the next saves fail like a misconfigured game:
        #: ``binary`` writes what ``save_as_binary=yes`` would, ``none`` writes nothing.
        self.save_mode = "text"
        self.saves_written: list[Path] = []
        self._wall = 0.0
        self._pending_saves: list[tuple[float, str]] = []
        self._last_save_mtime = 0.0
        #: iso date -> (``declare`` | ``peace``, tag, enemy divisions), played when the
        #: day comes, for a war a test wants declared (or ended) mid-run.
        self.war_script: dict[str, tuple[str, str, int]] = {}
        self.states = self._build_states(slots, slots_used)
        #: The world beyond the player and what the game's AI does under ``human_ai``.
        self.ai = FakeAI(self, seed=seed)
        self._key_offset = 0
        self._started = time.strftime("%H:%M:%S")
        self._write(self._stamp() + ": Fake HOI4 starting (hoi4-harness fakegame)")
        if self.visible:          # the mod's on_startup limits on llm_bridge_is_harness too
            self.emit_state()

    # --- the files ------------------------------------------------------------

    def _build_states(self, slots: int, used: int) -> dict[str, FakeState]:
        if self.index is None or not self.index.states:
            return {}
        return {
            sid: FakeState(sid, info.name, info.owner, slots=slots, used=used)
            for sid, info in self.index.states.items()
        }

    def _stamp(self) -> str:
        d = self.world.date
        return f"[{time.strftime('%H:%M:%S')}][{d.year}.{d.month:02d}.{d.day:02d}.{TICK_HOUR:02d}]"

    def _write(self, *lines: str) -> None:
        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(line + "\n" for line in lines))

    def _log(self, text: str) -> None:
        self._write(f"{self._stamp()}: {text}")

    def _llmb(self, record: str, **fields) -> str:
        body = "|".join(f"{k}={v}" for k, v in fields.items())
        return f"{self._stamp()}: LLMB|v{SCHEMA_VERSION}|{record}|{body}"

    @property
    def visible(self) -> bool:
        """Whether the mod's on_actions report this country: ``is_ai = no`` or the
        ``llmb_harness`` flag (``llm_bridge_is_harness``). Under ``human_ai`` with no flag
        the player counts as AI and the daily log goes silent, as it would in the game."""
        return not self.human_ai or "llmb_harness" in self.flags

    def emit_campaign(self) -> None:
        """The mod's ``llm_bridge_emit_campaign``: the weekly conquest numbers."""
        self._write(self._llmb("camp", **self.ai.campaign_fields()))

    def kill(self) -> None:
        """The game process dies: no more days, no more lines."""
        self.alive = False

    def lose_focus(self, seconds: float | None = None) -> None:
        """Another window takes the focus, for ``seconds`` of wall clock (None: for good)."""
        self.focused = False
        self._focus_back_at = None if seconds is None else self._wall + seconds

    def focus_check(self, title: str) -> FocusCheck:
        if self.focused:
            return FocusCheck(True, title)
        return FocusCheck(False, "Some Other Window", reason="focused window is 'Some Other Window'")

    def emit_state(self) -> None:
        """The mod's ``llm_bridge_emit_state``: the state line, the army line and
        the probe lines for the focus and the research that are running."""
        w, s, d = self.world, self.world.state, self.world.date
        tag = self.country
        lines = [self._llmb(
            "state",
            date=f" {TICK_HOUR}:00, {d.day} {MONTHS[d.month - 1]}, {d.year}", tag=tag,
            pp=f"{s.political_power:.4f}", stab=f"{s.stability * 100:.3f}",
            ws=f"{s.war_support * 100:.0f}", civ=s.civilian_factories, mil=s.military_factories,
            doc=s.dockyards, owned=s.civilian_factories + s.military_factories + s.dockyards,
            mp=s.manpower, ai=int(self.human_ai), techs=w.techs, **w.built)]
        lines.append(self._llmb("army", tag=tag, divisions=sum(g.count for g in s.divisions),
                                delegated=0))
        # focus_progress > 0: a focus started today has none yet, as in the game.
        if (s.national_focus and s.focus_days_remaining is not None and w.focus_total
                and s.focus_days_remaining < w.focus_total):
            lines.append(self._llmb("focus", tag=tag, id=s.national_focus))
        lines += [self._llmb("research", tag=tag, id=slot.technology)
                  for slot in s.research if slot.technology]
        lines += [self._llmb("war", tag=tag, enemy=war.against, divs=self._enemy_divisions(war),
                             border=int(war.borders is not False),
                             surr=f"{(war.surrender_progress or 0) * 100:.0f}")
                  for war in s.wars]
        self._write(*lines)

    def _enemy_divisions(self, war) -> int:
        if war.enemy_divisions is not None:
            return war.enemy_divisions
        return sum(f.divisions_enemy for f in self.world.state.fronts if f.enemy == war.against)

    def start_war(self, enemy: str, divisions: int = 30, declared_by_player: bool = False) -> None:
        """The player is at war with ``enemy`` from now: the mod's ``war_declared``
        event (logged in the declarer's scope), and a ``war`` line in every state tick
        from here on."""
        from .types import War

        state = self.world.state
        if not any(w.against == enemy for w in state.wars):
            state.wars.append(War(against=enemy, since=self.date, enemy_divisions=divisions,
                                  borders=True, surrender_progress=0.0))
        if self.visible:
            declarer, target = (self.country, enemy) if declared_by_player else (enemy, self.country)
            self._write(self._llmb("evt", tag=declarer, other=target, kind=1, detail=0))

    def capitulation(self, enemy: str) -> None:
        """``enemy`` capitulates: the mod's ``on_capitulation`` line (kind 2)."""
        if self.visible:
            self._write(self._llmb("evt", tag=enemy, other=self.country, kind=2, detail=0))

    def end_war(self, enemy: str) -> None:
        state = self.world.state
        state.wars = [w for w in state.wars if w.against != enemy]
    def emit_front(self) -> None:
        """The mod's ``llm_bridge_emit_front``: a ``div`` line per state with a
        division of either side on the front, then ``divend`` with the count. At
        peace (nobody at war with the player) only the ``divend`` is printed."""
        rows = self.army.front_rows() if self.army is not None and self.army.at_war else []
        lines = [self._llmb("div", tag=self.country, state=r["state"], ctl=r["ctl"], own=r["own"],
                            own_weak=r["own_weak"], foe=r["foe"], foe_weak=r["foe_weak"])
                 for r in rows]
        lines.append(self._llmb("divend", tag=self.country, n=len(rows)))
        self._write(*lines)

    @property
    def gui(self) -> FakeGui:
        """The mouse, for a driver built with ``gui=``: clicks land on the army's map."""
        if self.army is None:
            raise RuntimeError("this fake game has no army, so it has no map to click on")
        return FakeGui(self.army, lambda: self._held)

    def _event(self, code: int, detail: str = "0", other: str | None = None) -> None:
        fields = {"tag": self.country}
        if other:
            fields["other"] = other
        self._write(self._llmb("evt", **fields, kind=code, detail=detail))

    def _world_event(self, kind: str, text: str, severity: str) -> None:
        if kind == "war":
            # The scripted war: logged in the declarer's scope, the player the other side.
            attacker = "GER" if self.country != "GER" else "POL"
            self._write(self._llmb("evt", tag=attacker, other=self.country, kind=1, detail=0))

    # --- time -----------------------------------------------------------------

    @property
    def date(self) -> str:
        return self.world.date.isoformat()

    @property
    def date_ordinal(self) -> int:
        return self.world.date.toordinal()

    def tick_day(self) -> None:
        """One in-game day: the model moves, and the mod logs it."""
        self.world.date += dt.timedelta(days=1)
        self.world._tick_one_day()
        scripted = self.war_script.pop(self.date, None)
        if scripted is not None:
            action, enemy, divisions = scripted
            if action == "declare":
                self.start_war(enemy, divisions)
            else:
                self.end_war(enemy)
        if self.army is not None:
            self.army.tick_day()
        if self.human_ai:
            self.ai.tick_day()
        if self.visible:
            self.emit_state()
            self.emit_front()
            if self.date_ordinal % 7 == 0:
                self.emit_campaign()

    def _built(self, item: ConstructionItem) -> None:
        """A finished building leaves the queue and takes its slot for good."""
        state = next((s for s in self.states.values() if s.name == item.state), None)
        game_id = next((g for g, n in BUILDING_NAMES.items() if n == item.building), None)
        if state is None or game_id is None:
            return
        if game_id in SLOT_BUILDINGS:
            state.used += 1
        else:
            state.others[game_id] = state.others.get(game_id, 0) + 1

    def _queued(self, state: FakeState, building: str | None = None) -> int:
        return sum(1 for item in self.world.state.construction
                   if item.state == state.name
                   and (building is None or item.building == BUILDING_NAMES.get(building)))

    def run_days(self, days: int) -> None:
        for _ in range(days):
            self.tick_day()

    def sleep(self, seconds: float) -> None:
        """Let ``seconds`` of wall clock pass: in-game days go by while the game
        runs, none while it is paused. Never really waits."""
        if seconds > 0:
            self._wall += seconds
            if self._focus_back_at is not None and self._wall >= self._focus_back_at:
                self.focused, self._focus_back_at = True, None
            if self.alive:
                self._write_due_saves()
        if self.paused or seconds <= 0 or not self.alive:
            return
        self._acc += seconds
        day = SECONDS_PER_DAY[self.speed]
        while self._acc >= day and not self.paused:
            self._acc -= day
            if self.pause_in is not None and self.pause_in < 24:
                # The deadline falls inside this day: the game stops before it.
                self.paused, self.pause_in = True, None
                break
            if self.pause_in is not None:
                self.pause_in -= 24
            self.tick_day()
        if self.paused:
            self._acc = 0.0

    # --- saves ----------------------------------------------------------------

    def _savegame(self) -> None:
        """The console's ``savegame``: the state *now*, on disk a few seconds on."""
        if self.save_mode == "none":
            return
        text = fakesave.render_save(self, hour=TICK_HOUR + 1)
        self._pending_saves.append((self._wall + self.save_delay, text))

    def _write_due_saves(self) -> None:
        due = [p for p in self._pending_saves if p[0] <= self._wall]
        self._pending_saves = [p for p in self._pending_saves if p[0] > self._wall]
        for _, text in due:
            self.write_save(text)

    def write_save(self, text: str | None = None) -> Path:
        """A save file as the game names them, ``<TAG>_<Y>_<MM>_<DD>_<HH>.hoi4``: two
        in the same game hour are the same file, which is why the harness compares
        modification times and not names. Its mtime always moves forward."""
        d = self.world.date
        if text is None:
            text = fakesave.render_save(self, hour=TICK_HOUR + 1)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        name = f"{self.country}_{d.year}_{d.month:02d}_{d.day:02d}_{TICK_HOUR + 1:02d}.hoi4"
        path = self.save_dir / name
        if self.save_mode == "binary":
            path.write_bytes(b"HOI4bin\x00\x01" + text.encode("utf-8")[:64])
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
        self._last_save_mtime = max(time.time(), self._last_save_mtime + 1.0)
        os.utime(path, (self._last_save_mtime, self._last_save_mtime))
        self.saves_written.append(path)
        return path

    # --- the keyboard ---------------------------------------------------------

    def send(self, scan: int, up: bool) -> None:
        """What ``SendInput`` would deliver: one hardware scan code, down or up."""
        name = key_name(scan)
        if name is None:
            return
        if name in ("ctrl", "shift", "alt"):
            (self._held.discard if up else self._held.add)(name)
            return
        if up:
            return
        mods = frozenset(self._held)
        label = "+".join([*sorted(mods), name])
        self.keylog.append(label)
        if not self.console_open and name != "grave":
            self.stray_keys.append(label)
        self._key(name, mods)

    @staticmethod
    def _char(name: str, mods: frozenset[str]) -> str | None:
        if mods - {"shift"}:
            return None
        shift = "shift" in mods
        if len(name) == 1 and name.isalpha():
            return name.upper() if shift else name
        if name == "space":
            return " "
        if name == "-":
            return "_" if shift else "-"
        if len(name) == 1 and name in "0123456789'.,":
            return name
        return None

    def _key(self, name: str, mods: frozenset[str]) -> None:
        if self.menu_open:
            # Escape with no panel opened the game menu; it eats the next key.
            self.menu_open = False
            return
        if name == "grave":
            self.console_open = not self.console_open
            self.line = ""
            return
        if self.console_open:
            self._console_key(name, mods)
            return
        if self.search is not None:
            self._search_key(name, mods)
            return
        if name == "escape":
            if self.panel:
                self.panel = None
            else:
                self.menu_open = True
            return
        if name == "space" and not mods:
            self.paused = not self.paused
            return
        if name == "kp_plus":
            self.speed = min(5, self.speed + 1)
            return
        if name == "kp_minus":
            self.speed = max(1, self.speed - 1)
            return
        if name == "f" and mods == {"ctrl"}:
            self.search = ""
            return
        panel = {"q": "politics", "w": "research", "t": "construction", "y": "production",
                 "e": "market", "r": "trade"}.get(name)
        if name == "q" and mods == {"alt"}:
            panel = "focus"
        elif name == "q" and mods == {"shift"}:
            panel = "decisions"
        elif mods:
            return
        if panel:
            self.panel = None if self.panel == panel else panel

    def _console_key(self, name: str, mods: frozenset[str]) -> None:
        if name == "enter":
            line, self.line = self.line, ""
            self._console(line)
        elif name == "backspace":
            self.line = self.line[:-1]
        else:
            char = self._char(name, mods)
            if char is not None:
                self.line += char

    def _search_key(self, name: str, mods: frozenset[str]) -> None:
        if name == "escape":
            self.search = None
        elif name == "enter" and mods == {"shift"} and self.panel == "focus":
            text, self.search = self.search or "", None
            self._start_focus_from_search(text)
        elif name == "enter" and not mods:
            self.search = None          # the map's search: a result is picked with the mouse
        elif name == "backspace":
            self.search = (self.search or "")[:-1]
        else:
            char = self._char(name, mods)
            if char is not None:
                self.search = (self.search or "") + char

    def _start_focus_from_search(self, text: str) -> None:
        """Shift+enter in the focus panel's search: start the first match."""
        if self.index is None or not text.strip():
            return
        done = self.world.state.completed_focuses
        wanted = text.strip().lower()
        matches = sorted(
            focus for focus in self.index.available_focuses(self.country, done)
            if wanted in (self.index.focus_name(focus) or "").lower())
        if not matches:
            return
        result = self.world.apply(ActionCall("set_national_focus", {"focus_id": matches[0]}))
        if result.ok:
            self.panel = None
        else:
            self._log(f"focus {matches[0]} not started: {result.message}")

    # --- the console ----------------------------------------------------------

    def _console(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            self._command(line)
        except ScriptError as exc:
            self.errors.append(str(exc))
            self._log(str(exc))

    def _command(self, line: str) -> None:
        command, _, rest = line.partition(" ")
        command = command.lower()
        if command == "run":
            return self._run_file(rest.strip())
        if command == "gamespeed":
            return self._gamespeed(rest.strip())
        if command == "pause_in_hours":
            return self._pause_in_hours(rest.strip())
        if command == "human_ai":
            # A toggle, as the game's: "Toggles AI for Human countries".
            self.human_ai = not self.human_ai
            return None
        if command in ("goto_province", "goto_state"):
            return self._goto(command, rest.strip())
        if command == "effect":
            return self._scripted_effect(rest)
        if command == "eval_effect":
            return self._eval_effect(rest)
        if command in ("savegame", "save"):
            return self._savegame()
        raise ScriptError(f"Unknown command: {command}")

    def _run_file(self, name: str) -> None:
        path = self.user_dir / name
        if not name or not path.is_file():
            raise ScriptError(f"Could not open file: {name}")
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.lstrip().startswith("#")]
        self.runs.append(lines)
        self.run_walls.append(self._wall)
        for ln in lines:
            self._console(ln)

    def _gamespeed(self, rest: str) -> None:
        try:
            speed = int(rest)
        except ValueError:
            speed = -1
        if not 0 <= speed <= 5:
            raise ScriptError("gamespeed <0-5>")
        self.speed_log.append(speed)
        if speed == 0:
            self.paused = True
        else:
            self.paused, self.speed = False, speed

    def _goto(self, command: str, rest: str) -> None:
        """Centre the view (console_commands_documentation.md: "Centers to province")."""
        if self.army is None:
            raise ScriptError(f"{command}: this fake game has no map")
        try:
            ident = int(rest)
        except ValueError:
            raise ScriptError(f"{command} <id>") from None
        layer = self.army.layer
        if command == "goto_state":
            info = layer.states.get(ident)
            if info is None or not info.provinces:
                raise ScriptError(f"{command}: no state {ident}")
            ident = sorted(info.provinces)[0]
        try:
            self.army.goto(ident)
        except ValueError as exc:
            raise ScriptError(str(exc)) from None

    def _pause_in_hours(self, rest: str) -> None:
        try:
            hours = float(rest)
        except ValueError:
            raise ScriptError("pause_in_hours <hours>") from None
        self.pause_in = hours

    def _scripted_effect(self, rest: str) -> None:
        parts = rest.split()
        if len(parts) != 2:
            raise ScriptError("effect <tag> <scripted_effect_name>")
        self._require_scope(parts[0])
        self._scripted(parts[1])

    def _require_scope(self, tag: str) -> None:
        if tag.upper() != self.country:
            raise ScriptError(f"{tag}: not the country this game runs (only {self.country} "
                              "is modelled)")

    def _eval_effect(self, rest: str) -> None:
        for quoted in re.findall(r'"([^"]*)"', rest):
            if " " in quoted or "=" in quoted:
                # Seen on 1.19.5: the console splits on spaces and '=' inside quotes,
                # and takes the second piece for an effect.
                culprit = quoted.split(" ")[1] if " " in quoted else "="
                raise ScriptError(f"Unknown effect-type: {culprit}")
        entries = parse_script(rest)
        if len(entries) != 1 or not isinstance(entries[0].value, list):
            raise ScriptError("eval_effect <scope> = { ... }")
        self._require_scope(entries[0].key)
        self._effects(entries[0].value, {"country": True})

    # --- effects --------------------------------------------------------------

    def _effects(self, entries: list[Entry], scope: dict) -> None:
        for entry in entries:
            key = entry.key
            if key == "if":
                self._if(entry, scope)
            elif key == "capital_scope" or key.isdigit():
                self._in_state(entry, scope)
            elif key == "add_building_construction":
                self._add_construction(entry, scope)
            elif key == "add_equipment_production":
                self._add_production(entry)
            elif key == "activate_advisor":
                self._activate_advisor(str(entry.value))
            elif key == "add_political_power":
                self.world.state.political_power += _number(str(entry.value))
            elif key == "activate_decision":
                self._scripted(self._decision_effect(str(entry.value)))
            elif key == "set_country_flag":
                self.flags.add(str(entry.value))
            elif key == "clr_country_flag":
                self.flags.discard(str(entry.value))
            elif key == "log":
                self._log(str(entry.value))
            else:
                raise ScriptError(f"Unknown effect-type: {key}")

    def _if(self, entry: Entry, scope: dict) -> None:
        body = entry.value if isinstance(entry.value, list) else []
        limit = _get(body, "limit")
        if limit is not None and not self._triggers(limit.value, scope):
            return
        self._effects([e for e in body if e.key != "limit"], scope)

    def _in_state(self, entry: Entry, scope: dict) -> None:
        if not isinstance(entry.value, list):
            raise ScriptError(f"{entry.key} needs a block")
        if entry.key == "capital_scope":
            owned = sorted((s for s in self.states.values() if s.owner == self.country),
                           key=lambda s: int(s.id))
            if not owned:
                raise ScriptError("capital_scope: no capital")
            state = owned[0]
        else:
            state = self.states.get(entry.key)
            if state is None:
                raise ScriptError(f"Unknown scope: {entry.key}")
        self._effects(entry.value, {"country": True, "state": state})

    def _triggers(self, entries, scope: dict) -> bool:
        if not isinstance(entries, list):
            raise ScriptError("limit needs a block")
        return all(self._trigger(e, scope) for e in entries)

    def _trigger(self, entry: Entry, scope: dict) -> bool:
        key = entry.key
        if key == "always":
            return entry.value == "yes"
        if key == "NOT":
            return not self._triggers(entry.value, scope)
        if key == "has_political_power":
            return _compare(self.world.state.political_power, entry.op, _number(str(entry.value)))
        if key == "is_owned_by":
            state = scope.get("state")
            if state is None:
                raise ScriptError("is_owned_by needs a state scope")
            return state.owner == str(entry.value)
        if key == "free_building_slots":
            state = scope.get("state")
            body = entry.value if isinstance(entry.value, list) else []
            building, size = _get(body, "building"), _get(body, "size")
            if state is None or building is None or size is None:
                raise ScriptError("free_building_slots = { building = X size > 0 } in a state")
            return _compare(self._free(state, str(building.value)), size.op,
                            _number(str(size.value)))
        if key == "has_country_flag":
            return self._flag(str(entry.value))
        raise ScriptError(f"Unknown trigger: {key}")

    def _flag(self, flag: str) -> bool:
        if flag.startswith("llmb_target_"):
            return self.target == flag.removeprefix("llmb_target_")
        return flag in self.flags

    def _free(self, state: FakeState, building: str) -> int:
        if building in SLOT_BUILDINGS:
            return state.slots - state.used - sum(self._queued(state, b) for b in SLOT_BUILDINGS)
        return OTHER_BUILDING_CAP - state.others.get(building, 0) - self._queued(state, building)

    def _add_construction(self, entry: Entry, scope: dict) -> None:
        state = scope.get("state")
        if state is None:
            raise ScriptError("add_building_construction needs a state scope")
        body = entry.value if isinstance(entry.value, list) else []
        kind, level = _get(body, "type"), _get(body, "level")
        building = str(kind.value) if kind else ""
        if building not in BUILDING_NAMES:
            raise ScriptError(f"Unknown building type: {building or '?'}")
        if state.owner != self.country:
            self._log(f"add_building_construction: {state.name} is not owned by {self.country}")
            return
        for _ in range(int(_number(str(level.value))) if level else 1):
            if self._free(state, building) <= 0:
                self._log(f"add_building_construction: no free {building} slot in {state.name}")
                return
            self.world.apply(ActionCall("queue_construction", {
                "building": BUILDING_NAMES[building], "state": state.name, "count": 1}))

    def _add_production(self, entry: Entry) -> None:
        body = entry.value if isinstance(entry.value, list) else []
        equipment = _get(body, "equipment")
        requested = _get(body, "requested_factories")
        kind = _get(equipment.value, "type") if equipment and isinstance(equipment.value, list) else None
        if kind is None:
            raise ScriptError("add_equipment_production needs equipment = { type = ... }")
        name = str(kind.value)
        if self.index is not None and name not in self.index.equipment_types():
            raise ScriptError(f"Unknown equipment: {name}")
        s = self.world.state
        free = s.military_factories - sum(line.factories for line in s.production)
        if free <= 0:
            self._log(f"add_equipment_production: no free military factory for {name}")
            return
        wanted = int(_number(str(requested.value))) if requested else 1
        s.production.append(ProductionLine(equipment=name, factories=min(wanted, free)))

    def _activate_advisor(self, character: str) -> None:
        roles = self.index.advisor_roles(character) if self.index else None
        if not roles:
            raise ScriptError(f"Unknown character: {character}")
        slot = roles[0].slot or "advisor"
        seated = self.advisors.setdefault(slot, [])
        if character in seated:
            self._log(f"activate_advisor: {character} is already hired")
        elif len(seated) >= ADVISOR_SEATS.get(slot, 1):
            self._log(f"activate_advisor: no free {slot} slot for {character}")
        else:
            seated.append(character)
            self.world._advisor_pp_bonus += 0.15

    # --- the mod's scripted effects ---------------------------------------------

    def _decision_effect(self, decision: str) -> str:
        """The scripted effect a bridge decision calls (modgen._decisions)."""
        for prefix, effect in (("llmb_target_", "llm_bridge_select_"),
                               ("llmb_directive_", "llm_bridge_raise_"),
                               ("llmb_theater_", "llm_bridge_raise_theater_"),
                               ("llmb_posture_", "llm_bridge_set_posture_")):
            if decision.startswith(prefix):
                return effect + decision.removeprefix(prefix)
        if decision == "llmb_clear_directives":
            return "llm_bridge_clear_directives"
        raise ScriptError(f"Unknown decision: {decision}")

    def _scripted(self, name: str) -> None:
        if name == "llm_bridge_emit_state":
            return self.emit_state()
        if name == "llm_bridge_emit_front":
            return self.emit_front()
        if name == "llm_bridge_emit_campaign":
            return self.emit_campaign()
        if name == "llm_bridge_mark_harness":
            self.flags.add("llmb_harness")
            return self._event(16)
        if name == "llm_bridge_unmark_harness":
            self.flags.discard("llmb_harness")
            return self._event(17)
        if name == "llm_bridge_clear_directives":
            self.directives, self.global_posture, self.theater_postures = [], None, {}
            self.target = None
            self.world.state.ai_directives, self.world.state.posture = [], None
            self.world.state.theater_postures = {}
            return self._event(12)
        for posture in ("defensive", "offensive"):
            if name == f"llm_bridge_set_posture_{posture}":
                self.global_posture = self.world.state.posture = posture
                return self._event(10 if posture == "defensive" else 11)
        if name.startswith("llm_bridge_select_"):
            tag = name.removeprefix("llm_bridge_select_")
            if tag not in self.target_tags:
                raise ScriptError(f"Unknown scripted effect: {name}")
            self.target = tag
            return self._event(13, tag)
        if name.startswith("llm_bridge_raise_theater_"):
            posture = name.removeprefix("llm_bridge_raise_theater_")
            if posture not in modgen.THEATER_POSTURE_CHOICES:
                raise ScriptError(f"Unknown scripted effect: {name}")
            if self.target is None:
                return          # every branch is guarded by the target flag: nothing happens
            self.theater_postures[self.target] = posture
            self.world.state.theater_postures[self.target] = posture
            return self._event(15, f"{posture} {self.target}")
        if name.startswith("llm_bridge_raise_"):
            directive = name.removeprefix("llm_bridge_raise_")
            if directive not in AI_DIRECTIVES:
                raise ScriptError(f"Unknown scripted effect: {name}")
            if self.target is None:
                return
            entry = f"{directive} {self.target}"
            if entry not in self.directives:
                self.directives.append(entry)
                self.world.state.ai_directives.append(f"{entry} (100)")
            return self._event(14, entry)
        raise ScriptError(f"Unknown scripted effect: {name}")

    # --- a process, fed by files --------------------------------------------------

    @property
    def keys_path(self) -> Path:
        return self.user_dir / "keys.log"

    def poll_keys(self) -> int:
        """Play in the key events a harness in another process appended to
        ``keys.log`` (``FileKeys``). Returns how many were played."""
        path = self.keys_path
        if not path.exists():
            return 0
        with path.open("r", encoding="ascii") as handle:
            handle.seek(self._key_offset)
            played = 0
            while True:
                start = handle.tell()
                line = handle.readline()
                if not line.endswith("\n"):
                    handle.seek(start)
                    break
                code, _, up = line.strip().partition(" ")
                self.send(int(code, 16), up == "1")
                played += 1
            self._key_offset = handle.tell()
        return played

    def serve(self, stop: threading.Event, time_scale: float = 1.0, poll: float = 0.01) -> None:
        """Run as a game: the wall clock moves the game clock (``time_scale`` times
        as fast) and keys arrive through ``keys.log``."""
        last = time.monotonic()
        while not stop.is_set():
            self.poll_keys()
            now = time.monotonic()
            self.sleep((now - last) * time_scale)
            last = now
            stop.wait(poll)


class FileKeys:
    """The harness end of ``keys.log``: a ``send(scan, up)`` that appends instead
    of calling SendInput, for a run against ``hoi4-harness fakegame``."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, scan: int, up: bool) -> None:
        with self.path.open("a", encoding="ascii", newline="\n") as handle:
            handle.write(f"{scan:02x} {1 if up else 0}\n")
