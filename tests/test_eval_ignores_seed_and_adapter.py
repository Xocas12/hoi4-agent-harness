"""eval/compare/measure parse --seed and --adapter (common()) but run_scenario
always builds MockAdapter(seed=scenario.seed): both flags are silently dropped."""
from hoi4_harness import cli
from hoi4_harness.eval import runner


def _capture(monkeypatch):
    seen = {}
    real = runner.MockAdapter

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    monkeypatch.setattr(runner, "MockAdapter", spy)
    return seen


def test_eval_seed_flag_is_used_or_refused(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    code = cli.main(["eval", "economy_ramp", "--no-llm", "--seed", "7", "--run-dir", str(tmp_path)])
    # Either honour the flag or refuse it; silently running seed 1936 is the bug.
    assert code == 2 or seen.get("seed") == 7, f"ran with seed {seen.get('seed')} after --seed 7"


def test_eval_adapter_flag_is_used_or_refused(tmp_path, monkeypatch):
    seen = _capture(monkeypatch)
    code = cli.main(["eval", "economy_ramp", "--no-llm", "--adapter", "savegame",
                     "--run-dir", str(tmp_path)])
    assert code == 2 or not seen, "--adapter savegame was accepted and the mock was scored"
