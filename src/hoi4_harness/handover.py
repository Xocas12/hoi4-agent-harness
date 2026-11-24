"""Action handover: the harness must not type over the player (#33).

With a person playing, synthetic input lands in whatever panel they have open,
at whatever moment the loop decided to act. The window-focus guard does not
help -- the game *is* focused, it is just in use. Two people cannot share a
keyboard, so this is turn-taking:

* the agent decides on its own schedule, and every action it takes is
  **queued**, not executed -- the tool result says so, so it does not plan on
  a change that has not happened;
* the queue runs only when the player **grants** a window, and stops the moment
  they take it back, leaving whatever had not run still queued;
* each action is **re-checked** before it runs, because the world moved while
  it waited: a focus queued last week may be pointless now that one is running.
  Those are reported as stale, never clicked blindly -- the same "act, verify,
  report" contract as the input driver;
* every window is recorded: when it was granted, what ran, what went stale and
  how long each action waited. A model whose plans routinely go stale before
  they run is saying the handover cadence is wrong.

The grant is a file. ``hoi4-harness handover`` creates it (bind that to a hotkey
with whatever your OS offers) and the harness deletes it when the queue has
run; deleting it yourself mid-run takes the keyboard back. A file is the one
signal every platform, every terminal and every hotkey tool can produce without
a keyboard hook in this process, which would itself be input contention.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .adapters.input_driver import InputInterrupted
from .types import ActionCall, ActionResult, GameState

GRANT_FILENAME = "handover.grant"
QUEUE_FILENAME = "handover-queue.json"

#: Never queued: they change nothing the player could be clicking on.
PASS_THROUGH = frozenset({"note", "advance_time"})


@dataclass
class Pending:
    call: ActionCall
    queued_on: str          # game date
    queued_turn: int
    position: int
    #: Wall-clock time it was queued (``HandoverQueue.clock``), so the report can say how long
    #: it waited. 0: not known (an older queue file).
    queued_at: float = 0.0


@dataclass
class HandoverReport:
    """One granted window, for the transcript and the next brief."""

    granted_on: str
    ran: list[dict] = field(default_factory=list)
    stale: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    aborted: bool = False
    left_queued: int = 0
    #: The player said no to these (approve mode); a decision, so it is recorded.
    declined: list[dict] = field(default_factory=list)
    #: Which trigger opened the window: ``hotkey``, ``idle`` or ``approve``.
    mode: str = "hotkey"
    #: Wall-clock time the window opened, and how many seconds it stayed open.
    granted_at: float = 0.0
    window_seconds: float = 0.0
    #: Why an aborted window stopped (the player came back, took the grant away).
    stopped_because: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        bits = [f"{len(self.ran)} ran"]
        if self.stale:
            bits.append(f"{len(self.stale)} stale: " + "; ".join(
                f"{s['action']} ({s['reason']})" for s in self.stale[:3]))
        if self.failed:
            bits.append(f"{len(self.failed)} failed: " + "; ".join(
                f"{f['action']} ({f['reason']})" for f in self.failed[:3]))
        if self.declined:
            bits.append(f"{len(self.declined)} declined by the player: " + "; ".join(
                d["action"] for d in self.declined[:3]))
        if self.aborted:
            bits.append("player took control back"
                        + (f" ({self.stopped_because})" if self.stopped_because else "")
                        + f", {self.left_queued} still queued")
        return f"Handover on {self.granted_on}: " + ", ".join(bits)


def stale_reason(call: ActionCall, state: GameState) -> str | None:
    """Why a queued action no longer applies, judged against the state now.

    Only checks the harness can make from ``GameState`` without guessing; the
    adapter's own verification catches the rest when the action runs.
    """
    args = call.arguments
    if call.name == "set_national_focus" and state.known("national_focus") and state.national_focus:
        return f"'{state.national_focus}' is already running"
    if call.name == "set_national_focus" and args.get("focus_id") in state.completed_focuses:
        return "that focus is already complete"
    if call.name == "start_research" and state.known("research"):
        if not any(slot.technology is None for slot in state.research):
            return "no research slot is free any more"
        if any(slot.technology == args.get("technology") for slot in state.research):
            return "already being researched"
    if call.name == "set_production" and state.known("military_factories"):
        military = [line for line in state.production if line.kind == "military"]
        if not state.known("production") or any(line.factories is None for line in military):
            return None
        others = sum(line.factories for line in military
                     if line.equipment != args.get("equipment"))
        if others + int(args.get("factories", 0)) > state.military_factories:
            return "not enough free military factories any more"
    return None


class HandoverQueue:
    def __init__(self, run_dir: Path, clock: Callable[[], float] = time.time):
        self.run_dir = Path(run_dir)
        self.pending: list[Pending] = []
        self.reports: list[HandoverReport] = []
        self._next = 1
        #: Wall clock for "how long did it wait"; injectable so a test needs no real time.
        self.clock = clock
        #: A ``WindowPolicy`` (rival/safety.py, coop.py) that decides when the queue may run and
        #: whether the player has come back mid-queue. None: the grant file alone.
        self.window = None
        #: A ``KeyWatch`` that bounds a window for the input driver, when one is wired.
        self.watch = None
        #: ``callable(Confirmation) -> bool``: per-action approval (approve mode).
        self.approver: Callable | None = None
        self.mode = "hotkey"

    def use_window(self, policy, watch=None, *, mode: str | None = None) -> None:
        """Run by ``policy`` instead of the grant file alone (see coop.py)."""
        self.window, self.watch = policy, watch
        if mode:
            self.mode = mode

    # --- the grant ------------------------------------------------------------

    @property
    def grant_path(self) -> Path:
        return self.run_dir / GRANT_FILENAME

    def granted(self) -> bool:
        if self.window is not None:
            return self.window.check().ok
        return self.grant_path.exists()

    def grant(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.grant_path.touch()

    def _release(self) -> None:
        try:
            self.grant_path.unlink()
        except FileNotFoundError:
            pass

    # --- queueing -------------------------------------------------------------

    def enqueue(self, call: ActionCall, date: str, turn: int) -> ActionResult:
        # A queued action changes nothing until the window opens, so whatever
        # asked for it (a reflex, most often) asks again next turn. Queue it
        # once: running the same call N times is not what anyone decided.
        for entry in self.pending:
            if entry.call.name == call.name and entry.call.arguments == call.arguments:
                return ActionResult(
                    ok=True,
                    action=call.name,
                    call_id=call.call_id,
                    message=(
                        f"Already queued as #{entry.position} for the player's next handover. "
                        "NOT done yet. Do not plan as if it had happened."
                    ),
                    changed={"queued": entry.position, "executed": False},
                )
        entry = Pending(call=call, queued_on=date, queued_turn=turn, position=self._next,
                        queued_at=self.clock())
        self._next += 1
        self.pending.append(entry)
        self._publish()
        return ActionResult(
            ok=True,
            action=call.name,
            call_id=call.call_id,
            message=(
                f"Queued as #{entry.position} for the player's next handover. NOT done "
                "yet: it runs only when the player grants control, and is re-checked "
                "then. Do not plan as if it had happened."
            ),
            changed={"queued": entry.position, "executed": False},
        )

    def _publish(self) -> None:
        """The visible indicator: what the harness is about to do, in a file the
        player (or an overlay) can read at any moment."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload = [
            {"position": p.position, "action": p.call.name, "arguments": p.call.arguments,
             "queued_on": p.queued_on, "queued_turn": p.queued_turn, "queued_at": p.queued_at,
             "rationale": p.call.rationale}
            for p in self.pending
        ]
        (self.run_dir / QUEUE_FILENAME).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def restore(self) -> int:
        """Reload the queue a stopped run left behind (``play --resume``).

        The file is what the player was shown, so it is what they expect to run
        when they hand over. Returns how many actions came back.
        """
        path = self.run_dir / QUEUE_FILENAME
        if not path.exists():
            return 0
        items = json.loads(path.read_text(encoding="utf-8"))
        self.pending = [
            Pending(
                call=ActionCall(name=item["action"], arguments=item.get("arguments") or {},
                                rationale=item.get("rationale")),
                queued_on=item.get("queued_on", ""),
                queued_turn=int(item.get("queued_turn", 0)),
                position=int(item["position"]),
                queued_at=float(item.get("queued_at") or 0.0),
            )
            for item in items
        ]
        self._next = max((p.position for p in self.pending), default=0) + 1
        return len(self.pending)

    def clear(self) -> None:
        """Start a fresh run with an empty queue, not the last run's leftovers."""
        self.pending = []
        self._publish()

    def describe(self) -> list[str]:
        if not self.pending:
            return []
        items = ", ".join(f"#{p.position} {p.call.name}" for p in self.pending[:6])
        more = f" (+{len(self.pending) - 6})" if len(self.pending) > 6 else ""
        return [f"Queued for handover ({len(self.pending)}): {items}{more}"]

    # --- the window -----------------------------------------------------------

    def run(
        self,
        read_state: Callable[[], GameState],
        apply: Callable[[ActionCall], ActionResult],
        apply_batch: Callable[[list[ActionCall]], list[ActionResult]] | None = None,
    ) -> HandoverReport | None:
        """Run the queue if the player has granted a window. None if not.

        ``apply_batch`` is for queues that send everything at once (rival mode); this one
        runs its actions one at a time and ignores it."""
        if self.window is not None and not self.pending:
            self.window.idle()
            return None
        if not self.granted():
            return None
        opened = self.clock()
        report = HandoverReport(granted_on=read_state().date, mode=self.mode, granted_at=opened)
        guard = self.watch if self.watch is not None else self.window
        if guard is not None:
            guard.begin()
        try:
            while self.pending:
                why = self._stop_reason()
                if why:
                    # The player took the keyboard back: stop between actions and
                    # keep the rest, rather than finishing what we started.
                    report.aborted, report.stopped_because = True, why
                    break
                entry = self.pending.pop(0)
                state = read_state()
                waited = {"action": entry.call.name, "arguments": entry.call.arguments,
                          "queued_on": entry.queued_on, "ran_on": state.date}
                if entry.queued_at:
                    waited["waited_seconds"] = round(max(0.0, self.clock() - entry.queued_at), 1)
                reason = stale_reason(entry.call, state)
                if reason:
                    report.stale.append({**waited, "reason": reason})
                    continue
                if self.approver is not None and not self._approved(entry, state):
                    report.declined.append(waited)
                    continue
                why = self._stop_reason()       # again: approval and the state read take time
                if why:
                    self.pending.insert(0, entry)
                    report.aborted, report.stopped_because = True, why
                    break
                try:
                    result = apply(entry.call)
                except InputInterrupted as exc:
                    # A guard stopped the keys: nothing reached the game for this one.
                    self.pending.insert(0, entry)
                    report.aborted, report.stopped_because = True, str(exc)
                    break
                if self.window is not None:
                    self.window.note_own_input()
                if result.ok:
                    report.ran.append(waited)
                else:
                    report.failed.append({**waited, "reason": result.message})
        finally:
            if guard is not None:
                guard.end()
        report.left_queued = len(self.pending)
        report.window_seconds = round(max(0.0, self.clock() - opened), 1)
        if not report.aborted:
            self._release()
        self._publish()
        self.reports.append(report)
        return report

    def _stop_reason(self) -> str | None:
        """Why the window must close before the next action, or None."""
        if self.window is not None:
            return self.window.interrupted()
        return None if self.granted() else "the player withdrew the grant"

    def _approved(self, entry: Pending, state: GameState) -> bool:
        from .confirm import Confirmation

        return bool(self.approver(Confirmation(call=entry.call, date=state.date,
                                               turn=entry.queued_turn)))
