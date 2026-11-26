"""The data side of the micro controller (#85): exploration with logged propensities, per-sector and
per-front outcomes joined from the front history, off-policy evaluation, and the toy simulator used to
check the whole loop on a problem where the answer is known."""

from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest

from battle_helpers import division as save_division
from battle_helpers import save_text
from hoi4_harness import frontreport as FR
from hoi4_harness import world as W
from hoi4_harness.config import HarnessConfig
from hoi4_harness.micro import MicroOrder, Observation, RuleController, SectorState, ope
from hoi4_harness.micro import dataset as D
from hoi4_harness.micro import sim as S
from hoi4_harness.micro.baseline import baseline_from_features
from hoi4_harness.micro.explore import ExploringController, propensity, valid_orders
from hoi4_harness.micro.features import feature_dict
from hoi4_harness.micro.interface import ORDERS, Intent
from hoi4_harness.micro.learn import LinearBandit
from hoi4_harness.micro.runtime import MicroRuntime, build_controller
from orders_helpers import load_layer, write_grid_game
from test_85_micro_controller import FakeEnv, division


def sec(state, own=6, foe=3, rear=None, enemy="FRA"):
    return SectorState(state, enemy, own=own, foe=foe, rear_state=rear)


# -- exploration -------------------------------------------------------------------------------------


def test_85_epsilon_zero_never_explores_and_logs_propensity_one():
    obs = Observation("1940-01-01", [sec(1, 10, 3), sec(2, 4, 4)])
    ctl = ExploringController(RuleController(), 0.0, seed=1)
    ctl.decide([], obs)
    assert [d["explored"] for d in ctl.decisions] == [False, False]
    assert {d["propensity"] for d in ctl.decisions} == {1.0}


def test_85_the_logged_propensity_is_the_probability_of_the_order_chosen():
    assert propensity("push", "push", ORDERS, 0.2) == pytest.approx(0.8)
    assert propensity("hold", "push", ORDERS, 0.2) == pytest.approx(0.2 / 4)
    assert propensity("hold", "push", ("hold",), 0.2) == 1.0           # one valid order: no choice, no risk
    assert propensity("bogus", "push", ORDERS, 0.2) == 0.0
    # for any base order the probabilities over the valid orders sum to one
    assert sum(propensity(o, "attack", ORDERS, 0.3) for o in ORDERS) == pytest.approx(1.0)


def test_85_exploration_is_per_sector_seeded_and_close_to_epsilon():
    sectors = [sec(i, 8, 4) for i in range(1, 401)]
    obs = Observation("1940-03-01", sectors)
    a = ExploringController(RuleController(max_orders=1000), 0.25, seed=7)
    b = ExploringController(RuleController(max_orders=1000), 0.25, seed=7)
    c = ExploringController(RuleController(max_orders=1000), 0.25, seed=8)
    a.decide([], obs), b.decide([], obs), c.decide([], obs)
    assert a.decisions == b.decisions                       # the same seed draws the same coins
    assert a.decisions != c.decisions
    share = sum(d["explored"] for d in a.decisions) / len(a.decisions)
    assert 0.18 < share < 0.32
    for d in a.decisions:
        assert d["order"] not in d["alternatives"]
        assert set(d["alternatives"]) | {d["order"]} == set(d["valid"])
        if d["explored"]:
            assert d["order"] != d["base_order"] and d["propensity"] == pytest.approx(0.25 / 4)
        else:
            assert d["order"] == d["base_order"] and d["propensity"] == pytest.approx(0.75)
    # a different day is a different draw for the same sector
    later = Observation("1940-04-01", sectors)
    d2 = ExploringController(RuleController(max_orders=1000), 0.25, seed=7)
    d2.decide([], later)
    assert [x["explored"] for x in d2.decisions] != [x["explored"] for x in a.decisions]


def test_85_the_llms_intent_and_empty_sectors_are_not_explored():
    obs = Observation("1940-01-01", [sec(1, 10, 3), sec(2, 0, 5), sec(3, 6, 6)])
    ctl = ExploringController(RuleController(), 1.0, seed=3)
    orders = {o.state: o.order for o in ctl.decide([Intent("FRA", 1, "offensive", 1.0)], obs)}
    assert orders[1] == "push"                               # the LLM's sector keeps its order
    assert {d["state"] for d in ctl.decisions} == {2, 3}     # and is not a controller decision
    empty = next(d for d in ctl.decisions if d["state"] == 2)
    assert empty["valid"] == ["hold"] and not empty["explored"] and empty["propensity"] == 1.0
    assert valid_orders(sec(9, 0, 1)) == ("hold",)


