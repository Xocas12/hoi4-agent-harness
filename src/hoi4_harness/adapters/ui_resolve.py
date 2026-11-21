"""Turn an action's arguments into what the 1.19 click paths actually type and click.

The model speaks in ids (``SWE_defense_act``, ``infantry_at``, a state name); the
game's UI takes a localised name typed into a search box, or a pixel. This step
runs before any input is sent and can refuse the whole action: a name that cannot
be typed, a technology whose grid cell is off the screen, a calibration that does
not exist yet. Refusing here is the cheap, honest outcome -- half a script, typed
into the wrong place, is not.

Derived values a script may use as ``{placeholders}``:

* ``set_national_focus``: ``{focus_name}`` -- an ASCII-only fragment of the focus's
  English name. The focus tree's search (ctrl+f) matches names and descriptions.
* ``queue_construction``: ``{state_name}`` -- the same, for the state; ``{count}``
  defaults to 1.
* ``start_research``: ``{tech_folder}`` and the computed click target
  ``research.tech_point``. The tech tree has no search box, so a technology is
  clicked where the sub-tree layout says it is drawn. See ``tech_point``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..identifiers import IdentifierIndex, TechPlace
from ..techtree import CONTENT_ORIGIN, Gridbox, LayoutUnknown, gui_point
from ..uilayout import Geometry, LayoutRefused

#: Click targets computed per call rather than recorded, and the recorded targets
#: each is computed from. ``hoi4-harness calibrate`` asks for the latter.
COMPUTED_TARGETS: dict[str, tuple[str, ...]] = {
    # Needs no calibration: the layout comes from the game's files, anchored at a
    # measured 1080p origin (``research.tree.origin`` is an optional override).
    "research.tech_point": (),
    # The research panel's slot and the folder tabs come from the interface files
    # (uilayout.py); a recorded ``research.free_slot`` / ``research.tab.<folder>``
    # still overrides them.
    "research.free_slot": (),
    # The 1.20 research path's two popup buttons (REFERENCE_POINTS): scaled from the
    # points measured at 1920x1080, UI scale 1; a recorded point still wins.
    "research.confirm": (),
    "research.mio_choice": (),
}
#: Points measured live on 1.20.0 at 1920x1080, UI scale 1 (2026-11-21), in screen pixels. Both
#: are on centred popups, so they are scaled from the screen's centre, the way
#: ``uilayout.Geometry`` scales GUI units (pixels / 1.25 at the measured size, times the window's
#: own scale). Only the measured size is verified; every other size or UI scale is an
#: extrapolation, which is why a recorded point wins.
REFERENCE_SCREEN = (1920, 1080)
REFERENCE_POINTS: dict[str, tuple[int, int]] = {
    # The technology popup's Research button.
    "research.confirm": (750, 252),
    # The MIO chooser's "Research without a Military Industrial Organization" row.
    "research.mio_choice": (1207, 345),
}


def reference_point(name: str, window: Geometry) -> tuple[int, int]:
    """A measured 1080p point placed in ``window``: its offset from the screen's centre, in GUI
    units, times the window's scale (the identity at 1920x1080, UI scale 1)."""
    rx, ry = REFERENCE_POINTS[name]
    measured = Geometry(*REFERENCE_SCREEN)
    dx, dy = (rx - REFERENCE_SCREEN[0] / 2) / measured.scale, (ry - REFERENCE_SCREEN[1] / 2) / measured.scale
    gui_w, gui_h = window.gui_size
    return window.to_screen(gui_w / 2 + dx, gui_h / 2 + dy)
#: Click targets that start with one of these are computed too (the folder tabs).
COMPUTED_PREFIXES = ("research.tab.",)


def is_computed(target: str) -> bool:
    return target in COMPUTED_TARGETS or target.startswith(COMPUTED_PREFIXES)
#: Placeholders the resolver fills, beyond the action's own arguments.
DERIVED_PARAMETERS: dict[str, frozenset[str]] = {
    "set_national_focus": frozenset({"focus_name"}),
    "queue_construction": frozenset({"state_name", "count"}),
    "start_research": frozenset({"tech_folder"}),
}
#: The tech folders 1.19.5 draws a tab for (the ``folder`` names common/technologies
#: uses), so ``research.tab.{tech_folder}`` can be calibrated before it is needed.
TECH_FOLDERS = (
    "infantry_folder", "support_folder", "armour_folder", "artillery_folder",
    "nsb_armour_folder", "air_techs_folder", "bba_air_techs_folder", "naval_folder",
    "industry_folder", "electronics_folder",
)
#: Values a templated click target is calibrated for, per placeholder. Others
#: (an advisor, a production line) have too many values to list and are named by hand.
def template_values() -> dict[str, tuple[str, ...]]:
    from ..actions.catalog import BUILDINGS

    return {"building": tuple(BUILDINGS), "tech_folder": TECH_FOLDERS}


