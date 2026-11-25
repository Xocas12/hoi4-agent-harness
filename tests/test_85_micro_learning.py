"""The micro controller's data capture and offline learning loop (#85): a fixture run directory,
the dataset extractor, the linear bandit on tiny synthetic data, and the held-out evaluation."""

from __future__ import annotations

import json
import random

import pytest

from hoi4_harness.micro import dataset as D
from hoi4_harness.micro.__main__ import main
from hoi4_harness.micro.baseline import RuleController
from hoi4_harness.micro.evaluate import evaluate, render
from hoi4_harness.micro.features import feature_dict, sector_features
from hoi4_harness.micro.interface import ORDERS, Observation, SectorState
from hoi4_harness.micro.learn import LearnedController, LinearBandit, solve, split_episodes


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def make_run(tmp_path, name="run-a", with_micro=False, end_states=40):
    run = tmp_path / name
    run.mkdir()
    brief = "## GER\nFront picture (1940-01-10): own 12 vs foe 30 in 9 state(s) (x)\n"
    write_jsonl(run / "transcript.jsonl", [
        {"kind": "observe", "date": "1940-01-10", "brief": brief},
        {"kind": "llm", "tool_calls": [
            {"id": "c1", "name": "set_front_order", "arguments": {"state": "42", "order": "attack"}},
            {"id": "c2", "name": "set_front_order", "arguments": {"state": "43", "order": "hold"}},
            {"id": "c3", "name": "note", "arguments": {"text": "x"}}]},
        {"kind": "actions", "results": [
            {"call_id": "c1", "is_error": False, "name": "set_front_order"},
            {"call_id": "c2", "is_error": True, "name": "set_front_order"},
            {"call_id": "c3", "is_error": False, "name": "note"}]},
        {"kind": "observe", "date": "1940-05-20", "brief": "no picture here"},
        {"kind": "llm", "tool_calls": [
            {"id": "d1", "name": "set_front_order", "arguments": {"state": "7", "order": "push"}}]},
        {"kind": "actions", "results": [{"call_id": "d1", "is_error": False}]},
    ])
    write_jsonl(run / "campaign_progress.jsonl", [
        {"date": "1940-01-10", "controlled_states": 30, "divisions": 50},
        {"date": "1940-03-01", "controlled_states": 33, "divisions": 48},
        {"date": "1940-06-01", "controlled_states": end_states, "divisions": 60},
    ])
    if with_micro:
        write_jsonl(run / "micro_decisions.jsonl", [
            {"date": "1940-01-10", "state": 42, "order": "push", "controller": "rule",
             "features": feature_dict(10, 3)},
            {"date": "1940-01-10", "state": 1, "order": "bogus", "features": feature_dict(1, 1)}])
    return run


def test_extractor_pairs_each_accepted_order_with_its_outcome(tmp_path):
    rows = D.extract_run(make_run(tmp_path))
    assert [(r["state"], r["action"]) for r in rows] == [("42", "attack"), ("7", "push")]   # c2 failed
    first, second = rows
    assert first["source"] == "transcript" and first["episode"] == "run-a"
    # 1940-01-10 -> first sample on or after 60 days later (1940-03-01 is only 51 days): 1940-06-01
    assert first["reward"] == pytest.approx(40 - 30 - 0.0)       # divisions went up: no charge
    assert first["features"]["log_foe"] > first["features"]["log_own"]       # own 12 against foe 30
    assert second["reward"] is None                              # the run ends before 60 days pass


def test_extractor_charges_lost_divisions(tmp_path):
    run = make_run(tmp_path)
    write_jsonl(run / "campaign_progress.jsonl", [
        {"date": "1940-01-10", "controlled_states": 30, "divisions": 50},
        {"date": "1940-04-01", "controlled_states": 30, "divisions": 30}])
    assert D.extract_run(run)[0]["reward"] == pytest.approx(-0.02 * 20)


def test_extractor_reads_the_micro_decision_log_and_skips_invalid_rows(tmp_path):
    rows = D.extract_run(make_run(tmp_path, with_micro=True))
    micro = [r for r in rows if r["source"] == "micro"]
    assert len(micro) == 1 and micro[0]["action"] == "push" and micro[0]["controller"] == "rule"
    assert micro[0]["features"]["log_ratio"] > 0 and micro[0]["reward"] == 10


def test_extractor_uses_front_history_for_movement_features(tmp_path):
    run = make_run(tmp_path)
    (run / "front_history.json").write_text(json.dumps({"snapshots": [
        {"date": "1940-01-01", "fronts": {"FRA": {"gained": 6, "lost": 1, "baseline": False}}},
        {"date": "1939-01-01", "fronts": {"FRA": {"gained": 99, "lost": 0, "baseline": False}}}]}),
        encoding="utf-8")
    assert D.extract_run(run)[0]["features"]["net_gain"] == 0.5


