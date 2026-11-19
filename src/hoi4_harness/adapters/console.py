"""The console channel: run an action as script instead of walking the UI (#83).

The input driver acts the way a player does -- hotkeys, clicks, typed search
text -- and pays for it in calibration and fragility. For most actions the game
has an effect that does the same thing, and the console can run it: write the
effect into a file, press the console key, type ``run <file>``, press enter,
close the console. The same few keys every time, whatever panel is open and
wherever the camera is.

What the file holds is *console commands*, one per line, because that is what
``run`` reads (``console_commands_documentation.md``: "Runs the specified file
with list of commands"). Two commands carry the work:

* ``eval_effect <effect>`` -- "Runs the inlined effect on a selected scope". For
  the actions whose effect is plain script (construction, production, advisors).
* ``effect <tag> <scripted_effect_name>`` -- "Runs a scripted effect on selected
  scope". For the hybrid-control actions: the mod already has scripted effects
  for each (``llm_bridge_raise_protect`` ...), the very ones its decisions call,
  so the mod logs its usual telemetry events and verification is unchanged.

Effects skip the game's own checks (cost, free slots, valid states), so a plan
is refused *before anything is written* when a precondition the UI would have
enforced fails, and sends no input. Where the reader cannot see a precondition
it is listed under ``unchecked`` on the result rather than guessed at, and for
the ones that script can test (ownership, a free slot, enough political power)
the effect carries its own ``limit``, so the game itself declines to act.

Two things are unverified and settled by a live game (docs/adapters.md): which
folder ``run`` reads, and whether ``eval_effect`` takes its effect on the same
line. Both are configuration, not code.

Everything that reaches the keyboard goes through the input driver this channel
is built on, so its focus guard, dry-run switch and injectable key sender
apply unchanged; tests inject a fake and ``tests/conftest.py`` blocks the real
one.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import modgen, paths
from ..actions.catalog import AI_DIRECTIVES
from ..identifiers import IdentifierIndex
from ..observation.builder import snapshot
from ..types import ActionCall, ActionResult, GameState
from .input_driver import NotFocused
from .keys import text_chords
from .ui_scripts import verify

#: The actions the console can perform faithfully, and the channel each takes by
#: default. ``set_national_focus`` and ``start_research`` stay on the UI: the
#: only effects that exist for them (``complete_national_focus``, ``set_technology``)
#: skip the time and cost a player pays, which is a different game.
CONSOLE_ACTIONS = frozenset({
    "queue_construction", "set_production", "hire_advisor",
    "set_ai_directive", "set_ai_posture", "clear_ai_directives",
})
#: Everything on the UI path until the open questions in docs/adapters.md
#: (where `run` reads, whether `eval_effect` takes an inline effect, -debug)
#: are settled in a live game. Opt in per action with ``action_channels``.
DEFAULT_CHANNELS: dict[str, str] = {
    "set_national_focus": "ui",
    "start_research": "ui",
    "delegate_army_to_ai": "ui",
    **{name: "ui" for name in sorted(CONSOLE_ACTIONS)},
}
CHANNELS = ("ui", "console")

#: Harness building name -> the game's building id (``common/buildings``).
#: ``fortification`` is absent on purpose: the game's land fortification
#: (``bunker``) is a province building, which needs a province id the action
#: does not carry, so it is refused rather than aimed at a guessed province.
BUILDING_IDS = {
    "civilian_factory": "industrial_complex",
    "military_factory": "arms_factory",
    "dockyard": "dockyard",
    "infrastructure": "infrastructure",
    "air_base": "air_base",
    "anti_air": "anti_air_building",
    "radar": "radar_station",
    "synthetic_refinery": "synthetic_refinery",
}

_TAG = re.compile(r"^[A-Z0-9]{3}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")


@dataclass
class ConsoleConfig:
    #: The folder ``run`` reads from. UNVERIFIED: None means the game's user
    #: directory (``paths.find_game_dir``), the best guess; set it to wherever a
    #: live check shows ``run`` looking.
    directory: Path | None = None
    filename: str = "llmb_turn.txt"
    #: Console key, as a key name or a raw scan code (``0x29``). The key left of
    #: ``1`` is the console on every layout.
    open_key: str = "grave"
    close_key: str = "grave"
    #: The console command that takes an inline effect (see the module docstring).
    effect_command: str = "eval_effect"
    #: How hybrid actions reach the mod: its scripted effects (``scripted``), or
    #: ``activate_decision`` on the decision that calls them (``decision``).
    hybrid_via: str = "scripted"
    #: Put the preconditions script can test in the effect itself.
    guards: bool = True
    #: Countries the mod has a directive block for (modgen.DEFAULT_TAGS unless
    #: the mod was regenerated with others).
    target_tags: tuple[str, ...] = modgen.DEFAULT_TAGS
    #: Seconds for the console to open before typing, and to run the file.
    open_delay: float = 0.4
    run_delay: float = 0.8
    #: Where a dry run writes the file, and where live runs keep a copy. A
    #: dry run never touches ``directory``.
    archive_dir: Path | None = None
    country: str | None = None


@dataclass
class Planned:
    call: ActionCall
    lines: list[str]
    unchecked: list[str] = field(default_factory=list)


def _result(call: ActionCall, ok: bool, kind: str | None, message: str,
            **changed) -> ActionResult:
    return ActionResult(ok=ok, action=call.name, call_id=call.call_id, message=message,
                        error_kind=kind, changed={"channel": "console", **changed})


class Refused(Exception):
    """A precondition failed; nothing was written and nothing will be sent."""


class ConsoleChannel:
    def __init__(
        self,
        driver,
        config: ConsoleConfig | None = None,
        index: IdentifierIndex | None = None,
        read_state: Callable[[], GameState] | None = None,
        sleep: Callable[[float], None] | None = None,
        verify_timeout: float = 3.0,
    ):
        self.driver = driver
        self.config = config or ConsoleConfig()
        self.index = index
        self._read_state = read_state
        self._sleep = sleep or driver._sleep
        self.verify_timeout = verify_timeout
        self.runs = 0
        #: The last file written and the keys of the last run, for the transcript.
        self.last_lines: list[str] = []

    def attach_reader(self, read_state: Callable[[], GameState]) -> None:
        self._read_state = read_state

    # --- planning: preconditions, then the effect ----------------------------

    def plan(self, call: ActionCall, state: GameState) -> Planned:
        """The lines that perform ``call``, or ``Refused`` naming the failed check."""
        country = self._country(state)
        args = call.arguments
        if call.name == "queue_construction":
            return self._plan_construction(call, args, country)
        if call.name == "set_production":
            return self._plan_production(call, args, country, state)
        if call.name == "hire_advisor":
            return self._plan_advisor(call, args, country, state)
        return self._plan_hybrid(call, args, country)

    def _country(self, state: GameState) -> str:
        tag = state.country if state.country and state.country != "___" else self.config.country
        if not tag or not _TAG.match(tag.upper()):
            raise Refused("the player's country is not known (no telemetry yet and no "
                          "country configured), so there is no scope to write the effect in")
        tag = tag.upper()
        if self.index is not None and self.index.tags and tag not in self.index.tags:
            raise Refused(f"there is no country '{tag}' in this playset")
        return tag

    def _need_index(self, what: str) -> IdentifierIndex:
        if self.index is None:
            raise Refused(f"{what} needs the game's files to check against: pass --game-dir")
        return self.index

    def _inline(self, country: str, body: str) -> str:
        return f"{self.config.effect_command} {country} = {{ {body} }}"

    def _plan_construction(self, call, args, country) -> Planned:
        building = BUILDING_IDS.get(str(args["building"]))
        if building is None:
            raise Refused(f"{args['building']} cannot be queued from the console: it is a "
                          "province building and the action names no province")
        index = self._need_index("resolving a state")
        where = str(args["state"]).strip()
        if where.lower() == "capital":
            scope = "capital_scope"
        else:
            info = index.resolve_state(where)
            if info is None:
                raise Refused(f"no state {where!r} in this playset")
            scope = info.id
        count = int(args.get("count", 1))
        # One level per block, not one block of ``level = count``: the UI queues
        # one level per click, and the queue verification counts items.
        blocks = []
        for _ in range(count):
            build = (f"add_building_construction = {{ type = {building} level = 1 "
                     "instant_build = no }")
            if self.config.guards:
                build = (f"if = {{ limit = {{ is_owned_by = {country} free_building_slots = "
                         f"{{ building = {building} size > 0 }} }} {build} }}")
            blocks.append(build)
        line = self._inline(country, f"{scope} = {{ {' '.join(blocks)} }}")
        # The reader does not report who owns a state or its free slots, so the
        # harness cannot check either; with guards on the effect's own limit does.
        unchecked = ["state ownership", "free building slot"]
        return Planned(call, [line], unchecked)

    def _plan_production(self, call, args, country, state) -> Planned:
        equipment, factories = str(args["equipment"]), int(args["factories"])
        index = self._need_index("checking the equipment exists")
        if equipment not in index.equipment_types():
            raise Refused(f"there is no buildable equipment '{equipment}' in this playset")
        if factories < 1:
            raise Refused("setting 0 factories removes a line, and no effect does that the way "
                          "the UI does: use the ui channel for it")
        for name in ("production", "military_factories"):
            if not state.known(name):
                raise Refused(f"the reader cannot see {name}, so the free factories cannot be "
                              "counted; refusing rather than guessing")
        if any(line.equipment == equipment for line in state.production):
            raise Refused(f"a {equipment} line already exists; the effect would add a second "
                          "line instead of changing it. Change an existing line on the ui channel")
        free = state.military_factories - sum(line.factories for line in state.production)
        if factories > free:
            raise Refused(f"{factories} factories asked for, {free} free "
                          f"({state.military_factories} military, "
                          f"{state.military_factories - free} on other lines)")
        body = (f"add_equipment_production = {{ equipment = {{ type = {equipment} }} "
                f"requested_factories = {factories} }}")
        return Planned(call, [self._inline(country, body)])

    def _plan_advisor(self, call, args, country, state) -> Planned:
        advisor = str(args["advisor_id"])
        index = self._need_index("looking up the advisor's cost")
        roles = index.advisor_roles(advisor)
        if not roles:
            raise Refused(f"there is no advisor '{advisor}' in this playset")
        slot = args.get("slot")
        if slot:
            roles = [r for r in roles if r.slot == slot]
            if not roles:
                raise Refused(f"{advisor} has no {slot} role")
        costs = {r.cost for r in roles}
        if None in costs:
            raise Refused(f"{advisor}'s definition names no cost, so what to charge is unknown")
        if len(costs) > 1:
            raise Refused(f"{advisor} costs {sorted(costs)} depending on the role; "
                          "pass slot to say which")
        (cost,) = costs
        if not state.known("political_power"):
            raise Refused("the reader cannot see political power, so affordability is unknown")
        if state.political_power < cost:
            raise Refused(f"{advisor} costs {cost:g} political power, {state.political_power:g} held")
        char = index.advisor_character(advisor)
        # The effect charges nothing itself, so the cost is taken here.
        body = f"activate_advisor = {char} add_political_power = -{cost:g}"
        if self.config.guards:
            body = f"if = {{ limit = {{ has_political_power > {cost - 0.01:g} }} {body} }}"
        return Planned(call, [self._inline(country, body)], ["the advisor's allowed/available "
                       "conditions", "cost modifiers (the base cost is charged)"])

    def _plan_hybrid(self, call, args, country) -> Planned:
        via = self.config.hybrid_via

        def scripted(effect: str) -> str:
            return f"effect {country} {effect}"

        def decision(name: str) -> str:
            return self._inline(country, f"activate_decision = {name}")

        def target(tag: str) -> list[str]:
            tag = tag.upper()
            if not _TAG.match(tag):
                raise Refused(f"{tag!r} is not a country tag")
            if self.index is not None and self.index.tags and tag not in self.index.tags:
                raise Refused(f"there is no country '{tag}' in this playset")
            if tag not in self.config.target_tags:
                raise Refused(f"the mod has no directive plumbing for {tag}; regenerate it with "
                              f"`hoi4-harness mod-directives --tags ...,{tag}`")
            return [decision(f"llmb_target_{tag}") if via == "decision"
                    else scripted(f"llm_bridge_select_{tag}")]

        if call.name == "set_ai_directive":
            directive = str(args["directive"])
            if directive not in AI_DIRECTIVES:
                raise Refused(f"unknown directive {directive!r}")
            lines = target(str(args["target"]))
            lines.append(decision(f"llmb_directive_{directive}") if via == "decision"
                         else scripted(f"llm_bridge_raise_{directive}"))
            return Planned(call, lines)
        if call.name == "set_ai_posture":
            posture, theater = str(args["posture"]), args.get("theater")
            if theater:
                if posture not in modgen.THEATER_POSTURE_CHOICES:
                    raise Refused(f"unknown posture {posture!r}")
                lines = target(str(theater))
                lines.append(decision(f"llmb_theater_{posture}") if via == "decision"
                             else scripted(f"llm_bridge_raise_theater_{posture}"))
                return Planned(call, lines)
            if posture == "balanced":
                raise Refused("the mod has no effect that sets a global balanced posture "
                              "(clearing directives would drop them too); pick defensive or "
                              "offensive, or use clear_ai_directives")
            if posture not in ("defensive", "offensive"):
                raise Refused(f"unknown posture {posture!r}")
            return Planned(call, [decision(f"llmb_posture_{posture}") if via == "decision"
                                  else scripted(f"llm_bridge_set_posture_{posture}")])
        # clear_ai_directives
        return Planned(call, [decision("llmb_clear_directives") if via == "decision"
                              else scripted("llm_bridge_clear_directives")])

    # --- running --------------------------------------------------------------

    def keys(self, filename: str | None = None) -> list[str]:
        """The key sequence of a run, as the driver logs it."""
        name = filename or self.config.filename
        return [f"press {self.config.open_key}", f"type run {name}", "press enter",
                f"press {self.config.close_key}"]

    def execute(self, calls: list[ActionCall]) -> list[ActionResult]:
        """Plan every call, write one file for those that pass, send the keys once,
        verify each. One result per call, in order; a refused call has sent nothing."""
        if self._read_state is None:
            return [_result(c, False, "unverified",
                            "no reader to verify with; nothing was sent") for c in calls]
        state = self._read_state()
        results: dict[int, ActionResult] = {}
        planned: list[tuple[int, Planned]] = []
        for position, call in enumerate(calls):
            try:
                planned.append((position, self.plan(call, state)))
            except Refused as exc:
                results[position] = _result(call, False, "rejected",
                                            f"refused before writing: {exc}; nothing was sent")
        if planned:
            sent = self._send(snapshot(state), [p for _, p in planned])
            for position, outcome in sent.items():
                results[planned[position][0]] = outcome
        return [results[i] for i in range(len(calls))]

    def _send(self, before: GameState, planned: list[Planned]) -> dict[int, ActionResult]:
        driver, config = self.driver, self.config
        lines = [line for p in planned for line in p.lines]
        self.last_lines = lines
        try:
            text_chords(f"run {config.filename}")        # typable, before anything is written
            driver._require_focus()
            if driver.dry_run:
                path = self._write(self._archive_path(), lines)
            else:
                path = self._write(self._run_dir() / config.filename, lines)
                self._write(self._archive_path(), lines)
        except (NotFocused, ValueError, OSError, RuntimeError, KeyError) as exc:
            return {i: _result(p.call, False, "rejected", f"{exc}; nothing was sent")
                    for i, p in enumerate(planned)}
        if driver.dry_run:
            self._keys_sequence()
            return {i: _result(p.call, False, "not_executed", (
                f"dry run: would write {len(p.lines)} line(s) to {path.name} "
                f"and send {'; '.join(self.keys())}: " + " | ".join(p.lines)),
                lines=p.lines, unchecked=p.unchecked) for i, p in enumerate(planned)}
        try:
            self._keys_sequence()
        except Exception as exc:   # noqa: BLE001 - report it; _keys_sequence tried to close
            return {i: _result(p.call, False, "rejected",
                               f"{type(exc).__name__}: {exc}; the console may be open",
                               lines=p.lines) for i, p in enumerate(planned)}
        self._sleep(driver.config.settle_seconds)
        out = {}
        for i, p in enumerate(planned):
            outcome = verify(p.call, before, self._read_state, timeout=self.verify_timeout,
                             sleep=self._sleep)
            out[i] = _result(p.call, outcome.ok, outcome.kind, outcome.message,
                             lines=p.lines, unchecked=p.unchecked)
        return out

    def _keys_sequence(self) -> None:
        """Open the console, run the file, close it. Through the driver, so the
        focus guard, the dry-run log and the injectable sender all apply."""
        driver, config = self.driver, self.config
        driver.press(config.open_key)
        try:
            if not driver.dry_run:
                self._sleep(config.open_delay)
            driver.type_text(f"run {config.filename}")
            driver.press("enter")
            if not driver.dry_run:
                self._sleep(config.run_delay)
        finally:
            # Whatever went wrong after the console opened, try to close it: an
            # open console swallows every key the next action sends.
            try:
                driver.press(config.close_key)
            except Exception:   # noqa: BLE001 - the original error is the one to report
                pass

    def _run_dir(self) -> Path:
        directory = self.config.directory or paths.find_game_dir()
        if directory is None:
            raise RuntimeError("no folder to write the console file to: set console_dir "
                               "(HOI4_CONSOLE_DIR) to where the game's `run` command reads")
        return Path(directory)

    def _archive_path(self) -> Path:
        self.runs += 1
        base = self.config.archive_dir or Path("runs")
        return base / "console" / f"{int(time.time())}-{self.runs:03d}-{self.config.filename}"

    @staticmethod
    def _write(path: Path, lines: list[str]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        return path
