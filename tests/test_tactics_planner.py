"""The front planner (#85, stage 4) on hand-built maps, both observation modes."""

import pytest

from hoi4_harness.tactics import CONTROLLERS, TacticalEnv
from hoi4_harness.tactics.env import View
from hoi4_harness.tactics.picture import build_picture, reference_power
from hoi4_harness.tactics.planner import Planner, VanillaPlanner, planner_controller
from hoi4_harness.tactics.sim import World
from tactics_helpers import graph_scenario, grid_scenario


def inf(side, province, count=1, **kw):
    return {"side": side, "template": "inf_9", "province": province, "count": count, **kw}


def thin_sector_world():
    """4x3 grid, A in columns 0-1, B in 2-3. B is thick in rows 0 and 2 and thin in row 1."""
    sc = grid_scenario(4, 3, 2, [
        inf("A", "c1r0", 2), inf("A", "c1r1", 5), inf("A", "c1r2", 2),
        inf("B", "c2r0", 4), inf("B", "c2r1", 1), inf("B", "c2r2", 4),
    ], goals={"A": {"kind": "capture", "provinces": ["c3r1"]}, "B": {"kind": "hold", "provinces": ["c3r1"]}})
    return World(sc, 0)


@pytest.mark.parametrize("observe", ["aggregated", "full"])
def test_the_planner_attacks_the_thin_sector(observe):
    w = thin_sector_world()
    plans = Planner(observe).replan(View(w, "A"))
    offensive = [p for p in plans if p.kind == "offensive"]
    assert offensive, [p.name for p in plans]
    assert "c2r1" in offensive[0].offensive
    assert not {"c2r0", "c2r2"} & set(offensive[0].offensive)    # not the thick sectors


def pocket_world():
    """B's pocket P is supplied through T1 and T2; only taking both cuts it off."""
    nodes = {"a1": ("A", "A"), "a2": ("A", None), "a3": ("A", None),
             "t1": ("B", None), "t2": ("B", None), "p": ("B", None), "h": ("B", "B")}
    edges = [("a1", "a2"), ("a1", "a3"), ("a2", "t1"), ("a3", "t2"), ("t1", "p"), ("t2", "p"),
             ("t1", "h"), ("t2", "h")]
    sc = graph_scenario(nodes, edges, [inf("A", "a2", 3), inf("A", "a3", 3), inf("B", "t1"),
                                       inf("B", "t2"), inf("B", "p", 2), inf("B", "h")],
                        {"A": {"kind": "encircle", "min_cut_off": 2},
                         "B": {"kind": "hold", "provinces": ["h"]}})
    return World(sc, 0)


@pytest.mark.parametrize("observe", ["aggregated", "full"])
def test_the_planner_draws_a_pincer_on_two_axes_behind_a_salient(observe):
    w = pocket_world()
    plans = Planner(observe).replan(View(w, "A"))
    arrows = [p for p in plans if p.kind == "offensive"]
    assert len(arrows) == 2
    assert {t for p in arrows for t in p.offensive} == {"t1", "t2"}   # neither alone cuts the pocket


def test_one_axis_is_enough_when_it_cuts_the_pocket_alone():
    nodes = {"a1": ("A", "A"), "a2": ("A", None), "t1": ("B", None), "p": ("B", None), "h": ("B", "B")}
    edges = [("a1", "a2"), ("a2", "t1"), ("t1", "p"), ("t1", "h")]
    sc = graph_scenario(nodes, edges, [inf("A", "a2", 4), inf("B", "t1"), inf("B", "p", 2), inf("B", "h")],
                        {"A": {"kind": "encircle", "min_cut_off": 2},
                         "B": {"kind": "hold", "provinces": ["h"]}})
    plans = Planner("full").replan(View(World(sc, 0), "A"))
    arrows = [p for p in plans if p.kind == "offensive"]
    assert len(arrows) == 1 and arrows[0].offensive == ("t1",)


def test_a_forming_pocket_makes_a_fallback_plan_toward_supply():
    """a0 (hub) - a1 - a2 - a3: B is next to a1, so losing it strands a2 and a3. a1 is not an objective."""
    nodes = {"a0": ("A", "A"), "a1": ("A", None), "a2": ("A", None), "a3": ("A", None),
             "b1": ("B", None), "b0": ("B", "B")}
    edges = [("a0", "a1"), ("a1", "a2"), ("a2", "a3"), ("a1", "b1"), ("b1", "b0")]
    sc = graph_scenario(nodes, edges, [inf("A", "a1"), inf("A", "a2", 2), inf("A", "a3", 2),
                                       inf("B", "b1", 5)],
                        {"A": {"kind": "hold", "provinces": ["a0"]},
                         "B": {"kind": "capture", "provinces": ["a0"]}})
    plans = Planner("full").replan(View(World(sc, 0), "A"))
    fallbacks = [p for p in plans if p.kind == "fallback"]
    assert fallbacks and "a0" in fallbacks[0].front
    holders = [p for p in plans if "a1" in p.front and p.kind == "defend"]
    assert holders and holders[0].divisions                      # and the choke itself stays garrisoned


def test_defence_shares_divisions_by_threat_not_evenly():
    sc = grid_scenario(4, 3, 2, [inf("A", "c1r0", 2), inf("A", "c1r1", 2), inf("A", "c1r2", 2),
                                 inf("B", "c2r0", 5), inf("B", "c2r1", 1), inf("B", "c2r2", 1)],
                       goals={"A": {"kind": "hold", "provinces": ["c1r1"]},
                              "B": {"kind": "capture", "provinces": ["c1r1"]}})
    plans = Planner("full").replan(View(World(sc, 0), "A"))
    size = {p.front[0]: len(p.divisions) for p in plans if p.kind == "defend"}
    assert size["c1r0"] > size["c1r2"]                          # the sector facing five gets more


