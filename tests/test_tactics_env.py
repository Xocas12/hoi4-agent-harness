"""The environment API, each scenario's win condition, and controller sanity."""

import json

import pytest

from hoi4_harness.cli import main
from hoi4_harness.tactics import (
    CONTROLLERS,
    Order,
    TacticalEnv,
    list_scenarios,
    load_scenario,
)
from hoi4_harness.tactics.env import assign_line
from hoi4_harness.tactics.evaluate import evaluate, run_episode

FOUR = ["break_line", "close_encirclement", "escape_encirclement", "hold_line"]


def test_the_four_issue_scenarios_exist_and_load():
    assert set(FOUR) <= set(list_scenarios())
    for name in FOUR:
        sc = load_scenario(name)
        env = TacticalEnv()
        obs = env.reset(sc, seed=0)
        assert obs["provinces"] and obs["divisions"] and obs["goal"] == sc.goals["A"]


def test_reset_observe_step_and_done():
    env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
    obs = env.reset("hold_line", seed=1)
    assert obs["hour"] == 0 and not env.done
    res = env.step([])
    assert res.observation["hour"] == 6 and not res.done
    res = env.step([], hours=500)
    assert res.done and res.info["outcome"] in ("win", "loss")


def test_legal_actions_and_illegal_orders():
    env = TacticalEnv()
    env.reset("break_line", seed=0)
    acts = env.legal_actions()
    assert set(acts) == {d for d, v in env.observe()["divisions"].items() if v["side"] == "A"}
    kinds = {o.kind for v in acts.values() for o in v}
    assert kinds == {"hold", "move", "attack", "retreat"}
    # attack on a province with no enemy in it is refused
    env.step([Order("A1", "attack", "c0r0")])
    assert env.world.illegal_orders == 1


def test_orders_for_the_other_side_are_ignored_when_an_opponent_plays():
    env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
    env.reset("hold_line", seed=0)
    env.step([Order("B1", "move", "c0r0")])
    assert env.world.illegal_orders == 0
    assert env.world.divisions["B1"].path != ["c0r0"]


def test_assign_line_spreads_evenly():
    env = TacticalEnv()
    env.reset("hold_line", seed=0)
    ids = [d for d, v in env.observe()["divisions"].items() if v["side"] == "A"]
    orders = assign_line(env.world, ids, ["c3r0", "c3r1", "c3r2"])
    counts = {}
    for o in orders:
        target = o.target or env.world.divisions[o.division].province
        counts[target] = counts.get(target, 0) + 1
    assert sorted(counts.values()) == [2, 2, 2]


def test_same_seed_same_episode_and_seeds_differ():
    a = run_episode("break_line", 5, "scripted")
    assert a == run_episode("break_line", 5, "scripted")
    others = [run_episode("break_line", s, "vanilla") for s in range(4)]
    assert len({json.dumps(m, sort_keys=True) for m in others}) > 1


# ---- win conditions -------------------------------------------------------------------------


def test_hold_is_lost_when_a_province_falls_and_won_at_the_limit():
    env = TacticalEnv()
    env.reset("hold_line", seed=0)
    assert env.outcome() is None
    env.world.hour = env.scenario.max_hours
    assert env.outcome() == "win"
    env.world.provinces["c3r1"].controller = "B"
    assert env.outcome() == "loss"


def test_capture_is_won_when_every_objective_is_ours():
    env = TacticalEnv()
    env.reset("break_line", seed=0)
    assert env.outcome() is None
    env.world.provinces["c4r1"].controller = "A"
    assert env.outcome() is None
    env.world.provinces["c5r1"].controller = "A"
    assert env.outcome() == "win"


def test_capture_is_lost_at_the_time_limit():
    env = TacticalEnv()
    env.reset("break_line", seed=0)
    env.world.hour = env.scenario.max_hours
    assert env.outcome() == "loss"


def test_encircle_counts_cut_off_and_destroyed_while_cut_off():
    env = TacticalEnv()
    env.reset("close_encirclement", seed=0)
    w = env.world
    assert env.cut_off_count("B") == 0
    for pid in ("c4r2",):
        w.provinces[pid].controller = "A"
    w._update_supply()
    assert env.cut_off_count("B") >= 3 and env.outcome() == "win"
    # a cut-off division that dies still counts
    victim = next(d for d in w.alive("B") if d.encircled)
    before = env.cut_off_count("B")
    victim.alive = False
    assert env.cut_off_count("B") == before


def test_escape_needs_enough_of_the_original_pocket_alive_and_free():
    env = TacticalEnv()
    env.reset("escape_encirclement", seed=0)
    w = env.world
    pocket = sorted(i for i in w.initially_cut_off if i.startswith("A"))
    assert len(pocket) == 4 and env.outcome() is None
    for pid in ("c2r0", "c2r1", "c2r2"):
        w.provinces[pid].controller = "A"
    w._update_supply()
    assert env.outcome() == "win"
    w.provinces["c2r1"].controller = "B"
    w.provinces["c2r0"].controller = "B"
    w.provinces["c2r2"].controller = "B"
    w._update_supply()
    assert env.outcome() is None
    for did in pocket[:2]:
        w.divisions[did].alive = False
    assert env.outcome() == "loss"


def test_metrics_report_the_scored_quantities():
    m = run_episode("close_encirclement", 0, "scripted")
    for key in ("outcome", "territory_held", "divisions_destroyed", "casualty_ratio", "pockets_closed",
                "hours"):
        assert key in m
    assert m["pockets_closed"] >= 1 and m["outcome"] == "win"


# ---- controllers ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", FOUR)
def test_scripted_beats_vanilla_on_every_scenario(name):
    seeds = 8
    scripted = evaluate(name, "scripted", seeds)
    vanilla = evaluate(name, "vanilla", seeds)
    assert scripted["wins"] > vanilla["wins"]


def test_controllers_only_issue_orders_the_world_accepts():
    for name in FOUR:
        for ctl in ("vanilla", "scripted"):
            m = run_episode(name, 2, ctl)
            assert m["illegal_orders"] <= 3, (name, ctl, m["illegal_orders"])


def test_cli_run_prints_a_table_and_json(capsys):
    assert main(["tactics", "run", "hold_line", "--controller", "both", "--seeds", "2"]) == 0
    out = capsys.readouterr().out
    assert "| hold_line | scripted |" in out and "| hold_line | vanilla |" in out
    assert main(["tactics", "run", "hold_line", "--seeds", "2", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["seeds"] == 2
    assert main(["tactics", "run", "nope"]) == 2
    assert main(["tactics", "list"]) == 0
