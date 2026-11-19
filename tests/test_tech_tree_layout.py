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

from hoi4_harness.adapters.ui_resolve import tech_point
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.techtree import Gridbox, parse_gridboxes

FIXTURE = Path(__file__).parent / "fixtures" / "techtree"
SCREEN = (1920, 1080)


@pytest.fixture(scope="module")
def fixture_index() -> IdentifierIndex:
    return IdentifierIndex.build(Playset.from_paths(FIXTURE))


def point(index, tech):
    return tech_point(index.tech_places[tech], {}, SCREEN, tech, index.tech_gridboxes)


def test_gridboxes_are_read_per_folder_without_comments_percent_or_other_folders(fixture_index):
    boxes = fixture_index.tech_gridboxes
    assert boxes[("infantry_folder", "support_weapons_tree")] == Gridbox(140, 210, 70, 70, "LEFT")
    assert boxes[("infantry_folder", "infantry_weapons_tree")].y == 325   # not the commented copy
    assert ("infantry_folder", "percent_tree") not in boxes
    assert boxes[("armour_folder", "armour1_tree")].format == "UPPER_LEFT"
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
