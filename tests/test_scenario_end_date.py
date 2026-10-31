"""The last turn runs past a scenario's `until`, so it is scored after its end date."""
from hoi4_harness.config import HarnessConfig
from hoi4_harness.eval.runner import run_scenario
from hoi4_harness.eval.scenarios import SCENARIOS


def test_economy_ramp_is_scored_at_its_end_date(tmp_path):
    scenario = SCENARIOS["economy_ramp"]  # until 1936-10-01
    config = HarnessConfig(planner_enabled=False, days_per_turn=30, run_dir=tmp_path)
    card = run_scenario(scenario, config)
    assert card.reached_end_date is True
    assert card.end_date == scenario.until


def test_default_pacing_also_overshoots(tmp_path):
    scenario = SCENARIOS["economy_ramp"]
    card = run_scenario(scenario, HarnessConfig(planner_enabled=False, run_dir=tmp_path))
    assert card.end_date == scenario.until
