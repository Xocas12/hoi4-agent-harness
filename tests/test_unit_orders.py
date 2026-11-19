"""Division orders by click, end to end on the fake game (#85).

The fake game's army walks a real map layer, and its mouse resolves clicks through
a true camera the harness never sees: the harness aims with a fitted copy of it
(camera.json), so a wrong fit shows up as a click that selects nothing.
"""

from datetime import datetime, timedelta

import pytest

from hoi4_harness.adapters.unit_orders import (
    CameraRecord,
    CameraRefused,
    OrderRefused,
    decorate_state,
    plan_gestures,
    plan_order,
    stamp_now,
)
from hoi4_harness.mapcamera import Viewport
from hoi4_harness.observation.render import render_full
from hoi4_harness.types import ActionCall
from orders_helpers import camera_record, landmarks, stack, true_view, write_grid_game
from orders_helpers import load_layer as load_grid


def call(name, **args):
    return ActionCall(name, args)


def move(**args):
    return call("move_division", **args)


def attack(**args):
    return call("attack_province", **args)


@pytest.fixture
def s(tmp_path):
    return stack(tmp_path)


def order_lines(s):
    return decorate_state(s.state(), s.executor.ledger, None).order_status


# --- the front picture the orders are planned from ---------------------------------


def test_the_fake_mod_prints_the_front_and_the_reader_parses_it(s):
    picture = {f.state: f for f in s.state().state_forces}
    assert (picture[11].own, picture[11].controller) == (3, "SWE")
    assert (picture[12].foe, picture[12].controller) == (2, "GER")
    assert 10 not in picture and 13 not in picture             # nobody of either side there


def test_the_brief_shows_the_front(s):
    text = render_full(s.state())
    assert "Front picture (1936-01-02): own 3 vs foe 2 in 2 state(s)" in text


# --- validation against the map layer and the telemetry ------------------------------


def plan(s, c):
    return plan_order(s.layer, c, s.state())


def test_a_move_between_connected_states_is_planned(s):
    p = plan(s, move(from_state=11, to_state=10))
    assert (p.from_state, p.to_state, p.from_province) == (11, 10, None)
    assert p.target_province in (1, 5, 9)


@pytest.mark.parametrize("c, why", [
    (move(from_state=11, to_state=99), "no state 99"),
    (move(from_state=13, to_state=10), "shows no division of yours in state 13"),
    (move(from_state=11, to_state=11), "are both 11"),
    (move(from_state=11, to_state=12), "use attack_province"),
    (move(from_state=11, to_state=10, from_province=1), "not a land province of state 11"),
    (move(from_state=11, to_state=10, to_province=2), "not a land province of state 10"),
    (move(from_state=11, to_state=10, via=[1, 2, 3, 4, 5]), "at most 4 waypoints"),
    (attack(from_state=11, to_state=10), "nothing hostile is shown in state 10"),
    (attack(from_state=11, to_state=13), "nothing hostile is shown in state 13"),
    (attack(from_state=11, to_state=12, from_province=2, to_province=7), "borders province 2"),
])
def test_orders_the_map_or_the_picture_rules_out_are_refused(s, c, why):
    with pytest.raises(OrderRefused, match=why):
        plan(s, c)


def test_an_attack_needs_the_target_to_border_the_attacker(s):
    s.army.add("GER", 8)                                    # a foe in state 13
    s.fake.run_days(1)
    # State 13 now shows an enemy division but touches state 12, not state 11.
    with pytest.raises(OrderRefused, match="does not border state 11"):
        plan(s, attack(from_state=11, to_state=13))


def test_an_attack_aims_at_a_province_that_touches_the_attacker(s):
    p = plan(s, attack(from_state=11, to_state=12, from_province=6, to_province=7))
    assert (p.from_province, p.target_province) == (6, 7)
    assert plan(s, attack(from_state=11, to_state=12)).target_province in (3, 7, 11)


