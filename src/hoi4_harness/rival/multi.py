"""Several rivals in one game: ``hoi4-harness play --rival FRA,ENG,SOV``.

One strategist per country, ONE keyboard. Everything a country owns is its own: its adapter
(a log reader that keeps to its tag, so it can never see another country's lines or the
person's), its agent loop, transcript, notes, queue, progress series and budget share, in a
folder named for it. Everything that touches the person is shared and exists once:

* **the marks**: every rival is marked in one window at the start and unmarked in one window
  at the exit (or on a failure), as a single rival is;
* **the clock**: nobody here touches it. Each round, every country decides in turn (a model
  call only for the ones whose own schedule or events woke them), then ONE wait on the log's
  date runs until the earliest review any country asked for, or until anything critical
  happens to any of them;
* **the window**: the policy and the key guard are one. A window is one pass of the console
  keys and one run file holding every country's due actions, each in its own scope (``effect
  FRA ...``, ``eval_effect ENG = { ... }``), each re-checked first against that country's own
  state, taken in turn across the countries up to ``rival_window_cap`` so none starves.

Nothing here presses a key but the console key, the console's text and enter.
"""

from __future__ import annotations

import copy
import json
import math
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from ..actions import registry
from ..adapters.base import GameLost
from ..adapters.clock import days_between, wait_for_days
from ..adapters.console import CONSOLE_ACTIONS
from ..adapters.input_driver import InputInterrupted
from ..agent.llm.base import LLMClient
from ..agent.loop import AgentLoop, RunReport, Supervisor
from ..agent.memory import Memory
from ..campaign.progress import ProgressTracker
from ..campaign.runner import CampaignLog
from ..config import HarnessConfig
from ..env import HOI4Env
from ..handover import Pending
from ..types import ActionResult, GameState
from .queue import RivalQueue, RivalReport
from .runner import (
    RIVAL_LOG,
    VERIFY_SECONDS,
    VERIFY_TRIES,
    RivalSession,
    RivalStartError,
    _flowing,
    _marked,
    _read_rival,
    build_policy,
)
from .safety import KeyWatch
from .setup import apply_defaults, human_tags_in_log, share_weights

SUMMARY_FILE = "rival-summary.json"


# --- one country ----------------------------------------------------------------------------


@dataclass
class Member:
    """Everything one rival owns."""

    tag: str
    config: HarnessConfig
    env: HOI4Env
    run_dir: Path
    log: CampaignLog
    #: The slice of ``--max-usd`` this country may spend (None: uncapped).
    share_usd: float | None
    planner: LLMClient | None = None
    tracker: ProgressTracker | None = None
    loop: AgentLoop | None = None
    #: The days of game time this country's last decision asked the clock for.
    days: int | None = None
    done: bool = False
    #: Windows this country had actions or stale reports in.
    windows: int = 0
    #: Why this country's model calls stopped (its share ran out), once they did.
    budget_stop: str | None = None

    @property
    def queue(self) -> RivalQueue:
        return self.env.handover

    @property
    def console(self):
        return self.env.adapter.writer.router.console

    def counts(self) -> dict:
        """What this country's actions came to, from its queue's windows."""
        reports: list[RivalReport] = self.queue.reports
        spend = self.loop.budget.summary() if self.loop is not None else {}
        return {
            "queued": self.queue._next - 1,
            "ran": sum(len(r.ran) for r in reports),
            "stale": sum(len(r.stale) for r in reports),
            "failed": sum(len(r.failed) for r in reports),
            "advised": sum(len(r.advised) for r in reports),
            "left_queued": len(self.queue.pending),
            "windows": self.windows,
            "turns": self.loop.report.turns if self.loop is not None else 0,
            "planner_calls": self.loop.report.planner_calls if self.loop is not None else 0,
            "usd": spend.get("usd", 0.0),
            "share_usd": self.share_usd,
            "budget_stop": self.budget_stop,
            "end_date": self.loop.report.end_date if self.loop is not None else "",
        }


