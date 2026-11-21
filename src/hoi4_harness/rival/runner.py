"""Running a rival: the start, the loop, the windows, the exit.

``run_rival`` is what ``hoi4-harness play --rival TAG`` calls. Around the ordinary agent loop it
adds what steering an AI country in somebody else's game needs:

* the **start**: mark the country for the mod (``llm_bridge_mark_harness``) in a safe window,
  and prove it from the log (the mark event and a state line for the tag) before the first
  brief; a country already reporting is left as it is;
* the **windows**: while the loop waits on the in-game date, ``RivalSession.poll`` runs on
  every poll and, when the policy opens a window, types the queue as one batch
  (``rival/queue.py``, ``rival/safety.py``);
* the **exit**: unmark the country, in a window if one comes soon enough and otherwise by
  saying the one line to type, and leave the queue on disk.

The harness never touches the clock: no pause, no resume, no game speed. Nothing here presses
a key except the console key, the console's own text and enter; nothing presses Escape.
"""

from __future__ import annotations

import math
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from .. import cadence
from ..adapters.base import GameLost
from ..adapters.clock import days_between
from ..adapters.input_driver import InputInterrupted, NotFocused
from ..agent.llm.base import LLMClient
from ..agent.loop import AgentLoop, RunReport, Supervisor
from ..agent.memory import Memory
from ..campaign.progress import ProgressTracker
from ..campaign.runner import CampaignLog
from ..config import UNPRICED_PROVIDERS, HarnessConfig
from ..env import HOI4Env
from ..handover import GRANT_FILENAME
from ..types import GameState
from .safety import (
    HandoverPolicy,
    InboxPolicy,
    KeyWatch,
    LogClock,
    NeverPolicy,
    PausedPolicy,
    WindowPolicy,
    system_input_watch,
)
from .setup import apply_defaults

RIVAL_LOG = "rival.log"
#: How many times, and how far apart, the log is read to see the mark take.
VERIFY_TRIES = 8
VERIFY_SECONDS = 1.0
#: The most the process-alive check (``tasklist``) is run.
ALIVE_EVERY_SECONDS = 15.0
#: TAG's lines count as flowing when its last state is within this many days of the log's.
FLOWING_DAYS = 1


class RivalStartError(RuntimeError):
    """The rival could not be set up: the game is not in the state it needs."""


