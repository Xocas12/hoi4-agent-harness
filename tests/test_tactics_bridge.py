"""Telemetry -> simulator -> orders -> harness actions (#85): the plumbing, on a fake front."""

import pytest

from hoi4_harness.tactics import ScriptedController, VanillaLikeController
from hoi4_harness.tactics.bridge import BridgeError, ControllerBridge
from hoi4_harness.tactics.model import Order
from hoi4_harness.types import ActionCall, GameState
from orders_helpers import stack


@pytest.fixture
def s(tmp_path):
    return stack(tmp_path)


def test_the_front_picture_becomes_a_world_with_the_divisions_where_it_says(s):
    bridge = ControllerBridge(s.layer, VanillaLikeController())
    world = bridge.build(s.state())
    sides = {}
    for d in world.alive():
        sides.setdefault(d.side, []).append(bridge.home[d.id])
    assert sorted(sides["A"]) == [11, 11, 11] and sorted(sides["B"]) == [12, 12]
    # State 13 and 10 were not in the picture; they are the room around it.
    assert {p.controller for p in world.provinces.values()} == {"A", "B"}
    assert world.provinces["3"].terrain == "hills"              # from definition.csv


def test_the_weak_count_lowers_strength(s):
    state = s.state()
    for f in state.state_forces:
        if f.state == 11:
            f.own_weak = 1
    world = ControllerBridge(s.layer, VanillaLikeController()).build(state)
    mine = sorted(round(d.strength_frac, 2) for d in world.alive("A"))
    assert mine == [0.4, 1.0, 1.0]


def test_a_vanilla_controller_attacks_the_enemy_state_and_the_action_is_state_level(s):
    calls = ControllerBridge(s.layer, VanillaLikeController()).decide(s.state())
    assert [(c.name, c.arguments) for c in calls] == [
        ("attack_province", {"from_state": 11, "to_state": 12})]
    assert "vanilla: 1 division(s)" in calls[0].rationale


def test_the_scripted_controller_runs_through_the_same_plumbing(s):
    calls = ControllerBridge(s.layer, ScriptedController()).decide(s.state())
    assert calls and all(c.name in ("attack_province", "move_division") for c in calls)
    assert all(set(c.arguments) == {"from_state", "to_state"} for c in calls)


def test_the_controller_drives_real_orders_on_the_fake_game(s):
    calls = ControllerBridge(s.layer, VanillaLikeController()).decide(s.state())
    result = s.adapter.apply(calls[0])
    assert result.error_kind == "unverified", result.message
    s.fake.run_days(14)
    # The click selects the whole state, so all three go (the controller asked for one),
    # to the default province of state 12, which is empty: they walk in, the defenders
    # in province 7 keep the state.
    here = {f.state: f for f in s.state().state_forces}
    assert here[12].own == 3 and here[12].foe == 2 and s.army.control[12] == "GER"


def test_orders_inside_a_state_and_holds_are_not_actions(s):
    bridge = ControllerBridge(s.layer, VanillaLikeController())
    world = bridge.build(s.state())
    ids = sorted(bridge.home)
    mine = [d for d in ids if world.divisions[d].side == "A"]
    orders = [Order(mine[0], "hold"), Order(mine[1], "move", world.divisions[mine[1]].province),
              Order(mine[2], "retreat", "5")]
    calls = bridge.translate(world, orders)
    assert [(c.name, c.arguments) for c in calls] == [
        ("move_division", {"from_state": 11, "to_state": 10})]


def test_nothing_is_built_from_an_unobserved_or_empty_picture(s):
    bridge = ControllerBridge(s.layer, VanillaLikeController())
    for picture in (None, []):
        with pytest.raises(BridgeError, match="nothing to control"):
            bridge.build(GameState(country="SWE", state_forces=picture))
    assert isinstance(ActionCall("note", {}), ActionCall)


def test_a_template_the_simulator_lacks_is_refused(s):
    with pytest.raises(ValueError, match="no division template"):
        ControllerBridge(s.layer, VanillaLikeController(), template="panzer")
