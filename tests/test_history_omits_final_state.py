"""Objective.over() reads loop.history, which is the state at the *start* of each
turn: it includes the inherited opening (before the agent acted) and never the
final, scored state. At coarse pacing the opening is a large share of 'turns'."""
from hoi4_harness.config import HarnessConfig
from hoi4_harness.eval import runner


def test_over_objectives_see_the_end_state(tmp_path, monkeypatch):
    seen = {}
    real_score = runner.score

    def spy(scenario, final, report, history=None):
        seen["final"], seen["history"] = final.date, [s.date for s in history]
        return real_score(scenario, final, report, history)

    monkeypatch.setattr(runner, "score", spy)
    runner.run_scenario("economy_ramp", HarnessConfig(planner_enabled=False, days_per_turn=91,
                                                      run_dir=tmp_path))
    # 4 turns: history = [1936-01-01 (pre-play), ...], final 1936-10-01 absent.
    assert seen["final"] in seen["history"], (seen["final"], seen["history"])
