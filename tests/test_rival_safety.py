"""Rival mode's input safety, in pieces: the person's idle clock, the pause detector, the three
window policies and the guard the input driver calls before every key.

The end-to-end behaviour (a person pausing, typing, declaring war) is in test_rival_run.py; here
each rule is pinned alone, with injected clocks, so a failure says which rule broke.
"""

from __future__ import annotations

import pytest

from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter, InputInterrupted
from hoi4_harness.adapters.keys import ScanCodeKeys
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.rival import safety
from hoi4_harness.rival.safety import (
    HandoverPolicy,
    InputWatch,
    KeyWatch,
    LogClock,
    NeverPolicy,
    PausedPolicy,
    system_input_watch,
)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def stamp(date: str, hour: int = 21) -> str:
    y, m, d = date.split("-")
    return f"[12:00:00][{y}.{int(m)}.{int(d)}.{hour}]: LLMB|v5|state|tag=GER"


# --- the person's idle clock ----------------------------------------------------------


def test_idle_seconds_is_now_minus_the_last_input_stamp():
    watch = InputWatch(lambda: 10_000, lambda: 17_500)
    assert watch.supported and watch.idle_seconds() == 7.5


def test_the_tick_counter_wrapping_does_not_make_idle_time_negative():
    watch = InputWatch(lambda: 2**32 - 1000, lambda: 500)     # GetTickCount rolled over
    assert watch.idle_seconds() == 1.5


def test_a_platform_that_cannot_say_is_unsupported_and_idle_is_unknown():
    watch = InputWatch()
    assert not watch.supported and watch.idle_seconds() is None and watch.tick() is None


def test_system_input_watch_is_unsupported_off_windows(monkeypatch):
    monkeypatch.setattr(safety.sys, "platform", "linux")
    assert not system_input_watch().supported


# --- whether the game is paused ----------------------------------------------------------


def test_the_log_clock_reads_the_last_engine_date_from_the_end_of_the_log(tmp_path):
    log = tmp_path / "game.log"
    log.write_text("\n".join([stamp("1936-01-01"), "garbage", stamp("1936-01-05")]) + "\n",
                   encoding="utf-8")
    assert LogClock(log, Clock()).last_date() == "1936-01-05"


def test_the_log_clock_counts_stillness_from_the_first_time_it_saw_the_date(tmp_path):
    log, now = tmp_path / "game.log", Clock()
    log.write_text(stamp("1936-01-01") + "\n", encoding="utf-8")
    clock = LogClock(log, now)
    assert clock.still_for() == 0.0           # just started: nothing is known about a pause
    now.t = 12.0
    assert clock.still_for() == 12.0
    with log.open("a", encoding="utf-8") as handle:
        handle.write(stamp("1936-01-02") + "\n")
    now.t = 13.0
    assert clock.still_for() == 0.0           # the date moved: the game is running


def test_a_log_with_no_date_is_not_a_pause(tmp_path):
    assert LogClock(tmp_path / "missing.log", Clock()).still_for() is None


# --- the paused policy -----------------------------------------------------------------------


class World:
    """Everything the paused policy asks, as plain attributes a test can set."""

    def __init__(self, tmp_path):
        self.now = Clock()
        self.log = tmp_path / "game.log"
        self.log.write_text(stamp("1936-01-01") + "\n", encoding="utf-8")
        self.last_input = 0
        self.focused = True
        self.clock = LogClock(self.log, self.now)
        self.watch = InputWatch(lambda: self.last_input, lambda: int(self.now.t * 1000))
        self.policy = PausedPolicy(self.clock, self.watch, self.focus, paused_seconds=10,
                                   idle_seconds=5)
        self.clock.update()

    def focus(self):
        return FocusCheck(self.focused, "Hearts of Iron IV")

    def advance(self, seconds):
        self.now.t += seconds

    def date(self, iso):
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(stamp(iso) + "\n")