def test_extractor_tolerates_an_empty_or_missing_run(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert D.extract_run(empty) == []
    assert D.summary([])["rows"] == 0


def test_cli_extract_writes_a_dataset_and_a_summary(tmp_path, capsys):
    run = make_run(tmp_path)
    out = tmp_path / "data" / "d.jsonl"
    assert main(["extract", str(run), "-o", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 2
    assert len(D.load_dataset(out)) == 2


# -- the learner ---------------------------------------------------------------------------------------

TRUTH = {"push": 2.0, "attack": 1.0, "probe": 0.3, "hold": 0.0, "reserve": 0.0}


def synthetic(n, seed, episode="e"):
    """Reward of an order is its slope times log_ratio (+ noise): push pays when we are stronger, and
    costs when we are weaker. The logged order is random, as an exploring logger would give."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        own, foe = rng.randint(0, 20), rng.randint(0, 20)
        f = feature_dict(own, foe)
        action = rng.choice(ORDERS)
        rows.append({"episode": episode, "date": f"1940-{1 + i % 12:02d}-{1 + i % 27:02d}",
                     "action": action, "features": f, "state": i, "source": "micro",
                     "reward": TRUTH[action] * f["log_ratio"] + rng.gauss(0, 0.1)})
    return rows


def test_solver_solves_a_small_system_and_rejects_a_singular_one():
    assert solve([[2.0, 0.0], [0.0, 4.0]], [2.0, 8.0]) == pytest.approx([1.0, 2.0])
    with pytest.raises(ValueError):
        solve([[1.0, 1.0], [1.0, 1.0]], [1.0, 2.0])


def test_the_bandit_recovers_the_reward_slopes_and_picks_the_right_order():
    model = LinearBandit().fit(synthetic(600, 1), l2=0.01)
    f = feature_dict(18, 3)       # log_own, log_foe and log_ratio are collinear: compare predictions
    for order, slope in TRUTH.items():
        assert model.predict(f)[order] == pytest.approx(slope * f["log_ratio"], abs=0.2)
    assert model.act(feature_dict(18, 3)) == "push"          # far stronger
    assert model.act(feature_dict(3, 18)) in ("hold", "reserve")    # far weaker: attacking loses


def test_an_order_with_too_few_examples_is_not_trusted_and_the_baseline_decides():
    rows = [r for r in synthetic(200, 2) if r["action"] != "push"] + [
        r for r in synthetic(200, 3) if r["action"] == "push"][:3]
    model = LinearBandit().fit(rows)
    assert "push" not in model.trusted()
    assert model.act(feature_dict(10, 2)) != "push"
    assert LinearBandit().act(feature_dict(10, 2)) == "push"     # untrained: the baseline rule


def test_rows_without_an_outcome_are_not_trained_on():
    rows = synthetic(50, 4)
    for r in rows:
        r["reward"] = None
    assert LinearBandit().fit(rows).trusted() == []


def test_model_round_trips_and_refuses_another_feature_set(tmp_path):
    model = LinearBandit().fit(synthetic(100, 5))
    path = tmp_path / "m.json"
    model.save(path)
    again = LinearBandit.load(path)
    f = feature_dict(9, 4)
    assert again.predict(f) == pytest.approx(model.predict(f))
    data = json.loads(path.read_text())
    data["features"] = ["bias"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        LinearBandit.load(path)


def test_split_holds_out_whole_episodes_or_the_later_part_of_one():
    rows = synthetic(10, 6, "a") + synthetic(10, 7, "b") + synthetic(10, 8, "c")
    train, test = split_episodes(rows, 0.34)
    assert {r["episode"] for r in test} == {"c"} and {r["episode"] for r in train} == {"a", "b"}
    one = synthetic(20, 9, "solo")
    train, test = split_episodes(one, 0.25)
    assert len(test) == 5 and len(train) == 15
    assert max(r["date"] for r in train) <= min(r["date"] for r in test)


def test_learned_controller_uses_the_model_for_the_main_effort():
    model = LinearBandit().fit(synthetic(600, 10), l2=0.01)
    obs = Observation(None, [SectorState(1, "FRA", own=18, foe=3), SectorState(2, "FRA", own=2, foe=2)])
    orders = {o.state: o.order for o in LearnedController(model).decide([], obs)}
    assert orders[1] == "push" and orders[2] == "hold"
    assert sector_features(obs.sectors[0])["log_ratio"] > 0


def test_evaluation_on_held_out_episodes_beats_the_baseline_when_the_data_say_so():
    train = synthetic(600, 11, "train")
    test = synthetic(900, 12, "test")
    model = LinearBandit().fit(train, l2=0.01)
    report = evaluate(model, train, test)
    assert report["reward_model"]["mse_model"] < report["reward_model"]["mse_train_mean"]
    assert report["learned"]["model_estimate"] >= report["baseline"]["model_estimate"]
    assert report["verdict"].startswith(("learned ahead", "no difference", "baseline ahead"))
    text = render(report)
    assert "| learned |" in text and "Verdict:" in text


def test_evaluation_says_inconclusive_when_there_is_too_little_to_read():
    train, test = synthetic(60, 13, "a"), synthetic(12, 14, "b")
    report = evaluate(LinearBandit().fit(train), train, test)
    assert report["verdict"].startswith("inconclusive")
    assert evaluate(LinearBandit(), [], [])["verdict"].startswith("inconclusive")


def test_cli_train_then_eval_end_to_end(tmp_path, capsys):
    data = tmp_path / "d.jsonl"
    write_jsonl(data, synthetic(200, 15, "a") + synthetic(100, 16, "b"))
    model = tmp_path / "m.json"
    assert main(["train", str(data), "-o", str(model)]) == 0
    assert model.is_file()
    assert main(["eval", str(data), "--model", str(model), "--json"]) == 0
    out = capsys.readouterr().out
    assert '"verdict"' in out


def test_the_rule_controller_still_decides_without_a_model():
    obs = Observation(None, [SectorState(1, "FRA", own=9, foe=3)])
    assert [o.order for o in RuleController().decide([], obs)] == ["push"]
