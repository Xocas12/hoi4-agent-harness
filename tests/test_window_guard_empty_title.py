"""An empty expected title is a substring of every title, so the input guard
passed whatever window had focus."""
from hoi4_harness.adapters import window


def test_empty_expected_title_never_passes(monkeypatch):
    monkeypatch.setattr(window, "active_window_title", lambda: "Terminal")
    assert not window.check_focus("").focused
