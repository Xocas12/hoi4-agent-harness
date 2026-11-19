"""The rival's action queue: decide whenever, type only in a safe window, in one batch.

The queue is co-op's (handover.py): every action is queued, the tool result says it has not
happened, and each is re-checked before it runs. What differs is when it runs and how:

* **when**: a ``WindowPolicy`` (safety.py) says whether a window is open, instead of a grant
  file alone, and a ``KeyWatch`` stops the window between keys if the person comes back;
* **how**: the whole queue goes out as ONE console run file, so a window is one pass of the
  same few keys (console key, ``run llmb_turn.txt``, enter, console key) however many
  actions it holds, which is as little time at the person's keyboard as it can be;
* **an interrupted window loses nothing**: the actions stay queued, in order, for the next
  one. The run file may be on disk, but it is only ever run by the ``enter`` that was never
  sent.

Stale rules are the rival's own on top of co-op's: a directive already standing, a posture
already set, nothing to clear, ``protect`` or ``befriend`` aimed at a country we are now at
war with, and anything older than ``rival_stale_days`` in-game days, since the world the model
decided in is gone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.clock import days_between
from ..adapters.input_driver import InputInterrupted
from ..handover import HandoverQueue, HandoverReport, Pending, stale_reason
from ..types import ActionCall, ActionResult, GameState
from .safety import Gate, KeyWatch, WindowPolicy

#: The console left a window with these in the message: the game lost the focus mid-window.
_NOT_TYPED = ("is not focused", "refusing to send input")


@dataclass
class RivalReport(HandoverReport):
    """One window, for the transcript and the next brief."""

    policy: str = "paused"
    #: Why an interrupted window stopped (the person typed, unpaused, withdrew the grant).
    reason: str = ""
    #: ``never``: what was shown to the person instead of run.
    advised: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        if self.advised:
            return (f"Window on {self.granted_on}: policy never, {len(self.advised)} action(s) "
                    "shown to the person and not run: "
                    + "; ".join(a["action"] for a in self.advised[:4]))
        bits = [f"{len(self.ran)} ran"]
        if self.stale:
            bits.append(f"{len(self.stale)} stale: " + "; ".join(
                f"{s['action']} ({s['reason']})" for s in self.stale[:3]))
        if self.failed:
            bits.append(f"{len(self.failed)} failed: " + "; ".join(
                f"{f['action']} ({f['reason']})" for f in self.failed[:3]))
        if self.aborted:
            bits.append(f"stopped before it finished ({self.reason or 'the person came back'}), "
                        f"{self.left_queued} still queued")
        return f"Window on {self.granted_on}: " + ", ".join(bits)


def rival_stale_reason(call: ActionCall, state: GameState, queued_on: str,
                       stale_days: int) -> str | None:
    """Why a queued action no longer applies, judged against the state now."""
    if stale_days and queued_on:
        age = days_between(queued_on, state.date)
        if age > stale_days:
            return f"queued {age} days ago (more than {stale_days}): the world it was meant for is gone"
    args = call.arguments
    standing = {tuple(entry.split()[:2]) for entry in state.ai_directives}
    enemies = {w.against for w in state.wars} if state.known("wars") else set()
    if call.name == "set_ai_directive":
        directive, target = str(args.get("directive")), str(args.get("target", "")).upper()
        if (directive, target) in standing:
            return f"'{directive} {target}' is already standing"
        if directive in ("protect", "befriend") and target in enemies:
            return f"at war with {target} now: {directive} no longer makes sense"
    elif call.name == "set_ai_posture":
        posture, theater = str(args.get("posture")), args.get("theater")
        if theater and state.theater_postures.get(str(theater).upper()) == posture:
            return f"the {theater} theater is already {posture}"
        if not theater and state.posture == posture:
            return f"the posture is already {posture}"
    elif call.name == "clear_ai_directives":
        if not (state.ai_directives or state.posture or state.theater_postures):
            return "nothing is standing to clear"
    return stale_reason(call, state)


class RivalQueue(HandoverQueue):
    """The handover queue, run by a policy and sent as one batch."""

    def __init__(self, run_dir: Path, stale_days: int = 60):
        super().__init__(run_dir)
        self.stale_days = stale_days
        self.policy: WindowPolicy | None = None
        self.watch: KeyWatch | None = None
        #: What the last attempt to open a window found, for the log.
        self.last_gate: Gate | None = None

    def attach(self, policy: WindowPolicy, watch: KeyWatch | None) -> None:
        self.policy, self.watch = policy, watch

    # --- the grant file is the handover policy's, not the queue's ---------------------

    def granted(self) -> bool:
        gate = self.last_gate or (self.policy.check() if self.policy else None)
        return bool(gate and gate.ok)

    # --- queueing ---------------------------------------------------------------------

    def _when(self) -> str:
        return {"paused": "the person's next pause", "handover": "the person's next handover",
                "never": "the person to do it themselves"}.get(
                    self.policy.name if self.policy else "paused", "a safe window")

    def enqueue(self, call: ActionCall, date: str, turn: int) -> ActionResult:
        advice = self.policy is not None and self.policy.advice_only
        for entry in self.pending:
            if entry.call.name == call.name and entry.call.arguments == call.arguments:
                return ActionResult(
                    ok=True, action=call.name, call_id=call.call_id,
                    message=(f"Already queued as #{entry.position} for {self._when()}. NOT done "
                             "yet. Do not plan as if it had happened, and do not repeat it."),
                    changed={"queued": entry.position, "executed": False})
        entry = Pending(call=call, queued_on=date, queued_turn=turn, position=self._next)
        self._next += 1
        self.pending.append(entry)
        self._publish()
        if advice:
            message = (f"Shown to the person as #{entry.position}. It will NOT be run: this "
                       "game is set to advice only. Do not plan as if it had happened.")
        else:
            message = (f"Queued as #{entry.position}. NOT done yet: it runs in {self._when()}, "
                       "after a check that it still applies, which may be days of game time "
                       "away (or never, if the person never pauses). Do not plan as if it had "
                       "happened.")
        return ActionResult(ok=True, action=call.name, call_id=call.call_id, message=message,
                            changed={"queued": entry.position, "executed": False})

    def describe(self) -> list[str]:
        if not self.pending:
            return []
        items = ", ".join(f"#{p.position} {p.call.name}" for p in self.pending[:6])
        more = f" (+{len(self.pending) - 6})" if len(self.pending) > 6 else ""
        advice = self.policy is not None and self.policy.advice_only
        head = "Shown to the person, not run" if advice else "Queued for the person's next safe window"
        return [f"{head} ({len(self.pending)}): {items}{more}"]

    # --- the window -------------------------------------------------------------------

    def run(
        self,
        read_state: Callable[[], GameState],
        apply: Callable[[ActionCall], ActionResult],
        apply_batch: Callable[[list[ActionCall]], list[ActionResult]] | None = None,
    ) -> RivalReport | None:
        """Run the queue if a window is open. None if none was (or nothing was queued)."""
        policy = self.policy
        if policy is None:
            return None
        if not self.pending:
            policy.idle()
            return None
        if policy.advice_only:
            return self._advise(read_state)
        self.last_gate = policy.check()
        if not self.last_gate.ok:
            return None
        state = read_state()
        report, runnable = self.triage(state)
        results: list[ActionResult] | None = None
        if runnable:
            calls = [e.call for e in runnable]
            if self.watch is not None:
                self.watch.begin()
            try:
                results = (apply_batch(calls) if apply_batch is not None
                           else [apply(c) for c in calls])
            except InputInterrupted as exc:
                report.aborted, report.reason = True, str(exc)
            finally:
                if self.watch is not None:
                    self.watch.end()
                else:
                    policy.end()
        else:
            policy.end()
        self.settle(report, runnable, results)
        return report

    def triage(self, state: GameState, limit: int | None = None) -> tuple[RivalReport, list[Pending]]:
        """Drop what is stale against ``state`` (into the report) and return what may run.

        ``limit`` keeps at most that many of the survivors, oldest first; the rest stay queued
        for the next window (several rivals share one window, rival/multi.py)."""
        report = RivalReport(granted_on=state.date, policy=self.policy.name if self.policy else "paused")
        runnable: list[Pending] = []
        for entry in list(self.pending):
            why = rival_stale_reason(entry.call, state, entry.queued_on, self.stale_days)
            if why:
                self.pending.remove(entry)
                report.stale.append({"action": entry.call.name, "arguments": entry.call.arguments,
                                     "queued_on": entry.queued_on, "ran_on": state.date,
                                     "reason": why})
            else:
                runnable.append(entry)
        return report, runnable if limit is None else runnable[:max(0, limit)]

    def settle(self, report: RivalReport, runnable: list[Pending],
               results: list[ActionResult] | None) -> None:
        """Book what a window did: ``results`` (None: nothing was sent) are parallel to
        ``runnable``. Whatever never reached the game stays queued."""
        if results is not None:
            for entry, result in zip(runnable, results, strict=True):
                info = {"action": entry.call.name, "arguments": entry.call.arguments,
                        "queued_on": entry.queued_on, "ran_on": report.granted_on}
                if not result.ok and any(t in result.message.lower() for t in _NOT_TYPED):
                    report.aborted, report.reason = True, "the game window was not in front"
                    continue              # stays queued: nothing reached the game
                self.pending.remove(entry)
                if result.ok:
                    report.ran.append(info)
                else:
                    report.failed.append({**info, "reason": result.message})
        report.left_queued = len(self.pending)
        self._publish()
        self.reports.append(report)

    def _advise(self, read_state: Callable[[], GameState]) -> RivalReport:
        """``never``: show what the model asked for and drop it, so the queue does not pile up."""
        state = read_state()
        report = RivalReport(granted_on=state.date, policy="never")
        for entry in self.pending:
            report.advised.append({"action": entry.call.name, "arguments": entry.call.arguments,
                                   "rationale": entry.call.rationale, "queued_on": entry.queued_on})
        self.pending = []
        self._publish()
        self.reports.append(report)
        return report
