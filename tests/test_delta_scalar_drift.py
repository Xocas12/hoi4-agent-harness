"""Delta briefs diff against the last remembered brief, not the last *reported*
value. A scalar that moves just under its threshold each wake is never shown,
and the baseline moves with it, so the model's picture drifts without bound."""
from hoi4_harness.observation.builder import ObservationBuilder
from hoi4_harness.types import GameState


def test_slow_drift_is_eventually_reported():
    builder = ObservationBuilder(full_brief_every=100)
    state = GameState(date="1936-01-01", political_power=100.0)
    builder.build(state, turn=0)                          # full brief: PP 100
    briefs = []
    for i in range(1, 6):
        state = GameState(date=f"1936-01-{i + 1:02d}", political_power=100.0 + 4 * i)
        obs = builder.build(state, turn=i)
        assert obs.is_delta
        briefs.append(obs.brief)
    # PP is now 120 vs. the 100 the model was last shown; some delta must say so.
    assert any("PP" in b for b in briefs), briefs
