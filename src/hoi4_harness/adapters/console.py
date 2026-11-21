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

import dataclasses
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
from .input_driver import InputInterrupted, NotFocused
from .keys import text_chords
from .ui_scripts import verify

#: The actions the console can perform faithfully, and the channel each takes by
#: default. ``set_national_focus`` and ``start_research`` stay on the UI: the
#: only effects that exist for them (``complete_national_focus``, ``set_technology``)
#: skip the time and cost a player pays, which is a different game.
CONSOLE_ACTIONS = frozenset({
    "queue_construction", "set_production", "hire_advisor",
    "set_ai_directive", "set_ai_posture", "clear_ai_directives", "take_decision",
    "click_gui_button", "set_front_order", "set_region_order",
})
#: The console for every action it has a faithful effect for: settled live on
#: 1.19.5 (run reads the user directory, needs no -debug, and eval_effect takes
#: an inline effect; construction queued, a production line opened, an advisor
#: was hired and a directive raised, each through the harness). Focus, research
#: and delegation have no faithful effect and stay on the UI path.
DEFAULT_CHANNELS: dict[str, str] = {
    "set_national_focus": "ui",
    "start_research": "ui",
    "delegate_army_to_ai": "ui",
    **{name: "console" for name in sorted(CONSOLE_ACTIONS)},
}
CHANNELS = ("ui", "console")

#: Actions whose result only a text save can show (the queue and the production
#: lines are in no trigger or variable). With a save reader attached, the console
#: takes one before planning them (so the "before" is today's) and one after.
SAVE_VERIFIED = frozenset({"queue_construction", "set_production"})

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
    #: Let click_gui_button press a button whose script changes more than this country
    #: (global flags, every country, another country's scope). Off: an LLM rival pressing
    #: Expert AI's settings would change the person's game.
    gui_allow_global: bool = False
    #: Seconds for the console to open before typing, and to run the file.
    #: Seconds between the open key and the first typed character. 0.4 lost the race live on
    #: 2026-11-21 (paused 1936 game, a panel open): the console received `n llmb_turn.txt`, and
    #: the `r` and `u` typed before it opened went to the map (`r` opened Recruit & Deploy).
    #: 1.0 ran every time.
    open_delay: float = 1.0
    run_delay: float = 0.8
    #: Where a dry run writes the file, and where live runs keep a copy. A
    #: dry run never touches ``directory``.
    archive_dir: Path | None = None
    country: str | None = None
    #: Ask the mod for fresh telemetry after the actions, in the same run file.
    #: A paused game logs nothing on its own, so without this every action is
    #: "unverified" until the clock runs a day. The state line closes the tick
    #: that was open when the action went in, and the second one shows what the
    #: action did (see ``ui_scripts.verify``: it wants two ticks). Needs the
    #: scripted effect ``llm_bridge_emit_state``, which the mod ships.
    refresh: bool = False
    refresh_effect: str = "llm_bridge_emit_state"
    #: The inbox transport (inbox.py): how long to wait for the game to acknowledge a batch
    #: before reporting it as posted and not yet run. The game runs the inbox once a day, so
    #: a running game answers within a day of game time; a paused one never does.
    inbox_ack_seconds: float = 6.0
    refresh_repeats: int = 2
    #: Also ask for the front picture (divisions per state, #85) with the refresh.
    #: The effect lives in llm_bridge_divisions.txt; an older mod prints an
    #: unknown-effect line in the game's error log and nothing else.
    refresh_front: bool = False
    front_effect: str = "llm_bridge_emit_front"


class UnsafeConsoleLine(ValueError):
    """A line the console would split wrongly."""


def check_console_line(line: str) -> None:
    """Refuse a quoted string holding a space or ``=``.

    Seen live on 1.19.5: the console splits a run file's line on spaces and
    ``=`` even inside quotes, so ``log = "a b"`` failed with "Unknown
    effect-type: b" and ``log = "x=1"`` with "Unknown effect-type: =".
    """
    import re
    for quoted in re.findall(r'"([^"]*)"', line):
        if " " in quoted or "=" in quoted:
            raise UnsafeConsoleLine(
                f"the console splits quoted text on spaces and '=': {quoted!r}")