def test_85_a_rotation_order_on_a_rear_state_is_not_a_sector_choice():
    obs = Observation("1940-01-01", [SectorState(5, "FRA", own=8, foe=4, own_low_org=4, rear_state=99)])
    ctl = ExploringController(RuleController(), 1.0, seed=2)
    ctl.decide([], obs)
    rear = next(d for d in ctl.decisions if d["state"] == 99)
    assert rear["is_sector"] is False and rear["explored"] is False and rear["propensity"] == 1.0


def test_85_a_decision_the_cap_or_the_standing_order_decides_is_marked_applied_or_not():
    obs = Observation("1940-01-01", [sec(1, 9, 3), sec(2, 9, 3), sec(3, 9, 3)], current={3: "hold"})
    ctl = ExploringController(RuleController(max_orders=1), 0.0)
    sent = ctl.decide([], obs)
    applied = {d["state"]: d["applied"] for d in ctl.decisions}
    assert len(sent) == 1
    assert applied[3] is True                                # already standing as chosen
    assert sorted(applied.values()).count(False) == 1        # one fell to the cap


def test_85_a_controller_without_plan_cannot_be_wrapped_and_epsilon_is_checked():
    with pytest.raises(ValueError):
        ExploringController(SimpleNamespace(name="x", decide=lambda *a: []))
    with pytest.raises(ValueError):
        ExploringController(RuleController(), 1.5)


def test_85_the_profile_switches_exploration_on_and_build_controller_wraps(tmp_path):
    assert HarnessConfig().micro_explore == 0.0
    p = tmp_path / "p.json"
    p.write_text(json.dumps({"micro_controller": "rule", "micro_explore": 0.2, "micro_explore_seed": 5}),
                 encoding="utf-8")
    cfg = HarnessConfig.from_file(p)
    assert (cfg.micro_explore, cfg.micro_explore_seed) == (0.2, 5)
    ctl = build_controller("rule", None, cfg.micro_explore, cfg.micro_explore_seed)
    assert isinstance(ctl, ExploringController) and ctl.seed == 5
    assert not isinstance(build_controller("rule"), ExploringController)


def test_85_the_runtime_logs_every_sector_decision_with_its_propensity(tmp_path):
    own = [division(str(i), 110, 11) for i in range(6)]
    foe = [division("f1", 100, 10, country="FRA")]
    pic = SimpleNamespace(world=SimpleNamespace(enemies={"FRA"}, countries={}), date="1940-05-01", own=own,
                          contact={"FRA": foe}, borders={10: ("FRA", "")}, conditions=[])
    env = FakeEnv(pic)
    rt = MicroRuntime(build_controller("rule", None, 0.0 + 1e-9, 4), env, tmp_path)
    rt.step(SimpleNamespace(date="1940-05-01"))
    rt.step(SimpleNamespace(date="1940-05-01"))
    logged = [json.loads(x) for x in (tmp_path / "micro_decisions.jsonl").read_text().splitlines()]
    assert len(logged) == 2 and all(r["sector_state"] == 10 and r["enemy"] == "FRA" for r in logged)
    assert logged[0]["order"] == "push" and logged[0]["propensity"] == pytest.approx(1 - 1e-9)
    assert logged[0]["valid"] == list(ORDERS) and logged[0]["own"] == 6 and logged[0]["foe"] == 1
    assert all(r["seed"] == 4 and r["explored"] is False for r in logged)


# -- outcomes from the front history -----------------------------------------------------------------


def snap(date, gained=None, lost=None, baseline=False, tag="FRA"):
    gained, lost = gained or {}, lost or {}
    return {"date": date, "fronts": {tag: {
        "gained": sum(gained.values()), "lost": sum(lost.values()), "baseline": baseline,
        "gained_states": len(gained), "lost_states": len(lost),
        "gained_by_state": gained, "lost_by_state": lost}}}


def battle(state, ended, outcome="attacker", own_side="attacker", front="FRA"):
    return {"front": front, "state": state, "ended": ended, "outcome": outcome, "own_side": own_side,
            "first": ended, "att_div": 5, "def_div": 5}


