"""Armies by click and from the save (#8, the armies part of #137), against the fake game only.

Nothing here touches the real game: the mouse is the fake's, the saves are text written by the
test, and every assumption the click path rests on is listed in docs/armies.md for the live step."""

from datetime import datetime, timedelta

import pytest

from hoi4_harness import armies as A
from hoi4_harness.actions.catalog import BY_NAME
from hoi4_harness.adapters import savegame as sg
from hoi4_harness.adapters.armies import (
    ARMY_UI_ACTIONS,
    ArmyExecutor,
    ArmyLedger,
    ArmyOrder,
    army_steps,
    border_provinces,
    execute_point,
    plan_army_action,
)
from hoi4_harness.adapters.battle_plans import ASSIGN_POINT
from hoi4_harness.adapters.headless import MOUSE_ACTIONS
from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter, MouseDisabled
from hoi4_harness.adapters.unit_orders import OrderRefused, stamp_now
from hoi4_harness.control import DELEGATION_ACTIONS, resolve
from hoi4_harness.mapcamera import screen_point
from hoi4_harness.types import ActionCall, ArmyGroup, ArmyPlan, GameState
from orders_helpers import camera_record, stack, write_grid_game
from orders_helpers import load_layer as load_grid

COORDS = {
    "army.alpha": (100, 100), "army.create": (130, 100), ASSIGN_POINT: (200, 200),
    "plan.execute_careful": (300, 300), "plan.execute_balanced": (330, 300),
    "plan.execute_rush": (360, 300),
}


def call(name, **args):
    return ActionCall(name, args)


def army_stack(tmp_path, **kw):
    s = stack(tmp_path, **kw)
    s.driver.config.coordinates.update(COORDS)
    ex = ArmyExecutor(s.driver, s.layer, s.executor._camera, s.executor.centre, sleep=s.fake.sleep)
    s.driver.enable_army_ui(ex)
    return s, ex


@pytest.fixture
def s(tmp_path):
    return army_stack(tmp_path)[0]


# --- the save reader --------------------------------------------------------------------------

THEATRES = """c={ theatres={ theatre={ id={ id=1 type=67 }
 orders_group={ id={ id=29 type=53 } name="Army 1"
  order_instance={ type=5 states={ 42 54 } instance_id=7504 scheduled_member={ id=71593 type=4713 } }
  member={ unit={ id=71593 type=4713 } } member={ unit={ id=71594 type=4713 } }
  member={ request={ request=3 total=66 } } execution_type=1 }
 orders_group={ id={ id=7919 type=53 } name="Army 6"
  order_instance={ type=2 path={ 317 6389 6257 } postfix="o_strat_offensive_default"
     scheduled_member={ id=9366 type=4713 } scheduled_member={ id=76287 type=4713 } }
  order_instance={ type=2 path={ 11251 13258 } virtual_order=yes instance_id=7501 }
  order_instance={ type=1 path={ 1 2 } fallback=yes }
  member={ unit={ id=9366 type=4713 } } execution_type=2 }
 orders_group={ id={ id=8000 type=53 } name="Army 7" member={ unit={ id=5 type=4713 } } }
} } }"""


def groups():
    return A.read_army_groups(sg.parse_clausewitz(THEATRES)["c"])


def test_the_reader_finds_each_army_with_its_divisions_and_plans():
    g = {x.name: x for x in groups()}
    assert set(g) == {"Army 1", "Army 6", "Army 7"}
    assert g["Army 1"].members == 2                       # the reinforcement request is not a division
    assert g["Army 1"].plans[0].kind == "garrison" and g["Army 1"].plans[0].states == [42, 54]
    assert g["Army 1"].execution == 1


def test_the_reader_skips_virtual_orders_and_reads_kind_path_and_assignment():
    army6 = next(x for x in groups() if x.name == "Army 6")
    assert [(p.kind, p.path, p.assigned) for p in army6.plans] == [
        ("offensive", [317, 6389, 6257], 2), ("fallback", [1, 2], 0)]
    assert army6.execution == 2 and A.EXECUTION[2] == "rush"
    assert [p.kind for p in A.held_plans(army6)] == ["offensive"]      # the fallback has no divisions


