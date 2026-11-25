"""The shared person-activity guard (issue #33), path by path, with injected clocks and input
stamps. No test reads the real idle clock, sends input or touches the game: the "person" is a
number the test moves."""

from __future__ import annotations

import json

import pytest

from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter, PersonActive
from hoi4_harness.adapters.input_guard import InputGuard
from hoi4_harness.adapters.keys import ScanCodeKeys
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.types import ActionCall


class World:
    """A clock, the OS's last-input stamp (``stamp_ms``) and a person who can touch the keyboard."""

    def __init__(self):
        self.t = 100.0
        self.stamp_ms = 0            # the last-input stamp; 0 means long ago
        self.supported = True
        self.sent: list = []         # everything the harness sent, in order
        self.person_after = None     # the person acts in the gap after our Nth event
        self._due = False

    # InputWatch's surface
    def tick(self):
        return self.stamp_ms

    def idle_seconds(self):
        return self.t - self.stamp_ms / 1000.0

    def sleep(self, seconds):
        self.t += seconds

    def clock(self):
        return self.t

    def person_acts(self):
        self.stamp_ms = int(self.t * 1000) + 1

    def ours(self, what):
        """One of our events: it moves the same OS clock, as SendInput does."""
        self.sent.append(what)
        self.t += 0.01
        self.stamp_ms = int(self.t * 1000)
        if self.person_after is not None and len(self.sent) == self.person_after:
            self._due = True

    def gap(self):
        """Time passes between two of our events (a key gap, a cursor move): a due person acts."""
        if self._due:
            self._due = False
            self.person_acts()


class Gui:
    def __init__(self, world):
        self.w = world

    def moveTo(self, x, y, duration=0):          # noqa: N802 - pyautogui's names
        self.w.sent.append(("move", x, y))       # a cursor move is not input to the idle clock
        self.w.gap()

    def click(self, button="left"):
        self.w.ours(("click", button))

    def mouseDown(self, button="left"):          # noqa: N802
        self.w.ours(("down", button))

    def mouseUp(self, button="left"):            # noqa: N802
        self.w.ours(("up", button))


def build(tmp_path=None, *, quiet=5.0, wait=2.0, log=None):
    w = World()
    keys = ScanCodeKeys(send=lambda scan, up: w.ours(("key", scan, up)), sleep=lambda s: w.gap())
    driver = InputDriverAdapter(InputConfig(enforce_focus=False, coordinates={"a": (10, 10)},
                                            move_duration=0),
                                dry_run=False, keys=keys, gui=Gui(w), sleep=lambda s: None)
    guard = InputGuard(w, quiet_seconds=quiet, wait_seconds=wait, sleep=w.sleep, clock=w.clock,
                       log=driver.log.append,
                       log_path=(tmp_path / "handovers.jsonl") if tmp_path else None)
    driver.input_guard = guard
    guard.attach_keys(keys)
    return w, driver, guard


def outcomes(guard):
    return [(e["outcome"], e["path"]) for e in guard.record]


# --- never starts while the person is active -------------------------------------------------------


@pytest.mark.parametrize("act", [
    lambda d: d.press("space"),
    lambda d: d.type_text("run x.txt"),
    lambda d: d.click("a"),
    lambda d: d.click_at((5, 5), label="slot"),
    lambda d: d.drag((1, 1), (9, 9), label="box"),
    lambda d: d.drag_path([(1, 1), (5, 5), (9, 9)], label="line"),
], ids=["key", "type", "click", "click_at", "drag", "drag_path"])
def test_33_no_path_starts_within_the_quiet_time_and_nothing_is_sent(tmp_path, act):
    w, driver, guard = build(tmp_path, wait=1.0)
    w.person_acts()
    with pytest.raises(PersonActive) as err:
        act(driver)
    assert err.value.phase == "start" and "nothing was typed or clicked" in str(err.value)
    assert w.sent == []
    assert ("refused", err.value.path) in outcomes(guard)


