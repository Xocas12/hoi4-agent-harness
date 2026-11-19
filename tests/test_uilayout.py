"""Click points at any resolution, UI scale and window mode (#3).

The transform, the orientations and the settings are checked on small fixtures; the
real install is checked against the points measured live at 1920x1080, UI scale 1.0.
Nothing here needs the game, a keyboard or a mouse.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter
from hoi4_harness.adapters.keys import ScanCodeKeys
from hoi4_harness.adapters.ui_scripts import load_scripts
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.types import ActionCall, GameState
from hoi4_harness.uilayout import (
    DisplaySettings,
    Geometry,
    LayoutRefused,
    Rect,
    ResearchLayout,
    anchor,
    geometry_for,
    parse_display_settings,
    parse_gui,
    place,
)

SETTINGS = Path(__file__).parent / "fixtures" / "settings"
TECHTREE = Path(__file__).parent / "fixtures" / "techtree"
PROFILE = Path(__file__).resolve().parents[1] / "profiles" / "ui_scripts.1.19.json"


def read(name: str) -> str:
    return (SETTINGS / name).read_text(encoding="utf-8")


# --- the scale and the transform ----------------------------------------------


@pytest.mark.parametrize(("w", "h", "gui", "scale"), [
    (1920, 1080, 1.0, 1.25), (1280, 720, 1.0, 720 / 864), (2560, 1440, 1.0, 1440 / 864),
    (3440, 1440, 1.0, 1440 / 864), (1920, 1200, 1.0, 1200 / 864),
    (1920, 1080, 1.25, 1.5625), (1920, 1080, 0.8, 1.0),
])
def test_the_scale_is_the_height_over_864_times_the_ui_scale(w, h, gui, scale):
    assert Geometry(w, h, gui).scale == pytest.approx(scale)


def test_the_screen_in_gui_units_is_what_the_scale_leaves():
    assert Geometry(1920, 1080).gui_size == (1536.0, 864.0)
    assert Geometry(3440, 1440).gui_size[0] == pytest.approx(3440 / (1440 / 864))


def test_a_window_that_is_not_at_the_screen_origin_shifts_every_point():
    assert Geometry(1280, 720, 1.0, (100, 50)).to_screen(60, 120) == (150, 150)


PARENT = Rect(10, 20, 200, 100)


@pytest.mark.parametrize(("orientation", "point"), [
    ("UPPER_LEFT", (10, 20)), ("upper_right", (210, 20)), ("LOWER_LEFT", (10, 120)),
    ("LOWER_RIGHT", (210, 120)), ("CENTER", (110, 70)), ("CENTER_UP", (110, 20)),
    ("CENTER_DOWN", (110, 120)),
])
def test_an_orientation_names_the_parents_point_a_position_is_counted_from(orientation, point):
    assert anchor(orientation, PARENT) == point


def test_an_unknown_orientation_is_refused_not_guessed():
    with pytest.raises(LayoutRefused, match="DIAGONAL"):
        anchor("DIAGONAL", PARENT)


def test_a_window_is_its_position_from_the_anchor_with_percent_and_negative_sizes():
    node = parse_gui('w = { position = { x = -43 y = 9 } Orientation = "UPPER_RIGHT" '
                     'size = { width = 50%% height = -5 } }').children()[0]
    assert place(node, PARENT) == Rect(167, 29, 100, 95)


def test_show_position_is_where_a_sliding_window_ends_up():
    node = parse_gui("w = { position = { x = -606 y = 78 } show_position = { x = -6 y = 78 } }").children()[0]
    assert place(node, Rect(0, 0, 1536, 864)) == Rect(-6, 78, 0, 0)


def test_the_gui_parser_keeps_quoted_hashes_and_drops_comments():
    tree = parse_gui('a = { name = "x#y" # gone\n  b = { name = "n" } }')
    assert tree.children()[0].name == "x#y" and tree.children()[0].children()[0].name == "n"


# --- settings -------------------------------------------------------------------


def test_a_borderless_window_takes_the_screen_size_not_the_recorded_one():
    parsed = parse_display_settings(read("settings_borderless.txt"), read("pdx_borderless.txt"))
    assert parsed == DisplaySettings(gui_scale=1.25, mode="borderless", resolution=None)


def test_exclusive_fullscreen_reads_the_resolution_pdx_settings_records():
    parsed = parse_display_settings(read("settings_borderless.txt"), read("pdx_fullscreen.txt"))
    assert (parsed.mode, parsed.resolution) == ("fullscreen", (2560, 1440))


def test_windowed_mode_reads_the_window_size_and_falls_back_to_the_old_flags():
    parsed = parse_display_settings(read("settings_windowed.txt"))
    assert parsed == DisplaySettings(gui_scale=0.8, mode="windowed", resolution=(1280, 720))
    assert parse_display_settings(read("settings_borderless.txt")).mode == "borderless"


@pytest.mark.parametrize("text", ["", "graphics={ size={ x=1 y=2 } }", "gui_scale=zero\n", "gui_scale=0\n"])
def test_settings_without_a_usable_ui_scale_are_refused(text):
    with pytest.raises(LayoutRefused, match="gui_scale"):
        parse_display_settings(text)


def test_a_window_mode_with_no_resolution_anywhere_is_refused():
    with pytest.raises(LayoutRefused, match="no resolution"):
        parse_display_settings("gui_scale=1.0\nfullScreen=no\n")


# --- refusing a window that is not the one the settings describe -------------------


def test_the_window_must_be_the_size_a_windowed_setting_records():
    windowed = DisplaySettings(0.8, "windowed", (1280, 720))
    assert geometry_for(windowed, (40, 30, 1280, 720)) == Geometry(1280, 720, 0.8, (40, 30))
    with pytest.raises(LayoutRefused, match="1280x720 but the game window is 1920x1080"):
        geometry_for(windowed, (0, 0, 1920, 1080))


def test_a_borderless_window_is_trusted_at_whatever_size_it_is():
    assert geometry_for(DisplaySettings(1.0, "borderless"), (0, 0, 3440, 1440)).width == 3440


def test_unreadable_settings_or_window_refuse():
    with pytest.raises(LayoutRefused, match="settings"):
        geometry_for(None, (0, 0, 1920, 1080))
    with pytest.raises(LayoutRefused, match="window"):
        geometry_for(DisplaySettings(1.0, "borderless"), None)
    with pytest.raises(LayoutRefused, match="0x0"):
        geometry_for(DisplaySettings(1.0, "borderless"), (0, 0, 0, 0))


# --- the research controls ---------------------------------------------------------

PANEL = """guiTypes = {
 containerWindowType = { name = "countrytechnologyview"
  position = { x=-606 y=78 } show_position = { x=-6 y =78 } size = { width=550 height=100%% }
  containerWindowType = { name = "research_slots" position = { x=0 y=80 } size = { width=550 height=-5 }
   containerWindowType = { name = "slots_window" position = { x = 0 y=140 } size = { width=550 height=-1 }
    gridboxType = { name = "research_slots_grid" position = { x =15 y=0 }
     size = { width = -20 height = 100%% }
     slotsize = { width = 500 height = 100 } format = "UPPER_LEFT" } } } } }