def test_an_army_without_a_plan_reads_as_having_none():
    army7 = next(x for x in groups() if x.name == "Army 7")
    assert army7.plans == [] and army7.execution is None
    assert "no plan assigned in its own block" in A.describe(army7)


def test_a_block_without_theatres_is_unread_not_empty():
    assert A.read_army_groups({"units": {}}) is None
    assert A.read_army_groups({"theatres": {}}) == []
    assert A.read_army_groups(None) is None


def test_the_save_adapter_puts_the_armies_on_the_state():
    state = GameState()
    sg._read_country(state, sg.parse_clausewitz(THEATRES)["c"], {}, set())
    assert [g.name for g in state.army_groups] == ["Army 1", "Army 6", "Army 7"]
    line = A.brief_line(state.army_groups)
    assert "Army 6: 1 division(s), executes rush, offensive over 3 province(s)" in line


def test_the_real_save_shape_is_read(tmp_path):
    """The exact block a 1.20 save wrote for the German army group (copied, ids trimmed)."""
    text = """x={ theatres={ theatre={ id={ id=1 type=67 } orders_group={
        id={ id=7919 type=53 } name="Army 6"
        member={ unit={ id=36903 type=4713 } } member={ unit={ id=24052 type=4713 } }
        member={ request={ request=3 total=66 equipment_variant_index={ id=5106 type=70 } } }
        date="1945.6.16.2" } } } }"""
    (g,) = A.read_army_groups(sg.parse_clausewitz(text)["x"])
    assert (g.name, g.members, g.plans) == ("Army 6", 2, [])


# --- the catalog and who is offered what ------------------------------------------------------


def test_the_actions_are_in_the_catalog_the_army_domain_and_off_without_a_mouse():
    for name in ARMY_UI_ACTIONS:
        assert BY_NAME[name].category == "army"
    assert set(ARMY_UI_ACTIONS) <= MOUSE_ACTIONS
    driver = InputDriverAdapter(InputConfig(mouse=False), dry_run=False)
    with pytest.raises(MouseDisabled):
        driver.enable_army_ui(object())
    assert not set(ARMY_UI_ACTIONS) & driver.supported_actions


def test_army_llm_without_human_ai_is_offered_the_whole_set():
    cmap = resolve({"default": "llm"})
    assert not cmap.human_ai and cmap.steer == "off"
    offered = cmap.actions_for_model(set(ARMY_UI_ACTIONS) | {"move_division"})
    assert offered == set(ARMY_UI_ACTIONS) | {"move_division"}


def test_delegated_armies_are_offered_the_delegation_actions_only_while_the_model_steers():
    cmap = resolve({"default": "llm", "army": "game"})
    assert cmap.delegated and cmap.steer == "llm"
    allowed = set(ARMY_UI_ACTIONS)
    assert cmap.actions_for_model(allowed) == set(DELEGATION_ACTIONS)
    quiet = resolve({"default": "llm", "army": "game"}, steer="off")
    assert not quiet.actions_for_model(allowed)


def test_a_script_or_human_army_domain_is_not_offered_them():
    for who in ("script", "off", "human"):
        cmap = resolve({"default": "llm", "army": who})
        assert not cmap.actions_for_model(set(ARMY_UI_ACTIONS))


# --- refusals before any input ----------------------------------------------------------------


def refused(s, c, why):
    result = s.adapter.apply(c)
    assert (result.ok, result.error_kind) == (False, "rejected"), result.message
    assert why in result.message and "nothing was sent" in result.message
    assert s.gui.calls == []


