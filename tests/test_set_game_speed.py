"""set_game_speed(N>0) presses '+' once and reports success, unverified,
without unpausing a paused game."""
from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter
from hoi4_harness.types import ActionCall, GameState


class FakeGui:
    def __init__(self):
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)


def test_set_game_speed_is_not_reported_done_blind():
    gui = FakeGui()
    paused = GameState(date="1936-01-01")   # the date never moves: the game is paused
    driver = InputDriverAdapter(
        InputConfig(enforce_focus=False, clock_probe_seconds=1.0),
        dry_run=False, read_state=lambda: paused, sleep=lambda s: None, gui=gui,
    )
    result = driver.apply(ActionCall("set_game_speed", {"speed": 5}))
    game_runs = "space" in gui.pressed
    # Either the game was actually set running, or success is not claimed.
    assert game_runs or not result.ok, (gui.pressed, result)
    assert not gui.pressed, "nothing is pressed blind"
