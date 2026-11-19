"""Where the research screen's controls are, at any resolution, UI scale and window mode (#3).

The click path used to rest on one calibration at 1920x1080, UI scale 1.0. This
module derives the same points from what the game itself ships and reads:

* **The scale.** HOI4 authors its interface for a 864-unit-high screen and draws it
  at ``height / 864`` screen pixels per GUI unit (1.25 at 1080p, measured live), times
  the ``gui_scale`` of ``settings.txt``. 864 is not in any define or ``.gui`` file (the
  engine owns it); the evidence is the measured 1.25 at 1080p and that ``settings.txt``
  names 1536x864 as the default window size and ``min_gui`` 1280x720. The width does
  not enter the formula, which is what the left-anchored windows measured so far say;
  a different aspect ratio only moves the windows anchored right or centre. Whether
  the engine clamps the scale to ``min_gui`` or to the width on a narrow aspect
  ratio (16:10) is not known: UNVERIFIED, see docs/adapters.md.
* **The anchoring.** ``Orientation`` names the point of the parent a window's
  ``position`` is counted from (``UPPER_RIGHT`` is the parent's top-right corner, so
  ``x = -43`` is 43 units in from the right edge). The window's own top-left lands on
  that point plus the position. ``size`` is units, a percent of the parent, or
  (negative) the parent less that many units.
* **The windows.** ``interface/countrytechnologyview.gui`` is the research panel (the
  slot grid) and ``interface/countrytechtreeview.gui`` the tech tree (the folder tab
  buttons, whose size is that of the sprite's frame in its ``.dds`` header).

Display settings come from ``settings.txt`` and ``pdx_settings.txt`` in the user
directory. In a borderless window the resolution they record is not the one drawn at
(this install: 1536x864 recorded, 1920x1080 on screen), so there the live window is
the truth; in a windowed or exclusive-fullscreen mode the recorded resolution must
match the window, or nothing is clicked.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

#: The height the interface is authored for; the scale is ``height / REFERENCE_HEIGHT``.
REFERENCE_HEIGHT = 864


class LayoutRefused(Exception):
    """The geometry or the layout cannot be trusted; nothing should be clicked."""


# --- display settings and the GUI -> screen transform -------------------------


@dataclass(frozen=True)
class DisplaySettings:
    """What ``settings.txt`` / ``pdx_settings.txt`` say about the display."""

    gui_scale: float = 1.0
    #: ``windowed``, ``fullscreen`` or ``borderless``.
    mode: str = "windowed"
    #: The resolution the game draws at in this mode, or None for borderless (it
    #: takes the desktop's size, not the one the settings recorded).
    resolution: tuple[int, int] | None = None


def _setting(text: str, key: str) -> str | None:
    match = re.search(r"(?m)^\s*" + re.escape(key) + r"\s*=\s*\"?([^\s\"}]+)\"?", text)
    return match.group(1) if match else None


def _pdx_value(text: str, key: str) -> str | None:
    match = re.search(r'"' + re.escape(key) + r'"\s*=\s*\{[^}]*?value\s*=\s*"([^"]*)"', text)
    return match.group(1) if match else None


def parse_display_settings(settings: str, pdx_settings: str = "") -> DisplaySettings:
    """Read the display block of the two settings files. Raises LayoutRefused when
    ``settings.txt`` has no ``gui_scale`` (so it is not a settings file we can read)."""
    scale_raw = _setting(settings, "gui_scale")
    try:
        scale = float(scale_raw) if scale_raw is not None else None
    except ValueError:
        scale = None
    if scale is None or scale <= 0:
        raise LayoutRefused("settings.txt has no usable gui_scale, so the UI scale is unknown")
    mode_raw = (_pdx_value(pdx_settings, "display_mode") or "").lower()
    if mode_raw in ("windowed",):
        mode = "windowed"
    elif mode_raw.startswith("borderless"):
        mode = "borderless"
    elif mode_raw.startswith("fullscreen"):
        mode = "fullscreen"
    elif (_setting(settings, "fullScreen") or "").lower() == "yes":
        mode = "fullscreen"
    elif (_setting(settings, "borderless") or "").lower() == "yes":
        mode = "borderless"
    else:
        mode = "windowed"
    resolution = None
    if mode == "fullscreen":
        raw = _pdx_value(pdx_settings, "fullscreen_resolution") or ""
        found = re.fullmatch(r"(\d+)x(\d+)", raw)
        if found:
            resolution = (int(found.group(1)), int(found.group(2)))
    if mode in ("windowed", "fullscreen") and resolution is None:
        size = re.search(r"size\s*=\s*\{\s*x\s*=\s*(\d+)\s*y\s*=\s*(\d+)", settings)
        if size:
            resolution = (int(size.group(1)), int(size.group(2)))
    if mode != "borderless" and resolution is None:
        raise LayoutRefused(f"the settings name no resolution for {mode} mode")
    return DisplaySettings(scale, mode, resolution)


def read_display_settings(user_dir: Path) -> DisplaySettings:
    """The display settings in a game user directory (read-only). Raises LayoutRefused."""
    settings = Path(user_dir) / "settings.txt"
    try:
        text = settings.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise LayoutRefused(f"cannot read {settings}: {exc}") from None
    try:
        pdx = (Path(user_dir) / "pdx_settings.txt").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        pdx = ""
    return parse_display_settings(text, pdx)


@dataclass(frozen=True)
class Geometry:
    """The game window as the transform sees it: size in pixels, where its client
    area starts on the screen, and the UI scale."""

    width: int
    height: int
    gui_scale: float = 1.0
    origin: tuple[int, int] = (0, 0)

    @property
    def scale(self) -> float:
        """Screen pixels per GUI unit."""
        return self.height / REFERENCE_HEIGHT * self.gui_scale

    @property
    def gui_size(self) -> tuple[float, float]:
        """The screen in GUI units: the parent every top-level window is placed in."""
        return (self.width / self.scale, self.height / self.scale)

    def to_screen(self, gx: float, gy: float) -> tuple[int, int]:
        return (round(self.origin[0] + gx * self.scale), round(self.origin[1] + gy * self.scale))


def geometry_for(
    settings: DisplaySettings | None,
    window: tuple[int, int, int, int] | None,
) -> Geometry:
    """The geometry to click in, or LayoutRefused with the reason.

    ``window`` is the live client rect ``(x, y, width, height)``. It must be seen,
    and in windowed / exclusive-fullscreen mode it must be the size the settings
    record: clicks computed for one size land elsewhere on another."""
    if settings is None:
        raise LayoutRefused(
            "the display settings (settings.txt) could not be read, so the UI scale is unknown")
    if window is None:
        raise LayoutRefused("the game window's size could not be read, so no point can be placed on it")
    x, y, width, height = window
    if width <= 0 or height <= 0:
        raise LayoutRefused(f"the game window reports {width}x{height}")
    if settings.resolution is not None and settings.resolution != (width, height):
        raise LayoutRefused(
            f"the settings say {settings.mode} {settings.resolution[0]}x{settings.resolution[1]} but "
            f"the game window is {width}x{height}; change the setting or the window, "
            "or the clicks would land on the wrong controls")
    return Geometry(width, height, settings.gui_scale, (x, y))


# --- reading a .gui file ------------------------------------------------------

_TOKEN = re.compile(r'"[^"]*"|[{}=]|[^\s{}="]+')


@dataclass
class Node:
    """``key = { ... }`` of a .gui file; ``body`` keeps order and repeated keys."""

    kind: str
    body: list[tuple[str, object]] = field(default_factory=list)

    def get(self, key: str, default=None):
        key = key.lower()
        for k, v in self.body:
            if k.lower() == key:
                return v
        return default

    def children(self) -> list[Node]:
        return [v for _, v in self.body if isinstance(v, Node)]

    @property
    def name(self) -> str:
        value = self.get("name")
        return value.strip('"') if isinstance(value, str) else ""


def parse_gui(text: str) -> Node:
    """The whole file as one node (``guiTypes``'s body at the top)."""
    text = "\n".join(_strip_comment(line) for line in text.splitlines())
    tokens = _TOKEN.findall(text)
    pos = 0

    def block() -> list[tuple[str, object]]:
        nonlocal pos
        body: list[tuple[str, object]] = []
        while pos < len(tokens) and tokens[pos] != "}":
            key = tokens[pos]
            pos += 1
            if pos < len(tokens) and tokens[pos] == "=":
                pos += 1
                if pos < len(tokens) and tokens[pos] == "{":
                    pos += 1
                    node = Node(key, block())
                    pos += 1    # the closing brace
                    body.append((key, node))
                elif pos < len(tokens):
                    body.append((key, tokens[pos].strip('"') if tokens[pos].startswith('"') else tokens[pos]))
                    pos += 1
            else:
                body.append((key, ""))
        return body

    return Node("file", block())


def _strip_comment(line: str) -> str:
    inside = False
    for i, ch in enumerate(line):
        if ch == '"':
            inside = not inside
        elif ch == "#" and not inside:
            return line[:i]
    return line


# --- placing windows ----------------------------------------------------------

#: Orientation -> the point of the parent (as a share of its width and height).
ORIENTATIONS = {
    "UPPER_LEFT": (0.0, 0.0), "UPPER_RIGHT": (1.0, 0.0),
    "LOWER_LEFT": (0.0, 1.0), "LOWER_RIGHT": (1.0, 1.0),
    "CENTER": (0.5, 0.5), "CENTER_UP": (0.5, 0.0), "CENTER_DOWN": (0.5, 1.0),
}


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x + self.width / 2, self.y + self.height / 2)


