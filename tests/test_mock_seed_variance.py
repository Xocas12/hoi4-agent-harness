"""The mock's seed has to move outcomes, in bounded and reproducible ways.

Before this, a seed drew research durations and nothing else, so "8 seeds" of a
scenario mostly measured reproducibility. These pin the three properties that
make a seed worth varying: different seeds differ, one seed is exactly
repeatable, and the noise stays inside the band the scenarios were calibrated to.
"""

from __future__ import annotations

import statistics

import pytest

from hoi4_harness.adapters import mock as mock_module
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.eval import SCENARIOS
from hoi4_harness.types import ActionCall


def adapter_for(key, seed, **kwargs):
    scenario = SCENARIOS[key]
    return MockAdapter(seed=seed, country=scenario.country, start=scenario.start,
                       start_state=scenario.start_state, **kwargs)


def trajectory(adapter, days=70, step=7):
    """The fronts and events a run produced, week by week."""
    out = []
    for _ in range(days // step):
        state = adapter.advance(step)
        out.append((state.date,
                    [(f.name, f.divisions_enemy, f.supply, f.pressure) for f in state.fronts],
                    sum(g.count for g in state.divisions)))
    return out


def test_the_same_seed_is_an_identical_wartime_run():
    a, b = adapter_for("winter_crisis", 5), adapter_for("winter_crisis", 5)
    assert trajectory(a) == trajectory(b)
    assert a.read_state().to_dict() == b.read_state().to_dict()


def test_different_seeds_give_different_wartime_trajectories():
    runs = [trajectory(adapter_for("winter_crisis", seed)) for seed in range(1936, 1946)]
    assert len({repr(run) for run in runs}) >= 8


def test_seeds_move_the_opening_position_not_only_the_war():
    starts = [adapter_for("winter_crisis", seed).read_state() for seed in range(1936, 1946)]
    assert len({tuple(f.divisions_enemy for f in s.fronts) for s in starts}) > 1
    assert len({s.stockpiles["infantry_equipment_1"] for s in starts}) > 1
    assert len({s.manpower for s in starts}) > 1


def test_the_counteroffensive_lands_on_different_days_and_in_different_strength():
    landed = []
    for seed in range(1936, 1946):
        adapter = adapter_for("winter_crisis", seed)
        while adapter.date.isoformat() < "1941-12-20":
            state = adapter.advance(1)
            if any(f.name == "center" and f.divisions_enemy >= 140 for f in state.fronts):
                center = next(f for f in state.fronts if f.name == "center")
                landed.append((state.date, center.divisions_enemy))
                break
    assert len(landed) == 10
    assert len({date for date, _ in landed}) > 1
    assert len({strength for _, strength in landed}) > 1


def test_the_war_deadline_is_never_moved():
    # rearmament_race is scored on the day the war arrives; jittering it would
    # change what the scenario measures.
    for seed in range(1936, 1946):
        late = adapter_for("rearmament_race", seed).advance(900)
        assert late.date == "1938-06-01"
        assert any(e.kind == "war" and e.severity == "critical" for e in late.events)


def test_construction_overruns_differ_by_seed_and_stay_in_band():
    def days_to_finish(seed):
        adapter = MockAdapter(seed=seed)
        adapter.apply(ActionCall("queue_construction",
                                 {"building": "civilian_factory", "state": "capital"}))
        for day in range(1, 400):
            adapter.advance(1)
            if not adapter.state.construction:
                return day
        raise AssertionError("never finished")

    fixed = days_to_finish_with_no_noise()
    days = [days_to_finish(seed) for seed in range(1, 21)]
    low, high = mock_module.CONSTRUCTION_OVERRUN
    assert len(set(days)) > 3
    assert all(fixed * low - 1 <= d <= fixed * high + 1 for d in days)


def days_to_finish_with_no_noise():
    adapter = MockAdapter(seed=1, variance=0)
    adapter.apply(ActionCall("queue_construction",
                             {"building": "civilian_factory", "state": "capital"}))
    for day in range(1, 400):
        adapter.advance(1)
        if not adapter.state.construction:
            return day
    raise AssertionError("never finished")


def test_opening_jitter_stays_inside_its_bands():
    base = adapter_for("winter_crisis", 1, variance=0).read_state()
    for seed in range(1, 40):
        state = adapter_for("winter_crisis", seed).read_state()
        for noisy, plain in zip(state.fronts, base.fronts, strict=True):
            assert abs(noisy.divisions_enemy / plain.divisions_enemy - 1) <= (
                mock_module.START_ENEMY_BAND + 0.02)
            assert abs(noisy.supply - plain.supply) <= mock_module.START_SUPPLY_BAND + 0.01
        stock = state.stockpiles["infantry_equipment_1"]
        plain_stock = base.stockpiles["infantry_equipment_1"]
        assert abs(stock / plain_stock - 1) <= mock_module.START_STOCK_BAND + 0.001
        assert abs(state.manpower / base.manpower - 1) <= mock_module.START_MANPOWER_BAND + 0.001
        assert abs(state.stability - base.stability) <= mock_module.START_STABILITY_BAND + 0.001


def test_the_dice_are_bounded_and_have_the_calibrated_spread():
    adapter = adapter_for("defensive_war", 3)
    front = adapter.state.fronts[0]
    shocks = [adapter._dice(front) for _ in range(4000)]
    assert max(abs(s) for s in shocks) <= mock_module.DICE_CLAMP + 1e-9
    assert 0.5 * mock_module.DICE_SD < statistics.pstdev(shocks) <= mock_module.DICE_SD * 1.05


def test_variance_zero_has_no_combat_or_opening_noise():
    runs = [trajectory(adapter_for("winter_crisis", seed, variance=0)) for seed in (1, 2, 3)]
    assert runs[0] == runs[1] == runs[2]


def test_agents_on_one_seed_face_the_same_dice():
    """The streams never read the agent's state, so a passive run and an active
    one meet identical weather: that is what keeps paired comparisons paired."""
    passive, active = adapter_for("winter_crisis", 9), adapter_for("winter_crisis", 9)
    active.apply(ActionCall("deploy_divisions",
                            {"template": "Infantry", "count": 5, "location": "baltic"}))
    for _ in range(60):
        passive.advance(1)
        active.advance(1)
        assert passive._shock == active._shock
        assert passive._blizzard == active._blizzard


@pytest.mark.parametrize("seed", [1936, 1937, 1938])
def test_ignoring_the_crisis_never_wins_whatever_the_dice(seed):
    for key, front in (("defensive_war", "karelia"), ("winter_crisis", "center")):
        adapter = adapter_for(key, seed)
        end = SCENARIOS[key].until
        while adapter.date.isoformat() < end:
            state = adapter.advance(7)
        assert next(f for f in state.fronts if f.name == front).pressure == "losing_ground"


# --- the scenarios stay winnable and losable ----------------------------------

def _scores(key, decide, seeds=range(1936, 1946)):
    from dataclasses import replace

    from test_scenarios import run_card
    return [run_card(replace(SCENARIOS[key], seed=seed), decide).score for seed in seeds]


def test_the_designed_answers_still_win_most_seeds_but_not_all():
    from test_scenarios import _commit_the_reserve, _hold_karelia
    held = _scores("defensive_war", _hold_karelia)
    committed = _scores("winter_crisis", _commit_the_reserve)
    assert statistics.mean(held) >= 0.7 and statistics.mean(committed) >= 0.8
    # Winnable is not guaranteed: three divisions is a thin hold, and the dice
    # are allowed to beat it now and then. A scenario no seed could lose
    # measures nothing about margin.
    assert min(held) < 1.0


def test_ignoring_the_crisis_scores_nothing_on_every_seed():
    assert set(_scores("defensive_war", lambda state: [])) == {0.0}
    assert set(_scores("winter_crisis", lambda state: [])) == {0.0}