class MemberSupervisor(Supervisor):
    """One country's per-turn hook: its time series and a line in its own rival.log."""

    def __init__(self, member: Member, top: CampaignLog):
        self.member, self.top = member, top

    def before_turn(self, state: GameState) -> str | None:
        m = self.member
        spend = m.loop.budget.summary() if m.loop else None
        turn = m.loop.report.turns if m.loop else 0
        m.tracker.events(state.events, state.date)
        m.tracker.record(state, spend, turn)
        return None

    def after_turn(self, state: GameState, *, woke: bool, reason: str, actions: list[str],
                   tokens: int) -> None:
        m = self.member
        if woke:
            m.tracker.reviewed()
        wars = ", ".join(w.against for w in state.wars) or "none"
        marker = f"wake: {reason}" if woke else "ai plays"
        line = (f"{state.date}  {marker} | wars: {wars} | queued {len(m.queue.pending)} | "
                f"{', '.join(actions) if actions else '-'}")
        m.log.line(line)
        if woke:          # the combined log carries the decisions, not every quiet day
            self.top.line(f"[{m.tag}] {line}")


# --- the shared window ----------------------------------------------------------------------


class MultiRivalSession(RivalSession):
    """The windows for several countries: one policy, one guard, one run file per window."""

    def __init__(self, members: list[Member], config: HarnessConfig, policy, watch: KeyWatch,
                 log: CampaignLog, *, now, sleep, alive=None):
        self.members = members
        super().__init__(members[0].env, config, policy, watch, log, now=now, sleep=sleep,
                         alive=alive)
        for m in members:
            # The loops never run a window of their own: the session does, once per round and
            # on every poll of a clock wait, for every country at a time.
            m.env.run_handover = lambda: None
        self.last_gate = None
        #: Which country goes first in the next window (turns, so none is always last).
        self._rotation = 0

    def poll(self) -> None:
        self._check_alive()
        clock = getattr(self.policy, "clock", None)
        if clock is not None:
            clock.update()
        if self._busy:
            return
        self._busy = True
        try:
            reports = self.run_window()
        finally:
            self._busy = False
        if reports is None:
            self._note_wait()

    def _note_wait(self) -> None:
        gate = self.last_gate
        queued = sum(len(m.queue.pending) for m in self.members)
        if gate is None or gate.ok or not queued or gate.kind == self._announced:
            return
        self._announced = gate.kind
        who = ", ".join(f"{m.tag} {len(m.queue.pending)}" for m in self.members if m.queue.pending)
        self.log.line(f"{queued} queued ({who}), waiting: {gate.text}")

    def _order(self) -> list[Member]:
        k = self._rotation % len(self.members)
        return self.members[k:] + self.members[:k]

    def run_window(self) -> dict[str, RivalReport] | None:
        """Type every country's due actions as one run file, if the policy opens a window.

        Returns each country's report (only the ones that had something queued) or None when
        no window ran."""
        members, policy = self.members, self.policy
        if not any(m.queue.pending for m in members):
            policy.idle()
            return None
        if policy.advice_only:
            return self._book({m.tag: m.queue._advise(m.env.adapter.read_state)
                               for m in members if m.queue.pending})
        self.last_gate = policy.check()
        for m in members:
            m.queue.last_gate = self.last_gate
        if not self.last_gate.ok:
            return None
        order = [m for m in self._order() if m.queue.pending]
        triaged: dict[str, tuple[RivalReport, list[Pending]]] = {
            m.tag: m.queue.triage(m.env.adapter.read_state()) for m in order}
        chosen = self._choose(order, {tag: runnable for tag, (_, runnable) in triaged.items()})
        results: dict[str, list[ActionResult]] | None = None
        reason = ""
        if any(chosen.values()):
            self.watch.begin()
            try:
                results = self._send({m.tag: (m, chosen[m.tag]) for m in order})
            except InputInterrupted as exc:
                reason = str(exc)
            finally:
                self.watch.end()
            self._rotation += 1
        else:
            policy.end()
        reports: dict[str, RivalReport] = {}
        for m in order:
            report, _ = triaged[m.tag]
            if results is None and chosen[m.tag]:
                report.aborted, report.reason = True, reason
            m.queue.settle(report, chosen[m.tag], None if results is None else results[m.tag])
            reports[m.tag] = report
        return self._book(reports)

    def _choose(self, order: list[Member], runnable: dict[str, list[Pending]]) -> dict[str, list[Pending]]:
        """Up to ``rival_window_cap`` actions, one country at a time round and round, so a
        country with a long queue cannot crowd the others out of a short window."""
        cap = self.config.rival_window_cap
        chosen: dict[str, list[Pending]] = {m.tag: [] for m in order}
        taken = 0
        while taken < cap:
            progress = False
            for m in order:
                have = chosen[m.tag]
                if taken < cap and len(have) < len(runnable[m.tag]):
                    have.append(runnable[m.tag][len(have)])
                    taken += 1
                    progress = True
            if not progress:
                break
        return chosen

    def _send(self, plan: dict[str, tuple[Member, list[Pending]]]) -> dict[str, list[ActionResult]]:
        """Validate each action as its own country's env would, then one run file for all."""
        out: dict[str, dict[int, ActionResult]] = {tag: {} for tag in plan}
        parts: list[tuple[Member, list[int], list]] = []
        for tag, (m, entries) in plan.items():
            valid_at: list[int] = []
            valid = []
            for i, entry in enumerate(entries):
                call = entry.call
                problem = registry.check(call, m.env.allowed_actions)
                if problem is None and m.env.index is not None:
                    problem = m.env.index.check(call, m.env.adapter.read_state().country)
                if problem is None and not self._console_action(m, call):
                    out[tag][i] = m.env._apply(call)       # not a console action: its own way
                elif problem is not None:
                    out[tag][i] = problem
                else:
                    valid_at.append(i)
                    valid.append(call)
            if valid:
                parts.append((m, valid_at, valid))
        if parts:
            lead = self.members[0].console
            shared = lead.execute_shared([(m.console, calls) for m, _, calls in parts])
            for (m, valid_at, calls), results in zip(parts, shared, strict=True):
                for i, call, result in zip(valid_at, calls, results, strict=True):
                    if result.ok:
                        m.env._track_directive(call)
                    out[m.tag][i] = result
        return {tag: [out[tag][i] for i in range(len(entries))] for tag, (_, entries) in plan.items()}

    @staticmethod
    def _console_action(m: Member, call) -> bool:
        return (m.env.adapter.writer.router.channels.get(call.name) == "console"
                and call.name in CONSOLE_ACTIONS)

    def _book(self, reports: dict[str, RivalReport]) -> dict[str, RivalReport]:
        """Say a window in every log, and hand each country the report of its own part."""
        self._announced = None
        self.windows += 1
        by_tag = {m.tag: m for m in self.members}
        parts = []
        for tag, report in reports.items():
            m = by_tag[tag]
            m.windows += 1
            m.env._last_handover = report
            m.log.line(report.summary())
            if m.loop is not None:
                m.loop.transcript.write("handover", **report.to_dict())
            parts.append(f"{tag}: {_short(report)}")
        self.log.line(f"Window {self.windows}: " + "; ".join(parts))
        return reports