@pytest.mark.parametrize("c, why", [
    (call("assign_army_front", army="zulu", enemy="GER"), "has no recorded point"),
    (call("assign_army_front", army="alpha", enemy="SWE"), "not an opponent"),
    (call("assign_army_front", army="alpha", enemy="FRA"), "nothing for a front to face"),
    (call("assign_army_front", army="alpha", enemy="GER", province=1), "not a province GER holds"),
    (call("assign_army_front", army="alpha", enemy="GER", mode="blitz"), "not one of careful"),
    (call("create_army", from_state=12), "none of the player's divisions in state 12"),
    (call("create_army", from_state=99), "none of the player's divisions in state 99"),
    (call("create_army", from_state=11, from_province=3), "not in state 11"),
    (call("assign_divisions", army="zulu", from_state=11), "has no recorded point"),
    (call("set_army_execution", army="alpha", mode="x"), "not one of"),
    (call("release_army", army="zulu"), "has no recorded point"),
    (call("draw_fallback_line", army="alpha", states=[12]), "not shown as the player's"),
    (call("draw_fallback_line", army="alpha", states=[]), "1 to 8 states"),
])
def test_an_army_action_that_cannot_be_given_is_refused_and_sends_nothing(s, c, why):
    refused(s, c, why)


def test_missing_recorded_points_are_named_with_the_calibrate_command(tmp_path):
    s, _ = army_stack(tmp_path)
    for name in ("plan.execute_balanced", "army.create"):
        del s.driver.config.coordinates[name]
    refused(s, call("assign_army_front", army="alpha", enemy="GER"), "plan.execute_balanced")
    refused(s, call("set_army_execution", army="alpha", mode="balanced"), "hoi4-harness calibrate")
    refused(s, call("create_army", from_state=11), "army.create")


def test_a_stale_camera_refuses_like_unit_orders(tmp_path):
    layer = load_grid(write_grid_game(tmp_path / "x"))
    old = camera_record(layer, recorded=stamp_now(datetime.now() - timedelta(hours=30)))
    s, _ = army_stack(tmp_path / "run", record=old)
    refused(s, call("assign_army_front", army="alpha", enemy="GER"), "30 h old")


# --- the front (#8) ---------------------------------------------------------------------------


def test_the_border_provinces_are_the_enemys_that_touch_the_players(s):
    state = s.state()
    forces = {f.state: f for f in state.state_forces}
    assert border_provinces(s.layer, forces, "SWE", "GER") == [3, 7, 11]
    assert border_provinces(s.layer, forces, "SWE", "FRA") == []


def test_a_front_selects_the_army_picks_the_tool_clicks_the_province_and_sets_the_pace(s):
    result = s.adapter.apply(call("assign_army_front", army="alpha", enemy="GER"))
    assert (result.ok, result.error_kind) == (False, "unverified")
    assert "UNVERIFIED" in result.message and "next text save" in result.message
    log = s.driver.log
    log = log[next(i for i, line in enumerate(log) if line.startswith("click army.alpha")):]
    point = screen_point(s.layer, "province", 3, s.executor.record().camera_for(s.layer, 3))
    assert log[0].startswith("click army.alpha at (100, 100)")
    assert log[1] == "press z"
    assert log[2] == f"left-click enemy province 3 at {point}"
    assert log[3].startswith(f"click {ASSIGN_POINT} at (200, 200)")
    assert log[4] == "press shift+s"
    assert log[5].startswith("click plan.execute_balanced at (330, 300)")


def test_a_named_province_and_pace_are_used(s):
    s.adapter.apply(call("assign_army_front", army="alpha", enemy="GER", province=7, mode="rush"))
    assert any("enemy province 7" in line for line in s.driver.log)
    assert s.driver.log[-1].startswith("click plan.execute_rush at (360, 300)")


def test_the_view_is_centred_on_the_enemy_province_and_the_click_lands_in_it(s):
    """Against the fake's TRUE camera, which the harness never sees: the click it computed from
    its own fit hits the province it meant."""
    s.adapter.apply(call("assign_army_front", army="alpha", enemy="GER", province=7))
    assert s.army.view == 7
    clicks = [c for c in s.gui.calls if c[0] == "click"]
    map_click = clicks[1]                 # [0] is the army card (a UI point), [1] the province
    assert s.army._province_at(*map_click[2]) == 7