def test_the_paused_policy_opens_only_when_all_three_conditions_hold(tmp_path):
    w = World(tmp_path)
    assert w.policy.check().kind == "not paused"             # nothing has stood still yet
    w.advance(11)
    w.last_input = 8_000                                     # someone pressed a key at t=8
    assert w.policy.check().kind == "typing"
    w.last_input = 0
    w.focused = False
    assert w.policy.check().kind == "not in front"
    w.focused = True
    assert w.policy.check().ok


def test_a_running_game_is_never_a_window_however_idle_the_keyboard_is(tmp_path):
    w = World(tmp_path)
    for day in range(2, 8):
        w.advance(3)
        w.date(f"1936-01-{day:02d}")
        assert w.policy.check().kind == "not paused"


def test_without_an_idle_clock_the_paused_policy_refuses_rather_than_guesses(tmp_path):
    w = World(tmp_path)
    w.policy.watch = InputWatch()
    w.advance(30)
    gate = w.policy.check()
    assert not gate.ok and gate.kind == "no idle clock" and "handover" in gate.text


def test_a_window_is_interrupted_when_the_date_moves_or_the_person_types_or_focus_goes(tmp_path):
    w = World(tmp_path)
    w.advance(30)
    w.policy.begin()
    assert w.policy.interrupted() is None
    w.last_input = 31_000
    assert "keyboard" in w.policy.interrupted()
    w.policy.note_own_input()                                # our own key moved the stamp
    assert w.policy.interrupted() is None
    w.focused = False
    assert "focus" in w.policy.interrupted()
    w.focused = True
    w.date("1936-01-02")
    assert "unpaused" in w.policy.interrupted()


# --- the other two policies ----------------------------------------------------------------


def test_the_handover_policy_follows_the_grant_file_and_releases_it_afterwards(tmp_path):
    grant = tmp_path / "handover.grant"
    policy = HandoverPolicy(grant)
    assert policy.check().kind == "no grant"
    grant.touch()
    assert policy.check().ok and policy.interrupted() is None
    grant.unlink()
    assert "took the keyboard back" in policy.interrupted()
    grant.touch()
    policy.end()
    assert not grant.exists()


def test_a_grant_with_nothing_queued_is_used_up(tmp_path):
    grant = tmp_path / "handover.grant"
    grant.touch()
    HandoverPolicy(grant).idle()
    assert not grant.exists()


def test_the_never_policy_never_opens_and_says_it_is_advice_only():
    policy = NeverPolicy()
    assert policy.advice_only and not policy.check().ok


# --- the guard ---------------------------------------------------------------------------------


class Stub(safety.WindowPolicy):
    name = "stub"

    def __init__(self):
        self.reason = None
        self.begun = self.ended = 0
        self.own = 0

    def begin(self):
        self.begun += 1

    def end(self):
        self.ended += 1

    def note_own_input(self):
        self.own += 1

    def interrupted(self):
        return self.reason


def test_the_guard_passes_everything_outside_a_window():
    stub = Stub()
    stub.reason = "ignored"
    KeyWatch(stub).before("press", "x")          # not begun: nothing raised


def test_the_guard_stops_at_the_next_key_once_the_policy_says_so_and_keeps_stopping():
    stub = Stub()
    watch = KeyWatch(stub, "grave")
    watch.begin()
    watch.before("press", "grave")               # opening the console: fine
    watch.after("press", "grave")
    watch.before("type", "r")
    stub.reason = "the person started using the keyboard or mouse"
    with pytest.raises(InputInterrupted):
        watch.before("type", "u")
    stub.reason = None                           # the person stopped: still no more typing
    with pytest.raises(InputInterrupted):
        watch.before("press", "enter")
    assert watch.tripped and stub.begun == 1