def _short(report: RivalReport) -> str:
    if report.advised:
        return f"{len(report.advised)} shown, not run"
    bits = [f"{len(report.ran)} ran"]
    if report.stale:
        bits.append(f"{len(report.stale)} stale")
    if report.failed:
        bits.append(f"{len(report.failed)} failed")
    if report.aborted:
        bits.append(f"stopped ({report.reason or 'the person came back'}), "
                    f"{report.left_queued} left")
    elif report.left_queued:
        bits.append(f"{report.left_queued} left for the next window")
    return ", ".join(bits)


# --- building the members -------------------------------------------------------------------


def member_config(config: HarnessConfig, tag: str, share_usd: float | None) -> HarnessConfig:
    """``tag``'s own configuration: the shared one, as a one-country rival run in its folder."""
    mc = copy.deepcopy(config)
    mc.rival, mc.rivals, mc.rival_shares = tag, [], {}
    mc.country = tag
    mc.run_dir = Path(config.run_dir) / tag
    mc.budget.max_usd = share_usd
    return apply_defaults(mc)


def split_budget(config: HarnessConfig, tags: list[str]) -> dict[str, float | None]:
    """``--max-usd`` divided by the shares (equal unless ``rival_shares`` says otherwise)."""
    total = config.budget.max_usd
    if total is None:
        return {tag: None for tag in tags}
    weights = share_weights(config, tags)
    whole = sum(weights.values())
    return {tag: total * weights[tag] / whole for tag in tags}


