"""verify() gives up with 'unverified' if the field is unknown on the very
first read, without polling -- though for the log-tail reader a field like
ai_directives/posture becomes known only when the mod's event line lands."""
from hoi4_harness.adapters.ui_scripts import verify
from hoi4_harness.types import ActionCall, GameState


def test_field_that_becomes_known_while_polling_is_verified():
    unknown = GameState(unknown_fields=["ai_directives"])
    known = GameState(unknown_fields=[], ai_directives=["protect POL"])
    reads = iter([unknown, known, known, known])
    call = ActionCall(name="set_ai_directive",
                      arguments={"directive": "protect", "target": "POL", "weight": 100})
    outcome = verify(call, GameState(unknown_fields=["ai_directives"]), lambda: next(reads),
                     timeout=1.0, poll=0.25, sleep=lambda s: None)
    assert outcome.ok, outcome
