"""Battle-plan actions by click, and the bridge that turns planner plans into them (#85, stage 4).

Built against the fake game only: none of this has run in the real game."""

from datetime import datetime, timedelta

import pytest

from hoi4_harness.actions.catalog import BY_NAME
from hoi4_harness.adapters.battle_plans import (
    ASSIGN_POINT,
    FRONT_LINE_KEY,
    OFFENSIVE_KEY,
    PLAN_ACTIONS,
    BattlePlanExecutor,
    plan_assignment,
    plan_front_line,
    plan_offensive,
    plan_steps,
)
from hoi4_harness.adapters.headless import MOUSE_ACTIONS, excluded_actions
from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter, MouseDisabled
from hoi4_harness.adapters.unit_orders import OrderRefused, stamp_now
from hoi4_harness.config import HarnessConfig
from hoi4_harness.mapcamera import Viewport
from hoi4_harness.tactics.bridge import ControllerBridge
from hoi4_harness.tactics.planner import planner_controller
from hoi4_harness.types import ActionCall, StateForces
from orders_helpers import camera_record, stack, write_grid_game
from orders_helpers import load_layer as load_grid

COORDS = {"army.alpha": (100, 100), "army.all": (120, 100), ASSIGN_POINT: (200, 200)}


def call(name, **args):
    return ActionCall(name, args)


def front(**args):
    return call("draw_front_line", **{"army": "alpha", "enemy": "GER", "states": [11], **args})


def offensive(**args):
    return call("draw_offensive", **{"army": "alpha", "from_state": 11, "target_state": 12, **args})


def plan_stack(tmp_path, **kw):
    s = stack(tmp_path, **kw)
    s.driver.config.coordinates.update(COORDS)
    ex = BattlePlanExecutor(s.driver, s.layer, s.executor._camera, s.executor.centre, sleep=s.fake.sleep)
    s.driver.enable_battle_plans(ex)
    return s, ex


@pytest.fixture
def s(tmp_path):
    return plan_stack(tmp_path)[0]


# --- the interface-file facts the path is built on -------------------------------------------


def test_the_tool_keys_are_the_interface_files_shortcuts():
    assert (FRONT_LINE_KEY, OFFENSIVE_KEY) == ("z", "x")      # interface/battleplantools.gui l.134, l.152


def test_the_actions_are_in_the_catalog_and_off_without_a_mouse():
    for name in PLAN_ACTIONS:
        assert BY_NAME[name].category == "army"
    assert set(PLAN_ACTIONS) <= MOUSE_ACTIONS
    assert set(PLAN_ACTIONS) <= excluded_actions()
    assert HarnessConfig().battle_plans is False               # off unless the profile asks


def test_a_driver_without_a_mouse_refuses_to_offer_them():
    driver = InputDriverAdapter(InputConfig(mouse=False), dry_run=False)
    with pytest.raises(MouseDisabled):
        driver.enable_battle_plans(object())
    assert not set(PLAN_ACTIONS) & driver.supported_actions


def test_a_driver_only_offers_them_once_enabled(tmp_path):
    plain = stack(tmp_path)
    assert not set(PLAN_ACTIONS) & plain.driver.supported_actions
    s, _ = plan_stack(tmp_path / "again")
    assert set(PLAN_ACTIONS) <= s.driver.supported_actions


# --- refusals before any input ----------------------------------------------------------------


def refused(s, c, why):
    result = s.adapter.apply(c)
    assert (result.ok, result.error_kind) == (False, "rejected"), result.message
    assert why in result.message and "nothing was sent" in result.message
    assert s.gui.calls == [] and not [line for line in s.driver.log if line.startswith(("press", "drag"))]