def build_members(config: HarnessConfig, fake, planners: Callable[[str], LLMClient | None],
                  top: CampaignLog) -> list[Member]:
    from ..adapters.headless import build_headless
    from ..cli import _env_from_config

    shares = split_budget(config, config.rivals)
    members = []
    for tag in config.rivals:
        mc = member_config(config, tag, shares[tag])
        # A fake game is one object every country's adapter reads and types into; the real
        # game is one process every adapter finds through the same log and window.
        env = (HOI4Env(build_headless(mc, fake=fake), mc, index=mc.build_index())
               if fake is not None else _env_from_config(mc))
        mc.run_dir.mkdir(parents=True, exist_ok=True)
        members.append(Member(tag, mc, env, mc.run_dir, CampaignLog(mc.run_dir / RIVAL_LOG, None),
                              shares[tag], planner=planners(tag)))
    return members


def build_session(members: list[Member], config: HarnessConfig, log: CampaignLog) -> MultiRivalSession:
    """One policy and one key guard, wired into every country's driver."""
    from ..campaign.watch import process_running

    lead = members[0].env.adapter
    fake = getattr(lead, "fake_game", None)
    if fake is not None:
        now, sleep, alive = (lambda: fake._wall), fake.sleep, (lambda: fake.alive)
    else:
        name = config.campaign_game_process
        now, sleep, alive = time.monotonic, time.sleep, (lambda: process_running(name))
    policy = build_policy(config, lead, fake, now)
    watch = KeyWatch(policy, config.console_key)
    for m in members:
        driver = m.env.adapter.writer.router.driver
        driver.key_watch = watch
        keys = driver._keys()
        keys.on_send, keys.before_send = watch.own_input, watch.check
        m.queue.attach(policy, watch)
    session = MultiRivalSession(members, config, policy, watch, log, now=now, sleep=sleep,
                                alive=alive)
    lead.on_poll = session.poll
    lead._wait["seconds_per_day"] = math.inf      # a paused game stays paused as long as they like
    return session


# --- the start and the exit -----------------------------------------------------------------