#: The tech tree starts below the topbar and the folder tabs; a point above this
#: row is on the tabs, not on a technology.
MIN_TECH_Y = 150
MIN_FRAGMENT = 3

_TYPABLE = re.compile(r"[A-Za-z0-9'-]+")
_EDGE_PUNCT = ".,:;!?\"()"


class Unresolvable(Exception):
    """The action cannot be performed from the UI as asked; nothing was sent."""


@dataclass
class Resolved:
    arguments: dict
    coordinates: dict[str, tuple[int, int]] = field(default_factory=dict)


def search_fragment(name: str) -> str:
    """The longest run of ASCII words in a localised name, to type into a search box.

    Color codes (``§Y``), scripted loc (``[GetX]``), ``$KEY$`` references and
    escapes are not text the game would match, so they cut the name into runs;
    so do words with characters a US keyboard cannot type. A word that carries
    punctuation at its end ends its run too, since the search would not match
    across the punctuation. Raises Unresolvable when nothing usable is left.
    """
    text = re.sub(r"§.", "", name).replace("\\n", " ")
    best: list[str] = []

    def close(run: list[str]) -> None:
        nonlocal best
        if len(" ".join(run)) > len(" ".join(best)):
            best = run

    for segment in re.split(r"\[[^\]]*\]|\$[^$]*\$", text):
        run: list[str] = []
        for raw in segment.split():
            core = raw.lstrip(_EDGE_PUNCT).rstrip(_EDGE_PUNCT)
            if not _TYPABLE.fullmatch(core):
                close(run)
                run = []
                continue
            run.append(core)
            if raw.rstrip(_EDGE_PUNCT) != raw:      # punctuation after it: the run ends here
                close(run)
                run = []
        close(run)
    fragment = " ".join(best)
    if len(fragment) < MIN_FRAGMENT:
        raise Unresolvable(
            f"{name!r} has no ASCII-only fragment of {MIN_FRAGMENT}+ characters to type into "
            "the search box (accents, scripted names and symbols cannot be typed)"
        )
    return fragment


def tech_point(
    place: TechPlace,
    coordinates: dict[str, tuple[int, int]],
    screen: tuple[int, int] | None,
    tech: str = "",
    gridboxes: dict[tuple[str, str], Gridbox] | None = None,
    geometry: Geometry | None = None,
) -> tuple[int, int]:
    """Where to click a technology, from the interface file's layout (techtree.py).

    The tech's position is relative to its sub-tree; the sub-tree's box in
    ``countrytechtreeview.gui`` (``gridboxes``) puts it in the tab, in GUI units, and
    the screen scales those by ``height / 864`` (1.25 at 1080p, UI scale 1.0). The
    tab's content starts at a fixed spot, measured at 1080p; ``research.tree.origin``
    (the screen point where the content's GUI (0, 0) lands) replaces that guess if
    a layout or UI scale differs. An exact ``research.tech.<id>`` beats the rule. A
    point off the screen or on the tab strip is refused: this driver does not
    scroll the tree. So is a technology whose sub-tree the rule cannot place, or whose
    roots' boxes disagree on where it is. The rotated formats (``UP``, ``UPPER_LEFT``)
    are read from the interface file but not yet measured live (techtree.py).
    """
    exact = coordinates.get(f"research.tech.{tech}") if tech else None
    if exact is not None:
        return exact
    boxes = {root: gridboxes[(place.folder, f"{root}_tree")] for root in place.roots
             if (place.folder, f"{root}_tree") in (gridboxes or {})}
    if not boxes:
        raise Unresolvable(
            f"{tech or 'the technology'} hangs off {', '.join(place.roots) or 'no root'} in "
            f"{place.folder}, which has no sub-tree box in interface/countrytechtreeview.gui, "
            "so its place on the tab is unknown; calibrate research.tech.<id> for it"
        )
    if geometry is None:
        width, height = screen if screen else (1920, 1080)
        geometry = Geometry(width, height)
    width, height, scale = geometry.width, geometry.height, geometry.scale
    points = set()
    for root, box in boxes.items():
        try:
            points.add(gui_point(box, place.column, place.row, width / scale))
        except LayoutUnknown as why:
            raise Unresolvable(
                f"{tech or 'the technology'} is in the {root} sub-tree of {place.folder} "
                f"({box.format}) and {why}; calibrate research.tech.<id> for it"
            ) from None
    if len(points) > 1:
        raise Unresolvable(
            f"{tech or 'the technology'} is reached from {', '.join(boxes)} in {place.folder}, "
            "whose boxes put it in different places, so which one is drawn is unknown; "
            "calibrate research.tech.<id> for it"
        )
    box = next(iter(boxes.values()))
    (gx, gy), = points
    origin = coordinates.get("research.tree.origin")
    # A recorded origin is a screen point; the rule works in the window.
    ox, oy = ((origin[0] - geometry.origin[0], origin[1] - geometry.origin[1]) if origin
              else (CONTENT_ORIGIN[0] * scale, CONTENT_ORIGIN[1] * scale))
    x, y = round(ox + gx * scale), round(oy + gy * scale)
    # Points are checked in the window, then moved to where the window is on screen.
    if not (0 <= x < width and MIN_TECH_Y <= y < height):
        raise Unresolvable(
            f"{tech or 'the technology'} (column {place.column}, row {place.row} of the "
            f"{box_name(place)} sub-tree in {place.folder}) would be clicked at ({x}, {y}), "
            f"off the visible tree on a {width}x{height} screen; this driver does not scroll "
            "the tech tree"
        )
    return x + geometry.origin[0], y + geometry.origin[1]


