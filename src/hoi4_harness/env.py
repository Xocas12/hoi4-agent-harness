"""The environment: adapter + validation + observation, behind one object.

Everything the agent can do to the world goes through here, which makes this the
one place to enforce the rules that must not depend on the model behaving:
validation, the confirmation gate on irreversible actions, and the per-turn
action cap. The cap is applied by act_many() to the reflexes, and by the agent
loop to the planner's calls, which arrive one act() at a time across several
tool rounds; both refuse with ``error_kind="budget"`` (see over_budget()).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable

from .actions import registry
from .actions.catalog import PLAYSET_QUERIES, QUERY_ACTIONS
from .adapters.base import GameAdapter
from .adapters.input_driver import InputInterrupted
from .config import HarnessConfig
from .confirm import Confirmation
from .coop import coop_lines, refusal
from .handover import PASS_THROUGH, HandoverQueue, HandoverReport
from .identifiers import IdentifierIndex
from .observation import ObservationBuilder
from .observation.directives import DirectiveTracker
from .types import ActionCall, ActionResult, GameEvent, GameState, Observation

#: Calls the per-turn action cap does not count: a note only writes to the
#: journal, and advance_time hands the clock back -- the prompt asks for "up to
#: N actions, then advance_time", and refusing it would not stop the turn.
UNCAPPED_ACTIONS = frozenset({"note", "advance_time", "send_message", "choose_event_option"}) | QUERY_ACTIONS


class HOI4Env:
    def __init__(
        self,
        adapter: GameAdapter,
        config: HarnessConfig | None = None,
        index: IdentifierIndex | None = None,
    ):
        self.adapter = adapter
        self.config = config or HarnessConfig()
        #: What ids exist in the playset. None: nothing to check against, and
        #: ids pass through to the adapter as before.
        self.index = index
        self.builder = ObservationBuilder(full_brief_every=self.config.full_brief_every)
        self.turn = 0
        # set to a callable(Confirmation) -> bool for human approval, e.g. the
        # terminal prompt in confirm.py
        self.confirm_hook = None
        #: What each standing AI directive has visibly changed. The adapter's
        #: "Directive standing" only proves the harness recorded it.
        self.directives = DirectiveTracker()
        #: Co-op: actions wait here until the player hands over the keyboard.
        self.handover: HandoverQueue | None = (
            HandoverQueue(self.config.run_dir) if self.config.handover else None)
        if self.config.rival:
            # Rival mode (rival/): the same queue, run by a window policy and sent as one batch.
            from .rival.queue import RivalQueue

            self.handover = RivalQueue(self.config.run_dir, self.config.rival_stale_days)
        self._last_handover: HandoverReport | None = None
        #: Callables ``(state) -> lines`` whose output ends every brief (campaign mode adds
        #: the conquest numbers this way).
        self.brief_hooks: list[Callable[[GameState], list[str]]] = []
        #: The map and armies from the newest save (world.WorldObserver, #139); None: off.
        self.world = None
        #: This country's end of the single-player chat (chat.ChatChannel, #133); None: off.
        self.chat = None
        #: Multiplayer: the in-game chat for messages to a human (adapters/mp_chat.py); None: off.
        self.mp_chat = None

    def attach_mp_chat(self, sender) -> None:
        """Route send_message to a human through the game's own chat (multiplayer runs)."""
        self.mp_chat = sender

    def attach_chat(self, channel) -> None:
        """Give the brief a messages section and the model the send_message tool."""
        self.chat = channel
        self.brief_hooks.append(channel.brief_lines)

    def attach_world(self, observer) -> None:
        """Give the brief a forces-and-map section and the model the inspect_world tool."""
        self.world = observer
        self.brief_hooks.append(observer.brief_lines)

    # --- observation ---------------------------------------------------------

    @property
    def allowed_actions(self) -> set[str]:
        """Adapter capability, narrowed by the operator's whitelist/blacklist."""
        supported = set(self.adapter.supported_actions) | self.available_queries
        return self.config.allowed_actions(supported)

    @property
    def available_queries(self) -> set[str]:
        """The read-only tools this run can answer: the playset's (mod context) and the
        world's (a save observer)."""
        out = set(PLAYSET_QUERIES) if self.queries_enabled else set()
        if self.events is not None:
            out.add("read_event")
        if self.world is not None:
            out.add("inspect_world")
        if self.chat is not None or self.mp_chat is not None or self.config.multiplayer:
            out.add("send_message")
        return out

    @property
    def events(self):
        """The event reader the adapter was wired with (adapters/event_choice.py), or None."""
        return getattr(self.adapter, "event_reader", None)

    @property
    def script_actions(self) -> set[str]:
        """What the script controllers (auto/) may do: everything the adapter offers that the
        operator has not blocked. Unlike ``allowed_actions`` it is not narrowed by the control
        map, because a script's domain is one the model has no tools for."""
        return self.config.script_offered(set(self.adapter.supported_actions))

    def _acceptable(self) -> set[str]:
        """What a queued call is re-checked against when it finally runs (handover mode)."""
        allowed = self.allowed_actions
        if self.config.control_map().script_domains():
            allowed = allowed | self.script_actions
        return allowed

    @property
    def queries_enabled(self) -> bool:
        """The read-only decision tools exist when the run asked for mod context
        and has the playset's files to answer from."""
        return bool(self.config.mod_context and self.index is not None)

    def query(self, call: ActionCall) -> ActionResult:
        """Answer ``list_decisions`` / ``describe_action`` from the playset's files.
        Nothing is sent to the game and nothing counts against the action cap."""
        def fail(message: str) -> ActionResult:
            return ActionResult(ok=False, action=call.name, call_id=call.call_id, message=message,
                                error_kind="unsupported")

        if call.name == "inspect_world":
            if self.world is None:
                return fail("this run has no save observer (observe_world is off)")
            problem = registry.check(call, QUERY_ACTIONS)
            if problem is not None:
                return problem
            args = call.arguments
            data = self.world.query(self.adapter.read_state(), str(args.get("view", "divisions")),
                                    args.get("id"), int(args.get("limit", 60)))
            return ActionResult(ok="error" not in data, action=call.name, call_id=call.call_id,
                                message=json.dumps(data, sort_keys=True, ensure_ascii=False),
                                error_kind=None if "error" not in data else "invalid_args")
        if call.name == "read_event":
            return self._read_event(call, fail)
        if not self.queries_enabled:
            return fail("this run has no playset files to answer from (mod context is off)")
        problem = registry.check(call, QUERY_ACTIONS)
        if problem is not None:
            return problem
        ctx = self.index.mod_context()
        country = (self.config.country or self.adapter.read_state().country or "").upper()
        if call.name == "list_decisions":
            args = call.arguments
            data = ctx.list_decisions(country, args.get("category"), args.get("query"),
                                      int(args.get("limit", 25)))
        elif call.name == "list_gui_buttons":
            args = call.arguments
            data = ctx.list_gui_buttons(country, args.get("gui"), args.get("query"),
                                        int(args.get("limit", 40)))
        else:
            data = ctx.describe(str(call.arguments["id"]), country)
            if data is None:
                return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                    message=f"Nothing called '{call.arguments['id']}' is a decision, "
                                            f"a focus of {country}'s tree, or a technology or idea "
                                            "this playset adds.", error_kind="invalid_args")
        return ActionResult(ok=True, action=call.name, call_id=call.call_id,
                            message=json.dumps(data, sort_keys=True, ensure_ascii=False))

    def _read_event(self, call: ActionCall, fail) -> ActionResult:
        """``read_event``: the open window as read from the screen, or one event from the files."""
        reader = self.events
        if reader is None:
            return fail("this run does not read event windows (docs/events.md: needs Windows OCR "
                        "and mod context in an independent campaign with the mouse)")
        problem = registry.check(call, QUERY_ACTIONS)
        if problem is not None:
            return problem
        wanted = call.arguments.get("id")
        if wanted:
            info = reader.catalog.get(str(wanted))
            if info is None:
                return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                    message=f"no event called '{wanted}' in this playset",
                                    error_kind="invalid_args")
            data = {"status": "from_files", **info.describe()}
        else:
            data = reader.describe(reader.look())
        return ActionResult(ok=True, action=call.name, call_id=call.call_id,
                            message=json.dumps(data, sort_keys=True, ensure_ascii=False))

    def read_state(self) -> GameState:
        return self.adapter.read_state()

    def acknowledge_events(self, events: Iterable[GameEvent]) -> None:
        """Tell the adapter the loop has answered these events (see GameAdapter)."""
        self.adapter.acknowledge_events(events)

    def observe(self, notes: list[str] | None = None, *, remember: bool = True) -> Observation:
        state = self.adapter.read_state()
        observation = self.builder.build(
            state,
            turn=self.turn,
            legal_actions=sorted(self.allowed_actions),
            notes=notes,
            remember=remember,
        )
        # Every brief, full or delta: a directive that has done nothing for a
        # month is exactly the news that must not be diffed away.
        extra = self.directives.render(state)
        if self._last_handover is not None:
            # Said once: what the last window actually did, stale ones included,
            # so the model replans on what happened rather than what it queued.
            extra.append(self._last_handover.summary())
            self._last_handover = None
        if self.handover is not None:
            extra += self.handover.describe()
        extra += coop_lines(self.config)
        if self.events is not None:
            extra += self.events.brief_lines(state)
        for hook in self.brief_hooks:
            extra += hook(state)
        if self.index is not None:
            extra += self.index.brief_lines(state)
        if extra:
            observation.brief += "\n" + "\n".join(extra)
        return observation

    @property
    def playset(self) -> dict:
        """What the transcript records about the world this run played in."""
        if self.index is not None:
            return {**self.index.playset.describe(), **self.index.summary()}
        return {"name": f"unindexed ({self.adapter.info().name})"}

    @property
    def owns_clock(self) -> bool:
        return self.config.clock_owner == "harness"

    def reset(self) -> Observation:
        self.turn = 0
        if self.owns_clock:
            self.adapter.pause()
        # The brief reset returns is real (the observe CLI prints it), but it must
        # not set the diff baseline: the first observe() after reset is still full.
        observation = self.observe(remember=False)
        # Whatever this first read found already queued is history, not news: the
        # log-tail reader starts at the top of game.log, so a fresh start, a
        # `play --resume` or a co-op attach would otherwise wake turn 1 for every
        # war the mod has logged this session, including ones a previous run has
        # already answered. Only what this read returned: anything later is news.
        self.adapter.acknowledge_events(observation.state.events)
        return observation

    # --- acting --------------------------------------------------------------

    def act(self, call: ActionCall, date: str = "", *, auto: bool = False) -> ActionResult:
        """Validate, gate, then apply one action.

        ``date`` is the game date the caller is acting against. It is only used
        to give a human at the confirmation gate the context to judge the call,
        and it is passed in rather than read here: the loop already has it, and
        on a screen adapter a state read costs a vision-model call.
        """
        if call.name in QUERY_ACTIONS:
            return self.query(call)
        if call.name == "send_message":
            return self._send_message(call, date)
        if self.config.advisor:
            # The hard edge of advisor mode. The loop routes calls to the
            # advisor instead of here, but the env refusing is what makes the
            # mode structural: no call site can act by accident.
            return ActionResult(
                ok=False,
                action=call.name,
                call_id=call.call_id,
                message="Advisor mode: the harness cannot act. Nothing was executed.",
                error_kind="not_executed",
            )

        problem = self._refused(call)
        if problem is None:
            problem = registry.check(call, self.script_actions if auto else self.allowed_actions)
        if problem is None and self.index is not None:
            # An invented id never reaches the game, where it would do nothing
            # silently; the model gets the nearest real ones instead.
            problem = self.index.check(call, self.adapter.read_state().country)
        if problem is not None:
            return problem

        if registry.needs_confirmation(call) and self.config.require_confirmation:
            if self.config.dry_run:
                return ActionResult(
                    ok=False,
                    action=call.name,
                    call_id=call.call_id,
                    message=(
                        f"'{call.name}' is irreversible and the harness is in dry-run mode. "
                        "Nothing was done. Re-run with --live to allow it."
                    ),
                    error_kind="rejected",
                )
            # Approve-mode handover asks at execution time, where the player is at the screen; a
            # second ask here, when the model queues it, would be the same question twice.
            queued_for_approval = self.handover is not None and self.handover.approver is not None
            if not queued_for_approval and self.confirm_hook and not self.confirm_hook(
                Confirmation(call=call, date=date, turn=self.turn)
            ):
                return ActionResult(
                    ok=False,
                    action=call.name,
                    call_id=call.call_id,
                    message="A human declined this action.",
                    error_kind="rejected",
                )

        if self.handover is not None and call.name not in PASS_THROUGH:
            # The player owns the keyboard: decide now, click when handed over.
            state = self.adapter.read_state()
            return self.handover.enqueue(call, state.date, self.turn)
        return self._apply(call)

    def _refused(self, call: ActionCall) -> ActionResult | None:
        """The co-op and multiplayer refusals (coop.py): a domain the player holds, a country
        another person plays. Checked at queue time and again when a queued call runs."""
        why = refusal(self.config, call)
        if why is None:
            return None
        return ActionResult(ok=False, action=call.name, call_id=call.call_id, message=why,
                            error_kind="rejected")

    def _apply(self, call: ActionCall) -> ActionResult:
        try:
            result = self.adapter.apply(call)
        except InputInterrupted:
            raise          # a guard stopped the keys: the queue keeps the action for the next window
        except Exception as exc:  # noqa: BLE001 - an adapter fault must not end the run
            return ActionResult(
                ok=False,
                action=call.name,
                call_id=call.call_id,
                message=f"Adapter raised {type(exc).__name__}: {exc}",
                error_kind="rejected",
            )
        if result.ok:
            self._track_directive(call)
        return result

    def run_handover(self) -> HandoverReport | None:
        """Run the queued actions if the player has granted a window.

        Each is validated again at execution time -- the operator's whitelist
        or the playset may not have changed, but the call was accepted a while
        ago and this is the last point before input reaches the game.
        """
        if self.handover is None:
            return None

        def apply(call: ActionCall) -> ActionResult:
            problem = self._refused(call) or registry.check(call, self._acceptable())
            if problem is None and self.index is not None:
                problem = self.index.check(call, self.adapter.read_state().country)
            return problem if problem is not None else self._apply(call)

        def apply_batch(calls: list[ActionCall]) -> list[ActionResult]:
            """One validation pass per call, then every valid one in one send."""
            out: dict[int, ActionResult] = {}
            valid: list[ActionCall] = []
            for call in calls:
                problem = self._refused(call) or registry.check(call, self._acceptable())
                if problem is None and self.index is not None:
                    problem = self.index.check(call, self.adapter.read_state().country)
                if problem is not None:
                    out[id(call)] = problem
                else:
                    valid.append(call)
            if valid:
                try:
                    results = self.adapter.apply_batch(valid)
                except InputInterrupted:
                    raise          # not a failed action: the queue keeps them for the next window
                except Exception as exc:  # noqa: BLE001 - an adapter fault must not end the run
                    results = [ActionResult(
                        ok=False, action=c.name, call_id=c.call_id,
                        message=f"Adapter raised {type(exc).__name__}: {exc}",
                        error_kind="rejected") for c in valid]
                for call, result in zip(valid, results, strict=True):
                    if result.ok:
                        self._track_directive(call)
                    out[id(call)] = result
            return [out[id(call)] for call in calls]

        report = self.handover.run(self.adapter.read_state, apply, apply_batch)
        if report is not None:
            self._last_handover = report
        return report

    def _track_directive(self, call: ActionCall) -> None:
        if call.name == "set_ai_directive":
            self.directives.raised(
                call.arguments["directive"],
                call.arguments["target"],
                int(call.arguments.get("weight", 100)),
                self.adapter.read_state(),
            )
        elif call.name == "clear_ai_directives":
            self.directives.clear()

    def over_budget(self, call: ActionCall) -> ActionResult:
        """The refusal for a call past the per-turn action cap. Nothing runs.

        The planner's calls arrive one at a time, so the loop counts them across
        the turn's rounds and hands each one past the cap this result instead of
        passing it to act().
        """
        cap = self.config.max_actions_per_turn
        return ActionResult(
            ok=False,
            action=call.name,
            call_id=call.call_id,
            message=(
                f"The per-turn cap of {cap} actions is used up; '{call.name}' was not run. "
                "Call advance_time to end the turn."
            ),
            error_kind="budget",
        )

    def _send_message(self, call: ActionCall, date: str) -> ActionResult:
        def result(ok: bool, message: str) -> ActionResult:
            return ActionResult(ok=ok, action=call.name, call_id=call.call_id, message=message,
                                error_kind=None if ok else "invalid_args")

        to, text = str(call.arguments.get("to", "")), str(call.arguments.get("text", ""))
        channel = self.chat
        if self.config.multiplayer:
            # Multiplayer: a human reads the game's chat, so a line to one goes there (UNVERIFIED
            # key path, adapters/mp_chat.py); the file chat stays for single player and for talk
            # between LLM countries.
            if self.mp_chat is not None and self.mp_chat.reaches(to):
                channel = self.mp_chat
            elif channel is None:
                return result(False, "multiplayer chat is not available here (no input driver to "
                                     "type with), or that is not a person in this game")
        if channel is None:
            return result(False, "this run has no chat")
        try:
            sent = channel.send(to, text, date or None)
        except ValueError as exc:
            return result(False, f"not sent: {exc}")
        return result(True, f"sent to {sent.to} (message #{sent.id})")

    def act_many(self, calls: list[ActionCall], date: str = "", *,
                 auto: bool = False) -> list[ActionResult]:
        results: list[ActionResult] = []
        for call in calls[: self.config.max_actions_per_turn]:
            results.append(self.act(call, date, auto=auto))
        if len(calls) > self.config.max_actions_per_turn:
            results.append(
                ActionResult(
                    ok=False,
                    action="(dropped)",
                    message=(
                        f"Only the first {self.config.max_actions_per_turn} actions ran; "
                        f"{len(calls) - self.config.max_actions_per_turn} were dropped."
                    ),
                    error_kind="budget",
                )
            )
        return results

    # --- clock ---------------------------------------------------------------

    def advance(self, days: int | None = None) -> GameState:
        self.turn += 1
        return self.adapter.advance(days if days is not None else self.config.days_per_turn)

    def close(self) -> None:
        self.adapter.close()