def begin(members: list[Member], config: HarnessConfig, log: CampaignLog,
          session: MultiRivalSession) -> bool:
    """Make every rival report. Returns whether a mark was sent (so a failure knows to unmark).

    Rivals that already report are left alone. The rest are marked in ONE window, each with its
    telemetry refresh in the same run file, and the start waits for the log to show every mark
    event and a state line for every tag."""
    todo = []
    for m in members:
        if _flowing(_read_rival(m.env), session):
            log.line(f"{m.tag} already reports to the log: marking skipped")
        else:
            todo.append(m)
    if not todo:
        return False
    lines = [f"effect {m.tag} llm_bridge_mark_harness" for m in todo]
    for m in todo:
        lines += m.console.refresh_lines(m.tag)
    timeout = config.rival_start_wait_minutes * 60
    names = ", ".join(m.tag for m in todo)
    sent = False
    if session.policy.advice_only:
        log.line("policy never: type these in the game's console to start (" + names
                 + " must be marked): " + "; ".join(lines[:len(todo)]))
    else:
        log.line(f"marking {names}: {len(todo)} mark line(s) in one window (waiting for a safe "
                 + (f"window: pause the game and leave the keyboard alone for "
                    f"{config.rival_idle_seconds:g}s)" if config.rival_act_when == "paused"
                    else "window)"))
        if not session.send_in_window(lines, timeout, "mark"):
            raise RivalStartError(
                f"no safe window in {config.rival_start_wait_minutes:g} minutes to mark {names}. "
                "Pause the game and leave the keyboard alone, or type "
                + "; ".join(f"`{ln}`" for ln in lines[:len(todo)]) + " in the game's console "
                "yourself and start again")
        sent = True
    deadline = session.now() + (timeout if session.policy.advice_only else VERIFY_TRIES * VERIFY_SECONDS)
    waiting = list(todo)
    while True:
        for m in list(waiting):
            state = _read_rival(m.env)
            if state is not None and state.country == m.tag and _marked(state):
                log.line(f"{m.tag} marked: the mark event and a state line for {m.tag} are in the log")
                waiting.remove(m)
        if not waiting:
            return sent
        if session.now() >= deadline:
            raise _NotReporting(
                "did not start reporting: after the mark there is no state line in game.log for "
                + ", ".join(m.tag for m in waiting)
                + ". Is the mod schema v5 and the tag right? (hoi4-harness verify-bridge)", sent)
        session.sleep(VERIFY_SECONDS)


class _NotReporting(RivalStartError):
    def __init__(self, message: str, sent: bool):
        super().__init__(message)
        self.sent = sent


def finish(session: MultiRivalSession, members: list[Member], config: HarnessConfig,
           log: CampaignLog, lost: bool) -> None:
    """Unmark every rival in one window if one comes soon, else say what to type. Never raises."""
    lines = [f"effect {m.tag} llm_bridge_unmark_harness" for m in members]
    names = ", ".join(m.tag for m in members)
    if lost:
        log.line(f"the game is gone: {names} stay marked in the save, if there is one "
                 f"({'; '.join(lines)} undoes it)")
        return
    try:
        if session.send_in_window(lines, config.rival_exit_wait_seconds, "unmark"):
            log.line(f"{names} unmarked")
            return
    except Exception as exc:  # noqa: BLE001 - the exit must not hide how the run ended
        log.line(f"unmark failed: {type(exc).__name__}: {exc}")
    log.line(f"{names} are still marked (harmless: they keep logging). To undo it type in the "
             "console: " + "; ".join(lines))


# --- the rounds -----------------------------------------------------------------------------


def _wait(session: MultiRivalSession, members: list[Member], days: int) -> None:
    """ONE clock wait for every country: the log's date has moved ``days``, or something
    critical has happened to any of them. Never touches the clock; polls the session's windows."""
    lead = members[0].env.adapter

    def read_all():
        states = [getattr(m.env.adapter.reader, "peek_state", m.env.adapter.reader.read_state)()
                  for m in members]
        return SimpleNamespace(date=max(s.date for s in states),
                               events=[e for s in states for e in s.events])

    wait_for_days(lead._watched(read_all), days, lead.poll_seconds, **lead._wait)


