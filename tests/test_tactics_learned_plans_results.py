"""Rows of the committed learned-plans benchmark (data/learned_plans_results.json) replay exactly (#85)."""

import json

from hoi4_harness.tactics.evaluate import evaluate
from hoi4_harness.tactics.learned_plans import PARAMS_PATH
from hoi4_harness.tactics.scenario import SCENARIO_DIR

DATA = SCENARIO_DIR.parent
ROWS = json.loads((DATA / "learned_plans_results.json").read_text(encoding="utf-8"))
FRANCE = str(DATA / "realmap" / "germany_france_1940.json")


def row(scenario, controller, opponent):
    return next(r for r in ROWS["rows"] if (r["scenario"], r["controller"], r["opponent"])
                == (scenario, controller, opponent))


def test_the_file_covers_every_scenario_controller_and_opponent_on_fresh_seeds():
    assert ROWS["first_seed"] == 3000 and ROWS["seeds"] >= 30    # earlier tables: 1000-1029, 2000-2029
    assert len(ROWS["rows"]) == 6 * 6 * 2
    assert {r["controller"] for r in ROWS["rows"]} == {
        "vanilla_plans", "planner", "planner_full", "learned_plans", "scripted", "learned"}
    assert {"germany_poland_1939", "germany_france_1940"} <= {r["scenario"] for r in ROWS["rows"]}
    assert all(len(r["outcomes"]) == r["seeds"] and r["outcomes"].count("W") == r["wins"]
               for r in ROWS["rows"])


def test_the_committed_parameters_say_how_they_were_made():
    meta = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))["meta"]
    assert meta["held_out_front"] == "germany_france_1940"
    assert "germany_france_1940" not in meta["train_scenarios"]
    assert meta["wall_seconds"] <= 20 * 60


def test_a_committed_row_replays_exactly():
    want = row("close_encirclement", "learned_plans", "vanilla")
    got = evaluate("close_encirclement", "learned_plans", want["seeds"], "vanilla", ROWS["first_seed"])
    for key in ("wins", "casualty_ratio", "mean_hours", "mean_pockets_closed"):
        assert got[key] == want[key], key
    assert "".join("W" if r["outcome"] == "win" else "." for r in got["runs"]) == want["outcomes"]


def test_the_first_seeds_of_a_held_out_front_row_replay():
    want = row("germany_france_1940", "learned_plans", "vanilla")
    got = evaluate(FRANCE, "learned_plans", 3, "vanilla", ROWS["first_seed"])
    assert "".join("W" if r["outcome"] == "win" else "." for r in got["runs"]) == want["outcomes"][:3]