@dataclass
class Planned:
    call: ActionCall
    lines: list[str]
    unchecked: list[str] = field(default_factory=list)
    #: What verification checks, when that is not the call as the model wrote it
    #: (a state named by id or "capital" becomes the name the reader reports).
    verify_call: ActionCall | None = None


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
        #: fire_only_once decisions this run has taken: no trigger can say so.
        self.taken_once: set[str] = set()
        #: The last file written and the keys of the last run, for the transcript.
        self.last_lines: list[str] = []
        #: A reader that can take and read text saves (``combined.CombinedReader``).
        #: Without one, construction and production stay "unverified".
        self.saves = None
        #: The inbox transport (``inbox.Inbox``): when set, the lines this channel would type
        #: go into the mod's inbox file instead, and no key is ever sent (docs/inbox.md).
        self.inbox = None
        #: click_gui_button: the marker each planned click logs inside its guard.
        self.click_markers: dict[int, str] = {}
        #: set_front_order: what each planned order sets, and the orders the game confirmed.
        self.front_pending: dict[int, tuple[int, str]] = {}
        from ..front_orders import Standing
        self.front_standing = Standing(getattr(self.config, 'archive_dir', None))

    @property
    def transport(self) -> str:
        return "inbox" if self.inbox is not None else "keys"

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
        if call.name == "take_decision":
            return self._plan_decision(call, args, country, state)
        if call.name == "click_gui_button":
            return self._plan_click(call, args, country)
        if call.name == "set_front_order":
            return self._plan_front(call, args, country)
        if call.name == "set_region_order":
            return self._plan_region(call, args, country)
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
        line = f"{self.config.effect_command} {country} = {{ {body} }}"
        check_console_line(line)
        return line

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
        # The save reader names a state the way the game's files do, so verification
        # looks for that name: what the model typed may be an id, a key or "capital".
        if self.saves is None:
            return Planned(call, [line], unchecked)
        name = None
        if where.lower() == "capital":
            name = self.saves.capital_state_name() if self.saves is not None else None
        else:
            name = info.name
        arguments = {k: v for k, v in args.items() if k != "state"}
        if name:
            arguments["state"] = name
        return Planned(call, [line], unchecked, dataclasses.replace(call, arguments=arguments))

    def _plan_production(self, call, args, country, state) -> Planned:
        equipment, factories = str(args["equipment"]), int(args["factories"])
        index = self._need_index("checking the equipment exists")
        if equipment not in index.equipment_types():
            raise Refused(f"there is no buildable equipment '{equipment}' in this playset")
        if factories < 1:
            raise Refused("setting 0 factories removes a line, and no effect does that the way "
                          "the UI does: use the ui channel for it")
        # Seen live on 1.19.5: with the production panel unseen, an Infantry
        # Equipment I order added a second, empty line next to the existing one.
        # No trigger tells the effect a line exists, so an unseen panel refuses.
        unchecked: list[str] = []
        for name in ("production", "military_factories"):
            if not state.known(name):
                raise Refused(f"the reader cannot see {name}; add_equipment_production would "
                              "add a second line for equipment that already has one (seen "
                              "live), so this needs a reader that sees production (a text save)")
        if any(line.equipment == equipment for line in state.production):
            raise Refused(f"a {equipment} line already exists; the effect would add a second "
                          "line instead of changing it. Change an existing line on the ui channel")
        used = sum(line.factories for line in state.production)
        free = max(state.military_factories - used, 0)
        if factories > free:
            # A line just opened counts its requested factories though it may have
            # none yet, so "on other lines" can exceed the factories there are.
            raise Refused(f"{factories} factories asked for, {free} free "
                          f"({state.military_factories} military, {used} asked for by the "
                          f"{len(state.production)} existing line(s))")
        body = (f"add_equipment_production = {{ equipment = {{ type = {equipment} }} "
                f"requested_factories = {factories} }}")
        return Planned(call, [self._inline(country, body)], unchecked)

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

    def _plan_decision(self, call, args, country, state) -> Planned:
        decision_id = str(args["decision_id"])
        index = self._need_index("reading the decision's conditions")
        ctx = index.mod_context()
        if ctx.find_decision(decision_id) is None:
            raise Refused(f"there is no decision {decision_id!r} in this playset")
        if decision_id not in {d.id for d in ctx.decision_for(country)}:
            raise Refused(f"{decision_id!r} is not a decision {country} can have (its allowed/"
                          "visible tests name another country)")
        plan = ctx.plan_take(decision_id, country, args.get("target"), self.config.effect_command)
        if not plan.executable:
            raise Refused(f"{decision_id} cannot be taken faithfully from the console: {plan.reason}")
        decision = plan.decision
        if decision.fire_only_once and decision_id in self.taken_once:
            raise Refused(f"{decision_id} is fire-only-once and this run already took it")
        if plan.cost:
            if not state.known("political_power"):
                raise Refused("the reader cannot see political power, so affordability is unknown")
            if state.political_power < plan.cost:
                raise Refused(f"{decision_id} costs {plan.cost:g} political power, "
                              f"{state.political_power:g} held")
        if decision.fire_only_once and not self.driver.dry_run:
            self.taken_once.add(decision_id)
        return Planned(call, [plan.line], plan.unchecked)

    def _plan_click(self, call, args, country) -> Planned:
        index = self._need_index("reading the window's script")
        ctx = index.mod_context()
        gui_id, button = str(args["gui"]), str(args["button"])
        plan = ctx.plan_click(gui_id, button, country, effect_command=self.config.effect_command,
                              console=self.transport != "inbox",
                              allow_global=self.config.gui_allow_global)
        if plan is None:
            raise Refused(f"there is no button {button!r} in a window {gui_id!r} in this playset")
        if not plan.executable:
            raise Refused(f"{gui_id}/{button} cannot be pressed faithfully: {plan.reason}")
        self.click_markers[id(call)] = plan.nonce
        return Planned(call, [plan.line], plan.unchecked)

    def _plan_front(self, call, args, country) -> Planned:
        import secrets

        from .. import front_orders

        order = str(args["order"]).lower()
        if order not in (*front_orders.ALL_ORDERS, "clear"):
            raise Refused(f"{order!r} is not a front order (push, attack, probe, hold, reserve, "
                          "invade, clear)")
        index = self._need_index("finding the state")
        found = index.resolve_state(str(args["state"]))
        if found is None or not str(found.id).isdigit():
            raise Refused(f"there is no state {args['state']!r} in this playset")
        nonce = "f" + secrets.token_hex(5)
        self.click_markers[id(call)] = nonce
        self.front_pending[id(call)] = (int(found.id), order)
        body = f'{front_orders.effect(int(found.id), order)} log = "LLMB|v5|click|{nonce}"'
        return Planned(call, [self._inline(country, body)],
                       ["whether the state lies on a front: an order away from any front is "
                        "harmless and does nothing"])

    def regions(self):
        """The playset's strategic regions, named, read once."""
        if getattr(self, "_regions", None) is None:
            from .. import regions as R

            index = self._need_index("finding the region")
            self._regions = R.read_regions(index.playset)
            R.name_regions(self._regions, index.mod_context().names)
        return self._regions

    def _plan_region(self, call, args, country) -> Planned:
        import secrets

        from .. import regions as R

        order = str(args["order"]).lower()
        if order not in (*R.ORDERS, "clear"):
            raise Refused(f"{order!r} is not a region order ({', '.join(R.ORDERS)}, clear)")
        region = R.resolve(self.regions(), str(args["region"]))
        if region is None:
            raise Refused(f"there is no strategic region {args['region']!r} in this playset")
        if order != "clear" and R.ORDERS[order][2] and not region.sea:
            raise Refused(f"{region.name or region.id} is a land region: {order} is a naval order "
                          "and needs a sea region")
        nonce = "r" + secrets.token_hex(5)
        self.click_markers[id(call)] = nonce
        self.front_pending[id(call)] = (f"region:{region.id}", order)
        body = f'{R.effect(region.id, order)} log = "LLMB|v5|click|{nonce}"'
        return Planned(call, [self._inline(country, body)],
                       ["how strongly the weight moves the AI's fleet or air force"])

    def _verify_click(self, call: ActionCall):
        """The click logs its marker only inside its guard, so the line in the log is the
        proof that the game let it through; its absence once the batch ran is a refusal."""
        nonce = self.click_markers.pop(id(call), "")
        path = self.inbox.log_path if self.inbox is not None else None
        if path is None:
            from .logtail import find_log
            path = find_log()
        waited = 0.0
        while path is not None:
            try:
                with open(path, "rb") as fh:
                    fh.seek(0, 2)
                    fh.seek(max(0, fh.tell() - 512_000))
                    tail = fh.read().decode("utf-8", errors="replace")
            except OSError:
                tail = ""
            if nonce and f"|click|{nonce}" in tail:
                pending = self.front_pending.pop(id(call), None)
                if pending is not None:
                    self.front_standing.record(*pending)
                    where = (f"region {str(pending[0]).split(':', 1)[1]}"
                             if str(pending[0]).startswith("region:") else f"state {pending[0]}")
                    return True, None, (f"order {pending[1]} set on {where}: the game's AI "
                                        "steers by it from now on")
                return True, None, "pressed: the game ran the click (its conditions held)"
            if waited >= self.verify_timeout:
                break
            self._sleep(0.25)
            waited += 0.25
        if self.inbox is not None:
            return False, "rejected", (
                "the game ran the batch but not the click: the window's open_when or the "
                "button's enabled_when did not hold. Nothing changed.")
        return False, "unverified", ("sent, but the click's log line has not appeared yet. "
                                     "Treat it as not done until a later brief shows its effect.")

    def _verify_decision(self, call: ActionCall, before: GameState, planned: Planned):
        """Decisions are not in the telemetry, so the only trace is political power.
        A drop of at least half the cost proves the take; anything else is reported
        as unverified, never as done."""
        cost = 0.0
        ctx = self.index.mod_context() if self.index else None
        decision = ctx.find_decision(str(call.arguments["decision_id"])) if ctx else None
        if decision and decision.cost:
            cost = decision.cost
        if cost and before.known("political_power"):
            deadline, waited = self.verify_timeout, 0.0
            while True:
                after = self._read_state()
                dropped = before.political_power - after.political_power
                if after.known("political_power") and dropped >= cost / 2:
                    return True, None, (f"taken: political power fell from {before.political_power:g} "
                                        f"to {after.political_power:g}")
                if waited >= deadline:
                    break
                self._sleep(0.25)
                waited += 0.25
        return False, "unverified", (
            "input sent, but decisions are not in the telemetry"
            + (", and political power did not fall by the cost yet" if cost else
               " and this one costs nothing to compare")
            + ". Treat it as not done until a later brief shows its effect.")

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

    def refresh_lines(self, country: str) -> list[str]:
        """The lines that make the mod log its state again, now."""
        lines = [f"effect {country} {self.config.refresh_effect}"] * self.config.refresh_repeats
        if self.config.refresh_front:
            # First, so the two state lines still end the run: they close the tick.
            lines.insert(0, f"effect {country} {self.config.front_effect}")
        return lines

    def send_lines(self, lines: list[str]) -> Path:
        """Write ``lines`` as the run file and send the keys that run it.

        No planning and no verification: for commands that are not actions
        (``gamespeed``, a telemetry refresh), whose caller checks the outcome
        its own way. Raises what the guarded steps raise (NotFocused, OSError...);
        a dry run writes the archive copy only and sends nothing.
        """
        driver, config = self.driver, self.config
        if self.inbox is not None:
            self.last_lines = list(lines)
            if driver.dry_run:
                return self._write(self._archive_path(), lines)
            self.inbox.post(lines)
            return self._write(self._archive_path(), lines)
        for line in lines:
            check_console_line(line)
        text_chords(f"run {config.filename}")
        driver._require_focus()
        self.last_lines = list(lines)
        if driver.dry_run:
            path = self._write(self._archive_path(), lines)
            self._keys_sequence()
            return path
        path = self._write(self._run_dir() / config.filename, lines)
        self._write(self._archive_path(), lines)
        self._keys_sequence()
        self._sleep(driver.config.settle_seconds)
        return path

    def refresh(self, country: str | None = None) -> None:
        """Make the mod log its state now, outside any action's run file."""
        tag = country or self.config.country
        if not tag:
            state = self._read_state() if self._read_state else None
            tag = self._country(state) if state else None
        if not tag:
            return
        self.send_lines(self.refresh_lines(tag.upper()))

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
        if self._wants_save(calls) and not self.saves.fresh_for(state.date):
            # A queue or a production line seen as of an earlier day is not the
            # "before" an action can be judged against (or planned from).
            self.saves.refresh()
            state = self._read_state()
        results: dict[int, ActionResult] = {}
        planned: list[tuple[int, Planned]] = []
        for position, call in enumerate(calls):
            try:
                planned.append((position, self.plan(call, state)))
            except Refused as exc:
                why = f" [the save: {self.saves.last_error}]" if (
                    self.saves is not None and self.saves.last_error
                    and call.name in SAVE_VERIFIED) else ""
                results[position] = _result(call, False, "rejected",
                                            f"refused before writing: {exc}{why}; "
                                            "nothing was sent")
        if planned:
            sent = self._send(snapshot(state), [p for _, p in planned])
            for position, outcome in sent.items():
                results[planned[position][0]] = outcome
        return [results[i] for i in range(len(calls))]

    def _wants_save(self, calls: list[ActionCall]) -> bool:
        # A save is taken with the console's `savegame`, which the inbox cannot carry.
        return (self.saves is not None and not self.driver.dry_run and self.inbox is None
                and any(c.name in SAVE_VERIFIED for c in calls))

    def execute_shared(
        self, parts: list[tuple[ConsoleChannel, list[ActionCall]]],
    ) -> list[list[ActionResult]]:
        """Several countries' actions in ONE run file and one pass of the keys (rival mode
        with more than one rival).

        ``parts`` is one ``(console, calls)`` per country, each console planning and verifying
        its own country's calls against its own reader; ``self`` writes the file and sends the
        keys, so every part's console must share ``self``'s driver settings. The lines go out
        in the order given, then each country's telemetry refresh. One list of results per
        part, in order; a refused call has sent nothing."""
        results: list[dict[int, ActionResult]] = [{} for _ in parts]
        flat: list[tuple[int, int, ConsoleChannel, GameState, Planned]] = []
        for index, (console, calls) in enumerate(parts):
            if console._read_state is None:
                results[index] = {i: _result(c, False, "unverified",
                                             "no reader to verify with; nothing was sent")
                                  for i, c in enumerate(calls)}
                continue
            state = console._read_state()
            for position, call in enumerate(calls):
                try:
                    flat.append((index, position, console, snapshot(state),
                                 console.plan(call, state)))
                except Refused as exc:
                    results[index][position] = _result(
                        call, False, "rejected", f"refused before writing: {exc}; nothing was sent")
        if flat:
            sent = self._send_flat([(console, before, planned) for _, _, console, before, planned in flat])
            for slot, outcome in sent.items():
                index, position = flat[slot][0], flat[slot][1]
                results[index][position] = outcome
        return [[results[i][n] for n in range(len(calls))] for i, (_, calls) in enumerate(parts)]

    def _send(self, before: GameState, planned: list[Planned]) -> dict[int, ActionResult]:
        return self._send_flat([(self, before, p) for p in planned])

    def _send_flat(
        self, flat: list[tuple[ConsoleChannel, GameState, Planned]],
    ) -> dict[int, ActionResult]:
        """The shared half of ``_send``: ``flat`` is one ``(console, state before, plan)`` per
        action, the console being whichever one planned it (always ``self`` for one country)."""
        driver, config = self.driver, self.config
        planned = [p for _, _, p in flat]
        lines = [line for p in planned for line in p.lines]
        refreshed: list[ConsoleChannel] = []
        for console, before, _ in flat:
            if console.config.refresh and not any(console is seen for seen in refreshed):
                refreshed.append(console)
                lines += console.refresh_lines(console._country(before))
        needs_save = all(c is self for c, _, _ in flat) and self._wants_save(
            [p.call for p in planned])
        if self.inbox is not None:
            return self._send_inbox(flat, lines)
        if needs_save:
            # Last, so the save holds what the lines above did. The save is
            # written a few seconds later, from the state at this command.
            lines.append("savegame")
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
        if needs_save:
            self.saves.mark()
        try:
            self._keys_sequence()
        except InputInterrupted:
            raise            # the caller re-queues: this is not a failed action
        except Exception as exc:   # noqa: BLE001 - report it; _keys_sequence tried to close
            return {i: _result(p.call, False, "rejected",
                               f"{type(exc).__name__}: {exc}; the console may be open",
                               lines=p.lines) for i, p in enumerate(planned)}
        self._sleep(driver.config.settle_seconds)
        save_error = self.saves.collect() if needs_save else None
        out = {}
        for i, (console, before, p) in enumerate(flat):
            if p.call.name == "take_decision":
                ok, kind, message = console._verify_decision(p.call, before, p)
                out[i] = _result(p.call, ok, kind, message, lines=p.lines, unchecked=p.unchecked)
                continue
            if p.call.name in ("click_gui_button", "set_front_order", "set_region_order"):
                ok, kind, message = console._verify_click(p.call)
                out[i] = _result(p.call, ok, kind, message, lines=p.lines, unchecked=p.unchecked)
                continue
            via_save = needs_save and p.call.name in SAVE_VERIFIED
            baseline = before
            if via_save and save_error is None:
                # The save is the evidence here, whatever days the log has ticked.
                baseline = dataclasses.replace(before, telemetry_ticks=None)
            outcome = verify(p.verify_call or p.call, baseline, console._read_state,
                             timeout=console.verify_timeout, sleep=console._sleep)
            message = outcome.message
            if via_save and save_error is not None and not outcome.ok:
                message += f" The save could not be used: {save_error}."
            out[i] = _result(p.call, outcome.ok, outcome.kind, message,
                             lines=p.lines, unchecked=p.unchecked)
        return out

    def _send_inbox(self, flat, lines: list[str]) -> dict[int, ActionResult]:
        """``_send_flat`` for the inbox: post the lines, wait a little for the game to run
        them, verify what it ran. Nothing touches the keyboard, so nothing needs focus."""
        planned = [p for _, _, p in flat]
        self.last_lines = lines
        try:
            if self.driver.dry_run:
                path = self._write(self._archive_path(), lines)
                return {i: _result(p.call, False, "not_executed", (
                    f"dry run: would post {len(p.lines)} line(s) to the inbox ({path.name}): "
                    + " | ".join(p.lines)), lines=p.lines, unchecked=p.unchecked)
                    for i, p in enumerate(planned)}
            batches = self.inbox.post(lines)
            self._write(self._archive_path(), lines)
        except (ValueError, OSError) as exc:
            return {i: _result(p.call, False, "rejected", f"{exc}; nothing was posted")
                    for i, p in enumerate(planned)}
        ids = [b.id for b in batches]
        if not self.inbox.wait(ids, self.config.inbox_ack_seconds, poll_seconds=0.25):
            # Posted and safe in the file: the game runs it at its next daily pulse. Not
            # an error, and not done either; the next brief shows whether it landed.
            return {i: _result(p.call, True, None, (
                f"posted to the game's inbox (batch {', '.join(ids)}); the game runs it at its "
                "next in-game day, which has not come yet (paused, or between days). Not "
                "confirmed: do not plan as if it had happened until a brief shows it."),
                lines=p.lines, unchecked=p.unchecked, inbox=ids, acknowledged=False)
                for i, p in enumerate(planned)}
        out = {}
        for i, (console, before, p) in enumerate(flat):
            if p.call.name == "take_decision":
                ok, kind, message = console._verify_decision(p.call, before, p)
            elif p.call.name in ("click_gui_button", "set_front_order", "set_region_order"):
                ok, kind, message = console._verify_click(p.call)
            else:
                outcome = verify(p.verify_call or p.call, before, console._read_state,
                                 timeout=console.verify_timeout, sleep=console._sleep)
                ok, kind, message = outcome.ok, outcome.kind, outcome.message
            out[i] = _result(p.call, ok, kind, f"{message} (through the inbox, batch "
                             f"{', '.join(ids)})", lines=p.lines, unchecked=p.unchecked,
                             inbox=ids, acknowledged=True)
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