def anchor(orientation: str, parent: Rect) -> tuple[float, float]:
    """The parent's point a child's position is counted from."""
    share = ORIENTATIONS.get(orientation.upper())
    if share is None:
        raise LayoutRefused(f"orientation {orientation} is not one this layout reads")
    return (parent.x + parent.width * share[0], parent.y + parent.height * share[1])


def _amount(raw: object, parent: float) -> float | None:
    """A size or position: units, ``N%`` / ``N%%`` of the parent, or None when absent."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        if raw.endswith("%"):
            return float(raw.rstrip("%")) / 100 * parent
        return float(raw)
    except ValueError:
        return None


def place(node: Node, parent: Rect, natural: tuple[float, float] = (0.0, 0.0)) -> Rect:
    """Where a window lands inside its parent's rect, all in GUI units.

    ``natural`` is the size of a control with no ``size`` (a button's sprite frame)."""
    pos = node.get("show_position") or node.get("position")
    px = py = 0.0
    if isinstance(pos, Node):
        px = _amount(pos.get("x"), parent.width) or 0.0
        py = _amount(pos.get("y"), parent.height) or 0.0
    size = node.get("size")
    width, height = natural
    if isinstance(size, Node):
        w, h = _amount(size.get("width"), parent.width), _amount(size.get("height"), parent.height)
        if w is not None:
            width = parent.width + w if w < 0 else w
        if h is not None:
            height = parent.height + h if h < 0 else h
    orientation = node.get("orientation")
    ax, ay = anchor(orientation if isinstance(orientation, str) else "UPPER_LEFT", parent)
    return Rect(ax + px, ay + py, width, height)


def _find(parent: Node, name: str) -> Node:
    for child in parent.children():
        if child.name.lower() == name.lower():
            return child
    raise LayoutRefused(f"no window {name!r} in the interface files")


# --- the research screen ------------------------------------------------------

_SPRITE = re.compile(r"spriteType\s*=\s*\{(.*?)\}", re.I | re.S)


class ResearchLayout:
    """The research panel and tech-tree controls, from the game's interface files."""

    def __init__(self, panel_gui: str, tree_gui: str, sprite_size, gui_size_default=(1536.0, 864.0)):
        """``sprite_size(name)`` -> ``(width, height)`` of one frame of a sprite, in
        GUI units, or raises LayoutRefused."""
        self._panel = parse_gui(panel_gui)
        self._tree = parse_gui(tree_gui)
        self._sprite_size = sprite_size
        self._default_screen = gui_size_default

    @classmethod
    def from_playset(cls, playset) -> ResearchLayout:
        """Read the layered files (a mod's override wins). Raises LayoutRefused."""
        def last(name: str) -> str:
            files = playset.layered("interface", name)
            if not files:
                raise LayoutRefused(f"interface/{name} not found in the playset")
            return files[-1].read_text(encoding="utf-8-sig", errors="replace")

        sprites: dict[str, tuple[str, int]] = {}
        for gfx in playset.layered("interface", "*.gfx"):
            for body in _SPRITE.findall(gfx.read_text(encoding="utf-8-sig", errors="replace")):
                name = re.search(r'name\s*=\s*"([^"]+)"', body)
                tex = re.search(r'textureFile\s*=\s*"([^"]+)"', body, re.I)
                frames = re.search(r"noOfFrames\s*=\s*(\d+)", body, re.I)
                if name and tex:
                    sprites[name.group(1).lower()] = (tex.group(1), int(frames.group(1)) if frames else 1)
        roots = [m.path for m in reversed(playset.mods)] + [playset.game_dir]

        def sprite_size(name: str) -> tuple[float, float]:
            found = sprites.get(name.lower())
            if found is None:
                raise LayoutRefused(f"sprite {name} is not defined in interface/*.gfx")
            texture = found[0].replace("//", "/").replace("\\", "/")
            for root in roots:
                path = Path(root) / texture
                if path.is_file():
                    width, height = dds_size(path)
                    return (width / found[1], float(height))
            raise LayoutRefused(f"texture {texture} of sprite {name} not found")

        return cls(last("countrytechnologyview.gui"), last("countrytechtreeview.gui"), sprite_size)

    def _top(self, root: Node, name: str, screen: Rect) -> tuple[Node, Rect]:
        node = _find(root, name)
        return node, place(node, screen)

    def _guis(self, root: Node) -> Node:
        found = root.get("guiTypes")
        return found if isinstance(found, Node) else root

    def slot_centre(self, index: int, geometry: Geometry) -> tuple[int, int]:
        """Screen centre of research slot ``index`` (0-based) with the panel open and
        its slot list unscrolled. LayoutRefused if the slot is below the visible list."""
        width, height = geometry.gui_size
        screen = Rect(0, 0, width, height)
        panel, panel_rect = self._top(self._guis(self._panel), "countrytechnologyview", screen)
        area = _find(panel, "research_slots")
        area_rect = place(area, panel_rect)
        window = _find(area, "slots_window")
        window_rect = place(window, area_rect)
        grid = _find(window, "research_slots_grid")
        grid_rect = place(grid, window_rect)
        slot = grid.get("slotsize")
        if not isinstance(slot, Node):
            raise LayoutRefused("research_slots_grid has no slotsize")
        sw, sh = _amount(slot.get("width"), 0), _amount(slot.get("height"), 0)
        if not sw or not sh:
            raise LayoutRefused("research_slots_grid has an unreadable slotsize")
        if str(grid.get("format", "UPPER_LEFT")).strip('"').upper() != "UPPER_LEFT":
            raise LayoutRefused("research_slots_grid is not an UPPER_LEFT grid")
        columns = max(1, int(grid_rect.width // sw))
        col, row = index % columns, index // columns
        gx, gy = grid_rect.x + sw * (col + 0.5), grid_rect.y + sh * (row + 0.5)
        bottom = window_rect.y + window_rect.height if window_rect.height > 0 else screen.height
        if gy + sh / 2 > min(bottom, screen.height):
            raise LayoutRefused(f"research slot {index} is below the visible slot list at this size")
        return geometry.to_screen(gx, gy)

    def tab_centre(self, folder: str, geometry: Geometry) -> tuple[int, int]:
        """Screen centre of the tech tree's folder tab for ``folder`` (``infantry_folder``)."""
        width, height = geometry.gui_size
        screen = Rect(0, 0, width, height)
        tree, tree_rect = self._top(self._guis(self._tree), "countrytechtreeview", screen)
        strip = _find(tree, "folder_tabs")
        strip_rect = place(strip, tree_rect)
        button = _find(strip, f"{folder}_tab")
        sprite = button.get("quadTextureSprite") or button.get("spriteType")
        natural = self._sprite_size(sprite) if isinstance(sprite, str) else (0.0, 0.0)
        rect = place(button, strip_rect, natural)
        return geometry.to_screen(*rect.centre)


def dds_size(path: Path) -> tuple[int, int]:
    """``(width, height)`` from a DDS header (the texture sizes the .gfx files leave out)."""
    with open(path, "rb") as handle:
        head = handle.read(20)
    if len(head) < 20 or head[:4] != b"DDS ":
        raise LayoutRefused(f"{path} is not a DDS texture")
    height, width = struct.unpack("<II", head[12:20])
    return width, height