@pytest.mark.parametrize("act", [
    lambda d: d.press("space"),
    lambda d: d.type_text("ab"),
    lambda d: d.click("a"),
    lambda d: d.click_at((5, 5), label="slot"),
    lambda d: d.drag((1, 1), (9, 9), label="box"),
    lambda d: d.drag_path([(1, 1), (5, 5), (9, 9)], label="line"),
], ids=["key", "type", "click", "click_at", "drag", "drag_path"])
def test_33_a_quiet_person_lets_every_path_through_and_each_is_recorded(tmp_path, act):
    w, driver, guard = build(tmp_path)
    act(driver)
    assert w.sent
    assert guard.record[-1]["outcome"] == "completed"


def test_33_it_waits_for_the_person_to_go_quiet_and_then_goes(tmp_path):
    w, driver, guard = build(tmp_path, quiet=5.0, wait=30.0)
    w.person_acts()
    driver.press("space")
    assert w.sent                       # waited ~5 s of fake time, then sent
    assert [o for o, _ in outcomes(guard)] == ["waiting", "resumed", "completed"]
    assert w.t - 100.0 >= 5.0


def test_33_a_platform_that_cannot_say_refuses_rather_than_guesses(tmp_path):
    w, driver, guard = build(tmp_path, wait=0.5)
    w.supported = False
    guard.watch = type("Unsupported", (), {"supported": False, "tick": lambda s: None,
                                           "idle_seconds": lambda s: None})()
    with pytest.raises(PersonActive, match="cannot say"):
        driver.press("space")
    assert w.sent == []


# --- stops mid-sequence and says so --------------------------------------------------------------


def test_33_typing_stops_at_the_next_character_when_the_person_starts(tmp_path):
    w, driver, guard = build(tmp_path)
    w.person_after = 4                  # in the gap after the 2nd character
    with pytest.raises(PersonActive) as err:
        driver.type_text("abcd")
    assert err.value.phase == "mid" and "stopped before it finished" in str(err.value)
    downs = [s for s in w.sent if s[0] == "key" and not s[2]]
    assert len(downs) == 2              # a and b went out, c and d did not
    assert ("aborted", "type") in outcomes(guard)
    assert any("handover: aborted type" in line for line in driver.log)


def test_33_a_person_between_two_clicks_of_a_path_stops_the_rest(tmp_path):
    w, driver, guard = build(tmp_path)
    driver.click("a")
    w.person_acts()
    with pytest.raises(PersonActive):
        driver.click("a")
    assert [s for s in w.sent if s[0] == "click"] == [("click", "left")]


def test_33_a_drag_in_progress_always_lets_go_of_the_button(tmp_path):
    w, driver, guard = build(tmp_path)
    w.person_after = 2                  # during the first move after mouseDown
    with pytest.raises(PersonActive):
        driver.drag_path([(1, 1), (5, 5), (9, 9)], label="line")
    kinds = [s[0] for s in w.sent]
    assert kinds.count("down") == 1 and kinds.count("up") == 1
    assert ("move", 9, 9) not in w.sent


def test_33_a_keypress_inside_a_chord_is_seen_and_the_keys_come_up(tmp_path):
    w, driver, guard = build(tmp_path)
    w.person_after = 1                  # in the gap after the ctrl-down of a chord
    with pytest.raises(PersonActive) as err:
        driver.press("ctrl+space")
    assert err.value.phase == "mid"
    ups = [s for s in w.sent if s[0] == "key" and s[2]]
    downs = [s for s in w.sent if s[0] == "key" and not s[2]]
    assert len(ups) == len(downs)       # nothing left held down


def test_33_a_run_file_stops_where_the_person_comes_back_and_closes_only_its_console(tmp_path):
    w, driver, guard = build(tmp_path)
    driver.press("grave")                                 # we open the console
    driver.type_text("run a")
    w.person_acts()
    with pytest.raises(PersonActive):
        driver.press("enter")
    before = len(w.sent)
    driver.press("grave")                                 # the console we opened is closed anyway
    assert len(w.sent) > before
    with pytest.raises(PersonActive):
        driver.press("space")                             # but nothing else goes out


# --- the focus --------------------------------------------------------------------------------------


