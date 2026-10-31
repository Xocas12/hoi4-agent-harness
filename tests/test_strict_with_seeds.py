"""`eval --strict` promises "exit 1 unless every objective passes", but with --seeds
it only checks the median: 2 of 5 seeds failing an objective still exits 0."""
from hoi4_harness import cli
from hoi4_harness.eval import variance
from hoi4_harness.eval.metrics import ScoreCard


def test_strict_fails_when_any_seed_fails(tmp_path, monkeypatch):
    scores = iter([1.0, 1.0, 1.0, 0.0, 0.0])

    def fake_run(scenario, config, transcript_dir=None, seed=None, operational_control=None):
        s = next(scores)
        return ScoreCard(scenario.key, score=s, objectives={"civ_factories_20": s == 1.0})

    monkeypatch.setattr(variance, "run_scenario", fake_run)
    code = cli.main(["eval", "economy_ramp", "--seeds", "5", "--strict", "--run-dir", str(tmp_path)])
    assert code == 1, "two of five seeds failed civ_factories_20, yet --strict exited 0"