HISTORY = {"snapshots": [snap("1940-01-10", baseline=True),
                         snap("1940-02-10", {"42": 2, "43": 1}, {"42": 1}),
                         snap("1940-03-12", {"43": 4}),
                         snap("1940-04-11", {"42": 9})],
           "closed": [battle(42, "1940-02-09"), battle(42, "1940-02-09", "attacker", "defender"),
                      battle(43, "1940-03-10", "defender"), battle(42, "1940-04-01")]}


def test_85_outcomes_cover_the_saves_up_to_the_first_one_a_horizon_on():
    res = D.horizon_outcomes(HISTORY, "1940-01-10", "FRA", 42, horizon=30)
    assert res["end_date"] == "1940-02-10"
    assert res["sector"] == {"gained": 2, "lost": 1, "battles_won": 1, "battles_lost": 1}
    # the front sums every state: +3 -1 provinces, the three battles that ended in the window on it
    assert (res["front"]["gained"], res["front"]["lost"]) == (3, 1)
    assert (res["front"]["battles_won"], res["front"]["battles_lost"]) == (1, 1)
    two = D.horizon_outcomes(HISTORY, "1940-01-10", "FRA", 43, horizon=60)
    assert two["end_date"] == "1940-03-12" and two["sector"]["gained"] == 5
    assert (two["sector"]["battles_won"], two["sector"]["battles_lost"]) == (0, 1)


def test_85_a_decision_the_history_does_not_reach_is_censored_not_zero():
    assert D.horizon_outcomes(HISTORY, "1940-04-11", "FRA", 42, horizon=30) is None
    assert D.horizon_outcomes(HISTORY, "1940-01-10", "FRA", 42, horizon=120) is None
    assert D.horizon_outcomes(HISTORY, "1940-01-10", "ITA", 42, horizon=30) is None


def test_85_a_history_without_per_state_counts_gives_the_front_but_no_sector():
    first = {"FRA": {"gained": 0, "lost": 0, "baseline": True}}
    old = {"snapshots": [{"date": "1940-01-10", "fronts": first},
                         {"date": "1940-02-10", "fronts": {"FRA": {"gained": 3, "lost": 0, "baseline": False,
                                                                   "gained_states": 1, "lost_states": 0}}}],
           "closed": []}
    res = D.horizon_outcomes(old, "1940-01-10", "FRA", 42, horizon=30)
    assert res["sector"] is None and res["front"]["gained"] == 3


def test_85_the_sector_reward_weighs_provinces_battles_and_divisions_lost():
    r = {"gained": 2, "lost": 1, "battles_won": 2, "battles_lost": 1}
    assert D.sector_reward(r) == pytest.approx(1 + 0.25)
    assert D.sector_reward(r, own_change=-5) == pytest.approx(1.25 - 0.1)
    assert D.sector_reward(r, own_change=+3) == pytest.approx(1.25)


def write_run(tmp_path, decisions, history=HISTORY):
    run = tmp_path / "run-x"
    run.mkdir()
    text = "".join(json.dumps(d) + "\n" for d in decisions)
    (run / "micro_decisions.jsonl").write_text(text, encoding="utf-8")
    (run / "front_history.json").write_text(json.dumps(history), encoding="utf-8")
    return run


def decision(date, state, order, own=8, org=60.0, prop=0.8, **extra):
    return {"date": date, "state": state, "order": order, "controller": "rule+explore", "enemy": "FRA",
            "sector_state": state, "own": own, "foe": 4, "org_average": org,
            "features": feature_dict(own, 4), "propensity": prop, "valid": list(ORDERS),
            "alternatives": [], "explored": False, "is_sector": True, **extra}


def test_85_a_decision_is_joined_to_its_sectors_outcome_and_the_fronts(tmp_path):
    run = write_run(tmp_path, [decision("1940-01-10", 42, "push"),
                               decision("1940-02-10", 42, "hold", own=6, org=45.0),
                               decision("1940-01-10", 43, "attack")])
    rows = {(r["date"], r["state"]): r for r in D.extract_run(run, horizon=30)}
    first = rows[("1940-01-10", 42)]
    # 2 provinces gained, 1 lost, one battle each way on state 42, and 2 divisions fewer at the next decision
    assert first["reward"] == pytest.approx(1 + 0.0 - 0.04)
    assert first["reward_kind"] == "sector" and first["propensity"] == 0.8
    assert first["outcome"]["sector"]["own_change"] == -2
    assert first["outcome"]["sector"]["org_change"] == pytest.approx(-15.0)
    assert first["outcome"]["front"]["gained"] == 3
    other = rows[("1940-01-10", 43)]
    assert other["reward"] == pytest.approx(1.0) and other["outcome"]["sector"]["own_change"] is None
    assert rows[("1940-02-10", 42)]["outcome"]["end_date"] == "1940-03-12"


