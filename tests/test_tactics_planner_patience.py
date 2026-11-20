"""The planner's axis patience is a parameter, and can scale with map size (#112)."""

import json

import pytest

from hoi4_harness.tactics.controllers import CONTROLLERS
from hoi4_harness.tactics.env import TacticalEnv
from hoi4_harness.tactics.learned_plans import LearnedPlanner
from hoi4_harness.tactics.picture import build_picture
from hoi4_harness.tactics.planner import (
    PATIENCE,
    PATIENCE_REF_REGIONS,
    Planner,
    planner_controller,
    scaled_patience,
)
from hoi4_harness.tactics.scenario import SCENARIO_DIR

REAL = SCENARIO_DIR.parent / "realmap" / "germany_poland_1939.json"


def _pic(scenario):
    env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
    env.reset(str(scenario), seed=1)
    return build_picture(env.view(), "aggregated")


def test_default_patience_is_unchanged():
    assert PATIENCE == 96
    assert Planner().patience == 96
    assert planner_controller().planner.patience == 96


def test_patience_argument_is_honoured():
    pic = _pic(SCENARIO_DIR / "hold_line.json")
    assert Planner(patience=24).patience_hours(pic) == 24
    assert Planner(patience=0).patience_hours(pic) == 0
    assert planner_controller(patience=48).planner.patience == 48
    assert LearnedPlanner(patience=12).patience == 12


def test_bad_patience_is_refused():
    with pytest.raises(ValueError):
        Planner(patience=-1)
    with pytest.raises(ValueError):
        Planner(patience="long")


def test_scaling_rule():
    assert scaled_patience(1) == PATIENCE
    assert scaled_patience(PATIENCE_REF_REGIONS) == PATIENCE
    assert scaled_patience(2 * PATIENCE_REF_REGIONS) == PATIENCE // 2
    assert scaled_patience(10_000) == 1
    sizes = [10, 24, 30, 40, 80]
    vals = [scaled_patience(n) for n in sizes]
    assert vals == sorted(vals, reverse=True)


def test_scaled_patience_reads_the_map_size():
    small = _pic(SCENARIO_DIR / "hold_line.json")
    big = _pic(REAL)
    p = Planner(patience="scaled")
    assert p.patience_hours(small) == PATIENCE
    assert p.patience_hours(big) == scaled_patience(len(big.regions)) < PATIENCE


def test_patience_changes_how_long_an_axis_is_kept():
    """With patience 0 the planner re-decides at every re-plan, so on the same game it can differ
    from the 96 h planner; with equal patience it cannot."""
    def axes_over_game(patience):
        env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
        env.reset(str(REAL), seed=4000)
        ctrl = planner_controller(patience=patience)
        out, done = [], False
        while not done:
            done = env.step(ctrl.act(env.view())).done
            out.append(tuple(ctrl.planner._commit[0]) if ctrl.planner._commit else ())
        return out

    assert axes_over_game(96) == axes_over_game(96)
    assert axes_over_game(0) != axes_over_game(10_000)


def test_bench_results_if_committed():
    path = SCENARIO_DIR.parent / "patience_results.json"
    if not path.exists():
        pytest.skip("results not committed")
    data = json.loads(path.read_text(encoding="utf-8"))
    for r in data["rows"]:
        assert r["wins"] == r["outcomes"].count("W") and len(r["outcomes"]) == r["seeds"]
