"""compare's 'fixed for every run' line names a control mode the runs did not use."""
from hoi4_harness.config import HarnessConfig, LLMConfig
from hoi4_harness.eval.comparison import compare


def test_fixed_line_reports_the_control_mode_the_runs_used(tmp_path):
    config = HarnessConfig(run_dir=tmp_path, planner=LLMConfig(provider="scripted", model="scripted"))
    result = compare("defensive_war", ["scripted:scripted"], config)  # scenario forces control=ai
    assert result.results[0].card is not None
    assert "control=ai" in result.render()
