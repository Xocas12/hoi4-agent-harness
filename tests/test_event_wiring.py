"""The event reader's wiring into a headless campaign (adapters/headless._wire_event_reader):
what is offered, what is refused and why, and what the start log says. The fake game and a fake
OCR stand in for the real ones; no real OCR is started and no screen is read."""

from __future__ import annotations

from types import SimpleNamespace

from events_helpers import FIX, FakeEventScreen, catalog
from hoi4_harness.adapters.hands import Hands
from hoi4_harness.adapters.headless import MOUSE_ACTIONS, _wire_event_reader
from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.config import HarnessConfig
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.uilayout import Geometry

GEO = Geometry(1920, 1080)


def parts(tmp_path, **config):
    index = IdentifierIndex.build(Playset.from_paths(FIX))
    driver = InputDriverAdapter(InputConfig(geometry=GEO), dry_run=False, index=index,
                                focus_check=lambda t: FocusCheck(True, t), sleep=lambda s: None)
    cfg = HarnessConfig(country="GER", run_dir=tmp_path, **config)
    return driver, cfg, Hands(mouse=True)


def test_with_ocr_and_the_playset_the_reader_is_wired_and_the_action_is_offered(tmp_path):
    driver, cfg, hands = parts(tmp_path)
    screen = FakeEventScreen(GEO, catalog("GER"))
    _wire_event_reader(driver, cfg, hands, SimpleNamespace(event_ocr=screen.backend()))
    assert "choose_event_option" in driver.supported_actions and hands.event_reader is not None
    assert hands.event_reading["on"] and hands.event_reading["layout"] == "interface/eventwindow.gui"
    assert any("event reading: ON (UNVERIFIED" in line for line in hands.event_reading_lines())
    screen.open("germany.76")
    assert hands.event_reader.look().event.id == "germany.76"


def test_the_guard_is_given_the_readers_names_for_its_blind_answers(tmp_path):
    from hoi4_harness.adapters.event_windows import EventGuard, EventSighting

    driver, cfg, hands = parts(tmp_path)
    driver.event_guard = EventGuard(lambda: EventSighting("country"))
    screen = FakeEventScreen(GEO, catalog("GER"))
    _wire_event_reader(driver, cfg, hands, SimpleNamespace(event_ocr=screen.backend()))
    assert driver.event_guard.namer == hands.event_reader.open_title


def test_no_ocr_means_no_action_and_the_start_log_says_why(tmp_path):
    driver, cfg, hands = parts(tmp_path)
    screen = FakeEventScreen(GEO, catalog("GER"))
    _wire_event_reader(driver, cfg, hands, SimpleNamespace(
        event_ocr=screen.backend("Windows has no OCR language")))
    assert "choose_event_option" not in driver.supported_actions and hands.event_reader is None
    line = hands.event_reading_lines()[0]
    assert "event reading: OFF: Windows has no OCR language" in line and "answered blind" in line


def test_the_profile_can_switch_it_off_and_a_dry_run_reads_no_screen(tmp_path):
    driver, cfg, hands = parts(tmp_path, event_screen={"enabled": False})
    _wire_event_reader(driver, cfg, hands, None)
    assert "enabled is false" in hands.event_reading["why"] and hands.event_reader is None
    driver, cfg, hands = parts(tmp_path, dry_run=True)
    _wire_event_reader(driver, cfg, hands, None)
    assert "dry run" in hands.event_reading["why"]


def test_a_fake_game_that_draws_no_text_or_no_playset_is_named(tmp_path):
    driver, cfg, hands = parts(tmp_path)
    _wire_event_reader(driver, cfg, hands, SimpleNamespace())
    assert "draws no event text" in hands.event_reading["why"]
    driver.index = None
    hands = Hands(mouse=True)
    _wire_event_reader(driver, cfg, hands, None)
    assert "no playset index" in hands.event_reading["why"]


def test_the_action_is_a_mouse_action_so_a_keyboard_only_run_does_not_offer_it():
    assert "choose_event_option" in MOUSE_ACTIONS


def test_the_prompt_tells_the_model_about_events_only_when_the_action_is_offered(tmp_path):
    from hoi4_harness.agent.prompts import campaign_llm

    driver, cfg, hands = parts(tmp_path)
    screen = FakeEventScreen(GEO, catalog("GER"))
    _wire_event_reader(driver, cfg, hands, SimpleNamespace(event_ocr=screen.backend()))
    on = hands.summary(driver.supported_actions)
    assert on["events"] is True
    text = campaign_llm(on)
    assert "choose_event_option(option)" in text and "answer it blind first" in text
    off = hands.summary(set())
    assert off["events"] is False and "choose_event_option" not in campaign_llm(off)