def test_85_an_exploration_row_the_history_cannot_score_stays_censored_not_the_theatre_outcome(tmp_path):
    run = write_run(tmp_path, [decision("1940-04-11", 42, "push")])
    (run / "campaign_progress.jsonl").write_text(
        json.dumps({"date": "1940-04-11", "controlled_states": 10, "divisions": 5}) + "\n"
        + json.dumps({"date": "1940-07-01", "controlled_states": 99, "divisions": 5}) + "\n",
        encoding="utf-8")
    (row,) = D.extract_run(run, horizon=30)
    assert row["reward"] is None and row["reward_kind"] == "sector"


def test_85_a_row_from_an_old_run_without_sector_data_keeps_the_theatre_outcome(tmp_path):
    run = tmp_path / "old"
    run.mkdir()
    (run / "micro_decisions.jsonl").write_text(json.dumps(
        {"date": "1940-01-10", "state": 1, "order": "push", "features": feature_dict(8, 4)}) + "\n",
        encoding="utf-8")
    (run / "campaign_progress.jsonl").write_text(
        json.dumps({"date": "1940-01-10", "controlled_states": 10, "divisions": 5}) + "\n"
        + json.dumps({"date": "1940-03-20", "controlled_states": 13, "divisions": 5}) + "\n",
        encoding="utf-8")
    (row,) = D.extract_run(run)
    assert row["reward"] == 3.0 and row["reward_kind"] == "theatre" and "propensity" not in row


def test_85_the_front_history_records_provinces_moved_per_state(tmp_path):
    grid = load_layer(write_grid_game(tmp_path / "game"))

    def save(name, date, controllers):
        swe = (save_division(1, 6, 1, org=50.0, strength=200.0)
               + save_division(2, 6, 1, org=50.0, strength=200.0))
        ger = "".join(save_division(10 + i, 7, 3) for i in range(4))
        path = tmp_path / name
        path.write_text(save_text(swe, ger, "", controllers=controllers, date=date), encoding="utf-8")
        return W.read_world(path, "SWE", layer=grid)

    history = FR.FrontHistory()
    assert history.record(save("a.hoi4", "1941.6.30.12", {11: "SWE"}), "SWE", grid)
    assert history.record(save("b.hoi4", "1941.8.31.12", {11: "SWE", 7: "SWE"}), "SWE", grid)
    entry = history.snapshots[-1]["fronts"]["GER"]
    assert entry["gained"] == 1 and entry["gained_by_state"] == {"12": 1} and entry["lost_by_state"] == {}
    # and the dataset reads that file format back
    res = D.horizon_outcomes({"snapshots": history.snapshots, "closed": history.closed},
                             "1941-06-30", "GER", 12, horizon=30)
    assert res["sector"]["gained"] == 1


# -- off-policy evaluation ---------------------------------------------------------------------------