class RivalSession:
    """The windows: owns the policy and the guard, and is the clock wait's poll hook."""

    def __init__(self, env: HOI4Env, config: HarnessConfig, policy: WindowPolicy, watch: KeyWatch,
                 log: CampaignLog, *, now: Callable[[], float], sleep: Callable[[float], None],
                 alive: Callable[[], bool | None] | None = None):
        self.env, self.config, self.policy, self.watch, self.log = env, config, policy, watch, log
        self.now, self.sleep, self.alive = now, sleep, alive
        self.loop: AgentLoop | None = None
        self._busy = False
        self._announced: str | None = None
        self._alive_at = float("-inf")
        self.windows = 0
        self._run_handover = env.run_handover
        env.run_handover = self.run_window

    @property
    def queue(self):
        return self.env.handover

    @property
    def console(self):
        return self.env.adapter.writer.router.console

    # --- the clock wait's hook ---------------------------------------------------------

    def poll(self) -> None:
        """Called before every read of a clock wait: the game's death, then a window if one
        is open and something is queued."""
        self._check_alive()
        clock = getattr(self.policy, "clock", None)
        if clock is not None:
            clock.update()            # time the stillness even while nothing is queued
        if self._busy:
            return
        self._busy = True
        try:
            report = self.env.run_handover()
        finally:
            self._busy = False
        if report is None:
            self._note_wait()
        elif self.loop is not None:
            # (the loop writes the transcript record for the windows it runs itself)
            self.loop.transcript.write("handover", **report.to_dict())

    def run_window(self):
        """``env.run_handover``, with every window it runs said in rival.log."""
        report = self._run_handover()
        if report is not None:
            self._announced = None
            self.windows += 1
            self.log.line(report.summary())
        return report

    def _check_alive(self) -> None:
        if self.alive is None or self.now() - self._alive_at < ALIVE_EVERY_SECONDS:
            return
        self._alive_at = self.now()
        if self.alive() is False:
            raise GameLost("the game process is gone")

    def _note_wait(self) -> None:
        """Say once why the queue is waiting, and again only when the reason changes."""
        gate = self.queue.last_gate if self.queue is not None else None
        if gate is None or gate.ok or not self.queue.pending or gate.kind == self._announced:
            return
        self._announced = gate.kind
        self.log.line(f"{len(self.queue.pending)} queued, waiting: {gate.text}")

    # --- a window the harness asks for itself (mark, unmark) ---------------------------------

    def send_in_window(self, lines: list[str], timeout: float, what: str) -> bool:
        """Type ``lines`` as one run file in the first safe window, waiting up to ``timeout``
        seconds for one. False when none came (or the policy never types)."""
        deadline = self.now() + timeout
        announced = None
        while True:
            gate = self.policy.check()
            if gate.ok:
                self.watch.begin()
                try:
                    self.console.send_lines(lines)
                    return True
                except InputInterrupted as exc:
                    self.log.line(f"{what}: stopped before it ran ({exc}); waiting for the next window")
                except NotFocused as exc:
                    self.log.line(f"{what}: not typed ({exc}); waiting")
                finally:
                    self.watch.end()
            elif gate.kind != announced:
                announced = gate.kind
                self.log.line(f"{what}: waiting: {gate.text}")
            if self.policy.advice_only or self.now() >= deadline:
                return False
            self.sleep(self.config.headless_poll_seconds)


# --- building the session -----------------------------------------------------------------


def build_policy(config: HarnessConfig, adapter, fake, now: Callable[[], float]) -> WindowPolicy:
    if config.rival_act_when == "never":
        return NeverPolicy()
    if config.rival_act_when == "handover":
        return HandoverPolicy(Path(config.run_dir) / GRANT_FILENAME)
    if config.rival_act_when == "inbox":
        return InboxPolicy()
    driver = adapter.writer.router.driver
    watch = fake.input_watch() if fake is not None else system_input_watch()
    return PausedPolicy(LogClock(getattr(adapter.reader, "log_path", config.log_path), now), watch,
                        driver.focus, config.rival_paused_seconds, config.rival_idle_seconds)


def build_session(env: HOI4Env, config: HarnessConfig, log: CampaignLog) -> RivalSession:
    """Wire the policy, the key guard and the poll hook into ``env``'s adapter."""
    from ..campaign.watch import process_running

    adapter = env.adapter
    fake = getattr(adapter, "fake_game", None)
    if fake is not None:
        now, sleep, alive = (lambda: fake._wall), fake.sleep, (lambda: fake.alive)
    else:
        name = config.campaign_game_process
        now, sleep, alive = time.monotonic, time.sleep, (lambda: process_running(name))
    policy = build_policy(config, adapter, fake, now)
    watch = KeyWatch(policy, config.console_key)
    if isinstance(policy, InboxPolicy):
        # No key is ever sent, so there is nothing for a key guard to watch.
        env.handover.attach(policy, None)
    else:
        driver = adapter.writer.router.driver
        driver.key_watch = watch
        keys = driver._keys()
        keys.on_send, keys.before_send = watch.own_input, watch.check
        env.handover.attach(policy, watch)
    session = RivalSession(env, config, policy, watch, log, now=now, sleep=sleep, alive=alive)
    adapter.on_poll = session.poll
    # A paused game stays paused as long as the person likes: a clock wait has no deadline.
    adapter._wait["seconds_per_day"] = math.inf
    return session