def box_name(place: TechPlace) -> str:
    return place.roots[0] if place.roots else "?"


def resolve(
    name: str,
    arguments: dict,
    index: IdentifierIndex | None,
    coordinates: dict[str, tuple[int, int]],
    screen: tuple[int, int] | None,
    wanted: set[str] | frozenset[str] = frozenset(),
    geometry: Callable[[], Geometry] | None = None,
) -> Resolved:
    """Derive the extra arguments and computed click points for one action.

    ``wanted`` is what the script uses (its placeholders and click targets): only
    those are derived, so a script that never types a focus name does not need
    the localisation to run. ``geometry`` gives the window the points are placed
    in (uilayout.py), called only when a point is computed; it may raise
    LayoutRefused, which refuses the action before any input."""
    args = dict(arguments)
    computed: dict[str, tuple[int, int]] = {}
    if name == "set_national_focus" and "focus_name" in wanted:
        if index is None:
            raise Unresolvable("typing a focus name needs the game's localisation: pass --game-dir")
        focus_id = str(args["focus_id"])
        display = index.focus_name(focus_id)
        if not display:
            raise Unresolvable(f"no English name for focus {focus_id}, so there is nothing to search for")
        args["focus_name"] = search_fragment(display)
    elif name == "queue_construction" and wanted & {"state_name", "count"}:
        if index is None:
            raise Unresolvable("typing a state name needs the game's localisation: pass --game-dir")
        state = index.resolve_state(str(args["state"]))
        if state is None:
            raise Unresolvable(f"no state {args['state']!r} in this playset")
        args["state_name"] = search_fragment(state.name)
        args.setdefault("count", 1)
    elif name == "start_research" and wanted & {"tech_folder", "research.tech_point"}:
        if index is None:
            raise Unresolvable("locating a technology needs common/technologies: pass --game-dir")
        tech = str(args["technology"])
        place = index.tech_places.get(tech)
        if place is None:
            raise Unresolvable(f"no folder position for technology {tech} in this playset")
        args["tech_folder"] = place.folder
        try:
            window = geometry() if geometry else None
            computed["research.tech_point"] = tech_point(
                place, coordinates, screen, tech, index.tech_gridboxes, window)
            _research_panel(computed, coordinates, index, place.folder, wanted, window, screen)
            for point in REFERENCE_POINTS:
                if point in wanted and point not in coordinates:
                    computed[point] = reference_point(
                        point, window or Geometry(*(screen or REFERENCE_SCREEN)))
        except LayoutRefused as why:
            raise Unresolvable(str(why)) from None
    return Resolved(args, computed)


def _research_panel(
    computed: dict[str, tuple[int, int]],
    coordinates: dict[str, tuple[int, int]],
    index: IdentifierIndex,
    folder: str,
    wanted: set[str] | frozenset[str],
    window: Geometry | None,
    screen: tuple[int, int] | None,
) -> None:
    """The first research slot and the technology's folder tab, from the interface
    files, unless the operator recorded them (a recorded point wins)."""
    tab = f"research.tab.{folder}"
    todo = []
    if "research.free_slot" in wanted and "research.free_slot" not in coordinates:
        todo.append("research.free_slot")
    if (tab in wanted or "research.tab.{tech_folder}" in wanted) and tab not in coordinates:
        todo.append(tab)
    if not todo:
        return
    if window is None:
        window = Geometry(*(screen or (1920, 1080)))
    layout = index.research_layout()      # LayoutRefused: nothing derived, nothing sent
    if "research.free_slot" in todo:
        computed["research.free_slot"] = layout.slot_centre(0, window)
    if tab in todo:
        computed[tab] = layout.tab_centre(folder, window)