def test_a_dry_run_lists_every_gesture_and_sends_nothing(tmp_path):
    s, ex = army_stack(tmp_path, dry_run=True)
    result = s.adapter.apply(call("assign_army_front", army="alpha", enemy="GER"))
    assert (result.ok, result.error_kind) == (False, "not_executed")
    assert "dry run: would give army 'alpha' a front against GER at province 3" in result.message
    assert "press z (front line tool)" in result.message
    assert [st.kind for st in ex.last_steps] == [
        "click_recorded", "key", "gesture", "click_recorded", "key", "click_recorded"]
    assert s.gui.calls == []


def test_without_the_assign_button_recorded_it_is_skipped_not_required(tmp_path):
    s, ex = army_stack(tmp_path)
    del s.driver.config.coordinates[ASSIGN_POINT]
    s.adapter.apply(call("assign_army_front", army="alpha", enemy="GER"))
    assert ASSIGN_POINT not in " ".join(s.driver.log)
    assert [st.kind for st in ex.last_steps] == [
        "click_recorded", "key", "gesture", "key", "click_recorded"]


# --- the other actions --------------------------------------------------------------------------


def test_create_army_boxes_the_divisions_and_presses_the_card(s):
    result = s.adapter.apply(call("create_army", from_state=11))
    assert result.error_kind == "unverified"
    log = [line for line in s.driver.log if line.startswith(("drag", "click"))]
    assert log[-2].startswith("drag box over state 11")
    assert log[-1].startswith("click army.create at (130, 100)")


def test_create_army_can_click_one_stack(s):
    s.adapter.apply(call("create_army", from_state=11, from_province=6))
    assert any("select stack in province 6" in line for line in s.driver.log)


def test_assign_divisions_drags_the_selected_counter_onto_the_army_card(s):
    result = s.adapter.apply(call("assign_divisions", army="alpha", from_state=11, from_province=6))
    assert result.error_kind == "unverified"
    drag = [line for line in s.driver.log if "drop the selection" in line][-1]
    assert drag.endswith("-> (100, 100)")


def test_set_army_execution_opens_the_pace_window_and_clicks_the_mode(s):
    result = s.adapter.apply(call("set_army_execution", army="alpha", mode="careful"))
    assert result.error_kind == "unverified"
    log = [line for line in s.driver.log if line.startswith(("click", "press"))][-3:]
    assert [line.split(" at ")[0] for line in log] == [
        "click army.alpha", "press shift+s", "click plan.execute_careful"]


def test_release_army_unassigns_its_units(s):
    s.adapter.apply(call("release_army", army="alpha"))
    assert s.driver.log[-1] == "press ctrl+h" and s.driver.log[-2].startswith("click army.alpha")


def test_a_fallback_line_is_drawn_with_its_tool_through_own_states(s):
    s.adapter.apply(call("draw_fallback_line", army="alpha", states=[11]))
    assert "press c" in s.driver.log
    assert any(line.startswith("drag path draw_fallback_line over states [11]") for line in s.driver.log)


def test_the_pace_of_each_mode_has_its_own_recorded_point():
    assert [execute_point(m) for m in A.EXECUTION.values()] == [
        "plan.execute_careful", "plan.execute_balanced", "plan.execute_rush"]


# --- the ledger: only the save confirms ---------------------------------------------------------


def front_order(mode="balanced"):
    return ArmyOrder("assign_army_front", "SWE", army="alpha", centre_province=3, target_province=3,
                     enemy="GER", mode=mode)


def at(date, groups):
    return GameState(date=date, army_groups=groups)


def group(name="alpha", plans=(), execution=1, members=3, ident="1"):
    return ArmyGroup(ident, name, members, list(plans), execution)


@pytest.fixture
def ledger(s):
    return ArmyLedger(s.layer)


def test_a_front_is_not_delegated_until_the_save_shows_it(ledger):
    ledger.add(front_order(), at("1939.9.1", [group()]))
    state = ledger.decorate(at("1939.9.2", [group()]))
    assert state.delegated_armies == []
    assert "sent, nothing in the save yet" in state.order_status[0]


