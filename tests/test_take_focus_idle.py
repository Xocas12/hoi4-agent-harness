"""campaign_take_focus brings the game to the front only when nobody is at the keyboard."""

from hoi4_harness.adapters.headless import idle_gated


def test_takes_only_after_the_quiet_time():
    taken = []
    take = lambda title: taken.append(title) or True  # noqa: E731
    assert idle_gated(take, lambda: 30.0, 120)("Hearts of Iron IV") is False
    assert idle_gated(take, lambda: None, 120)("Hearts of Iron IV") is False
    assert taken == []
    assert idle_gated(take, lambda: 300.0, 120)("Hearts of Iron IV") is True
    assert taken == ["Hearts of Iron IV"]