def test_without_a_front_picture_nothing_is_planned(s):
    state = s.state()
    state.state_forces = None
    with pytest.raises(OrderRefused, match="no front picture"):
        plan_order(s.layer, move(from_state=11, to_state=10), state)


# --- the camera ------------------------------------------------------------------------


def test_the_recorded_camera_predicts_where_the_fake_game_draws_things(s):
    record = camera_record(s.layer)
    for view in (6, 2, 12):
        camera = record.camera_for(s.layer, view)
        truth = s.army.camera_for(view)
        for pid in (1, 5, 8, 11):
            ax, ay = s.layer.anchor(pid, 0)
            got, want = camera.to_screen(ax, ay), truth.to_screen(ax, ay)
            assert abs(got[0] - want[0]) < 2 and abs(got[1] - want[1]) < 2


def test_a_missing_camera_file_refuses(tmp_path):
    with pytest.raises(CameraRefused, match="no camera fit"):
        CameraRecord.load(tmp_path / "camera.json")


def test_the_camera_file_round_trips(tmp_path, s):
    path = camera_record(s.layer).save(tmp_path / "camera.json")
    again = CameraRecord.load(path)
    assert again.pairs == camera_record(s.layer).pairs and again.validated


def test_a_stale_fit_is_refused(tmp_path):
    layer = load_grid(write_grid_game(tmp_path / "x"))
    long_ago = stamp_now(datetime.now() - timedelta(hours=30))
    s = stack(tmp_path / "run", record=camera_record(layer, recorded=long_ago))
    result = s.adapter.apply(move(from_state=11, to_state=10))
    assert (result.ok, result.error_kind) == (False, "rejected")
    assert "30 h old" in result.message and "nothing was sent" in result.message
    assert s.gui.calls == []


def test_a_four_point_fit_is_refused_until_it_has_been_validated(tmp_path):
    layer = load_grid(write_grid_game(tmp_path / "x"))
    record = camera_record(layer, pairs=landmarks(layer, true_view(), (1, 4, 9, 12)), validated=False)
    s = stack(tmp_path / "run", record=record)
    result = s.adapter.apply(move(from_state=11, to_state=10))
    assert "exactly four landmarks" in result.message and s.gui.calls == []


def test_a_fit_for_another_resolution_is_refused(tmp_path):
    layer = load_grid(write_grid_game(tmp_path / "x"))
    s = stack(tmp_path / "run", record=camera_record(layer, screen=(2560, 1440)))
    result = s.adapter.apply(move(from_state=11, to_state=10))
    assert "fitted at 2560x1440" in result.message and s.gui.calls == []


def test_a_target_outside_the_view_is_refused_before_any_click(tmp_path):
    layer = load_grid(write_grid_game(tmp_path / "x"))
    narrow = camera_record(layer, viewport=Viewport(1920, 1080, 900, 60, 900, 120))
    s = stack(tmp_path / "run", record=narrow)
    result = s.adapter.apply(move(from_state=11, to_state=10))
    assert (result.ok, result.error_kind) == (False, "rejected")
    assert "too far from the stack to share one view" in result.message
    assert s.gui.calls == []


# --- the click path -----------------------------------------------------------------------


def test_a_move_selects_the_state_with_a_box_and_right_clicks_the_destination(s):
    result = s.adapter.apply(move(from_state=11, to_state=10))
    assert (result.ok, result.error_kind) == (False, "unverified")
    assert "order #1 sent" in result.message and "Do not repeat" in result.message
    assert [c[0] for c in s.gui.calls] == ["drag", "click"]
    assert s.gui.calls[1][1] == "right" and s.gui.calls[1][3] is False
    assert len(s.army.selected) == 3
    assert any("order to province" in line for line in s.army.log)
    # The console put the view on the first province of the state the stack is in.
    assert s.army.view == 2 and s.fake.runs[-1] == ["goto_province 2"]