def test_the_save_with_the_plan_and_the_pace_confirms_and_lists_the_army(ledger):
    ledger.add(front_order(), at("1939.9.1", [group(plans=[])]))
    plan = ArmyPlan("line", [2, 3, 4], assigned=3)
    state = ledger.decorate(at("1939.9.3", [group(plans=[plan], execution=1)]))
    assert "confirmed: alpha holds a line line through province 3, executing balanced" in (
        state.order_status[0])
    assert state.delegated_armies == ["alpha"]


def test_a_plan_with_nobody_assigned_or_far_from_the_province_does_not_confirm(ledger):
    ledger.add(front_order(), at("1939.9.1", [group()]))
    for plan in (ArmyPlan("line", [2, 3], assigned=0), ArmyPlan("line", [9, 10], assigned=3)):
        state = ledger.decorate(at("1939.9.2", [group(plans=[plan])]))
        assert state.delegated_armies == [] and "confirmed" not in state.order_status[0]


def test_a_line_at_the_wrong_pace_says_so_and_is_not_confirmed(ledger):
    ledger.add(front_order("rush"), at("1939.9.1", [group()]))
    state = ledger.decorate(at("1939.9.2", [group(plans=[ArmyPlan("line", [3], assigned=2)],
                                                   execution=0)]))
    assert "executes careful, not rush" in state.order_status[0]
    assert state.delegated_armies == []


def test_an_army_that_loses_its_plan_drops_off_the_delegated_list(ledger):
    ledger.add(front_order(), at("1939.9.1", [group()]))
    held = group(plans=[ArmyPlan("line", [3], assigned=2)])
    assert ledger.decorate(at("1939.9.2", [held])).delegated_armies == ["alpha"]
    assert ledger.decorate(at("1939.9.9", [group(plans=[])])).delegated_armies == []


def test_an_order_the_save_never_shows_is_called_not_taken(ledger):
    ledger.add(front_order(), at("1939.9.1", [group()]))
    state = ledger.decorate(at("1939.9.6", [group()]))
    assert "not seen in the save after 5 days" in state.order_status[0]


def test_a_reader_without_a_save_says_it_cannot_confirm(ledger):
    ledger.add(front_order(), at("1939.9.1", [group()]))
    state = ledger.decorate(GameState(date="1939.9.2"))
    assert "no save read yet" in state.order_status[0] and state.delegated_armies == []


def test_created_armies_assignments_pace_and_release_are_each_judged_from_the_save(ledger):
    created = ArmyOrder("create_army", "SWE", from_state=11)
    ledger.add(created, at("1939.9.1", [group()]))
    state = ledger.decorate(at("1939.9.2", [group(), group("Army 9", ident="9", members=2)]))
    assert "confirmed: Army 9 exists with 2 division(s)" in state.order_status[0]
    ledger2 = ArmyLedger(ledger.layer)
    ledger2.add(ArmyOrder("assign_divisions", "SWE", army="alpha", from_state=11),
                at("1939.9.1", [group(members=3)]))
    assert "went 3 -> 5 divisions" in ledger2.decorate(
        at("1939.9.2", [group(members=5)])).order_status[0]
    ledger3 = ArmyLedger(ledger.layer)
    ledger3.add(ArmyOrder("set_army_execution", "SWE", army="alpha", mode="rush"),
                at("1939.9.1", [group(execution=0)]))
    assert "confirmed: alpha executes rush" in ledger3.decorate(
        at("1939.9.2", [group(execution=2)])).order_status[0]
    ledger4 = ArmyLedger(ledger.layer)
    ledger4.add(ArmyOrder("release_army", "SWE", army="alpha"), at("1939.9.1", [group()]))
    assert "carries out no plan" in ledger4.decorate(at("1939.9.2", [group()])).order_status[0]


def test_a_fallback_line_is_confirmed_by_its_kind_through_the_states(s):
    ledger = ArmyLedger(s.layer)
    order = ArmyOrder("draw_fallback_line", "SWE", army="alpha", centre_province=2, states=(11,))
    ledger.add(order, at("1939.9.1", [group()]))
    wrong = ArmyPlan("line", [2], assigned=2)
    assert "confirmed" not in ledger.decorate(at("1939.9.2", [group(plans=[wrong])])).order_status[0]
    right = ArmyPlan("fallback", [2, 6], assigned=2)
    assert "fallback line through the states" in ledger.decorate(
        at("1939.9.3", [group(plans=[right])])).order_status[0]


