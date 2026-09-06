from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.types import ActionCall


def test_same_seed_same_run():
    a, b = MockAdapter(seed=7), MockAdapter(seed=7)
    for adapter in (a, b):
        adapter.apply(ActionCall("queue_construction",
                                 {"building": "civilian_factory", "state": "capital", "count": 3}))
        adapter.advance(200)
    assert a.read_state().to_dict() == b.read_state().to_dict()


def test_construction_completes_and_adds_a_factory():
    adapter = MockAdapter(seed=1)
    before = adapter.read_state().civilian_factories
    adapter.apply(ActionCall("queue_construction",
                             {"building": "civilian_factory", "state": "capital"}))
    adapter.advance(365)
    assert adapter.read_state().civilian_factories > before


def test_advance_stops_early_on_a_critical_event():
    adapter = MockAdapter(seed=1, start="1939-08-25")
    state = adapter.advance(90)
    assert state.date == "1939-09-01"
    assert any(event.severity == "critical" for event in state.events)


def test_production_cannot_exceed_military_factories():
    adapter = MockAdapter(seed=1)
    result = adapter.apply(ActionCall("set_production",
                                      {"equipment": "artillery", "factories": 99}))
    assert not result.ok
    assert "military factories" in result.message


def test_unsupported_action_is_refused_not_faked():
    adapter = MockAdapter(seed=1)
    result = adapter.apply(ActionCall("set_air_mission",
                                      {"wing": "1", "region": "Baltic", "mission": "patrol"}))
    assert not result.ok
    assert result.error_kind == "unsupported"
