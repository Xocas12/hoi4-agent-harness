"""Battle plans and the engine executor (#85, stage 4): spread, attack along the line, re-form."""

import pytest

from hoi4_harness.tactics.env import View
from hoi4_harness.tactics.model import Order
from hoi4_harness.tactics.plans import (
    BattlePlan,
    EngineExecutor,
    PlanController,
    min_odds,
)
from hoi4_harness.tactics.sim import World
from tactics_helpers import grid_scenario


def world(divisions, cols=4, rows=3, split=2, **kw):
    return World(grid_scenario(cols, rows, split, divisions, **kw), 0)


def ids(w, side="A"):
    return tuple(d.id for d in w.alive(side))


def orders_by_division(orders):
    return {o.division: o for o in orders}


def test_a_plan_validates_its_shape():
    with pytest.raises(ValueError, match="unknown plan kind"):
        BattlePlan("x", "A", ("A1",), ("c1r1",), kind="charge")
    with pytest.raises(ValueError, match="between 0 and 1"):
        BattlePlan("x", "A", ("A1",), ("c1r1",), aggressiveness=1.5)
    with pytest.raises(ValueError, match="needs a front line"):
        BattlePlan("x", "A", ("A1",), (), offensive=("c2r1",))


def test_aggressiveness_maps_to_a_local_odds_floor():
    assert min_odds(0.0) is None
    assert min_odds(1.0) == 0.0
    assert min_odds(0.25) == pytest.approx(1.5)


def test_divisions_are_spread_along_the_front_line():
    w = world([{"side": "A", "template": "inf_9", "province": "c0r1", "count": 4},
               {"side": "B", "template": "inf_9", "province": "c3r1"}])
    front = ("c1r0", "c1r1", "c1r2")
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("line", "A", ids(w), front)])
    targets = [o.target for o in ex.orders(w) if o.kind == "move"]
    assert sorted(set(targets)) == sorted(front)                 # every province of the line is taken
    assert max(targets.count(p) for p in front) == 2             # 4 over 3 provinces: 2, 1, 1


def test_a_division_on_the_line_stays_and_does_not_shuffle():
    w = world([{"side": "A", "template": "inf_9", "province": "c1r0"},
               {"side": "A", "template": "inf_9", "province": "c1r1"},
               {"side": "A", "template": "inf_9", "province": "c1r2"},
               {"side": "B", "template": "inf_9", "province": "c3r1"}])
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("line", "A", ids(w), ("c1r0", "c1r1", "c1r2"))])
    assert {o.kind for o in ex.orders(w)} == {"hold"}


def test_the_line_is_weighted_toward_the_arrow():
    w = world([{"side": "A", "template": "inf_9", "province": "c0r1", "count": 6},
               {"side": "B", "template": "inf_9", "province": "c2r1"}])
    front = ("c1r0", "c1r1", "c1r2")
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("push", "A", ids(w), front, ("c2r1",), 1.0, "offensive")])
    got = [o.target for o in ex.orders(w) if o.kind == "move"]
    assert got.count("c1r1") > got.count("c1r0")                 # the province facing the arrow gets more


def test_divisions_attack_across_the_line_only_next_to_the_arrow():
    w = world([{"side": "A", "template": "inf_9", "province": "c1r1"},
               {"side": "A", "template": "inf_9", "province": "c1r0"},
               {"side": "B", "template": "inf_9", "province": "c2r1"}])
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("push", "A", ids(w), ("c1r0", "c1r1"), ("c2r1",), 1.0, "offensive")])
    got = orders_by_division(ex.orders(w))
    assert got["A1"] == Order("A1", "attack", "c2r1")
    assert got["A2"].kind != "attack"                            # c1r0 does not touch the arrow


def test_aggressiveness_zero_never_attacks_and_a_cautious_plan_waits_for_odds():
    divs = [{"side": "A", "template": "inf_9", "province": "c1r1"},
            {"side": "B", "template": "inf_9", "province": "c2r1", "count": 3}]
    w = world(divs)
    for aggr in (0.0, 0.25):                                    # 0.25 needs 1.5x: one against three is not
        ex = EngineExecutor()
        ex.set_plans([BattlePlan("p", "A", ids(w), ("c1r1",), ("c2r1",), aggr, "offensive")])
        assert not [o for o in ex.orders(w) if o.kind == "attack"], aggr
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("p", "A", ids(w), ("c1r1",), ("c2r1",), 1.0, "offensive")])
    assert [o.kind for o in ex.orders(w)] == ["attack"]


def test_a_tired_division_does_not_commit():
    w = world([{"side": "A", "template": "inf_9", "province": "c1r1", "org": 0.2},
               {"side": "B", "template": "inf_9", "province": "c2r1"}])
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("p", "A", ids(w), ("c1r1",), ("c2r1",), 1.0, "offensive")])
    assert not [o for o in ex.orders(w) if o.kind == "attack"]


def test_an_empty_enemy_province_on_the_arrow_is_walked_into():
    w = world([{"side": "A", "template": "inf_9", "province": "c1r1"},
               {"side": "B", "template": "inf_9", "province": "c3r1"}])
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("p", "A", ids(w), ("c1r1",), ("c2r1", "c3r1"), 0.5, "offensive")])
    assert ex.orders(w) == [Order("A1", "move", "c2r1")]


def test_the_line_re_forms_on_ground_the_arrow_has_won():
    w = world([{"side": "A", "template": "inf_9", "province": "c1r1", "count": 2},
               {"side": "B", "template": "inf_9", "province": "c3r1", "count": 2}])
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("p", "A", ids(w), ("c1r1",), ("c2r1", "c3r1"), 1.0, "offensive")])
    w.provinces["c2r1"].controller = "A"                         # the first province of the arrow is taken
    got = ex.orders(w)
    assert {o.target for o in got if o.kind == "move"} == {"c2r1"}   # the line moves up to c2r1


def test_a_division_serves_one_plan():
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("one", "A", ("A1", "A2"), ("c1r0",)),
                  BattlePlan("two", "A", ("A2",), ("c1r2",))])
    assert ex.plans[1].divisions == ()
    assert ex.assigned() == {"A1", "A2"}


def test_unassigned_divisions_get_no_order():
    w = world([{"side": "A", "template": "inf_9", "province": "c1r1", "count": 2},
               {"side": "B", "template": "inf_9", "province": "c3r1"}])
    ex = EngineExecutor()
    ex.set_plans([BattlePlan("one", "A", ("A1",), ("c1r0",))])
    assert {o.division for o in ex.orders(w)} == {"A1"}


def test_a_plan_controller_runs_a_planner_then_the_executor():
    w = world([{"side": "A", "template": "inf_9", "province": "c1r1"},
               {"side": "B", "template": "inf_9", "province": "c2r1"}])

    class Fixed:
        def update(self, view):
            return [BattlePlan("p", "A", ("A1",), ("c1r1",), ("c2r1",), 1.0, "offensive")]

    ctrl = PlanController(Fixed(), name="fixed")
    assert ctrl.act(View(w, "A")) == [Order("A1", "attack", "c2r1")]
    assert ctrl.history[0][1][0].name == "p"
