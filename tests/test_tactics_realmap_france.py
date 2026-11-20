"""The second real-map front, Germany against France and the Low Countries 1940 (#85), on a fixture."""

import json
from pathlib import Path

from hoi4_harness.tactics import TacticalEnv
from hoi4_harness.tactics.picture import build_picture
from hoi4_harness.tactics.realmap import AbstractLayer, scenario_from_layer
from hoi4_harness.tactics.scenario import SCENARIO_DIR, Scenario, load_scenario

FIXTURE = Path(__file__).parent / "fixtures" / "realmap_layer_germany_france_1940.json"
FRONT = SCENARIO_DIR.parent / "realmap" / "germany_france_1940.json"
SPEC = {
    "name": "fixture-ardennes", "granularity": "state", "max_hours": 240, "step_hours": 12,
    # Moselland, Rhineland | Ardennes, Luxembourg, Wallonia
    "sides": {"A": [42, 51], "B": [980, 8, 34]},
    "hubs": {"A": [51], "B": [34]},
    "forces": [{"side": "A", "template": "inf_9", "state": 42, "count": 3},
               {"side": "A", "template": "armor_6", "state": 51, "count": 1},
               {"side": "B", "template": "inf_9", "state": 980, "count": 2}],
    "goals": {"A": {"kind": "capture", "states": [980]}, "B": {"kind": "hold", "states": [980]}},
}


def test_the_fixture_is_a_few_states_not_the_map():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(raw) == {"provinces", "states", "adjacent", "centroids"}
    assert len(raw["states"]) == 5 and len(raw["provinces"]) < 40
    assert FIXTURE.stat().st_size < 10_000


def test_the_loader_reads_the_ardennes_corner_from_the_fixture():
    raw = scenario_from_layer(AbstractLayer.load(FIXTURE), SPEC)
    edges = {(a, b) for a, b, _ in raw["edges"]}
    assert {("s42", "s980"), ("s8", "s980"), ("s34", "s980"), ("s42", "s8")} <= edges
    assert ("s34", "s42") not in edges                      # Wallonia does not touch Moselland
    sc = Scenario.from_dict(raw)
    assert sc.goals["A"]["provinces"] == ["s980"]
    env = TacticalEnv()
    env.reset(sc, seed=0)
    pic = build_picture(env.view(), "aggregated")
    assert pic.regions["s980"].foe == 2 and pic.regions["s42"].own == 3


def test_the_committed_front_is_a_labelled_scenario_with_the_stated_forces():
    raw = json.loads(FRONT.read_text(encoding="utf-8"))
    sc = load_scenario(FRONT)
    assert len(sc.provinces) == 40 and all(p["region"] == p["id"] for p in sc.provinces)
    side = {p["id"]: p["controller"] for p in sc.provinces}
    assert sum(1 for c in side.values() if c == "A") == 10
    n = {"A": 0, "B": 0}
    for d in sc.divisions:
        n[d["side"]] += d["count"]
    assert n == {"A": 23, "B": 25}
    assert {p["id"] for p in sc.provinces if p.get("hub")} == {"s55", "s16"}
    assert sc.goals["A"]["kind"] == "capture" and len(sc.goals["A"]["provinces"]) == 4
    assert len(raw["assumptions"]) >= 5 and "assumptions of ours" in raw["description"]


def test_the_front_steps_in_twelve_hour_turns():
    env = TacticalEnv()
    env.reset(load_scenario(FRONT), seed=0)
    for _ in range(3):
        env.step([])
    assert env.view().hour == 36
