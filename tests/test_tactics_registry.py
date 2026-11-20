"""Army controllers by name (#85, stage 4)."""

import pytest

from hoi4_harness.tactics import CONTROLLERS, TacticalEnv
from hoi4_harness.tactics.controllers import ScriptedController, VanillaLikeController
from hoi4_harness.tactics.learned import LearnedController
from hoi4_harness.tactics.plans import PlanController
from hoi4_harness.tactics.registry import FACTORIES, create


def test_the_registry_names_the_army_controllers():
    assert {"scripted", "learned", "vanilla_like", "planner"} <= set(FACTORIES)


@pytest.mark.parametrize("name, kind", [
    ("scripted", ScriptedController), ("learned", LearnedController),
    ("vanilla_like", VanillaLikeController), ("planner", PlanController),
])
def test_a_name_builds_a_fresh_controller(name, kind):
    first, second = create(name), create(name)
    assert isinstance(first, kind) and first is not second


def test_an_unknown_name_says_what_is_known():
    with pytest.raises(KeyError, match="no army controller 'gambit'.*planner"):
        create("gambit")


def test_the_planner_from_the_registry_plays_a_scenario():
    env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
    env.reset("hold_line", seed=1)
    ctrl = create("planner")
    for _ in range(5):
        env.step(ctrl.act(env.view()))
    assert env.metrics()["illegal_orders"] == 0


def test_the_evaluation_table_knows_the_plan_controllers():
    assert {"vanilla_plans", "planner", "planner_full"} <= set(CONTROLLERS)