def test_the_divisions_march_and_the_ledger_says_what_the_picture_can_show(s):
    s.adapter.apply(move(from_state=11, to_state=10))
    assert order_lines(s) == ["#1 move state 11 -> 10 (given 1936-01-02): sent, nothing to see yet"]
    s.fake.run_days(4)                                  # one plains border takes a day
    assert {d.province for d in s.army.divisions.values() if d.owner == "SWE"} <= {1, 5, 9}
    # State 10 is behind the front, so the picture does not list it: the order can
    # be seen to have left state 11 but not to have arrived. The ledger says only that.
    assert order_lines(s) == ["#1 move state 11 -> 10 (given 1936-01-02): underway: "
                              "3 division(s) have left state 11"]


def test_the_order_lines_reach_the_brief(s):
    s.adapter.apply(move(from_state=11, to_state=10))
    s.fake.run_days(4)
    brief = render_full(decorate_state(s.state(), s.executor.ledger, None))
    assert "Order: #1 move state 11 -> 10 (given 1936-01-02): underway" in brief


def test_a_march_into_empty_enemy_ground_is_seen_to_arrive(s):
    s.adapter.apply(attack(from_state=11, to_state=12, from_province=6, to_province=7))
    s.fake.run_days(12)                                 # the defenders fall, the state is taken
    assert s.army.control[12] == "SWE"
    s.army.add("GER", 4)                                # state 13 stays contested: province 8 is clear
    s.fake.run_days(1)
    s.adapter.apply(move(from_state=12, to_state=13, from_province=7, to_province=8))
    s.fake.run_days(4)
    assert s.army.control[13] == "GER"
    line = [ln for ln in order_lines(s) if ln.startswith("#2")][0]
    assert line == ("#2 move state 12 -> 13 (given 1936-01-15): "
                    "arrived: your divisions in state 13 went 0 -> 3")


def test_a_stack_click_attack_wears_the_defenders_down_and_takes_the_state(s):
    s.adapter.apply(attack(from_state=11, to_state=12, from_province=6, to_province=7))
    assert [c[0] for c in s.gui.calls] == ["click", "click"] and len(s.army.selected) == 3
    s.fake.run_days(12)
    assert s.army.control[12] == "SWE"
    assert order_lines(s)[0].endswith("taken: state 12 is now yours")


def test_a_queued_waypoint_is_a_shift_right_click_after_the_first(s):
    result = s.adapter.apply(move(from_state=11, to_state=13, via=[10]))
    assert result.error_kind == "unverified", result.message
    clicks = [(c[1], c[3]) for c in s.gui.calls if c[0] == "click"]
    assert clicks == [("right", False), ("right", True)]
    assert "state 10, then province" in result.message


def test_a_dry_run_logs_the_gestures_and_clicks_nothing(tmp_path):
    s = stack(tmp_path, dry_run=True)
    result = s.adapter.apply(move(from_state=11, to_state=10))
    assert (result.ok, result.error_kind) == (False, "not_executed")
    assert "drag" in result.message and "right-click" in result.message
    assert s.gui.calls == [] and s.army.selected == []


def test_a_wrong_counter_offset_selects_nothing_and_the_ledger_does_not_pretend(tmp_path):
    layer = load_grid(write_grid_game(tmp_path / "x"))
    s = stack(tmp_path / "run", record=camera_record(layer, counter_offset=(40, 0)))
    result = s.adapter.apply(move(from_state=11, to_state=10, from_province=6))
    assert result.error_kind == "unverified"                 # the harness cannot tell yet
    assert s.army.selected == [] and "selected nothing" in s.army.log[0]
    s.fake.run_days(5)
    line = order_lines(s)[0]
    assert "no change after" in line and "may not have taken" in line
    assert {f.state: f.own for f in s.state().state_forces}[11] == 3          # nobody moved


