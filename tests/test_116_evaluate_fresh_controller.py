"""evaluate() gives every game a controller no earlier game touched (#116)."""

from hoi4_harness.tactics.evaluate import evaluate
from hoi4_harness.tactics.planner import planner_controller

SEEDS, FIRST = 8, 4000


def _outcomes(res):
    return [r["outcome"] for r in res["runs"]]


def test_116_instance_gives_the_same_games_as_the_registered_name():
    by_name = evaluate("break_line", "planner", SEEDS, "vanilla", FIRST)
    by_instance = evaluate("break_line", planner_controller(), SEEDS, "vanilla", FIRST)
    assert by_name["wins"] == SEEDS   # the planner wins this scenario; a leak turns wins into losses
    assert _outcomes(by_instance) == _outcomes(by_name)


def test_116_instance_passed_in_is_left_untouched():
    ctrl = planner_controller()
    evaluate("break_line", ctrl, 3, "vanilla", FIRST)
    assert ctrl.planner._commit is None
    assert ctrl.history == []


def test_116_factory_is_called_once_per_game():
    made = []

    def factory():
        made.append(planner_controller())
        return made[-1]

    res = evaluate("break_line", factory, 3, "vanilla", FIRST)
    assert len(made) == 3 and len({id(c) for c in made}) == 3
    assert res["wins"] == 3
