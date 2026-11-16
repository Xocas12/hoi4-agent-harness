"""Where a technology is drawn on the research screen (#3).

``position`` in common/technologies is relative to the technology's sub-tree, and
interface/countrytechtreeview.gui places the sub-trees (hoi4_harness/techtree.py).
The first tests run the rule on a small committed fixture; the last runs it on the
real install against screen centres measured in the live game (1920x1080, UI
scale 1.0, infantry tab, tree unscrolled).
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

import pytest

from hoi4_harness.adapters.ui_resolve import Unresolvable, tech_point
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.techtree import Gridbox, gui_point, parse_gridboxes

FIXTURE = Path(__file__).parent / "fixtures" / "techtree"
SCREEN = (1920, 1080)


@pytest.fixture(scope="module")
def fixture_index() -> IdentifierIndex:
    return IdentifierIndex.build(Playset.from_paths(FIXTURE))


def point(index, tech):
    return tech_point(index.tech_places[tech], {}, SCREEN, tech, index.tech_gridboxes)


def test_gridboxes_are_read_per_folder_without_comments_or_other_folders(fixture_index):
    boxes = fixture_index.tech_gridboxes
    assert boxes[("infantry_folder", "support_weapons_tree")] == Gridbox(140, 210, 70, 70, "LEFT")
    assert boxes[("infantry_folder", "infantry_weapons_tree")].y == 325   # not the commented copy
    percent = boxes[("infantry_folder", "percent_tree")]
    assert (percent.x, percent.x_percent, percent.y_percent, percent.width) == (10, True, False, None)
    assert boxes[("armour_folder", "armour1_tree")].format == "UPPER_LEFT"
    wide = boxes[("air_techs_folder", "wide_tree")]
    assert (wide.width, wide.width_percent, wide.margin_left, wide.margin_right) == (80, True, 0, 25)
    assert boxes[("air_techs_folder", "early_fighter_tree")].width == 400
    assert boxes[("armour_folder", "armour1_tree")].margin_left == 13     # the default
    assert parse_gridboxes("gridboxtype = { name = \"x\" position = { x = 1 y = 2 } }") == {}


def test_a_tech_belongs_to_the_root_whose_chain_reaches_it(fixture_index):
    places = fixture_index.tech_places
    a, b = places["support_weapons2"], places["infantry_weapons2"]
    support, improved = point(fixture_index, "support_weapons2"), point(fixture_index, "infantry_weapons2")
    assert (a.column, a.row) == (b.column, b.row)
    assert support[0] == improved[0] == 580
    assert improved[1] - support[1] == 144       # (325 - 210) GUI units at 1.25


def test_the_layout_rule_on_the_fixture(fixture_index):
    # centre = scale * (content origin + box + slot * (cell + 1/2)); scale = 1080 / 864
    assert point(fixture_index, "support_weapons") == (230, 435)
    assert point(fixture_index, "infantry_weapons") == (142, 579)     # column -1
    assert point(fixture_index, "infantry_at") == (1280, 754)         # row 2


# --- the rotated formats ----------------------------------------------------
# GUI = (box + ...) as in techtree.py; screen = (origin + GUI) * 1.25, origin (9, 103),
# less 13 GUI units of x in a folder whose left margin is 0 (the air tabs).


def test_upper_left_swaps_the_axes_and_starts_at_the_top_left(fixture_index):
    # armour1: row (x) 2, column (y) 3 -> GUI (0 + 70*2.5, 40 + 70*3.5) = (175, 285)
    assert point(fixture_index, "armour1") == (230, 485)


def test_up_centres_row_zero_on_the_box_and_runs_the_years_down(fixture_index):
    # box (300, 32) 400 wide, slots 60x70, left margin 0: x = 300 + 200 + 60*row - 13
    assert point(fixture_index, "early_fighter") == (620, 388)
    assert point(fixture_index, "fighter1") == (620, 475)          # a year later: lower
    assert point(fixture_index, "cas1") == (320, 475)              # row -4: 240 units left


def test_up_boxes_placed_and_sized_by_percent_use_the_width_between_the_margins(fixture_index):
    # 1536 GUI wide, margins 0 + 25: 10% + 80% / 2 of 1511 = 742.5, less 13, plus 70 for row 1
    x, y = point(fixture_index, "wide1")
    assert abs(x - 1027) <= 1 and y == 560


def test_a_box_that_cannot_be_placed_is_refused_with_the_reason(fixture_index):
    with pytest.raises(Unresolvable, match="UP box has no size"):
        point(fixture_index, "percent")
    with pytest.raises(Unresolvable, match="different places"):
        point(fixture_index, "up_shared")


def test_roots_that_draw_a_tech_in_the_same_place_do_not_make_it_ambiguous(fixture_index):
    assert fixture_index.tech_places["tw_shared"].roots == ("tw1", "tw2")
    assert point(fixture_index, "tw_shared") == (580, 885)


@functools.cache
def _real_index() -> IdentifierIndex:
    return IdentifierIndex.build(Playset.from_paths(_install()))


def _install() -> Path | None:
    candidates = [os.environ.get("HOI4_GAME_DIR", ""),
                  "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"]
    for candidate in candidates:
        path = Path(candidate) if candidate else None
        if path and (path / "interface" / "countrytechtreeview.gui").is_file() \
                and (path / "common" / "technologies" / "infantry.txt").is_file():
            return path
    return None


#: (technology id, measured screen centre). Wide equipment boxes and small icons on
#: the same cell share a centre.
MEASURED = [
    ("support_weapons", (230, 435)), ("support_weapons2", (580, 435)),
    ("support_weapons3", (930, 435)), ("support_weapons4", (1280, 435)),
    ("infantry_weapons", (143, 578)), ("infantry_weapons1", (405, 578)),
    ("infantry_weapons2", (580, 578)), ("improved_infantry_weapons", (758, 578)),
    ("improved_infantry_weapons_2", (930, 578)), ("advanced_infantry_weapons", (1280, 578)),
    ("advanced_infantry_weapons2", (1630, 578)),
    ("infantry_at", (1280, 750)), ("infantry_at2", (1455, 750)),
    ("tech_trucks", (143, 945)), ("motorised_infantry", (405, 945)),
    ("mechanised_infantry", (930, 945)), ("mechanised_infantry2", (1280, 945)),
    ("mechanised_infantry3", (1630, 945)),
]


@pytest.mark.skipif(_install() is None, reason="no HOI4 install to read the layout from")
@pytest.mark.parametrize(("tech", "truth"), MEASURED)
def test_the_real_install_reproduces_the_measured_infantry_tab(tech, truth):
    x, y = point(_real_index(), tech)
    assert abs(x - truth[0]) <= 25 and abs(y - truth[1]) <= 25, (tech, (x, y), truth)


# --- internal consistency of the rotated tabs (not measured live) ------------

SCREEN_GUI_WIDTH = 1536      # 1920 px at the 1.25 scale
ROTATED = ("armour_folder", "nsb_armour_folder", "artillery_folder", "air_techs_folder",
           "bba_air_techs_folder", "industry_folder", "electronics_folder")
#: ``techtree_stripes`` (or the ``size_filler``) container size per folder, from the
#: interface file; a folder's techs must sit inside it. Electronics' filler is 710
#: wide but its subtitles and techs run to 1400, so only its height is a bound.
CONTENT_SIZE = {
    "armour_folder": (1450, 1090), "nsb_armour_folder": (1900, 1090),
    "artillery_folder": (1515, 1390), "air_techs_folder": (2500, 1300),
    "bba_air_techs_folder": (2240, 1300), "industry_folder": (1650, 1000),
    "electronics_folder": (None, 1100),
}


def _placed(folder):
    """tech -> {GUI centre per root that has a box}, for one folder of the install."""
    index = _real_index()
    out = {}
    for tech, place in index.tech_places.items():
        if place.folder != folder:
            continue
        out[tech] = {
            gui_point(box, place.column, place.row, SCREEN_GUI_WIDTH)
            for root in place.roots
            if (box := index.tech_gridboxes.get((folder, f"{root}_tree")))
        }
    return out


@pytest.mark.skipif(_install() is None, reason="no HOI4 install to read the layout from")
@pytest.mark.parametrize("folder", ROTATED)
def test_every_rotated_tech_gets_one_place_and_no_two_share_it(folder):
    placed = _placed(folder)
    assert placed
    assert [t for t, pts in placed.items() if len(pts) != 1] == []
    seen: dict[tuple[float, float], str] = {}
    for tech, pts in placed.items():
        (pt,) = pts
        assert pt not in seen, (tech, seen[pt], pt)
        seen[pt] = tech


@pytest.mark.skipif(_install() is None, reason="no HOI4 install to read the layout from")
@pytest.mark.parametrize("folder", ROTATED)
def test_a_rotated_chain_runs_down_the_tab_never_back_up(folder):
    index, placed = _real_index(), _placed(folder)
    for tech, targets in index.tech_leads.items():
        for nxt in targets:
            if tech in placed and nxt in placed:
                (_, y), (_, next_y) = next(iter(placed[tech])), next(iter(placed[nxt]))
                assert next_y >= y, (tech, nxt, y, next_y)


@pytest.mark.skipif(_install() is None, reason="no HOI4 install to read the layout from")
@pytest.mark.parametrize("folder", ROTATED)
def test_the_techs_of_one_year_share_a_height_within_a_box(folder):
    """The year is the position's y, so two techs of one sub-tree on the same y
    must be level, and a later y strictly lower."""
    index = _real_index()
    by_box: dict[tuple[str, int], float] = {}
    for place in index.tech_places.values():
        if place.folder != folder:
            continue
        for root in place.roots:
            box = index.tech_gridboxes.get((folder, f"{root}_tree"))
            if box:
                _, y = gui_point(box, place.column, place.row, SCREEN_GUI_WIDTH)
                assert by_box.setdefault((root, place.column), y) == y
    for (root, column), y in by_box.items():
        for (other, later), later_y in by_box.items():
            if other == root and later > column:
                assert later_y > y


@pytest.mark.skipif(_install() is None, reason="no HOI4 install to read the layout from")
@pytest.mark.parametrize("folder", ROTATED)
def test_every_rotated_tech_is_inside_the_folders_content(folder):
    width, height = CONTENT_SIZE[folder]
    for tech, pts in _placed(folder).items():
        for x, y in pts:
            assert x >= 0 and (width is None or x <= width), (tech, x, y)
            assert 0 <= y <= height, (tech, x, y)