def test_33_the_focus_is_not_taken_while_the_person_is_active(tmp_path):
    w, _, guard = build(tmp_path)
    taken = []
    take = guard.gate_take(lambda title: taken.append(title) or True)
    w.person_acts()
    assert take("Hearts of Iron IV") is False and taken == []
    w.t += 10
    assert take("Hearts of Iron IV") is True and taken == ["Hearts of Iron IV"]
    assert [o for o, _ in outcomes(guard)] == ["focus_withheld", "focus_taken"]


def test_33_a_waiting_patient_focus_does_not_pull_the_window_over_a_person(tmp_path):
    from hoi4_harness.campaign.watch import PatientFocus

    w, _, guard = build(tmp_path)
    state = {"front": False}
    taken = []

    def take(title):
        taken.append(title)
        state["front"] = True
        return True

    def check(title):
        return FocusCheck(state["front"], "x", reason="elsewhere")

    w.person_acts()

    def sleep(seconds):                  # the person keeps working for a while, then stops
        w.t += seconds
        if w.t < 300:
            w.person_acts()
        if w.t > 400:
            state["front"] = True

    patient = PatientFocus(check, sleep=sleep, retry_seconds=60, take=guard.gate_take(take))
    assert patient("Hearts of Iron IV").focused
    withheld = [e for e in guard.record if e["outcome"] == "focus_withheld"]
    assert len(withheld) >= 4            # asked every minute while the person worked: no
    assert taken == ["Hearts of Iron IV"] and w.t >= 305   # taken only once they had gone quiet


# --- the record and the action result ---------------------------------------------------------------


def test_33_every_handover_is_in_the_run_log_file_and_the_driver_log(tmp_path):
    w, driver, guard = build(tmp_path, wait=1.0)
    driver.press("space")
    w.person_acts()
    with pytest.raises(PersonActive):
        driver.press("space")
    lines = [json.loads(x) for x in (tmp_path / "handovers.jsonl").read_text().splitlines()]
    assert [(e["outcome"], e["path"], e["what"]) for e in lines] == [
        ("completed", "key", "space"), ("waiting", "key", "space"), ("refused", "key", "space")]
    assert all("t" in e for e in lines) and "keyboard or mouse input" in lines[-1]["why"]
    assert any("handover: refused key 'space'" in line for line in driver.log)


def test_33_an_action_that_met_a_person_reports_not_executed_not_rejected():
    w, driver, guard = build(wait=0.5)
    w.person_acts()

    def refuse(call):
        with guard.sequence("click", "slot"):
            pass

    driver._dispatch_mouse = refuse
    result = driver.apply(ActionCall("start_research", {"technology": "x"}))
    assert not result.ok and result.error_kind == "not_executed"
    assert "person" in result.message


def test_33_no_guard_and_a_dry_run_change_nothing():
    w, driver, _ = build()
    driver.input_guard = None
    w.person_acts()
    driver.press("space")
    assert w.sent                        # unguarded behaviour is as it was
    w2, dry, guard2 = build()
    dry.dry_run = True
    w2.person_acts()
    dry.press("space")
    assert w2.sent == [] and guard2.record == []     # a dry run sends and records nothing


# --- wiring ---------------------------------------------------------------------------------------------


def test_33_the_headless_wiring_makes_a_guard_only_for_a_real_run(tmp_path):
    from hoi4_harness.adapters.headless import _wire_input_guard
    from hoi4_harness.config import HarnessConfig

    w, driver, _ = build()
    driver.input_guard = None
    cfg = HarnessConfig(run_dir=tmp_path, dry_run=False)
    assert _wire_input_guard(driver, cfg, object(), w.sleep, watch=w) is None      # the fake game
    assert _wire_input_guard(driver, HarnessConfig(run_dir=tmp_path, dry_run=True), None, w.sleep,
                             watch=w) is None
    assert _wire_input_guard(driver, HarnessConfig(run_dir=tmp_path, dry_run=False,
                                                   input_quiet_seconds=0), None, w.sleep,
                             watch=w) is None
    guard = _wire_input_guard(driver, cfg, None, w.sleep, watch=w)
    assert driver.input_guard is guard and guard.quiet_seconds == 5.0
    assert guard.keys_hooked and guard.log_path == tmp_path / "handovers.jsonl"
