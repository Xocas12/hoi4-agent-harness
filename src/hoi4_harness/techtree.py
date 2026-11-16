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

The other formats are the same rule with the axes and the origin changed, read off
the labels the interface file itself draws in each tab (the subtitles and the year
numbers sit where the cells are). ``column`` is the position's ``y`` and ``row`` its
``x``, as above:

* ``LEFT``: years run across. ``x = box.x + w*(column + 1/2)``,
  ``y = box.y + h*(row + 1/2)``. Measured live (infantry tab).
* ``UPPER_LEFT``: years run down, origin at the box's top-left.
  ``x = box.x + w*(row + 1/2)``, ``y = box.y + h*(column + 1/2)``.
* ``UP``: years run down and the box is centred on its width, so ``row`` 0 is the
  middle lane and negative rows go left: ``x = box.x + width/2 + w*row``,
  ``y = box.y + h*(column + 1/2)``. A box with no ``size`` has no centre, and is refused.

A percent ``x`` or ``width`` is a share of the folder's inner width (the screen's
width in GUI units less the folder margins), and is approximate. The rotated formats
have not been measured live: see docs/live-session.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: GUI units per screen pixel scale: 1080p draws the 864-unit-high interface at 1.25.
REFERENCE_HEIGHT = 864
#: A tech folder's left and right ``margin`` in the interface file, as the infantry
#: folder has them (the measured ``CONTENT_ORIGIN`` includes the left one). Percent
#: sizes and positions of a box are shares of the width between the margins, and a
#: folder with another left margin (air, 0) shifts its content by the difference.
FOLDER_MARGINS = (13.0, 25.0)
#: Where GUI (0, 0) of a folder's content lands, in GUI units, measured in the
#: infantry tab at 1920x1080 (screen centre of support weapons I = (230, 435)).
CONTENT_ORIGIN = (9.0, 103.0)

_FOLDER = re.compile(r'containerWindowType\s*=\s*\{\s*name\s*=\s*"(\w+_folder)"', re.I)
_GRIDBOX = re.compile(r"gridboxtype\s*=\s*\{", re.I)
_NAME = re.compile(r'name\s*=\s*"([^"]+)"')
_NUM = r"(-?\d+(?:\.\d+)?)"
_POSITION = re.compile(r"\bposition\s*=\s*\{\s*x\s*=\s*" + _NUM + r"(%?)\s*y\s*=\s*" + _NUM + r"(%?)")
_SIZE = re.compile(r"(?<![a-z])size\s*=\s*\{\s*width\s*=\s*" + _NUM + r"(%?)", re.I)
_MARGIN = re.compile(r"margin\s*=\s*\{[^}]*?left\s*=\s*" + _NUM + r"[^}]*?right\s*=\s*" + _NUM)
_NESTED = re.compile(r"(containerWindowType|gridboxtype|iconType)\s*=\s*\{", re.I)
_SLOT = re.compile(r"slotsize\s*=\s*\{\s*width\s*=\s*" + _NUM + r"\s*height\s*=\s*" + _NUM)
_FORMAT = re.compile(r'format\s*=\s*"?(\w+)"?')


class LayoutUnknown(ValueError):
    """A sub-tree whose place on the tab the rule cannot give; the message says why."""


@dataclass(frozen=True)
class Gridbox:
    """One sub-tree's box: its top-left in the folder's content, in GUI units.

    ``width`` is the ``size`` width (None when the box has none); the ``*_percent``
    flags say a value is a percentage of the folder's inner width rather than units."""

    x: float
    y: float
    slot_width: float
    slot_height: float
    format: str
    width: float | None = None
    x_percent: bool = False
    width_percent: bool = False
    y_percent: bool = False
    margin_left: float = FOLDER_MARGINS[0]
    margin_right: float = FOLDER_MARGINS[1]


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
    """``(folder, gridbox name)`` -> box, for every gridbox in a tech folder
    container (``*folder``) of a ``.gui`` file."""
    text = "\n".join(line.split("#", 1)[0] for line in gui.splitlines())
    boxes: dict[tuple[str, str], Gridbox] = {}
    for folder in _FOLDER.finditer(text):
        end = _close(text, text.index("{", folder.start()))
        nested = _NESTED.search(text, folder.end(), end)
        margin = _MARGIN.search(text, folder.end(), nested.start() if nested else end)
        margins = (float(margin.group(1)), float(margin.group(2))) if margin else FOLDER_MARGINS
        for found in _GRIDBOX.finditer(text, folder.end(), end):
            body = text[found.end() - 1:_close(text, found.end() - 1)]
            name, pos, slot = _NAME.search(body), _POSITION.search(body), _SLOT.search(body)
            if not (name and pos and slot):
                continue
            fmt, size = _FORMAT.search(body), _SIZE.search(body)
            boxes[(folder.group(1), name.group(1))] = Gridbox(
                float(pos.group(1)), float(pos.group(3)),
                float(slot.group(1)), float(slot.group(2)),
                (fmt.group(1) if fmt else "LEFT").upper(),
                float(size.group(1)) if size else None,
                bool(pos.group(2)), bool(size and size.group(2)), bool(pos.group(4)), *margins)
    return boxes


def gui_point(box: Gridbox, column: int, row: int, screen_width: float | None = None) -> tuple[float, float]:
    """Centre of a technology's slot in GUI units, within the folder's content.

    ``screen_width`` is the screen's width in GUI units, needed only for boxes placed
    or sized by percent. Raises ``LayoutUnknown`` for a box the rule cannot place."""
    view_width = None if screen_width is None else screen_width - box.margin_left - box.margin_right
    shift = box.margin_left - FOLDER_MARGINS[0]
    if box.y_percent:
        raise LayoutUnknown("its box is placed by a percent y, which the rule does not read")

    def across(value: float, percent: bool) -> float:
        if not percent:
            return value
        if view_width is None:
            raise LayoutUnknown("its box is placed by percent and the screen width is unknown")
        return value / 100 * view_width

    left = across(box.x, box.x_percent) + shift
    if box.format == "LEFT":
        return (left + box.slot_width * (column + 0.5), box.y + box.slot_height * (row + 0.5))
    if box.format == "UPPER_LEFT":
        return (left + box.slot_width * (row + 0.5), box.y + box.slot_height * (column + 0.5))
    if box.format == "UP":
        if box.width is None:
            raise LayoutUnknown("its UP box has no size, so its centre is unknown")
        return (left + across(box.width, box.width_percent) / 2 + box.slot_width * row,
                box.y + box.slot_height * (column + 0.5))
    raise LayoutUnknown(f"its box has format {box.format}, which the rule does not know")