def test_the_executor_adds_a_ledger_line_the_next_read_judges(s):
    s.adapter.apply(call("assign_army_front", army="alpha", enemy="GER"))
    ledger = s.driver.army_executor.ledger
    assert len(ledger.orders) == 1 and ledger.orders[0].zone >= {3}
    state = ledger.decorate(at("1939.9.4", [group(plans=[ArmyPlan("line", [3], assigned=2)])]))
    assert state.delegated_armies == ["alpha"]


def test_plan_and_steps_are_pure_functions_of_the_map_and_camera(s):
    order = plan_army_action(s.layer, call("assign_army_front", army="alpha", enemy="GER"),
                             s.state(), COORDS)
    camera = s.executor.record().camera_for(s.layer, order.centre_province)
    steps = army_steps(s.layer, camera, order, s.state(), COORDS)
    assert [st.kind for st in steps][:3] == ["click_recorded", "key", "gesture"]
    with pytest.raises(OrderRefused, match="not an opponent"):
        plan_army_action(s.layer, call("assign_army_front", army="alpha", enemy="SWE"),
                         s.state(), COORDS)


# --- wired into a campaign ----------------------------------------------------------------------


def test_the_army_actions_are_off_by_default_and_the_log_says_why(tmp_path):
    from test_independent_hands import make_world, offered_after_start

    world = make_world(tmp_path)
    assert not offered_after_start(world) & set(ARMY_UI_ACTIONS)
    assert "army actions not offered: army_ui is off in the profile" in world.log


def test_the_army_actions_are_offered_when_the_profile_asks_and_the_camera_is_good(tmp_path):
    from test_independent_hands import make_world, offered_after_start

    world = make_world(tmp_path, army_ui=True)
    assert set(ARMY_UI_ACTIONS) <= offered_after_start(world)
    assert "army actions live: " in world.log and "UNVERIFIED against the real game" in world.log


def test_the_army_actions_need_what_division_orders_need(tmp_path):
    from test_independent_hands import make_world, offered_after_start

    world = make_world(tmp_path, army_ui=True, camera="missing")
    assert not offered_after_start(world) & set(ARMY_UI_ACTIONS)
    assert "army actions not offered: the army actions need what division orders need" in world.log


def test_a_text_save_with_armies_reaches_the_state_and_confirms_an_order(tmp_path, s):
    """The whole loop on fixture files: a real (trimmed) save with the country's ``theatres`` block
    put in, read by the save adapter, judged by the ledger that sent the order."""
    from pathlib import Path

    from hoi4_harness.adapters.savegame import SaveGameAdapter, read_save

    fixture = Path(__file__).parent / "fixtures" / "swe_1938_02_04.hoi4"
    text = fixture.read_text(encoding="utf-8")
    block = ("\t\ttheatres={ theatre={ orders_group={ id={ id=1 type=53 } name=\"alpha\" "
             "order_instance={ type=2 path={ 2 3 } scheduled_member={ id=9 type=4713 } } "
             "member={ unit={ id=9 type=4713 } } execution_type=1 } } }\n")
    marker = "\t\tunits={"
    assert text.count(marker) == 1
    save = tmp_path / "saves" / "SWE_1938_02_04_12.hoi4"
    save.parent.mkdir()
    save.write_text(text.replace(marker, block + marker), encoding="utf-8")
    state = SaveGameAdapter(save.parent)._to_state(read_save(save))
    assert [(g.name, g.members, g.execution) for g in state.army_groups] == [("alpha", 1, 1)]
    ledger = ArmyLedger(s.layer)
    ledger.add(front_order(), at("1938.2.1", [group(plans=[])]))
    state.date = "1938.2.4"
    assert ledger.decorate(state).delegated_armies == ["alpha"]
