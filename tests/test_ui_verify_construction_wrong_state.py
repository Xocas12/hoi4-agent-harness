"""_verify_construction counts buildings of the type anywhere in the queue, so
a click that landed on the wrong state is reported 'done and verified'."""
from hoi4_harness.adapters.ui_scripts import verify
from hoi4_harness.types import ActionCall, ConstructionItem, GameState


def test_building_queued_in_another_state_is_not_verified():
    before = GameState(unknown_fields=[])
    after = GameState(unknown_fields=[], construction=[
        ConstructionItem(building="civilian_factory", state="Norrbotten")])
    call = ActionCall(name="queue_construction",
                      arguments={"building": "civilian_factory", "state": "Stockholm", "count": 1})
    outcome = verify(call, before, lambda: after, timeout=0, sleep=lambda s: None)
    assert not outcome.ok, outcome
