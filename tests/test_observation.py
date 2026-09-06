from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.observation import ObservationBuilder, render_delta, render_full
from hoi4_harness.types import ActionCall, GameState


def test_unobservable_fields_render_as_unknown_not_zero():
    state = GameState(country="FRA", unknown_fields=["political_power", "manpower"])
    brief = render_full(state)
    assert "PP unknown" in brief
    assert "manpower unknown" in brief


def test_full_brief_flags_the_things_that_need_a_decision():
    brief = render_full(MockAdapter().read_state())
    assert "NONE SELECTED" in brief      # no focus running
    assert "SLOT(S) FREE" in brief       # idle research
    assert "QUEUE EMPTY" in brief        # nothing being built


def test_delta_is_much_shorter_than_a_full_brief():
    adapter = MockAdapter()
    before = adapter.read_state()
    builder = ObservationBuilder(full_brief_every=8)
    first = builder.build(before, turn=0)

    adapter.advance(7)
    second = builder.build(adapter.read_state(), turn=1)

    assert not first.is_delta and second.is_delta
    assert len(second.brief) < len(first.brief)


def test_delta_reports_a_new_war_and_a_finished_focus():
    previous = GameState(country="SWE", national_focus="industrial_effort")
    current = GameState(country="SWE", national_focus=None)
    text = render_delta(previous, current)
    assert "NONE SELECTED" in text


def test_a_critical_event_forces_a_full_brief():
    adapter = MockAdapter(start="1939-08-30")
    builder = ObservationBuilder(full_brief_every=8)
    builder.build(adapter.read_state(), turn=0)          # first is always full
    adapter.apply(ActionCall("advance_time", {"days": 1}))
    builder.build(adapter.read_state(), turn=1)          # would normally be a delta
    adapter.advance(7)                                    # war breaks out here
    observation = builder.build(adapter.read_state(), turn=2)
    assert not observation.is_delta
