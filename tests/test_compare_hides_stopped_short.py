"""compare's table never shows reached_end_date=False: a model that ran out of
max_turns before the scenario's end date is scored and listed like a finished run."""
from hoi4_harness.config import HarnessConfig
from hoi4_harness.eval.comparison import compare
from hoi4_harness.eval.experiment import run_experiment


def test_compare_flags_a_run_that_stopped_short(tmp_path):
    config = HarnessConfig(days_per_turn=1, run_dir=tmp_path)  # 274 days > max_turns=200
    comparison = compare("economy_ramp", None, config)
    card = comparison.results[0].card
    assert card.reached_end_date is False and card.end_date < "1936-10-01"
    text = comparison.render()
    assert "short" in text.lower(), text


def test_experiment_flags_a_run_that_stopped_short(tmp_path):
    config = HarnessConfig(days_per_turn=1, run_dir=tmp_path)
    result = run_experiment("economy_ramp", config, seeds=1, transcript_dir=tmp_path)
    assert all(c.reached_end_date is False for a in result.arms.values() for c in a.cards)
    assert "short" in result.render().lower(), result.render()
