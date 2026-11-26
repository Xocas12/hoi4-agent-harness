"""The optional ML model class for the micro controller (#85): the pure-Python half runs everywhere
(the fallback message, the loader, the CLI refusing cleanly); the half that trains needs the ``ml``
extra and is skipped without it."""

from __future__ import annotations

import json
import random

import pytest

from hoi4_harness.micro import dataset as D
from hoi4_harness.micro import ope
from hoi4_harness.micro import sim as S
from hoi4_harness.micro.__main__ import main
from hoi4_harness.micro.baseline import baseline_from_features
from hoi4_harness.micro.boosted import BoostedBandit, MissingExtra, ml_available
from hoi4_harness.micro.explore import ExploringController
from hoi4_harness.micro.features import feature_dict
from hoi4_harness.micro.interface import ORDERS
from hoi4_harness.micro.learn import LearnedController, LinearBandit, load_model
from hoi4_harness.micro.runtime import build_controller

needs_ml = pytest.mark.skipif(not ml_available(),
                              reason="the ml extra (numpy, scikit-learn) is not installed")


def synthetic(n=400, seed=0, explore=True):
    """Rows where push is best when we are strong, hold when weak, logged with epsilon exploration."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        own, foe = rng.randint(1, 20), rng.randint(1, 20)
        f = feature_dict(own, foe)
        best = "push" if own > 1.5 * foe else "hold"
        a = best if (not explore or rng.random() > 0.4) else rng.choice(ORDERS)
        p = 1.0 if not explore else (0.6 + 0.4 / 5 if a == best else 0.4 / 5)
        gain = 2.0 if a == "push" and best == "push" else (-1.0 if a == "push" else 0.0)
        rows.append({"episode": f"e{i % 8}", "features": f, "action": a, "propensity": p,
                     "valid": list(ORDERS), "reward": gain + rng.gauss(0, 0.3)})
    return rows


# -- no extra needed ---------------------------------------------------------------------------------


def test_85_ml_training_without_the_extra_says_what_to_install(monkeypatch, tmp_path, capsys):
    import hoi4_harness.micro.boosted as B

    monkeypatch.setattr(B, "_need_sklearn", lambda: (_ for _ in ()).throw(MissingExtra()))
    with pytest.raises(MissingExtra) as err:
        BoostedBandit().fit(synthetic(30))
    assert "hoi4-agent-harness[ml]" in str(err.value) and isinstance(err.value, ImportError)
    data = tmp_path / "d.jsonl"
    data.write_text("".join(json.dumps(r) + "\n" for r in synthetic(60)), encoding="utf-8")
    code = main(["train", str(data), "--model-type", "boosted", "-o", str(tmp_path / "m.json")])
    assert code == 2 and "hoi4-agent-harness[ml]" in capsys.readouterr().err
    assert not (tmp_path / "m.json").exists()


def test_85_the_linear_model_is_unchanged_and_the_loader_tells_the_two_apart(tmp_path):
    lin = LinearBandit().fit(synthetic(200, explore=False))
    lin.save(tmp_path / "lin.json")
    assert isinstance(load_model(tmp_path / "lin.json"), LinearBandit)
    old = json.loads((tmp_path / "lin.json").read_text(encoding="utf-8"))
    old.pop("kind")                                      # a file written before the kind field existed
    (tmp_path / "old.json").write_text(json.dumps(old), encoding="utf-8")
    assert isinstance(load_model(tmp_path / "old.json"), LinearBandit)
    with pytest.raises(ValueError):
        BoostedBandit.load(tmp_path / "lin.json")


def test_85_a_hand_written_boosted_file_loads_and_predicts_without_the_extra(tmp_path):
    tree = {"left": [1, -1, -1], "right": [2, -1, -1], "feature": [3, -2, -2],
            "threshold": [0.0, -2.0, -2.0], "value": [0.0, -1.0, 3.0]}      # log_ratio <= 0 -> -1 else 3
    model = BoostedBandit({"push": {"init": 0.5, "rate": 0.5, "trees": [tree]}}, {"push": 9})
    path = tmp_path / "m.json"
    model.save(path)
    ctl = build_controller("learned", path)
    assert isinstance(ctl, LearnedController) and isinstance(ctl.model, BoostedBandit)
    assert ctl.model.predict(feature_dict(10, 2)) == {"push": 2.0}
    assert ctl.model.predict(feature_dict(2, 10)) == {"push": 0.0}
    assert ctl.model.act(feature_dict(10, 2)) == "push"


# -- needs numpy and scikit-learn --------------------------------------------------------------------


@needs_ml
@pytest.mark.parametrize("mode", ["dr", "ipw"])
def test_85_ml_both_modes_learn_which_order_is_best_from_logged_propensities(mode):
    rows = synthetic(600)
    model = BoostedBandit(mode=mode).fit(rows)
    assert model.act(feature_dict(16, 4)) == "push"
    assert model.act(feature_dict(3, 15)) != "push"      # every other order is a tie at zero there
    assert set(model.trusted()) == {o for o in ORDERS if model.counts[o] >= 5}


@needs_ml
def test_85_ml_the_saved_model_predicts_what_the_library_did_and_reloads(tmp_path):
    from hoi4_harness.micro.boosted import _need_sklearn
    np, regressor = _need_sklearn()
    rng = np.random.default_rng(0)
    x = rng.normal(size=(120, 4))
    y = x[:, 1] * 2 + rng.normal(size=120) * 0.1
    est = regressor(random_state=0, n_estimators=25, max_depth=3)
    est.fit(x, y, sample_weight=rng.uniform(.5, 2, 120))
    from hoi4_harness.micro.boosted import _export, _predict
    exported = _export(est)
    for row in x[:20]:
        assert _predict(exported, list(row)) == pytest.approx(float(est.predict(row[None])[0]), abs=1e-9)
    model = BoostedBandit().fit(synthetic(300))
    model.save(tmp_path / "m.json")
    again = BoostedBandit.load(tmp_path / "m.json")
    f = feature_dict(12, 5)
    assert again.predict(f) == model.predict(f) and again.counts == model.counts


@needs_ml
def test_85_ml_the_cli_trains_evaluates_and_the_runtime_loads_the_file(tmp_path, capsys):
    data = tmp_path / "d.jsonl"
    rows = synthetic(500)
    for i, r in enumerate(rows):
        r.update({"date": f"1940-01-{1 + i % 28:02d}", "state": 1, "is_sector": True})
    data.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    out = tmp_path / "m.json"
    assert main(["train", str(data), "--model-type", "boosted", "-o", str(out)]) == 0
    assert "trained on" in capsys.readouterr().out
    assert main(["eval", str(data), "--model", str(out), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ope"]["learned"]["dr_ci"] and "verdict" in report
    assert isinstance(build_controller("learned", out).model, BoostedBandit)


@needs_ml
def test_85_ml_end_to_end_on_the_toy_simulator_against_the_ridge_bandit_and_the_baseline(tmp_path_factory,
                                                                                        capsys):
    tmp = tmp_path_factory.mktemp("ml-loop")
    base = S.FeaturePolicyController(baseline_from_features, "baseline")
    n_train, n_test = 40, 16
    states: list = []
    for sd in range(n_train + n_test):
        S.run_episode(ExploringController(base, 0.4, seed=sd), sd, 12, 6, run_dir=tmp / f"r{sd:03d}",
                      keep_states=states if sd >= n_train else None)
    rows = D.extract_runs([tmp / f"r{sd:03d}" for sd in range(n_train + n_test)], 30)
    train = [r for r in rows if int(r["episode"][1:]) < n_train]
    test = [r for r in rows if int(r["episode"][1:]) >= n_train]
    ridge = LinearBandit().fit(train, 1.0)
    boosted = BoostedBandit(mode="dr").fit(train)
    ipw = BoostedBandit(mode="ipw").fit(train)
    mean = sum(r["reward"] for r in train) / len(train)
    pols = {"baseline": baseline_from_features, "ridge": ridge.act, "boosted-dr": boosted.act,
            "boosted-ipw": ipw.act}
    # the doubly robust estimate of every policy uses the SAME evaluation reward model (the ridge one)
    est = {k: ope.estimate(test, p, ridge, mean, seed=1) for k, p in pols.items()}
    truth = {k: S.one_step_value(p, states, samples=3) for k, p in pols.items()}
    diff = {k: ope.compare(test, pols[k], pols["baseline"], ridge, mean, seed=1)
            for k in pols if k != "baseline"}
    diff_ridge = ope.compare(test, pols["boosted-dr"], pols["ridge"], ridge, mean, seed=1)
    lines = ["policy | OPE dr (95% interval) | simulator truth | dr minus baseline (95% interval)"]
    for k in pols:
        ci = est[k]["dr_ci"]
        d = diff.get(k)
        dtxt = f"{d['difference']:+.3f} ({d['ci'][0]:+.3f}, {d['ci'][1]:+.3f})" if d else "-"
        lines.append(f"{k} | {est[k]['dr']:+.3f} ({ci[0]:+.3f}, {ci[1]:+.3f}) | {truth[k]:+.3f} | {dtxt}")
    lines.append(f"boosted-dr minus ridge: {diff_ridge['difference']:+.3f} "
                 f"({diff_ridge['ci'][0]:+.3f}, {diff_ridge['ci'][1]:+.3f}); train rows {len(train)}, "
                 f"test rows {len(test)}")
    with capsys.disabled():
        print("\n" + "\n".join(lines))
    # the claims the numbers must support: the model beats the baseline in the simulator, and the log says so
    assert truth["boosted-dr"] > truth["baseline"] + 0.05
    assert diff["boosted-dr"]["ci"][0] > 0
    assert est["boosted-dr"]["off_support"] == 0
