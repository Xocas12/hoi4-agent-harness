"""Print how scenario scores spread across mock seeds. Free: no model is called.

    PYTHONPATH=src python scripts/seed_spread.py [--seeds 10] [--variance 1.0]

Three arms per scenario, each over N consecutive seeds from the scenario's own:

* ``no-llm``    reflexes alone (the recorded baseline's arm);
* ``scripted``  the offline scripted provider (a keyword policy that plays badly);
* ``reference`` a small hand-written policy that makes the scenario's designed
  move, the same ones tests/test_scenarios.py uses to show each scenario is
  passable.

The first two sit far below their thresholds by design, so they show noise only
where a margin is thin. The reference arm is the one that shows whether the noise
reaches a *winning* play: its pass rate should be high but not certain.
"""

from __future__ import annotations

import argparse
import statistics

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.loop import RunReport
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.eval import SCENARIOS, score
from hoi4_harness.eval.runner import run_scenario
from hoi4_harness.observation import snapshot
from hoi4_harness.types import ActionCall


def _hold_karelia(state):
    if state.delegated_armies:
        return []
    return [
        ActionCall("deploy_divisions", {"template": "Infantry", "count": 3, "location": "karelia"}),
        ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}),
        ActionCall("set_ai_posture", {"posture": "defensive"}),
    ]


def _commit_the_reserve(count):
    def decide(state):
        if state.delegated_armies:
            return []
        return [
            ActionCall("deploy_divisions", {"template": "Infantry", "count": count,
                                            "location": "home"}),
            ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}),
            ActionCall("set_ai_posture", {"posture": "defensive", "theater": "SOV"}),
        ]
    return decide


def _fix_the_shortage(state):
    if state.resources.get("rubber", 0) > 0:
        return []
    return [ActionCall("set_trade", {"resource": "rubber", "from_country": "GER", "factories": 1})]


def _rearm(state):
    calls = []
    queued = sum(1 for item in state.construction if item.building == "military_factory")
    if state.military_factories + queued < 6:
        calls.append(ActionCall("queue_construction",
                                {"building": "military_factory", "state": "capital"}))
    line = next((c for c in state.production if c.equipment == "infantry_equipment_1"), None)
    if (line.factories if line else 0) < state.military_factories:
        calls.append(ActionCall("set_production", {"equipment": "infantry_equipment_1",
                                                   "factories": state.military_factories}))
    total = sum(g.count for g in state.divisions)
    stock = state.stockpiles.get("infantry_equipment_1", 0)
    if total < 10 and stock >= 900:
        calls.append(ActionCall("deploy_divisions", {"template": "Infantry",
                                                     "count": min(10 - total, stock // 900)}))
    return calls


def _diagnose(state):
    calls = []
    line = next((c for c in state.production if c.equipment == "infantry_equipment_1"), None)
    if (line.factories if line else 0) < 4:
        support = next((c for c in state.production if c.equipment == "support_equipment"), None)
        if support and support.factories > 2:
            calls.append(ActionCall("set_production",
                                    {"equipment": "support_equipment", "factories": 2}))
        calls.append(ActionCall("set_production",
                                {"equipment": "infantry_equipment_1", "factories": 4}))
    if any(slot.technology is None for slot in state.research):
        calls.append(ActionCall("start_research", {"technology": "construction1"}))
    if not state.construction:
        calls.append(ActionCall("queue_construction",
                                {"building": "civilian_factory", "state": "capital", "count": 2}))
    return calls


REFERENCE = {
    "defensive_war": _hold_karelia,
    "winter_crisis": _commit_the_reserve(20),
    "resource_starved": _fix_the_shortage,
    "rearmament_race": _rearm,
    "recovery": _diagnose,
}


def reference_card(scenario, seed, variance, decide):
    adapter = MockAdapter(seed=seed, country=scenario.country, start=scenario.start,
                          start_state=scenario.start_state, variance=variance)
    config = HarnessConfig(adapter="mock", planner_enabled=False,
                           operational_control=scenario.operational_control)
    env = HOI4Env(adapter, config)
    env.reset()
    history = []
    for _ in range(scenario.max_turns):
        state = env.read_state()
        history.append(snapshot(state))
        env.act_many(decide(state))
        state = env.advance()
        if state.date >= scenario.until:
            break
    return score(scenario, env.read_state(), RunReport(), history)


def card_for(arm, scenario, seed, variance):
    if arm == "reference":
        return reference_card(scenario, seed, variance, REFERENCE[scenario.key])
    config = HarnessConfig()
    if arm == "no-llm":
        config.planner_enabled = False
    else:
        config.planner.provider = "scripted"
    return run_scenario(scenario, config, seed=seed, mock_variance=variance)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--variance", type=float, default=1.0)
    args = parser.parse_args()
    print(f"{args.seeds} seeds, variance {args.variance}")
    print(f"{'arm':10}{'scenario':18}{'mean':>6} {'sd':>5} {'min':>5} {'max':>5}  objectives passed")
    for arm in ("no-llm", "scripted", "reference"):
        for key, scenario in SCENARIOS.items():
            if arm == "reference" and key not in REFERENCE:
                continue
            cards = [card_for(arm, scenario, scenario.seed + i, args.variance)
                     for i in range(args.seeds)]
            scores = [c.score for c in cards]
            passed = " ".join(
                f"{name}={sum(1 for c in cards if c.objectives.get(name))}/{len(cards)}"
                for name in cards[0].objectives)
            print(f"{arm:10}{key:18}{statistics.mean(scores):6.2f} "
                  f"{statistics.pstdev(scores):5.2f} {min(scores):5.2f} {max(scores):5.2f}  {passed}")


if __name__ == "__main__":
    main()
