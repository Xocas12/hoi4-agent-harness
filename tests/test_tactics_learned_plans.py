"""The learned plan chooser (#85): aggregated-only features, distillation, the ES honesty rule."""

import json

import pytest

from hoi4_harness.tactics import CONTROLLERS
from hoi4_harness.tactics.env import View
from hoi4_harness.tactics.learned_plans import (
    FEATURE_NAMES,
    AxisTeacher,
    History,
    LearnedPlanner,
    PlanPolicy,
    candidate_features,
    candidates,
    default_axes,
    learned_plans_controller,
    teacher_choice,
)
from hoi4_harness.tactics.picture import build_picture
from hoi4_harness.tactics.plans import PlanController
from hoi4_harness.tactics.registry import make
from hoi4_harness.tactics.scenario import SCENARIO_DIR, load_scenario
from hoi4_harness.tactics.sim import World
from hoi4_harness.tactics.train_plans import (
    HELD_OUT_FRONT,
    clone,
    collect_teacher,
    evolve,
    keep_if_better,
    make_jobs,
    make_pool,
    training_sources,
)
from hoi4_harness.tactics.variants import load_raw, vary
from tactics_helpers import grid_scenario


def inf(side, province, count=1, **kw):
    return {"side": side, "template": "inf_9", "province": province, "count": count, **kw}


def world(b_row1=1):
    """4x3 grid, A in columns 0-1, B in 2-3, B thin in the middle row; A must take c3r1."""
    sc = grid_scenario(4, 3, 2, [
        inf("A", "c1r0", 2), inf("A", "c1r1", 5), inf("A", "c1r2", 2),
        inf("B", "c2r0", 4), inf("B", "c2r1", b_row1), inf("B", "c2r2", 4),
    ], goals={"A": {"kind": "capture", "provinces": ["c3r1"]}, "B": {"kind": "hold", "provinces": ["c3r1"]}})
    return World(sc, 0)


def feats_of(w, observe="aggregated"):
    pic = build_picture(View(w, "A"), observe)
    return pic, candidates(pic, History())


def test_the_student_refuses_a_full_information_picture():
    pic = build_picture(View(world(), "A"), "full")
    with pytest.raises(ValueError, match="aggregated"):
        candidate_features(pic, History(), (), ())


def test_features_cannot_see_what_the_aggregated_picture_does_not_carry():
    """Same counts, different organisation and strength (above the half-strength line): same features.
    Take a division under half strength and the picture, and so the features, change."""
    a, b = world(), world()
    for d in b.alive("B"):
        d.org = d.org * 0.3
        d.strength = d.strength * 0.8
    for d in b.alive("A"):
        d.strength = d.strength * 0.9
    _, fa = feats_of(a)
    _, fb = feats_of(b)
    assert [(c.regions, c.feats) for c in fa] == [(c.regions, c.feats) for c in fb]
    weak = world()
    next(iter(weak.alive("B"))).strength *= 0.3
    _, fw = feats_of(weak)
    assert [c.feats for c in fw] != [c.feats for c in fa]


def test_history_deltas_reveal_where_the_enemy_went():
    w = world()
    pic = build_picture(View(w, "A"), "aggregated")
    hist = History()
    hist.observe(pic)
    for d in w.alive("B"):               # the enemy pulls its c2r2 stack up into c2r1's region
        if d.province == "c2r2":
            d.province = "c2r1"
    pic2 = build_picture(View(w, "A"), "aggregated")
    hist.observe(pic2)
    rid = pic2.region_of["c2r1"]
    assert hist.delta[rid] > 0
    still = History()
    still.observe(pic2)
    default = default_axes(pic2)
    a = candidate_features(pic2, hist, (rid,), tuple(sorted(default)))
    b = candidate_features(pic2, still, (rid,), tuple(sorted(default)))
    assert a != b
    assert a[FEATURE_NAMES.index("d_member")] > 0 == b[FEATURE_NAMES.index("d_member")]


def test_candidates_hold_the_empty_plan_and_the_planners_own_pick():
    pic, cands = feats_of(world())
    sets = [c.regions for c in cands]
    assert () in sets
    assert tuple(sorted(default_axes(pic))) in sets
    assert all(len(c.feats) == len(FEATURE_NAMES) for c in cands)


