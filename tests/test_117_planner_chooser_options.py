"""The two planner options that reproduce the plan chooser's habits (#117), both off by default."""

from hoi4_harness.tactics.controllers import CONTROLLERS
from hoi4_harness.tactics.env import TacticalEnv
from hoi4_harness.tactics.learned_plans import _IDX, PlanPolicy, learned_plans_controller
from hoi4_harness.tactics.picture import build_picture
from hoi4_harness.tactics.planner import Planner
from hoi4_harness.tactics.plans import PlanController
from hoi4_harness.tactics.scenario import SCENARIO_DIR

FRANCE = str(SCENARIO_DIR.parent / "realmap" / "germany_france_1940.json")
SEED = 4001   # the game where prior-only wins and the plain 0 h planner loses


def _play(ctrl, scenario=FRANCE, seed=SEED):
    env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
    env.reset(scenario, seed=seed)
    done = False
    while not done:
        done = env.step(ctrl.act(env.view())).done
    return env.metrics()["outcome"], [(h, [(p.name, p.offensive) for p in ps]) for h, ps in ctrl.history]


def _prior_only():
    w = PlanPolicy()
    w.weights[_IDX["is_default"]] = 5.0
    return learned_plans_controller(w)


def test_117_options_are_off_by_default():
    p = Planner()
    assert p.reset_idle_commit is False and p.sort_axes is False


def test_117_sort_axes_hands_the_axes_on_in_region_id_order():
    env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
    env.reset(FRANCE, seed=SEED)
    view = env.view()
    plain = Planner(patience=0)
    sorted_ = Planner(patience=0, sort_axes=True)
    pic = build_picture(view, "aggregated")
    names = [s.name for s in plain.plan(view, pic)]
    plans = sorted_.plan(view, build_picture(view, "aggregated"))
    assert names == [s.name for s in plans]
    axes = [p for p in plans if p.kind == "offensive"]
    # the first offensive plan is the one aimed at the lowest region id
    targets = [min(pic.region_of[x] for x in p.offensive[:1]) for p in axes]
    assert targets == sorted(targets)


def test_117_reset_idle_commit_stops_an_idle_planner_replanning_every_step():
    plain = PlanController(Planner(patience=0), name="p")
    reset = PlanController(Planner(patience=0, reset_idle_commit=True), name="p")
    _play(plain)
    _play(reset)
    assert len(reset.history) < len(plain.history)


def test_117_both_options_together_are_the_prior_only_chooser():
    chooser = _prior_only()
    ported = PlanController(Planner(patience=0, reset_idle_commit=True, sort_axes=True), name="p")
    assert _play(ported) == _play(chooser)