def test_a_box_that_would_catch_another_states_divisions_is_refused(s):
    s.army.add("SWE", 5)                  # province 5 is state 10: its counter sits beside state 11's
    s.fake.run_days(1)
    state = s.state()
    p = plan_order(s.layer, move(from_state=11, to_state=10), state)
    camera = s.executor.record().camera_for(s.layer, 2)
    # At this zoom the columns are 200 px apart and the box clears state 10 ...
    assert plan_gestures(s.layer, camera, p, state)[0].kind == "drag"

    # ... squeeze the view until the columns share a line and it would take both.
    class Squeezed:
        viewport = camera.viewport

        def to_screen(self, x, y):
            sx, sy = camera.to_screen(x, y)
            return 960 + (sx - 960) * 0.01, sy

    with pytest.raises(OrderRefused, match=r"would also catch the counter of province \d+ in state 10"):
        plan_gestures(s.layer, Squeezed(), p, state)


def test_two_clicks_that_would_be_one_are_refused(s):
    state = s.state()
    p = plan_order(s.layer, move(from_state=11, to_state=10, from_province=2, to_province=1), state)
    camera = s.executor.record().camera_for(s.layer, 2)

    class Tiny:
        viewport = camera.viewport

        def to_screen(self, x, y):
            sx, sy = camera.to_screen(x, y)
            return 960 + (sx - 960) * 0.01, 500 + (sy - 500) * 0.01

    with pytest.raises(OrderRefused, match="under 12 px apart"):
        plan_gestures(s.layer, Tiny(), p, state)


def test_headless_has_no_mouse_and_so_no_division_orders():
    from hoi4_harness.adapters.headless import excluded_actions
    from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter, MouseDisabled

    assert {"move_division", "attack_province"} <= excluded_actions()
    driver = InputDriverAdapter(InputConfig(mouse=False), dry_run=False)
    with pytest.raises(MouseDisabled):
        driver.enable_unit_orders(object())
    assert not {"move_division", "attack_province"} & driver.supported_actions


def test_the_orders_are_offered_only_where_the_driver_can_perform_them(s):
    from hoi4_harness.adapters.input_driver import InputDriverAdapter

    assert {"move_division", "attack_province"} <= s.adapter.supported_actions
    assert not {"move_division", "attack_province"} & InputDriverAdapter().supported_actions


def test_the_orders_are_direct_commands_so_a_delegating_run_does_not_get_them():
    from hoi4_harness.agent.hybrid import actions_for_mode

    allowed = {"move_division", "attack_province", "note"}
    assert actions_for_mode(set(allowed), "ai") == {"note"}
    assert actions_for_mode(set(allowed), "llm") == allowed


def test_the_configured_stack_offers_the_orders_and_shows_them_in_the_brief(tmp_path):
    from hoi4_harness.adapters import build_adapter
    from hoi4_harness.config import HarnessConfig

    s = stack(tmp_path / "fake")
    path = camera_record(s.layer).save(tmp_path / "camera.json")
    config = HarnessConfig(adapter="logtail+input", country="SWE", dry_run=True, unit_orders=True,
                           game_dir=tmp_path / "fake" / "game", log_path=s.fake.log_path,
                           camera_path=path, run_dir=tmp_path / "runs", console_dir=tmp_path / "user")
    adapter = build_adapter(config)
    assert {"move_division", "attack_province"} <= adapter.supported_actions
    result = adapter.apply(move(from_state=11, to_state=10))
    assert (result.ok, result.error_kind) == (False, "not_executed")
    assert "box over state 11" in result.message and "goto_province 2" in str(
        (tmp_path / "runs" / "console").glob("*").__next__().read_text())
    assert "Front picture" in render_full(adapter.read_state())


def test_division_orders_need_the_playset_for_the_map(tmp_path):
    from hoi4_harness.adapters import build_adapter
    from hoi4_harness.config import HarnessConfig

    with pytest.raises(ValueError, match="pass --game-dir"):
        build_adapter(HarnessConfig(adapter="logtail+input", unit_orders=True, run_dir=tmp_path,
                                    log_path=tmp_path / "game.log"))
