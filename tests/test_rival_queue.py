"""The rival's queue: what it tells the model, what it re-checks, how a window ends.

A stub policy and a recording batch sender stand in for the safe window and the console, so each
rule is pinned alone. The same queue against the fake game with a person in it is test_rival_run.py.
"""

from __future__ import annotations

import pytest

from hoi4_harness.adapters.input_driver import InputInterrupted
from hoi4_harness.rival.queue import RivalQueue, rival_stale_reason
from hoi4_harness.rival.safety import Gate, KeyWatch, WindowPolicy
from hoi4_harness.types import ActionCall, ActionResult, GameState, War

DIRECTIVE = ActionCall("set_ai_directive", {"directive": "invade", "target": "POL"})
POSTURE = ActionCall("set_ai_posture", {"posture": "defensive"})
PROTECT = ActionCall("set_ai_directive", {"directive": "protect", "target": "FIN"})


class Policy(WindowPolicy):
    name = "paused"

    def __init__(self, open_=True):
        self.open, self.ended, self.reason = open_, 0, None

    def check(self):
        return Gate(True) if self.open else Gate(False, "not paused", "the game is not paused")

    def interrupted(self):
        return self.reason

    def end(self):
        self.ended += 1


def state(date="1936-02-01", **fields) -> GameState:
    return GameState(date=date, country="SWE", **fields)


def ok(call):
    return ActionResult(ok=True, action=call.name, call_id=call.call_id, message="done")


def queue_with(tmp_path, policy=None, stale_days=60):
    queue = RivalQueue(tmp_path, stale_days)
    policy = policy or Policy()
    queue.attach(policy, KeyWatch(policy))
    return queue, policy


def run(queue, st=None, batch=None, sent=None):
    sent = sent if sent is not None else []

    def apply_batch(calls):
        sent.append(list(calls))
        return batch(calls) if batch else [ok(c) for c in calls]

    return queue.run(lambda: st or state(), lambda c: ok(c), apply_batch)


# --- what the model is told -----------------------------------------------------------------


def test_a_queued_action_says_it_has_not_happened_and_names_when_it_will(tmp_path):
    queue, _ = queue_with(tmp_path)
    result = queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    assert result.ok and result.changed == {"queued": 1, "executed": False}
    assert "NOT done yet" in result.message and "pause" in result.message
    assert "days of game time" in result.message


def test_the_same_call_is_queued_once_and_the_model_is_told_not_to_repeat_it(tmp_path):
    queue, _ = queue_with(tmp_path)
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    again = queue.enqueue(DIRECTIVE, "1936-02-08", 2)
    assert "Already queued as #1" in again.message and len(queue.pending) == 1


def test_the_brief_lists_the_queue_and_the_queue_file_shows_it_to_the_person(tmp_path):
    queue, _ = queue_with(tmp_path)
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    assert queue.describe() == ["Queued for the person's next safe window (1): #1 set_ai_directive"]
    assert "set_ai_directive" in (tmp_path / "handover-queue.json").read_text(encoding="utf-8")


# --- one window, one batch ---------------------------------------------------------------------


def test_the_whole_queue_goes_out_as_one_batch(tmp_path):
    queue, policy = queue_with(tmp_path)
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    queue.enqueue(POSTURE, "1936-02-01", 1)
    sent: list = []
    report = run(queue, sent=sent)
    assert [len(batch) for batch in sent] == [2]
    assert [r["action"] for r in report.ran] == ["set_ai_directive", "set_ai_posture"]
    assert queue.pending == [] and policy.ended == 1


def test_no_open_window_sends_nothing_and_keeps_everything(tmp_path):
    queue, _ = queue_with(tmp_path, Policy(open_=False))
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    sent: list = []
    assert run(queue, sent=sent) is None and sent == [] and len(queue.pending) == 1
    assert queue.last_gate.kind == "not paused"


def test_an_empty_queue_does_not_open_a_window(tmp_path):
    queue, _ = queue_with(tmp_path)
    sent: list = []
    assert run(queue, sent=sent) is None and sent == []


def test_a_failed_action_is_reported_and_dropped_not_retried_forever(tmp_path):
    queue, _ = queue_with(tmp_path)
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)

    def fail(calls):
        return [ActionResult(ok=False, action=c.name, message="refused: no", error_kind="rejected")
                for c in calls]
    report = run(queue, batch=fail)
    assert report.failed[0]["reason"] == "refused: no" and queue.pending == []


# --- an interrupted window loses nothing ---------------------------------------------------------


def test_a_window_the_person_interrupts_keeps_every_action_queued_in_order(tmp_path):
    queue, _ = queue_with(tmp_path)
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    queue.enqueue(POSTURE, "1936-02-01", 1)

    def interrupted(calls):
        raise InputInterrupted("the person started using the keyboard or mouse")
    report = run(queue, batch=interrupted)
    assert report.aborted and "keyboard" in report.reason and report.left_queued == 2
    assert [p.call.name for p in queue.pending] == ["set_ai_directive", "set_ai_posture"]
    assert "stopped before it finished" in report.summary()