# --- the start and the exit ------------------------------------------------------------------


def _read_rival(env: HOI4Env) -> GameState | None:
    try:
        return env.adapter.read_state()
    except RuntimeError:
        return None


def _flowing(state: GameState | None, session: RivalSession) -> bool:
    """Whether the rival's telemetry is current: it has a state line and its date is the log's."""
    if state is None:
        return False
    clock = getattr(session.policy, "clock", None) or LogClock(
        getattr(session.env.adapter.reader, "log_path", None), session.now)
    latest = clock.last_date()
    return latest is None or abs(days_between(state.date, latest)) <= FLOWING_DAYS


def begin(env: HOI4Env, config: HarnessConfig, log: CampaignLog, session: RivalSession) -> GameState:
    """Make the rival report, and return what the log then says about it.

    A rival that already reports is left alone. Otherwise the mark goes out in the first safe
    window, with a telemetry refresh in the same run file, and the start waits for the log to
    show both the mark event and a state line for the tag. Under ``never`` the line to type is
    printed instead and the log is watched for the person to have typed it."""
    tag = config.rival
    state = _read_rival(env)
    if _flowing(state, session):
        log.line(f"{tag} already reports to the log: marking skipped")
        if config.telemetry_hours is not None:
            log.line("telemetry_hours is set but the country is already marked, so nothing is "
                     f"sent; type `{cadence.lines(tag, config.telemetry_hours)[0]}` in the "
                     "game's console to set it")
        return state
    lines = [f"effect {tag} llm_bridge_mark_harness", *session.console.refresh_lines(tag)]
    if config.telemetry_hours is not None:
        # In the same run file as the mark: one window, and the cadence is set from the start.
        lines = [lines[0], *cadence.lines(tag, config.telemetry_hours), *lines[1:]]
        log.line(f"telemetry cadence: {cadence.describe(config.telemetry_hours)} ({lines[1]})")
    timeout = config.rival_start_wait_minutes * 60
    if session.policy.advice_only:
        log.line(f"policy never: type these in the game's console to start ({tag} must be marked): "
                 + "; ".join(lines[:len(lines) - len(session.console.refresh_lines(tag))]))
    else:
        if config.rival_act_when == "paused":
            log.line(f"marking {tag}: {lines[0]} (waiting for a safe window: pause the game and "
                     f"leave the keyboard alone for {config.rival_idle_seconds:g}s)")
        elif config.rival_act_when == "inbox":
            log.line(f"marking {tag}: {lines[0]} (posted to the inbox: it runs at the game's next "
                     "in-game day, so the game must be running, not paused)")
        else:
            log.line(f"marking {tag}: {lines[0]}")
        if not session.send_in_window(lines, timeout, "mark"):
            raise RivalStartError(
                f"no safe window in {config.rival_start_wait_minutes:g} minutes to mark {tag}. "
                f"Pause the game and leave the keyboard alone, or type `{lines[0]}` in the "
                "game's console yourself and start again")
    # Under never the person types it; on the inbox the game runs it when the person next lets
    # a day pass. Either can take as long as the person likes, up to the start wait.
    patient = session.policy.advice_only or config.rival_act_when == "inbox"
    deadline = session.now() + (timeout if patient else VERIFY_TRIES * VERIFY_SECONDS)
    while True:
        state = _read_rival(env)
        if state is not None and state.country == tag and _marked(state):
            log.line(f"{tag} marked: the mark event and a state line for {tag} are in the log")
            return state
        if session.now() >= deadline:
            if config.rival_act_when == "inbox":
                raise RivalStartError(
                    f"{tag} did not start reporting in {config.rival_start_wait_minutes:g} minutes: "
                    "the game never ran the inbox. Was it started with -debug_mode, is it running "
                    "the installed mod (hoi4-harness inbox status), and did any day pass?")
            raise RivalStartError(
                f"{tag} did not start reporting: after the mark there is no state line for it in "
                "game.log. Is the mod schema v5 and the tag right? (hoi4-harness verify-bridge)")
        session.sleep(VERIFY_SECONDS)