"""
TREE = """guiTypes = {
 containerWindowType = { name = "countrytechtreeview" position = { x=-3 y=78 }
  size = { width = 100%% height = 100%% }
  containerWindowType = { name = "folder_tabs" position = { x=0 y=7 } size = { width = 100%% height = 30 }
   buttonType = { name = "infantry_folder_tab" position = { x = 22 y = 0 }
    quadTextureSprite = "GFX_inf" frame = 1 }
   buttonType = { name = "right_tab" position = { x = -100 y = 5 } quadTextureSprite = "GFX_inf"
    Orientation = "UPPER_RIGHT" } } }
}
"""


@pytest.fixture
def layout() -> ResearchLayout:
    return ResearchLayout(PANEL, TREE, lambda name: (90.0, 60.0))


def test_a_slot_centre_is_the_grid_cell_centre_scaled_to_the_screen(layout):
    # panel x -6 + grid 15 + half a 500 slot = 259; y 78 + 80 + 140 + half a 100 slot = 348
    assert layout.slot_centre(0, Geometry(1920, 1080)) == (324, 435)
    assert layout.slot_centre(2, Geometry(1920, 1080)) == (324, 685)
    s = 720 / 864
    assert layout.slot_centre(0, Geometry(1280, 720)) == (round(259 * s), round(348 * s))


def test_the_ui_scale_multiplies_the_scale_and_a_window_offset_shifts_the_point(layout):
    assert layout.slot_centre(0, Geometry(1920, 1080, 1.25)) == (round(259 * 1.5625), round(348 * 1.5625))
    assert layout.slot_centre(0, Geometry(1280, 720, 1.0, (200, 100))) == (200 + 216, 100 + 290)


def test_a_left_anchored_slot_does_not_move_on_a_wider_screen_in_gui_units(layout):
    narrow, wide = Geometry(2560, 1440), Geometry(3440, 1440)
    assert layout.slot_centre(1, narrow) == layout.slot_centre(1, wide)


def test_a_slot_below_the_visible_list_is_refused(layout):
    with pytest.raises(LayoutRefused, match="slot 9 is below"):
        layout.slot_centre(9, Geometry(1280, 720))


def test_a_tab_is_the_buttons_corner_plus_half_its_sprite_frame(layout):
    # tree (-3, 78) + strip (0, 7) + button (22, 0) + frame/2 (45, 30)
    assert layout.tab_centre("infantry_folder", Geometry(1920, 1080)) == (round(64 * 1.25), round(115 * 1.25))


def test_a_right_anchored_tab_follows_the_right_edge_of_the_screen(layout):
    narrow = layout.tab_centre("right", Geometry(1280, 720))
    wide = layout.tab_centre("right", Geometry(2560, 720))
    assert wide[0] - narrow[0] == 1280 and wide[1] == narrow[1]


def test_a_tab_the_interface_does_not_have_is_refused(layout):
    with pytest.raises(LayoutRefused, match="no window 'plasma_folder_tab'"):
        layout.tab_centre("plasma_folder", Geometry(1920, 1080))


# --- the real install, against what was measured live ------------------------------


def _install() -> Path | None:
    for candidate in (os.environ.get("HOI4_GAME_DIR", ""),
                      "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"):
        path = Path(candidate) if candidate else None
        if path and (path / "interface" / "countrytechnologyview.gui").is_file() \
                and (path / "gfx" / "interface" / "techtree" / "techtree_infantry_tab.dds").is_file() \
                and (path / "common").is_dir():
            return path
    return None


@pytest.fixture(scope="module")
def real_layout() -> ResearchLayout:
    return ResearchLayout.from_playset(Playset.from_paths(_install()))


#: Measured live on 1.19.5 at 1920x1080, UI scale 1.0 (centres of the clickable controls).
MEASURED_1080 = {"slot0": (322, 428), "infantry_folder": (78, 140), "support_folder": (190, 140)}


@pytest.mark.skipif(_install() is None, reason="no HOI4 install to read the layout from")
def test_the_real_install_reproduces_the_measured_1080p_points(real_layout):
    g = Geometry(1920, 1080)
    got = {"slot0": real_layout.slot_centre(0, g),
           "infantry_folder": real_layout.tab_centre("infantry_folder", g),
           "support_folder": real_layout.tab_centre("support_folder", g)}
    for name, truth in MEASURED_1080.items():
        dx, dy = got[name][0] - truth[0], got[name][1] - truth[1]
        assert abs(dx) <= 10 and abs(dy) <= 10, (name, got[name], truth)


@pytest.mark.skipif(_install() is None, reason="no HOI4 install to read the layout from")
def test_every_tech_folder_the_script_can_name_has_a_tab_on_the_real_install(real_layout):
    from hoi4_harness.adapters.ui_resolve import TECH_FOLDERS

    for folder in TECH_FOLDERS:
        x, y = real_layout.tab_centre(folder, Geometry(1920, 1080))
        assert 0 < x < 1920 and 100 < y < 200, folder


# --- wired into start_research ---------------------------------------------------------


class Wire:
    def __init__(self):
        self.events: list = []

    def __call__(self, code, up):
        self.events.append((code, up))


class Gui:
    def __init__(self):
        self.sent: list[str] = []

    def moveTo(self, x, y, duration=0):
        self.sent.append(f"move {x},{y}")

    def click(self):
        self.sent.append("click")


@pytest.fixture(scope="module")
def tech_index() -> IdentifierIndex:
    index = IdentifierIndex.build(Playset.from_paths(TECHTREE))
    index._research_layout = ResearchLayout(PANEL, TREE, lambda name: (90.0, 60.0))
    return index


def driver_for(index, *, dry_run=True, screen=None, geometry=None, coordinates=None,
               settings=None, window=None):
    wire, gui = Wire(), Gui()
    driver = InputDriverAdapter(
        InputConfig(coordinates=dict(coordinates or {}), screen=screen, geometry=geometry,
                    settle_seconds=0),
        dry_run=dry_run, scripts=load_scripts(PROFILE), index=index, gui=gui,
        keys=ScanCodeKeys(send=wire, sleep=lambda s: None), read_state=GameState,
        focus_check=lambda t: FocusCheck(True, "Hearts of Iron IV"), sleep=lambda s: None,
        verify_timeout=0, display_settings=lambda: settings, window_rect=lambda: window)
    return driver, wire, gui


def research(driver):
    return driver.apply(ActionCall("start_research", {"technology": "infantry_at"}))


def test_start_research_derives_the_slot_and_the_tab_with_no_calibration(tech_index):
    driver, _, _ = driver_for(tech_index, geometry=Geometry(1920, 1080))
    assert research(driver).error_kind == "not_executed"
    assert "click research.free_slot at (324, 435)" in driver.log
    assert "click research.tab.infantry_folder at (80, 144)" in driver.log


def test_start_research_follows_the_live_window_and_the_ui_scale(tech_index):
    settings = DisplaySettings(1.25, "borderless")
    driver, _, gui = driver_for(tech_index, dry_run=False, settings=settings, window=(0, 0, 1280, 720))
    research(driver)
    s = 720 / 864 * 1.25
    assert f"move {round(259 * s)},{round(348 * s)}" in gui.sent


def test_a_recorded_point_overrides_the_derived_one(tech_index):
    driver, _, _ = driver_for(tech_index, geometry=Geometry(1920, 1080),
                              coordinates={"research.free_slot": (100, 200)})
    research(driver)
    assert "click research.free_slot at (100, 200)" in driver.log
    assert "click research.tab.infantry_folder at (80, 144)" in driver.log


def test_a_window_that_is_not_the_size_the_settings_say_sends_nothing(tech_index):
    settings = DisplaySettings(1.0, "windowed", (1280, 720))
    driver, wire, gui = driver_for(tech_index, dry_run=False, settings=settings, window=(0, 0, 1920, 1080))
    result = research(driver)
    assert result.error_kind == "rejected" and "nothing was sent" in result.message
    assert "1280x720" in result.message and "1920x1080" in result.message
    assert wire.events == [] and gui.sent == []


@pytest.mark.parametrize(("settings", "window", "why"), [
    (None, (0, 0, 1920, 1080), "settings"),
    (DisplaySettings(1.0, "borderless"), None, "window"),
])
def test_unreadable_settings_or_window_send_nothing(tech_index, settings, window, why):
    driver, wire, gui = driver_for(tech_index, dry_run=False, settings=settings, window=window)
    result = research(driver)
    assert result.error_kind == "rejected" and why in result.message
    assert wire.events == [] and gui.sent == []


def test_a_playset_without_the_research_windows_refuses_and_says_which_file():
    index = IdentifierIndex.build(Playset.from_paths(TECHTREE))
    driver, wire, gui = driver_for(index, dry_run=False, geometry=Geometry(1920, 1080))
    result = research(driver)
    assert result.error_kind == "rejected" and "countrytechnologyview.gui" in result.message
    assert wire.events == [] and gui.sent == []
