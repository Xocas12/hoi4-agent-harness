"""Where the research screen draws a technology.

``common/technologies`` gives every technology ``folder = { name position = { x y } }``,
but that position is not a cell of the tab. It is relative to the technology's
*sub-tree*: the chain that hangs off one root technology through
``path = { leads_to_tech = ... }``. ``interface/countrytechtreeview.gui`` places each
sub-tree as a ``gridboxtype`` named ``<root technology>_tree`` inside the folder's
container, and the game lays the technologies out in that box. So the tab position
of a technology is

    gridbox.position + slotsize * (cell + 1/2)        (GUI units, centre of the slot)

with ``cell`` = ``(column, row)`` = ``(y, x)`` of the technology's own position
(``format = "LEFT"``: the years run across the screen, so ``y`` is the column).
A wide equipment box and a small icon on the same cell share a centre.

Only ``LEFT`` boxes are understood. ``UP`` and ``UPPER_LEFT`` (armour, air,
industry) turn the tree through a quarter and have not been measured, so they are
left unplaced rather than guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: GUI units per screen pixel scale: 1080p draws the 864-unit-high interface at 1.25.
REFERENCE_HEIGHT = 864
#: Where GUI (0, 0) of a folder's content lands, in GUI units, measured in the
#: infantry tab at 1920x1080 (screen centre of support weapons I = (230, 435)).
CONTENT_ORIGIN = (9.0, 103.0)

_FOLDER = re.compile(r'containerWindowType\s*=\s*\{\s*name\s*=\s*"(\w+_folder)"', re.I)
_GRIDBOX = re.compile(r"gridboxtype\s*=\s*\{", re.I)
_NAME = re.compile(r'name\s*=\s*"([^"]+)"')
_NUM = r"(-?\d+(?:\.\d+)?)"
_POSITION = re.compile(r"\bposition\s*=\s*\{\s*x\s*=\s*" + _NUM + r"(%?)\s*y\s*=\s*" + _NUM + r"(%?)")
_SLOT = re.compile(r"slotsize\s*=\s*\{\s*width\s*=\s*" + _NUM + r"\s*height\s*=\s*" + _NUM)
_FORMAT = re.compile(r'format\s*=\s*"?(\w+)"?')


@dataclass(frozen=True)
class Gridbox:
    """One sub-tree's box: its top-left in the folder's content, in GUI units."""

    x: float
    y: float
    slot_width: float
    slot_height: float
    format: str


def _close(text: str, open_at: int) -> int:
    """Index just past the brace that closes the one at ``open_at``."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(text)


def parse_gridboxes(gui: str) -> dict[tuple[str, str], Gridbox]:
    """``(folder, gridbox name)`` -> box, for every absolutely-placed gridbox in a
    ``*_folder`` container of a ``.gui`` file. Percent positions are skipped."""
    text = "\n".join(line.split("#", 1)[0] for line in gui.splitlines())
    boxes: dict[tuple[str, str], Gridbox] = {}
    for folder in _FOLDER.finditer(text):
        end = _close(text, text.index("{", folder.start()))
        for found in _GRIDBOX.finditer(text, folder.end(), end):
            body = text[found.end() - 1:_close(text, found.end() - 1)]
            name, pos, slot = _NAME.search(body), _POSITION.search(body), _SLOT.search(body)
            if not (name and pos and slot) or pos.group(2) or pos.group(4):
                continue
            fmt = _FORMAT.search(body)
            boxes[(folder.group(1), name.group(1))] = Gridbox(
                float(pos.group(1)), float(pos.group(3)),
                float(slot.group(1)), float(slot.group(2)),
                (fmt.group(1) if fmt else "LEFT").upper())
    return boxes


def gui_point(box: Gridbox, column: int, row: int) -> tuple[float, float]:
    """Centre of a technology's slot in GUI units, within the folder's content."""
    return (box.x + box.slot_width * (column + 0.5), box.y + box.slot_height * (row + 0.5))