def _marked(state: GameState) -> bool:
    return any(e.kind == "harness_marked" for e in state.events)


def finish(session: RivalSession, config: HarnessConfig, log: CampaignLog, lost: bool) -> None:
    """Unmark the rival: in a window if one comes soon, else say what to type. Never raises."""
    tag = config.rival
    line = f"effect {tag} llm_bridge_unmark_harness"
    if lost:
        log.line(f"the game is gone: {tag} stays marked in the save, if there is one ({line} undoes it)")
        return
    try:
        if session.send_in_window([line], config.rival_exit_wait_seconds, "unmark"):
            log.line(f"{tag} unmark posted to the inbox: it runs at the game's next in-game day"
                     if config.rival_act_when == "inbox" else f"{tag} unmarked")
            return
    except Exception as exc:  # noqa: BLE001 - the exit must not hide how the run ended
        log.line(f"unmark failed: {type(exc).__name__}: {exc}")
    log.line(f"{tag} is still marked (harmless: it keeps logging). To undo it type in the console: {line}")


# --- the supervisor -------------------------------------------------------------------------


class RivalSupervisor(Supervisor):
    """What the loop calls around each turn: the time series, and a line in rival.log."""

    def __init__(self, config: HarnessConfig, session: RivalSession, tracker: ProgressTracker,
                 log: CampaignLog):
        self.config, self.session, self.tracker, self.log = config, session, tracker, log
        self.loop: AgentLoop | None = None

    def before_turn(self, state: GameState) -> str | None:
        spend = self.loop.budget.summary() if self.loop else None
        turn = self.loop.report.turns if self.loop else 0
        self.tracker.events(state.events, state.date)
        self.tracker.record(state, spend, turn)
        return None

    def after_turn(self, state: GameState, *, woke: bool, reason: str, actions: list[str],
                   tokens: int) -> None:
        if woke:
            self.tracker.reviewed()
        queued = len(self.session.queue.pending) if self.session.queue is not None else 0
        wars = ", ".join(w.against for w in state.wars) or "none"
        marker = f"wake: {reason}" if woke else "ai plays"
        self.log.line(f"{state.date}  {marker} | wars: {wars} | queued {queued} | "
                      f"{', '.join(actions) if actions else '-'}")


# --- running ----------------------------------------------------------------------------------


def _check_ready(config: HarnessConfig) -> None:
    from .setup import RivalConfigError

    try:
        apply_defaults(config)
    except RivalConfigError as exc:
        raise RivalStartError(str(exc)) from exc
    if config.dry_run:
        raise RivalStartError("rival mode types into the game: pass --live (against the real game, "
                              "loaded and in front) or use --fake-game")
    if config.console_transport == "inbox" and config.rival_act_when == "paused":
        config.rival_act_when = "inbox"      # the default window policy means "no window" here
    if config.rival_act_when == "inbox" or config.console_transport == "inbox":
        if config.console_transport != "inbox" or config.rival_act_when != "inbox":
            raise RivalStartError("the inbox needs both halves: --transport inbox (orders go into "
                                  "the mod file) sets rival_act_when=inbox (no window to wait for)")
    elif not config.fake_game and not config.enforce_window_focus:
        raise RivalStartError("rival mode never types without the game in front: leave the window "
                              "guard on (drop --no-window-guard)")
    # Subscription-side answerers (a mailbox agent, the claude CLI) bill nothing here.
    priced = config.planner_enabled and config.planner.provider not in UNPRICED_PROVIDERS
    if priced and config.budget.max_usd is None:
        raise RivalStartError("a rival run spends money while you play: give it a hard cap "
                              "with --max-usd N")