def play_rounds(session: MultiRivalSession, members: list[Member], config: HarnessConfig,
                log: CampaignLog, until: str | None) -> str | None:
    """Every country decides in turn, one wait, every turn closed: until each is done. Returns
    why the run ended early (None: every country reached the end date or ran out of turns)."""
    stagger = max(0, config.rival_stagger_days)
    peace = config.campaign_review_peace_days
    for k, m in enumerate(members):
        m.loop.begin(days_since_planner=max(0, peace - k * stagger))
    rnd = 0
    while True:
        live = [m for m in members if not m.done]
        if not live:
            return None
        if all(m.loop.budget.why_blocked() for m in live):
            return "every country's budget is spent: " + "; ".join(
                f"{m.tag} {m.loop.budget.why_blocked()}" for m in live)
        k = rnd % len(live)
        for m in live[k:] + live[:k]:
            before = m.loop.budget.why_blocked()
            m.days = m.loop.decide(until)
            if before is None and m.loop.budget.why_blocked() and m.budget_stop is None:
                m.budget_stop = m.loop.budget.why_blocked()
                m.log.line(f"budget: {m.budget_stop}: {m.tag} stops deciding, the others go on")
                log.line(f"[{m.tag}] budget: {m.budget_stop}: stops deciding, the others go on")
            if m.days is None:
                m.done = True
        session.run_window()
        live = [m for m in live if not m.done]
        if not live:
            return None
        _wait(session, members, max(1, min(m.days for m in live)))
        for m in live:
            if m.loop.complete(m.env.read_state(), until):
                m.done = True
        rnd += 1


# --- running --------------------------------------------------------------------------------