def test_a_window_that_found_the_game_out_of_focus_requeues_instead_of_failing(tmp_path):
    queue, _ = queue_with(tmp_path)
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)

    def unfocused(calls):
        return [ActionResult(ok=False, action=c.name, error_kind="rejected",
                             message="NotFocused: 'Hearts of Iron IV' is not focused; nothing was sent")
                for c in calls]
    report = run(queue, batch=unfocused)
    assert report.aborted and report.failed == [] and len(queue.pending) == 1


def test_the_guard_is_begun_and_ended_around_every_window_even_a_failing_one(tmp_path):
    policy = Policy()
    queue = RivalQueue(tmp_path)
    watch = KeyWatch(policy)
    queue.attach(policy, watch)
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    seen = []

    def spy(calls):
        seen.append(watch.active)
        raise InputInterrupted("x")
    run(queue, batch=spy)
    assert seen == [True] and not watch.active


# --- stale: re-checked against the world as it is when the window opens ---------------------------


@pytest.mark.parametrize("call, st, why", [
    (DIRECTIVE, state(ai_directives=["invade POL"]), "already standing"),
    (DIRECTIVE, state(ai_directives=["invade POL (100)"]), "already standing"),
    (PROTECT, state(wars=[War(against="FIN")]), "at war with FIN"),
    (ActionCall("set_ai_directive", {"directive": "befriend", "target": "FIN"}),
     state(wars=[War(against="FIN")]), "at war with FIN"),
    (POSTURE, state(posture="defensive"), "already defensive"),
    (ActionCall("set_ai_posture", {"posture": "offensive", "theater": "POL"}),
     state(theater_postures={"POL": "offensive"}), "POL theater is already offensive"),
    (ActionCall("clear_ai_directives", {}), state(), "nothing is standing"),
])
def test_actions_the_world_has_overtaken_are_stale(call, st, why):
    assert why in rival_stale_reason(call, st, st.date, 60)


@pytest.mark.parametrize("call, st", [
    (DIRECTIVE, state()),
    (DIRECTIVE, state(wars=[War(against="POL")])),          # invade a country we already fight
    (PROTECT, state(wars=[War(against="POL")])),
    (POSTURE, state(posture="offensive")),
    (ActionCall("clear_ai_directives", {}), state(ai_directives=["invade POL"])),
])
def test_actions_that_still_apply_are_not_stale(call, st):
    assert rival_stale_reason(call, st, st.date, 60) is None


def test_an_action_older_than_the_stale_days_is_stale_whatever_it_says(tmp_path):
    reason = rival_stale_reason(DIRECTIVE, state("1936-05-01"), "1936-02-01", 60)
    assert "queued 90 days ago" in reason
    assert rival_stale_reason(DIRECTIVE, state("1936-05-01"), "1936-02-01", 0) is None


def test_stale_actions_are_reported_and_never_sent(tmp_path):
    queue, _ = queue_with(tmp_path)
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    queue.enqueue(POSTURE, "1936-02-01", 1)
    sent: list = []
    report = run(queue, st=state(posture="defensive"), sent=sent)
    assert [s["action"] for s in report.stale] == ["set_ai_posture"]
    assert [c.name for c in sent[0]] == ["set_ai_directive"]
    assert "1 stale: set_ai_posture (the posture is already defensive)" in report.summary()


def test_a_window_of_nothing_but_stale_actions_types_nothing(tmp_path):
    queue, policy = queue_with(tmp_path)
    queue.enqueue(POSTURE, "1936-02-01", 1)
    sent: list = []
    report = run(queue, st=state(posture="defensive"), sent=sent)
    assert sent == [] and report.stale and policy.ended == 1


# --- never: advice only ----------------------------------------------------------------------------


def test_under_never_the_result_says_it_will_not_run_and_the_queue_is_shown_then_dropped(tmp_path):
    class Never(WindowPolicy):
        name, advice_only = "never", True

    queue = RivalQueue(tmp_path)
    queue.attach(Never(), None)
    result = queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    assert "will NOT be run" in result.message and "advice only" in result.message
    sent: list = []
    report = run(queue, sent=sent)
    assert sent == [] and queue.pending == []
    assert [a["action"] for a in report.advised] == ["set_ai_directive"]
    assert "not run" in report.summary()


# --- across restarts ----------------------------------------------------------------------------------


def test_a_resumed_run_reloads_what_the_person_was_shown_as_queued(tmp_path):
    queue, _ = queue_with(tmp_path)
    queue.enqueue(DIRECTIVE, "1936-02-01", 1)
    fresh, _ = queue_with(tmp_path)
    assert fresh.restore() == 1 and fresh.pending[0].call.name == "set_ai_directive"
