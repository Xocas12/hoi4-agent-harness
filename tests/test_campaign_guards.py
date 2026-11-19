"""The guards an unattended run keeps: the watchdog and the patient focus check."""

from __future__ import annotations

import pytest

from hoi4_harness.adapters.base import GameLost
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.campaign.watch import PatientFocus, Watchdog

# --- the guards ---------------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_the_watchdog_notices_a_log_that_stopped_growing(tmp_path):
    log, clock = tmp_path / "game.log", Clock()
    log.write_text("a\n")
    dog = Watchdog(log, silence_seconds=600, now=clock, check_every=1)
    dog.reset()
    for _ in range(9):                                      # nine quiet minutes: fine
        clock.sleep(60)
        dog.check()
    log.write_text("a\nb\n")                                 # a line: the clock starts again
    clock.sleep(300)
    dog.check()                                              # grew since: the silence restarts
    clock.sleep(300)
    dog.check()
    clock.sleep(300)
    with pytest.raises(GameLost, match="has not grown in 10 minutes"):
        dog.check()


def test_the_watchdog_notices_a_dead_process_and_ignores_an_unknown_one(tmp_path):
    clock, alive = Clock(), {"v": True}
    dog = Watchdog(tmp_path / "x.log", silence_seconds=0, process_alive=lambda: alive["v"],
                   now=clock, check_every=1)
    dog.reset()
    dog.check()
    alive["v"] = None                                        # cannot tell: not read as dead
    clock.sleep(5)
    dog.check()
    alive["v"] = False
    clock.sleep(5)
    with pytest.raises(GameLost, match="process is gone"):
        dog.check()


def test_the_focus_guard_waits_a_minute_at_a_time_and_then_lets_the_key_through():
    clock, away = Clock(), {"for": 3}
    said = []

    def check(title):
        away["for"] -= 1
        if away["for"] < 0:
            return FocusCheck(True, title)
        return FocusCheck(False, "Mail", reason="Mail")

    guard = PatientFocus(check, sleep=clock.sleep, retry_seconds=60, log=said.append)
    assert guard("Hearts of Iron IV").focused
    assert clock.t == 180 and guard.waited == 180 and guard.episodes == 1
    assert any("waiting" in s for s in said) and "back in front" in said[-1]


def test_the_focus_guard_gives_up_with_a_reason_after_the_limit():
    clock = Clock()
    guard = PatientFocus(lambda t: FocusCheck(False, "Mail", reason="focused window is Mail"),
                         sleep=clock.sleep, retry_seconds=60, max_wait_seconds=300)
    with pytest.raises(GameLost, match="has not come back to the front in 5 minutes"):
        guard("Hearts of Iron IV")
    assert clock.t == 300


def test_the_focus_guard_does_not_wait_where_the_platform_cannot_tell():
    clock = Clock()
    guard = PatientFocus(lambda t: FocusCheck(False, None, supported=False, reason="no xdotool"),
                         sleep=clock.sleep)
    assert not guard("x").focused and clock.t == 0