@pytest.mark.parametrize("c, why", [
    (front(army="zulu"), "has no recorded point"),
    (front(states=[]), "1 to 8 states"),
    (front(states=[11, 11]), "named twice"),
    (front(states=[12]), "not shown as the player's"),
    (front(states=[10]), "not shown as the player's"),       # the picture lists only the front
    (front(states=[99]), "no state 99"),
    (front(enemy="SWE"), "not an opponent"),
    (front(enemy="FRA"), "nothing for it to face"),
    (offensive(army="zulu"), "has no recorded point"),
    (offensive(target_state=11), "are both 11"),
    (offensive(target_state=10), "nothing hostile"),
    (offensive(from_state=12, target_state=11), "not shown as the player's"),
    (offensive(via=[1, 2, 3, 4, 5]), "at most 4 waypoints"),
    (call("assign_battle_plan", army="zulu"), "has no recorded point"),
])
def test_a_plan_action_that_cannot_be_given_is_refused_and_sends_nothing(s, c, why):
    refused(s, c, why)


def test_a_line_must_be_a_chain_of_bordering_states(s):
    state = s.state()
    state.state_forces.append(StateForces(13, "SWE", own=1))      # two states apart from 11, with 12 between
    with pytest.raises(OrderRefused, match="do not border"):
        plan_front_line(s.layer, front(states=[11, 13]), state, COORDS)


def test_an_offensive_needs_a_chain_through_its_waypoints(s):
    with pytest.raises(OrderRefused, match="do not border"):
        plan_offensive(s.layer, offensive(via=[13]), s.state(), COORDS)


def test_assigning_needs_the_recorded_button_the_interface_file_does_not_give_a_shortcut_for(s):
    state = s.state()
    with pytest.raises(OrderRefused, match="no shortcut"):
        plan_assignment(call("assign_battle_plan", army="alpha"), state, {"army.alpha": (1, 1)})


def test_a_stale_camera_refuses_like_unit_orders(tmp_path):
    layer = load_grid(write_grid_game(tmp_path / "x"))
    old = camera_record(layer, recorded=stamp_now(datetime.now() - timedelta(hours=30)))
    s, _ = plan_stack(tmp_path / "run", record=old)
    result = s.adapter.apply(front())
    assert (result.ok, result.error_kind) == (False, "rejected")
    assert "30 h old" in result.message and "nothing was sent" in result.message
    assert s.gui.calls == []


# --- the gestures ----------------------------------------------------------------------------


def test_a_dry_run_lists_every_gesture_and_sends_nothing(tmp_path):
    s, ex = plan_stack(tmp_path, dry_run=True)
    result = s.adapter.apply(front())
    assert (result.ok, result.error_kind) == (False, "not_executed")
    assert "dry run: would draw a front line" in result.message
    assert "click army.alpha" in result.message and "press z (front line tool)" in result.message
    assert "drag draw_front_line over states [11]" in result.message
    assert [step.kind for step in ex.last_steps] == ["click_recorded", "key", "path"]
    assert s.gui.calls == []
    result = s.adapter.apply(offensive())
    assert "press x (offensive tool)" in result.message and "over states [11, 12]" in result.message


def test_a_front_line_presses_its_tool_and_drags_through_the_states(s):
    result = s.adapter.apply(front())
    assert (result.ok, result.error_kind) == (False, "unverified")
    assert "UNVERIFIED" in result.message and "cannot be read back" in result.message
    log = s.driver.log
    log = log[next(i for i, line in enumerate(log) if line.startswith("click army.alpha")):]
    assert log[0].startswith("click army.alpha at (100, 100)")
    assert log[1] == "press z"
    assert log[2].startswith("drag path draw_front_line over states [11]")
    assert [c[0] for c in s.gui.calls if c[0] == "drag"] == ["drag"]


def test_an_offensive_arrow_runs_from_the_start_state_to_the_target(s):
    s.adapter.apply(offensive())
    drags = [line for line in s.driver.log if line.startswith("drag path")]
    assert len(drags) == 1
    points = drags[0].split(" over states [11, 12] ")[1].split(" -> ")
    assert len(points) == 2 and points[0] != points[1]
    assert "press x" in s.driver.log


