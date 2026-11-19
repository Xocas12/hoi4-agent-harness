"""Verification through a reader that sees the game one tick at a time.

Seen live on 1.19.5: the driver started a focus in a paused game, the reader had
no new day to look at, and the action came back "rejected: the game did not
change" -- while the focus was running on screen.
"""

from hoi4_harness.adapters.ui_scripts import verify
from hoi4_harness.types import ActionCall, GameState

CALL = ActionCall("set_national_focus", {"focus_id": "SWE_urbanization"})


def state(ticks, focus=None):
    return GameState(date="1937-06-01", telemetry_ticks=ticks, national_focus=focus)


def run(after):
    return verify(CALL, state(5), lambda: after, timeout=0.0, sleep=lambda s: None)


def test_a_paused_game_is_unverified_not_rejected():
    outcome = run(state(5))
    assert not outcome.ok and outcome.kind == "unverified"
    assert "full day" in outcome.message


def test_the_day_already_open_at_the_input_proves_nothing():
    assert run(state(6)).kind == "unverified"


def test_a_day_begun_after_the_input_that_shows_no_change_is_rejected():
    assert run(state(7)).kind == "rejected"


def test_positive_evidence_verifies_at_once():
    assert run(state(5, focus="SWE_urbanization")).ok


def test_a_reader_that_sees_the_game_whole_still_rejects_at_once():
    whole = GameState(date="1937-06-01", national_focus=None)
    outcome = verify(CALL, GameState(date="1937-06-01"), lambda: whole, timeout=0.0,
                     sleep=lambda s: None)
    assert outcome.kind == "rejected"