def test_teacher_choice_maps_provinces_to_the_aggregated_regions():
    pic, cands = feats_of(world())
    teacher_axes = ["c2r1"]
    idx = teacher_choice(pic, cands, teacher_axes)
    assert idx is not None and cands[idx].regions == (pic.region_of["c2r1"],)
    assert teacher_choice(pic, cands, ["c0r0"]) is None      # not among the candidates


def test_cloning_reproduces_the_teachers_choices_on_a_fixed_set():
    data = collect_teacher(make_jobs([load_scenario("close_encirclement"), load_scenario("break_line")],
                                     [0, 1], ["vanilla"]))
    assert len(data) >= 20
    zero, acc0 = clone(data, epochs=0)
    policy, acc = clone(data, epochs=14)
    assert acc >= 0.65
    assert acc > acc0 + 0.1
    picks = sum(max(range(len(f)), key=lambda i: policy.score(f[i])) == y for f, y in data)
    assert picks / len(data) >= 0.65
    assert zero.weights == [0.0] * len(FEATURE_NAMES)


def test_es_is_kept_only_if_it_beats_the_clone_on_validation():
    start = PlanPolicy([0.0] * len(FEATURE_NAMES))
    better = PlanPolicy([1.0] * len(FEATURE_NAMES))
    worse = PlanPolicy([-1.0] * len(FEATURE_NAMES))
    fit = lambda p, jobs: sum(p.weights)          # noqa: E731
    chosen, verdict = keep_if_better(start, better, [None], fit)
    assert chosen is better and verdict["es_kept"]
    chosen, verdict = keep_if_better(start, worse, [None], fit)
    assert chosen is start and not verdict["es_kept"]
    chosen, verdict = keep_if_better(start, PlanPolicy([0.0] * len(FEATURE_NAMES)), [None], fit)
    assert chosen is start and not verdict["es_kept"]      # a tie keeps the clone


def test_evolution_strategies_climb_a_fitness_it_can_see():
    start = PlanPolicy([0.0] * len(FEATURE_NAMES))
    fit = lambda p, jobs: -sum((w - 1.0) ** 2 for w in p.weights[:5])      # noqa: E731
    out, history = evolve(start, lambda g: [None], seed=1, seconds=30, max_generations=40, fit=fit)
    assert fit(out, None) > fit(start, None)
    assert len(history) == 40


def test_the_student_plays_and_is_deterministic():
    sc = load_scenario("close_encirclement")
    from hoi4_harness.tactics.evaluate import run_episode

    a = run_episode(sc, 3, learned_plans_controller(), "vanilla")
    b = run_episode(sc, 3, learned_plans_controller(), "vanilla")
    assert a == b


def test_a_recording_student_labels_its_own_states_with_the_teachers_pick():
    from hoi4_harness.tactics.evaluate import run_episode

    rec: list = []
    ctrl = PlanController(LearnedPlanner(PlanPolicy(), teacher=AxisTeacher(), recorder=rec), name="s")
    run_episode(load_scenario("break_line"), 0, ctrl, "vanilla")
    assert rec and all(0 <= y < len(f) for f, y in rec)


def test_the_name_is_registered_and_builds_a_fresh_controller():
    first, second = make("learned_plans"), make("learned_plans")
    assert isinstance(first, PlanController) and first is not second
    assert first.name == "learned_plans" and "learned_plans" in CONTROLLERS


def test_variants_are_seeded_changes_of_the_same_map_and_goals():
    raw = load_raw("hold_line")
    a, b, c = vary(raw, 1), vary(raw, 1), vary(raw, 2)
    assert json.dumps(a.provinces) == json.dumps(b.provinces) and a.divisions == b.divisions
    assert (a.provinces, a.edges, a.divisions) != (c.provinces, c.edges, c.divisions)
    assert a.goals == raw["goals"] and len(a.provinces) == len(raw["provinces"])
    assert a.name == "hold_line~v1"


def test_the_held_out_front_is_never_in_the_training_pool():
    sources = training_sources()
    assert not any(HELD_OUT_FRONT in s for s in sources)
    assert any("germany_poland_1939" in s for s in sources) and len(sources) == 5
    pool = make_pool(sources, [0, 1])
    assert not any(HELD_OUT_FRONT in sc.name for sc in pool)
    assert (SCENARIO_DIR.parent / "realmap" / f"{HELD_OUT_FRONT}.json").exists()
