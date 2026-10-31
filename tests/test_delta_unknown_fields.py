"""A field that becomes readable (or unreadable) between turns is silently left
out of the delta."""
from hoi4_harness.observation.delta import render_delta
from hoi4_harness.types import GameState


def test_field_becoming_known_is_reported():
    before = GameState(unknown_fields=["war_support"])
    after = GameState(war_support=0.45)
    assert "war support" in render_delta(before, after)


def test_field_becoming_unknown_is_reported():
    before = GameState(war_support=0.45)
    after = GameState(unknown_fields=["war_support"])
    assert "war support" in render_delta(before, after)


def test_the_lines_say_unknown_rather_than_zero():
    before = GameState(war_support=0.45)
    after = GameState(unknown_fields=["war_support"])
    assert "war support: 45% -> unknown" in render_delta(before, after)
    assert "war support: unknown -> 45%" in render_delta(after, before)
