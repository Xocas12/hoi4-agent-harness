"""Keyboard input as hardware scan codes (#3): HOI4 ignores pyautogui's key events."""

from __future__ import annotations

import json

import pytest

from hoi4_harness.adapters import keys as keys_module
from hoi4_harness.adapters.input_driver import (
    HOTKEYS,
    InputConfig,
    InputDriverAdapter,
    topbar_shortcuts,
)
from hoi4_harness.adapters.keys import ScanCodeKeys, UnsupportedPlatform
from hoi4_harness.adapters.ui_scripts import load_scripts, parse_steps
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.types import ActionCall, GameState

Q, W, T, Y, F, SHIFT, CTRL, ALT, SPACE, ENTER = 0x10, 0x11, 0x14, 0x15, 0x21, 0x2A, 0x1D, 0x38, 0x39, 0x1C


class Wire:
    """Records (scan code, up) the way SendInput would receive them."""

    def __init__(self):
        self.events: list[tuple[int, bool]] = []

    def __call__(self, code, up):
        self.events.append((code, up))


def keys_with(wire):
    return ScanCodeKeys(send=wire, sleep=lambda s: None)


def test_a_chord_presses_modifiers_first_and_releases_in_reverse():
    wire = Wire()
    keys_with(wire).chord("alt+q")
    assert wire.events == [(ALT, False), (Q, False), (Q, True), (ALT, True)]


def test_a_single_key_is_one_down_and_one_up():
    wire = Wire()
    keys_with(wire).chord("space")
    assert wire.events == [(SPACE, False), (SPACE, True)]


@pytest.mark.parametrize(("name", "code"), [("kp_plus", 0x4E), ("KP_MINUS", 0x4A), ("return", ENTER)])
def test_key_names_and_aliases_resolve(name, code):
    wire = Wire()
    keys_with(wire).chord(name)
    assert wire.events[0] == (code, False)


def test_text_is_typed_per_character_with_shift_for_capitals_and_underscore():
    wire = Wire()
    keys_with(wire).type_text("De f_")
    downs = [code for code, up in wire.events if not up]
    assert downs == [SHIFT, 0x20, 0x12, SPACE, 0x21, SHIFT, 0x0C]


def test_text_that_cannot_be_typed_sends_nothing_at_all():
    wire = Wire()
    with pytest.raises(ValueError, match="cannot type 'ö'"):
        keys_with(wire).type_text("Götaland")
    assert wire.events == []


def test_an_unknown_key_is_refused():
    with pytest.raises(ValueError, match="no scan code"):
        keys_with(Wire()).chord("ctrl+nonsense")


def test_the_default_sender_says_so_off_windows(monkeypatch, real_sendinput):
    monkeypatch.setattr(keys_module.sys, "platform", "linux")   # refused before any SendInput
    monkeypatch.setattr(keys_module, "_sendinput", real_sendinput)
    with pytest.raises(UnsupportedPlatform, match="Windows"):
        ScanCodeKeys(sleep=lambda s: None).chord("space")


# --- through the driver ------------------------------------------------------


def driver(wire=None, dry_run=False, **kw):
    wire = wire if wire is not None else Wire()
    return wire, InputDriverAdapter(
        InputConfig(), dry_run=dry_run, keys=keys_with(wire),
        focus_check=lambda t: FocusCheck(True, "Hearts of Iron IV"), sleep=lambda s: None, **kw)


def test_the_driver_presses_a_named_hotkey_as_its_binding():
    wire, d = driver()
    d.press("national_focus")
    assert d.log == ["press alt+q"]
    assert [c for c, up in wire.events if not up] == [ALT, Q]


def test_a_dry_run_logs_the_keys_and_sends_none():
    wire, d = driver(dry_run=True)
    d.press("research")
    d.type_text("Defense Act")
    assert d.log == ["press w", "type Defense Act"]
    assert wire.events == []


def test_typing_goes_through_the_scan_code_keys():
    wire, d = driver()
    d.type_text("ab")
    assert [c for c, up in wire.events if not up] == [0x1E, 0x30]


# --- the hotkey table --------------------------------------------------------

TOPBAR = '''
    buttonType = {
        name = "player_flag"
        shortcut = "q"
    }
    buttonType = {
        name = "national_focus_shortcut"
        shortcut = "alt+q"
    }
    buttonType = {
        name = "pause_button_date"
        shortcut = "SPACE" #Will be disabled in MP
    }
    buttonType = {
        name = "construction_button"
        shortcut = "t"
    }
'''


def test_topbar_shortcuts_reads_the_binding_by_button_name():
    found = topbar_shortcuts(TOPBAR)
    assert found == {"player_flag": "q", "national_focus_shortcut": "alt+q",
                     "pause_button_date": "space", "construction_button": "t"}


def test_the_hotkey_table_is_the_verified_1_19_one():
    assert HOTKEYS["national_focus"] == "alt+q"
    assert HOTKEYS["research"] == "w"
    assert HOTKEYS["construction"] == "t"
    assert HOTKEYS["production"] == "y"
    assert HOTKEYS["decisions"] == "shift+q"
    assert HOTKEYS["politics"] == "q"
    assert HOTKEYS["pause"] == "space"
    assert (HOTKEYS["speed_up"], HOTKEYS["speed_down"]) == ("kp_plus", "kp_minus")
    assert "diplomacy" not in HOTKEYS          # no panel hotkey exists


# --- Escape ------------------------------------------------------------------


def test_a_script_with_escape_is_refused_at_load(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"schema": 1, "scripts": {
        "set_national_focus": [{"press": "national_focus"}, {"press": "escape"}]}}))
    with pytest.raises(ValueError, match="Escape.*game menu"):
        load_scripts(path)


def test_a_script_built_in_code_with_escape_is_refused_before_any_key():
    wire, d = driver(scripts={"set_national_focus": parse_steps(
        [{"press": "national_focus"}, {"press": "esc"}])}, read_state=GameState)
    d.config.coordinates = {}
    result = d.apply(ActionCall("set_national_focus", {"focus_id": "X"}))
    assert result.error_kind == "rejected" and "Escape" in result.message
    assert wire.events == []
