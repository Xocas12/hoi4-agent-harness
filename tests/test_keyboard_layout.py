"""The game window must type with the US layout the scan codes assume (seen live 2026-11-21:
under a Spanish/Galician layout the console received `ºrun llmb?turn.txt` and nothing ran).

Nothing here reads or changes this machine's keyboard layout: every Windows call is injected."""

from __future__ import annotations

import pytest

from hoi4_harness.adapters.window import US_LAYOUT, LayoutUnavailable, ensure_us_layout

GALICIAN_ES = 0x040A0456
GERMAN = 0x04070407


class Window:
    def __init__(self, layout, takes=True):
        self.layout, self.takes, self.requests = layout, takes, []

    def read(self):
        return self.layout

    def request(self, layout):
        self.requests.append(layout)
        if self.takes:
            self.layout = layout


def run(window, installed):
    return ensure_us_layout(read=window.read, installed=lambda: installed,
                            request=window.request, sleep=lambda s: None)


def test_a_us_window_is_left_alone():
    w = Window(US_LAYOUT)
    assert run(w, [US_LAYOUT]) is None and w.requests == []


def test_another_layout_is_switched_to_us_and_said_so():
    w = Window(GALICIAN_ES)
    line = run(w, [GALICIAN_ES, US_LAYOUT, GERMAN])
    assert w.requests == [US_LAYOUT] and w.layout == US_LAYOUT
    assert "0x040a0456" in line and "US" in line


def test_no_us_layout_installed_refuses_before_any_switch():
    w = Window(GERMAN)
    with pytest.raises(LayoutUnavailable, match="no US English layout"):
        run(w, [GERMAN])
    assert w.requests == []


def test_a_switch_that_does_not_take_refuses():
    w = Window(GALICIAN_ES, takes=False)
    with pytest.raises(LayoutUnavailable, match="did not"):
        run(w, [GALICIAN_ES, US_LAYOUT])


def test_a_platform_without_per_window_layouts_is_a_no_op():
    w = Window(None)
    assert run(w, []) is None and w.requests == []


def test_the_driver_refuses_a_key_when_the_layout_cannot_be_us(monkeypatch):
    from hoi4_harness.adapters import input_driver, window
    from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter, NotFocused

    driver = InputDriverAdapter(InputConfig(enforce_focus=False), dry_run=False)
    monkeypatch.setattr(window, "ensure_us_layout",
                        lambda: (_ for _ in ()).throw(LayoutUnavailable("no US English layout")))
    sent = []
    monkeypatch.setattr(driver, "_keys", lambda: type("K", (), {"chord": sent.append})())
    with pytest.raises(NotFocused, match="no US English layout"):
        driver.press("grave")
    assert sent == []
    assert input_driver  # imported for the monkeypatch target's module