def test_assigning_selects_the_army_then_presses_the_recorded_button(s):
    result = s.adapter.apply(call("assign_battle_plan", army="alpha"))
    assert result.error_kind == "unverified"
    clicks = [line for line in s.driver.log if line.startswith("click")]
    assert clicks[-2].startswith("click army.alpha at (100, 100)")
    assert clicks[-1].startswith(f"click {ASSIGN_POINT} at (200, 200)")


def test_a_line_that_does_not_fit_the_view_is_refused_before_any_input(tmp_path):
    layer = load_grid(write_grid_game(tmp_path / "x"))
    narrow = camera_record(layer, viewport=Viewport(1920, 1080, 900, 60, 900, 120))
    s, _ = plan_stack(tmp_path / "run", record=narrow)
    result = s.adapter.apply(offensive())
    assert (result.ok, result.error_kind) == (False, "rejected")
    assert "has to fit one view" in result.message and s.gui.calls == []


def test_steps_are_computed_from_the_camera(s):
    order = plan_offensive(s.layer, offensive(), s.state(), COORDS)
    camera = s.executor.record().camera_for(s.layer, 6)
    steps = plan_steps(s.layer, camera, order)
    assert [st.kind for st in steps] == ["click_recorded", "key", "path"]
    a, b = steps[2].value
    assert a != b


# --- planner plans -> plan actions, or state-level orders --------------------------------------


def bridge(s, **kw):
    return ControllerBridge(s.layer, planner_controller(), **kw)


def test_planner_plans_become_battle_plan_actions_when_they_are_offered(s):
    calls = bridge(s, offered=set(PLAN_ACTIONS), armies={"default": "alpha"}).decide(s.state())
    names = [c.name for c in calls]
    assert {"draw_front_line", "draw_offensive", "assign_battle_plan"} <= set(names)
    assert "move_division" not in names and "attack_province" not in names
    line = next(c for c in calls if c.name == "draw_front_line")
    assert line.arguments == {"army": "alpha", "enemy": "GER", "states": [11]}
    arrow = next(c for c in calls if c.name == "draw_offensive")
    assert (arrow.arguments["from_state"], arrow.arguments["target_state"]) == (11, 12)
    assert "planner: plan" in line.rationale


def test_every_call_the_bridge_makes_passes_the_executors_own_refusals(s):
    calls = bridge(s, offered=set(PLAN_ACTIONS), armies={"default": "alpha"}).decide(s.state())
    for c in calls:
        result = s.adapter.apply(c)
        assert result.error_kind == "unverified", (c.name, result.message)


def test_without_the_plan_actions_it_falls_back_to_state_level_orders(s):
    for offered in (set(), {"draw_front_line", "draw_offensive"}):
        calls = bridge(s, offered=offered, armies={"default": "alpha"}).decide(s.state())
        assert calls and {c.name for c in calls} <= {"move_division", "attack_province"}


def test_a_plan_with_no_army_falls_back_per_plan(s):
    calls = bridge(s, offered=set(PLAN_ACTIONS), armies={}).decide(s.state())
    assert calls and {c.name for c in calls} <= {"move_division", "attack_province"}


def test_a_division_level_controller_still_goes_through_the_old_path(s):
    from hoi4_harness.tactics import ScriptedController

    calls = ControllerBridge(s.layer, ScriptedController(), offered=set(PLAN_ACTIONS)).decide(s.state())
    assert {c.name for c in calls} <= {"move_division", "attack_province"}


def test_the_army_domain_owns_the_plan_actions_and_the_script_uses_them_when_asked():
    from hoi4_harness.auto.army import ArmyScript
    from hoi4_harness.auto.base import AutoContext
    from hoi4_harness.control import DOMAIN_ACTIONS

    assert set(PLAN_ACTIONS) <= DOMAIN_ACTIONS["army"]
    ctx = AutoContext(HarnessConfig(battle_plans=True, army_controller="planner"), layer=lambda: None)
    assert ArmyScript(ctx).controller_name == "planner"
