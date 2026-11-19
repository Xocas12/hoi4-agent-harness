"""The 1.19 click paths (#3): names typed into the game's search boxes, tech grid
points, and the shipped profile -- all checked without a keyboard, a mouse or a game."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter
from hoi4_harness.adapters.keys import ScanCodeKeys
from hoi4_harness.adapters.ui_resolve import (
    Unresolvable,
    resolve,
    search_fragment,
    tech_point,
)
from hoi4_harness.adapters.ui_scripts import expanded_targets, load_scripts, missing_targets
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.calibration import DEFAULT_TARGETS
from hoi4_harness.identifiers import IdentifierIndex, Playset, TechPlace
from hoi4_harness.types import ActionCall, ConstructionItem, GameState, ResearchSlot

TECHTREE = Path(__file__).resolve().parent / "fixtures" / "techtree"
PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "ui_scripts.1.19.json"

ALT, Q, CTRL, F, SHIFT, ENTER = 0x38, 0x10, 0x1D, 0x21, 0x2A, 0x1C


# --- search fragments --------------------------------------------------------


@pytest.mark.parametrize(("name", "fragment"), [
    ("Defense Act", "Defense Act"),
    ("Seek [GetBalticAxisInvesmentsAdj] Investments", "Investments"),
    ("§YThe Big Plan§!", "The Big Plan"),
    ("Götaland Expansion Plan", "Expansion Plan"),
    ("Rise, Fall and Rise Again", "Fall and Rise Again"),
    ("Army $COUNTRY$ Reform Plan", "Reform Plan"),
    ("Königsberg Port", "Port"),
])
def test_the_longest_ascii_run_of_words_is_what_gets_typed(name, fragment):
    assert search_fragment(name) == fragment


@pytest.mark.parametrize("name", ["Västra Götaland", "[GetName]", "Ö", "日本"])
def test_a_name_with_nothing_typable_is_refused(name):
    with pytest.raises(Unresolvable, match="ASCII-only"):
        search_fragment(name)


# --- the index: names and tech places ---------------------------------------


@pytest.fixture
def game(tmp_path) -> Path:
    root = tmp_path / "game"
    (root / "common/national_focus").mkdir(parents=True)
    (root / "common/national_focus/swe.txt").write_text(
        'focus_tree = { id = swe country = { factor = 0 modifier = { tag = SWE } }\n'
        'focus = { id = SWE_defense_act x = 1 y = 1 }\n'
        'focus = { id = SWE_scripted x = 2 y = 1 }\n'
        'focus = { id = SWE_nameless x = 3 y = 1 } }\n', encoding="utf-8")
    shutil.copytree(TECHTREE / "common/technologies", root / "common/technologies")
    shutil.copytree(TECHTREE / "interface", root / "interface")
    (root / "localisation/english").mkdir(parents=True)
    (root / "localisation/english/swe_l_english.yml").write_text(
        'l_english:\n SWE_defense_act: "Defense Act"\n'
        ' SWE_scripted: "[GetX] §YDrive§!"\n', encoding="utf-8-sig")
    (root / "localisation/english/state_names_l_english.yml").write_text(
        'l_english:\n STATE_1: "Stockholm"\n STATE_2: "Västra Götaland"\n', encoding="utf-8-sig")
    (root / "history/states").mkdir(parents=True)
    for number in (1, 2):
        (root / f"history/states/{number}.txt").write_text(
            f"state = {{ id = {number} name = STATE_{number} history = {{ owner = SWE }} }}\n",
            encoding="utf-8")
    return root


@pytest.fixture
def index(game) -> IdentifierIndex:
    return IdentifierIndex.build(Playset.from_paths(game))


def test_focus_names_come_from_english_localisation(index):
    assert index.focus_name("SWE_defense_act") == "Defense Act"
    assert index.focus_name("SWE_nameless") is None


def test_tech_places_resolve_year_variables_to_columns(index):
    assert index.tech_places["infantry_weapons1"] == TechPlace("infantry_folder", column=2, row=0)
    assert index.tech_places["infantry_at"] == TechPlace("infantry_folder", column=12, row=2)


def test_a_focus_resolves_to_its_search_fragment(index):
    resolved = resolve("set_national_focus", {"focus_id": "SWE_scripted"}, index, {}, None,
                       wanted={"focus_name"})
    assert resolved.arguments["focus_name"] == "Drive"
    with pytest.raises(Unresolvable, match="no English name"):
        resolve("set_national_focus", {"focus_id": "SWE_nameless"}, index, {}, None,
                wanted={"focus_name"})


def test_a_state_with_no_typable_name_is_refused(index):
    with pytest.raises(Unresolvable, match="ASCII-only"):
        resolve("queue_construction", {"state": "2", "building": "dockyard"}, index, {}, None,
                wanted={"state_name"})
    ok = resolve("queue_construction", {"state": "Stockholm", "building": "dockyard"}, index, {},
                 None, wanted={"state_name", "count"})
    assert ok.arguments["state_name"] == "Stockholm" and ok.arguments["count"] == 1


# --- the tech grid -----------------------------------------------------------

def test_a_tech_is_clicked_where_its_subtree_puts_it(index):
    assert tech_point(index.tech_places["infantry_weapons1"], {}, (1920, 1080), "infantry_weapons1",
                      index.tech_gridboxes) == (405, 579)
    assert tech_point(index.tech_places["infantry_at"], {}, (1920, 1080), "infantry_at",
                      index.tech_gridboxes) == (1280, 754)


def test_a_tree_origin_and_an_exact_tech_override_the_rule(index):
    at, boxes = index.tech_places["infantry_at"], index.tech_gridboxes
    moved = tech_point(at, {"research.tree.origin": (0, 0)}, (1920, 1080), "infantry_at", boxes)
    assert moved == (1269, 625)     # GUI (140 + 70*12.5, 325 + 70*2.5) * 1.25 from (0, 0)
    exact = {"research.tech.special": (9, 9)}
    assert tech_point(at, exact, (1920, 1080), "special", boxes) == (9, 9)


def test_a_cell_off_the_screen_is_refused_not_clicked(index):
    far = index.tech_places["far_tech"]
    with pytest.raises(Unresolvable, match="off the visible tree"):
        tech_point(far, {}, (1920, 1080), "far_tech", index.tech_gridboxes)


def test_a_subtree_without_a_box_or_in_a_turned_layout_is_not_guessed(index):
    for tech, why in (("orphan_root", "no sub-tree box"), ("armour1", "UPPER_LEFT")):
        with pytest.raises(Unresolvable, match=why):
            tech_point(index.tech_places[tech], {}, (1920, 1080), tech, index.tech_gridboxes)


# --- end to end through the driver, against the shipped profile -------------


class Wire:
    def __init__(self):
        self.events: list[tuple[int, bool]] = []

    def __call__(self, code, up):
        self.events.append((code, up))

    def downs(self):
        return [c for c, up in self.events if not up]


class Gui:
    def __init__(self):
        self.sent: list[str] = []

    def moveTo(self, x, y, duration=0):
        self.sent.append(f"move {x},{y}")

    def click(self):
        self.sent.append("click")


COORDS = {
    "research.free_slot": (100, 200), "research.tab.infantry_folder": (150, 120),
    "construction.building.dockyard": (50, 60), "map.search.first_result": (70, 80),
    "map.centre": (960, 540),
}


def build_driver(index, read_state, dry_run=False):
    wire, gui = Wire(), Gui()
    driver = InputDriverAdapter(
        InputConfig(coordinates=dict(COORDS), screen=(1920, 1080), settle_seconds=0),
        dry_run=dry_run, scripts=load_scripts(PROFILE), index=index, gui=gui,
        keys=ScanCodeKeys(send=wire, sleep=lambda s: None), read_state=read_state,
        focus_check=lambda t: FocusCheck(True, "Hearts of Iron IV"), sleep=lambda s: None,
        verify_timeout=0)
    return driver, wire, gui


def test_the_profile_loads_and_covers_the_five_actions():
    assert set(load_scripts(PROFILE)) == {
        "set_national_focus", "start_research", "queue_construction", "set_production",
        "hire_advisor"}


def test_set_national_focus_types_the_name_and_is_verified_by_the_reader(index):
    wire_ref = {}

    def read():
        started = ENTER in wire_ref["wire"].downs()
        return GameState(national_focus="SWE_defense_act" if started else None)

    driver, wire, _ = build_driver(index, read)
    wire_ref["wire"] = wire
    result = driver.apply(ActionCall("set_national_focus", {"focus_id": "SWE_defense_act"}))
    assert result.ok and result.message == "done and verified"
    assert wire.downs()[:4] == [ALT, Q, CTRL, F]               # alt+q, then ctrl+f
    assert wire.downs()[-2:] == [SHIFT, ENTER]                  # shift+enter starts it
    assert "type Defense Act" in driver.log


def test_a_focus_the_reader_cannot_see_is_unverified_never_success(index):
    driver, _, _ = build_driver(index, lambda: GameState(unknown_fields=["national_focus"]))
    result = driver.apply(ActionCall("set_national_focus", {"focus_id": "SWE_defense_act"}))
    assert not result.ok and result.error_kind == "unverified"


def test_a_focus_that_did_not_start_is_rejected(index):
    driver, _, _ = build_driver(index, lambda: GameState(national_focus=None))
    result = driver.apply(ActionCall("set_national_focus", {"focus_id": "SWE_defense_act"}))
    assert not result.ok and result.error_kind == "rejected"


def test_an_untypable_state_sends_nothing(index):
    driver, wire, gui = build_driver(index, GameState)
    result = driver.apply(ActionCall("queue_construction", {"building": "dockyard", "state": "2"}))
    assert result.error_kind == "rejected" and "nothing was sent" in result.message
    assert wire.events == [] and gui.sent == []


def test_construction_clicks_the_state_once_per_building_asked_for(index):
    driver, _, gui = build_driver(index, GameState, dry_run=True)
    result = driver.apply(ActionCall(
        "queue_construction", {"building": "dockyard", "state": "Stockholm", "count": 3}))
    assert result.error_kind == "not_executed"
    assert driver.log.count("click map.centre at (960, 540)") == 3
    assert "type Stockholm" in driver.log and "press ctrl+f" in driver.log
    assert gui.sent == []


def test_construction_at_queue_time_is_unverified_through_a_reader_that_cannot_see_it(index):
    driver, _, _ = build_driver(index, lambda: GameState(unknown_fields=["construction"]))
    result = driver.apply(ActionCall("queue_construction", {"building": "dockyard",
                                                             "state": "Stockholm"}))
    assert result.error_kind == "unverified"


def test_construction_is_verified_when_the_queue_shows_it(index):
    seen = {"n": 0}

    def read():
        seen["n"] += 1
        items = [ConstructionItem(state="Stockholm", building="dockyard")] if seen["n"] > 1 else []
        return GameState(construction=items)

    driver, _, _ = build_driver(index, read)
    assert driver.apply(ActionCall("queue_construction", {"building": "dockyard",
                                                           "state": "Stockholm"})).ok


def test_research_clicks_the_tab_then_the_computed_grid_point(index):
    driver, _, gui = build_driver(index, GameState, dry_run=True)
    result = driver.apply(ActionCall("start_research", {"technology": "infantry_at"}))
    assert result.error_kind == "not_executed"
    assert "click research.tab.infantry_folder at (150, 120)" in driver.log
    assert "click research.tech_point at (1280, 754)" in driver.log
    assert driver.log[-1] == "press enter"
    assert driver._computed == {}


def test_research_is_verified_when_the_slot_shows_the_tech(index):
    seen = {"n": 0}

    def read():
        seen["n"] += 1
        slots = [ResearchSlot(index=0, technology="infantry_at")] if seen["n"] > 1 else []
        return GameState(research=slots)

    driver, _, gui = build_driver(index, read)
    assert driver.apply(ActionCall("start_research", {"technology": "infantry_at"})).ok
    assert "move 1280,754" in gui.sent


def test_research_for_a_tech_off_the_visible_tree_sends_nothing(index):
    driver, wire, gui = build_driver(index, GameState)
    result = driver.apply(ActionCall("start_research", {"technology": "far_tech"}))
    assert result.error_kind == "rejected" and "does not scroll" in result.message
    assert wire.events == [] and gui.sent == []


def test_research_is_unverified_through_a_reader_that_cannot_see_slots(index):
    driver, _, _ = build_driver(index, lambda: GameState(unknown_fields=["research"]))
    result = driver.apply(ActionCall("start_research", {"technology": "infantry_at"}))
    assert result.error_kind == "unverified"


def test_a_missing_index_refuses_rather_than_typing_the_id():
    driver, wire, _ = build_driver(None, GameState)
    result = driver.apply(ActionCall("set_national_focus", {"focus_id": "SWE_defense_act"}))
    assert result.error_kind == "rejected" and "game-dir" in result.message
    assert wire.events == []


# --- calibration -------------------------------------------------------------


def test_the_default_targets_are_exactly_what_the_profile_clicks():
    wanted = [t for t in expanded_targets(load_scripts(PROFILE)) if "{" not in t]
    assert list(DEFAULT_TARGETS) == wanted


def test_check_scripts_passes_once_the_concrete_targets_are_calibrated(tmp_path, capsys):
    from hoi4_harness.calibration import Calibration, save_calibration
    from hoi4_harness.cli import main

    (tmp_path / "ui_scripts.json").write_text(PROFILE.read_text(encoding="utf-8"), encoding="utf-8")
    scripts = load_scripts(PROFILE)
    concrete = {t: (1, 1) for t in missing_targets(scripts, {})}
    assert "research.free_slot" in concrete and "research.tech_point" not in concrete
    save_calibration(Calibration(screen=(1920, 1080), coordinates=concrete),
                     tmp_path / "calibration.json")
    capsys.readouterr()
    assert main(["check-scripts", "--run-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ok: every concrete target is calibrated" in out
    assert json.loads((tmp_path / "calibration.json").read_text())["screen"] == [1920, 1080]
