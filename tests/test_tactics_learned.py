"""The learned controller: deterministic, trainable, and the committed parameters do what the docs say."""

import json
import random

from hoi4_harness.cli import main
from hoi4_harness.tactics import CONTROLLERS, TacticalEnv, load_scenario
from hoi4_harness.tactics.evaluate import evaluate, run_episode
from hoi4_harness.tactics.learned import (
    FEATURE_NAMES,
    N_FEATURES,
    PARAMS_PATH,
    LearnedController,
    Policy,
)
from hoi4_harness.tactics.train import (
    behaviour_clone,
    collect_demonstrations,
    evolve,
    fitness,
    make_jobs,
)

RESULTS = PARAMS_PATH.with_name("learned_results.json")


def random_policy(seed):
    rng = random.Random(seed)
    return Policy([rng.gauss(0, 0.5) for _ in range(N_FEATURES)])


def test_same_parameters_same_game_same_orders():
    pol = random_policy(1)
    runs = []
    for _ in range(2):
        env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
        env.reset("escape_encirclement", seed=4)
        ctrl = LearnedController(pol)
        log = []
        for _ in range(6):
            orders = ctrl.act(env.view())
            log.append([(o.division, o.kind, o.target) for o in orders])
            env.step(orders)
        runs.append((log, env.metrics()))
    assert runs[0] == runs[1]
    assert any(runs[0][0])


def test_different_parameters_play_differently():
    a = run_episode("hold_line", 2, LearnedController(Policy()), "vanilla")
    b = run_episode("hold_line", 2, LearnedController(Policy.load()), "vanilla")
    keys = ("strength_lost_own", "strength_lost_enemy", "hours")
    assert [a[k] for k in keys] != [b[k] for k in keys]


def test_parameters_round_trip_through_json(tmp_path):
    pol = random_policy(3)
    pol.save(tmp_path / "p.json")
    again = Policy.load(tmp_path / "p.json")
    assert again.weights == [round(x, 6) for x in pol.weights]
    raw = json.loads((tmp_path / "p.json").read_text())
    raw["features"] = raw["features"][:-1]
    try:
        Policy.from_json(raw)
    except ValueError:
        pass
    else:
        raise AssertionError("a different feature set must be refused")


def test_orders_are_always_legal():
    for name in ("break_line", "close_encirclement"):
        m = run_episode(name, 0, LearnedController(random_policy(5)), "scripted")
        assert m["illegal_orders"] == 0


def test_behaviour_cloning_and_es_improve_on_a_tiny_budget():
    sc = [load_scenario("escape_encirclement")]
    check = make_jobs(sc, [1000, 1001, 1002], ("vanilla",))
    zero = Policy()
    base = fitness(zero, check)
    clone = behaviour_clone(collect_demonstrations(make_jobs(sc, [0, 1], ("vanilla",))), epochs=3)
    assert fitness(clone, check) > base + 0.2
    # ES from zeros does not beat cloning at this budget (see docs); it must still run and be repeatable
    one, history = evolve(clone, sc, seed=0, generations=2, pairs=2, opponents=("vanilla",))
    two, _ = evolve(clone, sc, seed=0, generations=2, pairs=2, opponents=("vanilla",))
    assert len(history) == 2 and one.weights == two.weights


def test_committed_parameters_exist_and_match_the_feature_set():
    pol = Policy.load()
    assert len(pol.weights) == len(FEATURE_NAMES)
    assert pol.meta["stage"] in ("bc+es", "behaviour-clone")


def test_committed_parameters_reproduce_the_reported_held_out_numbers():
    report = json.loads(RESULTS.read_text())
    row = next(r for r in report["rows"] if (r["scenario"], r["controller"], r["opponent"])
               == ("escape_encirclement", "learned", "vanilla"))
    got = evaluate("escape_encirclement", "learned", row["seeds"], "vanilla", report["first_seed"])
    assert (got["wins"], got["casualty_ratio"], got["mean_pockets_closed"]) == (
        row["wins"], row["casualty_ratio"], row["mean_pockets_closed"])


def test_cli_runs_the_learned_controller(capsys):
    assert main(["tactics", "run", "escape_encirclement", "--controller", "learned", "--seeds", "1"]) == 0
    assert "learned" in capsys.readouterr().out