def test_the_console_key_that_closes_our_console_always_goes_through():
    stub = Stub()
    watch = KeyWatch(stub, "grave")
    watch.begin()
    watch.before("press", "grave")
    watch.after("press", "grave")                # we opened it
    stub.reason = "interrupted"
    with pytest.raises(InputInterrupted):
        watch.before("type", "r")
    watch.before("press", "grave")               # the cleanup: no raise
    watch.after("press", "grave")
    with pytest.raises(InputInterrupted):
        watch.before("press", "grave")           # a second open is not the cleanup


def test_a_guard_that_stops_the_opening_key_sends_nothing_and_there_is_nothing_to_close():
    stub = Stub()
    stub.reason = "interrupted"
    watch = KeyWatch(stub, "grave")
    watch.begin()
    with pytest.raises(InputInterrupted):
        watch.before("press", "grave")
    assert watch._console_open is False


def test_ending_a_window_ends_the_policys_too():
    stub = Stub()
    watch = KeyWatch(stub)
    watch.begin()
    watch.end()
    assert stub.ended == 1 and not watch.active


def test_own_keys_are_reported_to_the_policy_only_inside_a_window():
    stub = Stub()
    watch = KeyWatch(stub)
    watch.own_input()
    watch.begin()
    watch.own_input()
    assert stub.own == 1


# --- through the input driver ------------------------------------------------------------------


def make_driver(stub):
    sent = []
    keys = ScanCodeKeys(send=lambda scan, up: sent.append((scan, up)), sleep=lambda s: None)
    driver = InputDriverAdapter(InputConfig(enforce_focus=False), dry_run=False, keys=keys,
                                sleep=lambda s: None)
    watch = KeyWatch(stub, "grave")
    driver.key_watch = watch
    keys.on_send, keys.before_send = watch.own_input, watch.check
    return driver, watch, sent


def test_typed_text_is_guarded_character_by_character():
    stub = Stub()
    driver, watch, sent = make_driver(stub)
    watch.begin()
    driver.type_text("ab")
    assert len([1 for _, up in sent if not up]) == 2
    stub.reason = "interrupted"
    before = len(sent)
    with pytest.raises(InputInterrupted):
        driver.type_text("cdef")
    assert len(sent) == before               # not even the first character went out


def test_a_person_typing_between_two_characters_stops_the_rest_of_the_text():
    stub = Stub()
    driver, watch, sent = make_driver(stub)
    watch.begin()
    typed = []
    original = watch.before

    def before(kind, text):
        if kind == "type" and text == "c":
            stub.reason = "the person started using the keyboard or mouse"
        original(kind, text)
        typed.append(text)
    watch.before = before
    with pytest.raises(InputInterrupted):
        driver.type_text("abcd")
    assert typed == ["a", "b"]                # "c" was refused, "d" never asked


def test_the_driver_counts_every_key_event_of_its_own_as_its_own_input():
    stub = Stub()
    driver, watch, sent = make_driver(stub)
    watch.begin()
    driver.press("grave")
    assert stub.own == len(sent) == 2         # one down, one up


def test_untypable_text_is_refused_before_any_key_goes_out():
    stub = Stub()
    driver, watch, sent = make_driver(stub)
    watch.begin()
    with pytest.raises(ValueError):
        driver.type_text("abö")
    assert sent == []


def test_a_keypress_inside_one_of_our_chords_is_seen_at_the_next_event_and_never_leaves_a_key_down():
    stub = Stub()
    driver, watch, sent = make_driver(stub)
    watch.begin()
    events = []
    real = driver._keys()._send

    def spy(scan, up):
        events.append((scan, up))
        if not up:
            stub.reason = "the person started using the keyboard or mouse"   # between down and up
        real(scan, up)
    driver._keys()._send = spy
    driver.press("grave")                       # the chord completes: its key-up still goes out
    assert events == [(0x29, False), (0x29, True)]
    assert watch.tripped
    with pytest.raises(InputInterrupted):
        driver.press("enter")                   # and the next key is refused
