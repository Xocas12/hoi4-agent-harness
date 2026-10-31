"""Mock drops read-but-unacknowledged events on the next advance, contrary to
the GameAdapter contract (events leave only via acknowledge_events)."""
from hoi4_harness.adapters.mock import MockAdapter


def test_unacknowledged_critical_event_survives_next_advance():
    m = MockAdapter(start="1939-08-31")
    m.advance(5)  # stops on the critical 1939-09-01 war event
    state = m.read_state()
    assert any(e.severity == "critical" for e in state.events)
    # The loop's model call failed: it deliberately acknowledges nothing so the
    # war wakes the next turn (agent/loop.py ~line 380).
    m.advance(1)
    after = m.read_state()
    assert any(e.kind == "war" for e in after.events), (
        "war event was dropped although never acknowledged"
    )