def bandit_log(n, seed, p_push=0.5, deterministic=False):
    """x in [-1, 1]; pushing pays +1 when x > 0 and -1 otherwise; holding pays 0. Logged by a coin."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        x = rng.uniform(-1, 1)
        f = {"bias": 1.0, "log_ratio": x}
        if deterministic:
            order, prop = ("push" if x > 0 else "hold"), 1.0
        else:
            order = "push" if rng.random() < p_push else "hold"
            prop = p_push if order == "push" else 1 - p_push
        reward = (1.0 if x > 0 else -1.0) + rng.gauss(0, 0.3) if order == "push" else rng.gauss(0, 0.3)
        rows.append({"episode": f"e{i % 10}", "features": f, "action": order, "reward": reward,
                     "propensity": prop, "valid": ["push", "hold"], "is_sector": True})
    return rows


def pol_follow(f):
    return "push" if f["log_ratio"] > 0 else "hold"


def test_85_inverse_propensity_recovers_the_value_of_a_policy_that_was_never_run():
    rows = bandit_log(4000, 1)
    est = ope.estimate(rows, pol_follow, seed=1, n_boot=100)
    assert est["ipw"] == pytest.approx(0.5, abs=0.07)             # truth: P(x > 0) * 1
    assert est["snips"] == pytest.approx(0.5, abs=0.07)
    lo, hi = est["ipw_ci"]
    assert lo < est["ipw"] < hi and hi - lo < 0.15
    never = ope.estimate(rows, lambda f: "hold")
    assert never["ipw"] == pytest.approx(0.0, abs=0.05)
    always = ope.estimate(rows, lambda f: "push")
    assert always["ipw"] == pytest.approx(0.0, abs=0.07)          # +0.5 where right, -0.5 where wrong


def test_85_doubly_robust_is_tighter_than_ipw_with_a_decent_reward_model():
    rows = bandit_log(3000, 2)
    model = LinearBandit().fit(rows, 0.1)
    ipw = ope.estimate(rows, pol_follow, None, seed=3, n_boot=100)
    dr = ope.estimate(rows, pol_follow, model, seed=3, n_boot=100)
    assert dr["dr"] == pytest.approx(0.5, abs=0.08)
    assert (dr["dr_ci"][1] - dr["dr_ci"][0]) < (ipw["ipw_ci"][1] - ipw["ipw_ci"][0])
    cmp = ope.compare(rows, pol_follow, lambda f: "push", model, seed=3, n_boot=100)
    assert cmp["estimator"] == "dr" and cmp["ci"][0] > 0          # following x beats always pushing


def test_85_a_log_that_never_explored_cannot_score_a_different_policy():
    rows = bandit_log(2000, 4, deterministic=True)
    est = ope.estimate(rows, lambda f: "hold")
    assert est["matched"] < len(rows) * 0.6 and est["ess"] < len(rows) * 0.6
    # the logger itself is recovered exactly: it is the policy that ran
    est = ope.estimate(rows, pol_follow)
    assert est["matched"] == len(rows) and est["ipw"] == pytest.approx(
        sum(r["reward"] for r in rows) / len(rows))


def test_85_an_order_the_logger_could_not_choose_is_reported_off_support():
    rows = bandit_log(200, 5)
    for r in rows:
        r["valid"] = ["hold"]
    est = ope.estimate(rows, lambda f: "push")
    assert est["off_support"] == 1.0
    assert ope.usable([{"reward": 1.0, "propensity": 0.0}, {"reward": None, "propensity": 0.5},
                       {"reward": 1.0}, {"reward": 1.0, "propensity": 0.5, "is_sector": False}]) == []


def test_85_evaluate_reads_exploration_rows_with_the_off_policy_estimator_and_says_when_it_cannot():
    from hoi4_harness.micro.evaluate import evaluate, render

    rows = bandit_log(4000, 6)
    train, test = rows[:3000], rows[3000:]
    model = LinearBandit().fit(train, 0.1)
    report = evaluate(model, train, test)
    assert report["ope"]["rows"] == 1000 and "off-policy" in report["verdict"]
    assert "doubly robust" in render(report)
    tiny = evaluate(model, train, test[:15])
    assert tiny["verdict"].startswith("inconclusive")
    # rows with no propensity (an LLM-only log) keep the matched-rows verdict
    plain = [{k: v for k, v in r.items() if k != "propensity"} for r in test]
    assert "ope" not in evaluate(model, train, plain)


# -- the toy simulator -------------------------------------------------------------------------------


def test_85_the_simulator_is_deterministic_and_calibrated_to_the_orders_of_magnitude_of_the_real_battles():
    a = S.run_episode(RuleController(), 3, 6)
    b = S.run_episode(RuleController(), 3, 6)
    assert a == b
    cal = S.calibrate(400)
    assert cal["piecemeal"] < 0.2                  # 3 against 8, 6 against 7: mostly bounced
    assert cal["piecemeal"] < cal["even"] < cal["favourable"]
    assert cal["favourable"] > 0.7


def test_85_a_hold_costs_nothing_and_a_failed_push_costs_battles_and_divisions():
    sim = S.FrontSim(1, seed=5)
    s = sim.sectors[0]
    s.own = s.own0 = 6.0
    s.foe = s.foe0 = 20.0
    s.terrain = "mountains"
    hold = sim.copy().step([MicroOrder(s.state, "hold")], random.Random(1))[s.state]
    push = sim.copy().step([MicroOrder(s.state, "push")], random.Random(1))[s.state]
    assert hold["battles_lost"] == 0 and hold["gained"] == 0
    assert push["gained"] == 0 and push["battles_lost"] == 1 and push["own_change"] < hold["own_change"]


def test_85_a_rotation_order_on_the_rear_state_rests_the_sector():
    sim = S.FrontSim(2, seed=2)
    s = sim.sectors[0]
    s.org = 0.3
    sim.step([MicroOrder(S.REAR_BASE + s.state, "reserve")], random.Random(1))
    assert s.org > 0.6


def test_85_the_simulator_writes_what_the_extractor_reads(tmp_path):
    ctl = ExploringController(RuleController(), 0.5, seed=1)
    log = S.run_episode(ctl, 9, 6, 4, run_dir=tmp_path / "run-a")
    rows = D.extract_run(tmp_path / "run-a", horizon=30)
    assert rows and all(r["reward_kind"] == "sector" for r in rows)
    sector_rows = [r for r in rows if r["is_sector"]]
    assert len(sector_rows) == len(log)
    # the reward the extractor computes from the files matches the simulator's own, but for the last turn's
    # division count (no later decision to compare with) and the integer rounding of the observation
    by_key = {(r["date"], r["state"], r["action"]): r["reward"] for r in sector_rows}
    assert by_key
    close = sum(1 for e in log if any(abs(v - e["reward"]) < 0.15 for v in by_key.values()))
    assert close == len(log)
    assert any(r["explored"] for r in rows) and any(not r["explored"] for r in rows)


@pytest.fixture(scope="module")
def loop(tmp_path_factory):
    """Explore with the baseline as the logging policy, train on some runs, hold out others."""
    tmp = tmp_path_factory.mktemp("loop")
    base = S.FeaturePolicyController(baseline_from_features, "baseline")
    n_train, n_test = 40, 16
    states: list = []
    for sd in range(n_train + n_test):
        S.run_episode(ExploringController(base, 0.4, seed=sd), sd, 12, 6, run_dir=tmp / f"r{sd:03d}",
                      keep_states=states if sd >= n_train else None)
    rows = D.extract_runs([tmp / f"r{sd:03d}" for sd in range(n_train + n_test)], 30)
    train = [r for r in rows if int(r["episode"][1:]) < n_train]
    test = [r for r in rows if int(r["episode"][1:]) >= n_train]
    return SimpleNamespace(train=train, test=test, states=states, rows=rows)


def test_85_exploration_ope_and_learning_recover_a_better_policy_end_to_end(loop):
    model = LinearBandit().fit(loop.train, 1.0)
    mean = sum(r["reward"] for r in loop.train) / len(loop.train)

    def baseline(f):
        return baseline_from_features(f)

    # what the log says without running either policy
    est_b = ope.estimate(loop.test, baseline, model, mean, seed=1)
    est_l = ope.estimate(loop.test, model.act, model, mean, seed=1)
    cmp = ope.compare(loop.test, model.act, baseline, model, mean, seed=1)
    # what running them from those same states says (the simulator can be asked what the log cannot)
    true_b = S.one_step_value(baseline, loop.states, samples=3)
    true_l = S.one_step_value(model.act, loop.states, samples=3)
    assert true_l > true_b + 0.05                             # the learned policy really is better
    assert est_l["dr"] > est_b["dr"] and cmp["ci"][0] > 0     # and the log alone says so, with an interval
    assert est_b["dr"] == pytest.approx(true_b, abs=0.08)
    assert est_l["dr"] == pytest.approx(true_l, abs=0.08)
    assert est_b["off_support"] == 0 and est_l["off_support"] == 0


def test_85_the_same_loop_without_exploration_cannot_tell(tmp_path):
    base = S.FeaturePolicyController(baseline_from_features, "baseline")
    for sd in range(20):
        S.run_episode(ExploringController(base, 0.0, seed=sd), sd, 12, 6, run_dir=tmp_path / f"r{sd:02d}")
    rows = D.extract_runs([tmp_path / f"r{sd:02d}" for sd in range(20)], 30)
    assert {r["propensity"] for r in rows} == {1.0} and not any(r["explored"] for r in rows)
    model = LinearBandit().fit(rows, 1.0)
    est = ope.estimate(rows, lambda f: "hold", model, 0.0)
    # the log holds only what the baseline did: a different policy rests on the reward model alone
    assert est["matched"] < 0.5 * est["n"]
    from hoi4_harness.micro.evaluate import evaluate
    verdict = evaluate(model, rows[:1000], rows[1000:])["verdict"]
    assert "learned ahead" not in verdict or "interval" in verdict