def run_rivals(
    config: HarnessConfig,
    *,
    planner: LLMClient | None = None,
    planners: dict[str, LLMClient] | None = None,
    resume: bool = False,
    quiet: bool = False,
    echo: Callable[[str], None] | None = None,
    env: HOI4Env | None = None,
    until: str | None = None,
) -> tuple[RunReport, dict]:
    """Steer every country in ``config.rivals`` until ``until``, the turn backstop, or the
    first reason to stop (``GameLost``, every budget spent). The counterpart of ``run_rival``.

    ``planners`` is one model client per tag; ``planner`` one for all of them (a test's);
    neither: each country builds its own from the configuration, before anything is typed."""
    from ..adapters.headless import build_fake
    from ..agent.llm import build_llm

    tags = config.rivals
    got = {**{t: planner for t in tags}, **(planners or {})} if (planner or planners) else {}
    if not got and config.planner_enabled:
        got = {t: build_llm(config.planner) for t in tags}      # all of them, before any key
    run_dir = Path(config.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    out = echo or (lambda line: None)
    log = CampaignLog(run_dir / RIVAL_LOG, None if quiet else out)
    fake = getattr(env.adapter, "fake_game", None) if env is not None else None
    if fake is None and config.fake_game:
        fake = build_fake(config)
    members = build_members(config, fake, lambda tag: got.get(tag), top=log)
    human = [t for t in human_tags_in_log(config.log_path) if t in tags]
    if human:
        raise RivalStartError(f"{', '.join(human)} is the country you play (game.log shows it "
                              "as human): it cannot be a rival")
    session = build_session(members, config, log)
    for m in members:
        if resume:
            if (restored := m.queue.restore()):
                m.log.line(f"{restored} queued action(s) restored from the last run")
                log.line(f"[{m.tag}] {restored} queued action(s) restored from the last run")
        else:
            m.queue.clear()
        m.tracker = ProgressTracker(run_dir=m.run_dir, until=until, max_usd=m.share_usd,
                                    objective=m.config.objective, label="RIVAL")
        m.env.brief_hooks.append(m.tracker.brief_lines)
    cap = f"${config.budget.max_usd:.2f}" if config.budget.max_usd else "none"
    split = ", ".join(f"{m.tag} ${m.share_usd:.2f}" for m in members if m.share_usd is not None)
    log.line(f"rival {'resumed' if resume else 'started'}: steering {', '.join(tags)}, acts when "
             f"{config.rival_act_when}, until {until or 'stopped'}, cap {cap}"
             + (f" ({split})" if split else "") + f", model {config.planner.model}")

    try:
        begin(members, config, log, session)
        for m in members:
            m.env.reset()
    except (RivalStartError, GameLost) as exc:
        log.line(f"cannot start: {exc}")
        if getattr(exc, "sent", False):       # the mark went out: take it back
            finish(session, members, config, log, False)
        raise
    reason: str | None = None
    lost = False
    try:
        for m in members:
            m.loop = AgentLoop(
                env=m.env, planner=m.planner, config=m.config,
                memory=Memory.load(m.run_dir / "memory.json") if resume else Memory(),
                transcript_path=m.run_dir / "transcript.jsonl", resume=resume, progress=None,
                supervisor=MemberSupervisor(m, log))
        try:
            reason = play_rounds(session, members, config, log, until)
        except GameLost as exc:
            reason, lost = str(exc), True
            for m in members:
                m.loop.transcript.write("game_lost", reason=reason, date=m.loop.report.end_date)
    except Exception as exc:
        log.line(f"CRASH: {type(exc).__name__}: {exc}")
        for m in members:
            if m.loop is not None:
                m.loop.transcript.write("crash", error=f"{type(exc).__name__}: {exc}",
                                        trace=traceback.format_exc(limit=8))
        raise
    finally:
        for m in members:
            if m.loop is not None:
                m.loop.memory.save(m.run_dir / "memory.json")
                m.loop.report.spend = m.loop.budget.summary()
                m.loop.transcript.write("run_end", **m.loop.report.to_dict())
        finish(session, members, config, log, lost)
    return _conclude(members, session, config, log, reason, until)


def _conclude(members: list[Member], session: MultiRivalSession, config: HarnessConfig,
              log: CampaignLog, reason: str | None, until: str | None) -> tuple[RunReport, dict]:
    reports = [m.loop.report for m in members]
    dates = [r.end_date for r in reports if r.end_date]
    reached = bool(until) and all(r.reached_end_date for r in reports)
    stopped = reason or next((r.stopped_reason for r in reports if r.stopped_reason), None)
    per = {m.tag: m.counts() for m in members}
    total_usd = round(sum(c["usd"] for c in per.values()), 4)
    report = RunReport(
        turns=max(r.turns for r in reports), planner_calls=sum(r.planner_calls for r in reports),
        reflex_turns=sum(r.reflex_turns for r in reports),
        actions_ok=sum(r.actions_ok for r in reports),
        actions_failed=sum(r.actions_failed for r in reports),
        llm_errors=sum(r.llm_errors for r in reports),
        start_date=min((r.start_date for r in reports if r.start_date), default=""),
        end_date=max(dates, default=""), stopped_reason=stopped,
        reached_end_date=reached if until else None,
        spend={"usd": total_usd, "max_usd": config.budget.max_usd,
               "by_country": {t: c["usd"] for t, c in per.items()}})
    left = sum(c["left_queued"] for c in per.values())
    log.line("SUMMARY per country (queued / ran / stale / failed / still queued, spend of share):")
    for tag, c in per.items():
        share = f" of ${c['share_usd']:.2f}" if c["share_usd"] is not None else ""
        stop = f"  [{c['budget_stop']}]" if c["budget_stop"] else ""
        log.line(f"  {tag}: {c['queued']} / {c['ran']} / {c['stale']} / {c['failed']} / "
                 f"{c['left_queued']}  ${c['usd']:.2f}{share}, {c['planner_calls']} model call(s){stop}")
    log.line(f"STOP: {stopped or ('reached the end date' if reached else 'ran out of turns')} at "
             f"{report.end_date or '?'}; {session.windows} window(s) run, {left} action(s) still "
             f"queued; spent ${total_usd:.2f}"
             + (f" of ${config.budget.max_usd:.2f}" if config.budget.max_usd else ""))
    summary = {
        "stopped_reason": stopped, "reached_end_date": reached, "end_date": report.end_date,
        "start_date": report.start_date, "turns": report.turns, "spend": report.spend,
        "run_dir": str(config.run_dir), "rival": ",".join(config.rivals),
        "rivals": per, "windows": session.windows, "left_queued": left,
        "days_played": days_between(report.start_date, report.end_date),
    }
    Path(config.run_dir, SUMMARY_FILE).write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return report, summary