def run_rival(
    config: HarnessConfig,
    *,
    planner: LLMClient | None = None,
    resume: bool = False,
    quiet: bool = False,
    echo: Callable[[str], None] | None = None,
    env: HOI4Env | None = None,
    until: str | None = None,
) -> tuple[RunReport, dict]:
    """Steer the rival until ``until``, the turn backstop, or the first reason to stop.

    Returns the loop's report and a summary dict. Raises ``RivalStartError`` when the game is
    not ready; ``GameLost`` never (a clean stop with a reason). Any other failure is written to
    ``rival.log`` and the transcript, then raised."""
    from ..agent.llm import build_llm
    from ..cli import _env_from_config

    _check_ready(config)
    if len(config.rivals) > 1:
        # Several countries (rival/multi.py): the same start, windows and exit, one strategist each.
        from .multi import run_rivals

        return run_rivals(config, planner=planner, resume=resume, quiet=quiet, echo=echo,
                          env=env, until=until)
    # Before anything is typed: a planner that cannot be built (no key, no script) must not
    # leave the rival marked.
    if planner is None and config.planner_enabled:
        planner = build_llm(config.planner)
    run_dir = Path(config.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    out = echo or (lambda line: None)
    log = CampaignLog(run_dir / RIVAL_LOG, None if quiet else out)
    env = env or _env_from_config(config)
    session = build_session(env, config, log)
    queue = env.handover
    if resume:
        if (restored := queue.restore()):
            log.line(f"{restored} queued action(s) restored from the last run")
    else:
        queue.clear()
    tracker = ProgressTracker(run_dir=run_dir, until=until, max_usd=config.budget.max_usd,
                              objective=config.objective, label="RIVAL")
    env.brief_hooks.append(tracker.brief_lines)
    tag = config.rival
    cap = f"${config.budget.max_usd:.2f}" if config.budget.max_usd else "none"
    log.line(f"rival {'resumed' if resume else 'started'}: steering {tag}, acts when "
             f"{config.rival_act_when}, until {until or 'stopped'}, cap {cap}, "
             f"model {config.planner.model}")
    try:
        begin(env, config, log, session)
        env.reset()
    except (RivalStartError, GameLost) as exc:
        log.line(f"cannot start: {exc}")
        raise
    supervisor = RivalSupervisor(config, session, tracker, log)
    lost, loop = False, None
    try:
        loop = AgentLoop(
            env=env, planner=planner, config=config,
            memory=Memory.load(run_dir / "memory.json") if resume else Memory(),
            transcript_path=run_dir / "transcript.jsonl", resume=resume, progress=None,
            supervisor=supervisor,
        )
        supervisor.loop = session.loop = loop
        report = loop.run(config.turns, until=until)
        lost = bool(report.stopped_reason) and "game" in report.stopped_reason.lower()
    except Exception as exc:
        log.line(f"CRASH: {type(exc).__name__}: {exc}")
        if loop is not None:
            loop.transcript.write("crash", error=f"{type(exc).__name__}: {exc}",
                                  trace=traceback.format_exc(limit=8))
        raise
    finally:
        if loop is not None:
            loop.memory.save(run_dir / "memory.json")
        finish(session, config, log, lost)
    reason = report.stopped_reason or (
        "reached the end date" if report.reached_end_date else "ran out of turns")
    left = len(queue.pending)
    log.line(f"STOP: {reason} at {report.end_date or '?'}; {session.windows} window(s) run, "
             f"{left} action(s) still queued; spent ${loop.budget.spend.usd:.2f}")
    summary = {
        "stopped_reason": report.stopped_reason, "reached_end_date": report.reached_end_date,
        "end_date": report.end_date, "start_date": report.start_date, "turns": report.turns,
        "spend": report.spend, "run_dir": str(run_dir), "rival": tag,
        "windows": session.windows, "left_queued": left,
        "days_played": days_between(report.start_date, report.end_date),
    }
    return report, summary
