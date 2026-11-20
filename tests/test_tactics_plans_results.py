"""One row of the committed plan benchmark (data/plans_results.json) replays exactly (#85, stage 4)."""

import json

import pytest

from hoi4_harness.tactics.evaluate import evaluate
from hoi4_harness.tactics.scenario import SCENARIO_DIR

RESULTS = SCENARIO_DIR.parent / "plans_results.json"
ROWS = json.loads(RESULTS.read_text(encoding="utf-8"))


def row(scenario, controller, opponent):
    return next(r for r in ROWS["rows"] if (r["scenario"], r["controller"], r["opponent"])
                == (scenario, controller, opponent))


def test_the_file_covers_every_scenario_controller_and_opponent_on_30_held_out_seeds():
    assert ROWS["first_seed"] == 2000 and ROWS["seeds"] >= 30
    assert len(ROWS["rows"]) == 5 * 6 * 2
    assert {r["controller"] for r in ROWS["rows"]} == {
        "vanilla", "vanilla_plans", "planner", "planner_full", "scripted", "learned"}
    assert "germany_poland_1939" in {r["scenario"] for r in ROWS["rows"]}


@pytest.mark.parametrize("scenario, controller, opponent", [
    ("hold_line", "planner", "vanilla"), ("break_line", "planner_full", "scripted"),
])
def test_a_committed_row_replays(scenario, controller, opponent):
    want = row(scenario, controller, opponent)
    got = evaluate(scenario, controller, want["seeds"], opponent, ROWS["first_seed"])
    for key in ("wins", "casualty_ratio", "mean_hours", "mean_pockets_closed"):
        assert got[key] == want[key], key
