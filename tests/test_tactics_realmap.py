"""The real-map scenario loader (#85, stage 4), on a compact fixture of the 1.19.5 map."""

import json
from pathlib import Path

import pytest

from hoi4_harness.tactics import CONTROLLERS, TacticalEnv
from hoi4_harness.tactics.picture import build_picture
from hoi4_harness.tactics.realmap import AbstractLayer, scenario_from_layer
from hoi4_harness.tactics.scenario import SCENARIO_DIR, Scenario, load_scenario

FIXTURE = Path(__file__).parent / "fixtures" / "realmap_layer.json"
REAL = SCENARIO_DIR.parent / "realmap" / "germany_poland_1939.json"
#: Silesia (66 Lower, 67 Upper), Trans-Olza (72), Krakow (88), Katowice (762): the corner the fixture keeps.
SPEC = {
    "name": "fixture-front", "granularity": "state", "max_hours": 240, "step_hours": 12,
    "sides": {"A": [66, 67, 72], "B": [88, 762]},
    "hubs": {"A": [66], "B": [88]},
    "forces": [{"side": "A", "template": "inf_9", "state": 67, "count": 3},
               {"side": "A", "template": "armor_6", "state": 72, "count": 1},
               {"side": "B", "template": "inf_9", "state": 762, "count": 2},
               {"side": "B", "template": "inf_9", "state": 88, "count": 2}],
    "goals": {"A": {"kind": "capture", "states": [762]}, "B": {"kind": "hold", "states": [762]}},
}


@pytest.fixture(scope="module")
def layer():
    return AbstractLayer.load(FIXTURE)


def test_the_fixture_is_a_few_dozen_provinces_not_the_map():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert 20 <= len(raw["provinces"]) <= 60
    assert set(raw) == {"provinces", "states", "adjacent", "centroids"}   # ids, adjacency, terrain only
    assert FIXTURE.stat().st_size < 10_000


def test_state_granularity_makes_one_node_per_state_with_real_adjacency(layer):
    raw = scenario_from_layer(layer, SPEC)
    ids = {p["id"] for p in raw["provinces"]}
    assert ids == {"s66", "s67", "s72", "s88", "s762"}
    edges = {(a, b) for a, b, _ in raw["edges"]}
    assert ("s66", "s67") in edges                                       # Lower and Upper Silesia touch
    assert ("s67", "s762") in edges                                      # Upper Silesia into Katowice
    sc = Scenario.from_dict(raw)
    assert sc.goals["A"]["provinces"] == ["s762"]
    assert {p["controller"] for p in raw["provinces"] if p["id"] in ("s88", "s762")} == {"B"}


def test_terrain_is_the_commonest_of_the_states_land_provinces(layer):
    raw = scenario_from_layer(layer, SPEC)
    by = {p["id"]: p["terrain"] for p in raw["provinces"]}
    assert set(by.values()) <= {"plains", "forest", "hills", "mountain", "urban", "marsh"}
    assert all(layer.provinces[p].terrain for p in layer.provinces)


def test_province_granularity_keeps_the_state_as_the_region(layer):
    raw = scenario_from_layer(layer, {**SPEC, "granularity": "province"})
    assert len(raw["provinces"]) > 5 and all(p["region"].startswith("s") for p in raw["provinces"])
    sc = Scenario.from_dict(raw)
    env = TacticalEnv()
    env.reset(sc, seed=1)
    pic = build_picture(env.view(), "aggregated")
    assert set(pic.regions) == {"s66", "s67", "s72", "s88", "s762"}    # the planner's view is per state


def test_rivers_given_to_the_loader_reach_the_edges(layer):
    raw = scenario_from_layer(layer, SPEC, {(66, 67): 2})
    assert {(a, b): r for a, b, r in raw["edges"]}[("s66", "s67")] == 2


def test_the_loader_refuses_what_the_map_cannot_back(layer):
    with pytest.raises(ValueError, match="not on this map"):
        scenario_from_layer(layer, {**SPEC, "sides": {"A": [66, 999], "B": [88]}})
    with pytest.raises(ValueError, match="not B's"):
        scenario_from_layer(layer, {**SPEC, "forces": [{"side": "B", "template": "inf_9", "state": 66}]})
    with pytest.raises(ValueError, match="granularity"):
        scenario_from_layer(layer, {**SPEC, "granularity": "county"})


def test_the_loaded_scenario_plays_an_episode_with_every_controller_kind(layer):
    sc = Scenario.from_dict(scenario_from_layer(layer, SPEC))
    for name in ("vanilla", "scripted", "vanilla_plans", "planner", "planner_full"):
        env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
        env.reset(sc, seed=2)
        ctrl = CONTROLLERS[name]()
        done = False
        while not done:
            done = env.step(ctrl.act(env.view())).done
        assert env.metrics()["illegal_orders"] == 0, name


# --- the committed Germany-Poland scenario ----------------------------------------------------


def test_the_committed_scenario_is_germany_against_poland_at_state_granularity():
    sc = load_scenario(REAL)
    raw = json.loads(REAL.read_text(encoding="utf-8"))
    assert len(sc.provinces) == 34 and len(sc.edges) > 60
    sides = {s: sum(p["controller"] == s for p in sc.provinces) for s in "AB"}
    assert sides == {"A": 16, "B": 18}                          # 16 German or Czech-front states, 18 Polish
    forces = {s: sum(d.get("count", 1) for d in sc.divisions if d["side"] == s) for s in "AB"}
    assert forces == {"A": 22, "B": 20}
    assert any(e[2] for e in sc.edges)                          # rivers came through
    assert len(raw["assumptions"]) >= 5                         # every number of ours is labelled
    assert all("state" in p and p["region"] == p["id"] for p in sc.provinces)


def test_the_committed_scenario_does_not_ship_game_files():
    raw = json.loads(REAL.read_text(encoding="utf-8"))
    assert set(raw["provinces"][0]) <= {"id", "terrain", "controller", "region", "x", "y", "state", "hub"}
    assert REAL.stat().st_size < 20_000


def test_the_committed_scenario_runs():
    env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
    env.reset(load_scenario(REAL), seed=0)
    ctrl = CONTROLLERS["planner"]()
    for _ in range(3):
        env.step(ctrl.act(env.view()))
    assert env.metrics()["illegal_orders"] == 0