def test_the_reserve_is_kept_behind_the_hottest_sector_on_a_big_force():
    sc = grid_scenario(5, 3, 3, [inf("A", "c2r0", 3), inf("A", "c2r1", 4), inf("A", "c2r2", 3),
                                 inf("B", "c3r0", 1), inf("B", "c3r1", 1), inf("B", "c3r2", 1)],
                       goals={"A": {"kind": "hold", "provinces": ["c2r1"]},
                              "B": {"kind": "capture", "provinces": ["c2r1"]}})
    plans = Planner("full").replan(View(World(sc, 0), "A"))
    reserve = [p for p in plans if p.kind == "reserve"]
    assert reserve and reserve[0].aggressiveness == 0.0
    assert not reserve[0].offensive


def test_every_division_is_assigned_to_a_plan_once():
    w = thin_sector_world()
    plans = Planner("aggregated").replan(View(w, "A"))
    assigned = [d for p in plans for d in p.divisions]
    assert sorted(assigned) == sorted(d.id for d in w.alive("A"))


# --- the observation modes --------------------------------------------------------------


def test_aggregated_mode_sees_counts_per_region_not_divisions():
    w = thin_sector_world()
    w.divisions["A1"].strength = 5.0                              # one of A's is nearly dead
    agg = build_picture(View(w, "A"), "aggregated")
    full = build_picture(View(w, "A"), "full")
    assert len(full.regions) == 12 and len(agg.regions) < len(full.regions)
    region = agg.regions[agg.region_of["c1r0"]]
    assert (region.own, region.own_weak) == (2, 1)                # only "one is under half" survives
    ref = reference_power()
    assert region.own_power == pytest.approx(ref * (1 + 0.4))     # counts, not the real strengths
    assert full.regions["c1r0"].own_power < ref * 2               # full mode reads the real ones


def test_aggregated_regions_never_straddle_the_starting_front():
    w = thin_sector_world()
    pic = build_picture(View(w, "A"), "aggregated")
    for region in pic.regions.values():
        assert len({w.provinces[p].controller for p in region.provinces}) == 1


def test_a_region_changes_hands_on_a_strict_majority():
    w = thin_sector_world()
    pic = build_picture(View(w, "A"), "aggregated")
    both = pic.region_of["c2r1"]
    assert {p for p in pic.regions[both].provinces} == {"c2r1", "c3r1"}
    w.provinces["c2r1"].controller = "A"                         # half of the region
    assert build_picture(View(w, "A"), "aggregated").regions[both].controller == "B"
    w.provinces["c3r1"].controller = "A"
    assert build_picture(View(w, "A"), "aggregated").regions[both].controller == "A"


def test_the_planner_defaults_to_the_aggregated_observation():
    assert Planner().observe == "aggregated"
    assert planner_controller().planner.observe == "aggregated"
    assert planner_controller("full").name == "planner_full"
    with pytest.raises(ValueError):
        build_picture(View(thin_sector_world(), "A"), "telepathy")


# --- cadence and events ---------------------------------------------------------------


def test_it_replans_on_a_cadence_and_at_once_on_an_event():
    w = thin_sector_world()
    planner = Planner("full", cadence=24)
    view = View(w, "A")
    assert planner.update(view) is not None and planner.last_reason == "start"
    w.hour = 6
    assert planner.update(view) is None                          # nothing happened, not due
    w.provinces["c2r1"].controller = "A"                         # an event: a province changed hands
    assert planner.update(view) is not None and planner.last_reason == "control changed"
    assert planner.update(view) is None
    w.hour = 24 + 6
    assert planner.update(view) is not None and planner.last_reason == "cadence"


def test_a_lost_division_is_an_event():
    w = thin_sector_world()
    planner = Planner("full", cadence=1000)
    planner.update(View(w, "A"))
    w.divisions["A1"].alive = False
    assert planner.update(View(w, "A")) is not None
    assert planner.last_reason == "division lost"


# --- the baselines and a whole episode -------------------------------------------------------


def test_the_vanilla_plan_baseline_puts_everyone_on_one_plan_straight_across():
    w = thin_sector_world()
    plans = VanillaPlanner().update(View(w, "A"))
    assert len(plans) == 1 and plans[0].aggressiveness == 1.0
    assert sorted(plans[0].divisions) == sorted(d.id for d in w.alive("A"))
    assert VanillaPlanner().update(View(w, "A")) is not None
    v = VanillaPlanner()
    v.update(View(w, "A"))
    assert v.update(View(w, "A")) is None                         # daily, not every look


def test_the_planner_wins_an_episode_on_the_thin_sector_map():
    env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
    env.reset(grid_scenario(5, 3, 2, [inf("A", "c1r0", 2), inf("A", "c1r1", 6), inf("A", "c1r2", 2),
                                      inf("B", "c2r0", 3), inf("B", "c2r1", 1), inf("B", "c2r2", 3)],
                            max_hours=480,
                            goals={"A": {"kind": "capture", "provinces": ["c2r1", "c3r1"]},
                                   "B": {"kind": "hold", "provinces": ["c2r1", "c3r1"]}}), seed=4)
    ctrl = CONTROLLERS["planner"]()
    done = False
    while not done:
        done = env.step(ctrl.act(env.view())).done
    assert env.metrics()["outcome"] == "win"
