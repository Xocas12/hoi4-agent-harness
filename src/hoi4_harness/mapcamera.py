"""Map pixel <-> screen point, as an explicit model that a live check settles.

The harness cannot see HOI4's camera: there is no file for it and the game was
not running when this was written. So the transform is a small, honest model
with every free number named, and a way to fit them from two measurements:

    screen = origin + scale * (map_pixel - anchor)

``scale`` is screen pixels per map pixel at the current zoom, the same on both
axes (the map is drawn flat; HOI4 tilts it a little at some zoom levels, which
this ignores -- see docs/adapters.md). Both axes grow right and DOWN, matching
:mod:`mapdata`, whose y is not the BMP's bottom-up file row.

A camera is built one of two ways:

* :meth:`Camera.from_references` -- two points whose map pixel and screen
  position are both known (hover the mouse on a landmark, read the province
  under it), solving scale and offset. The two must be far apart, and the x and
  y scales they imply must agree, or it refuses: that is the model being wrong,
  not the operator.
* :meth:`Camera.centred_on` -- "the map search centres the camera on a state":
  the target's map pixel sits at the centre of the map viewport, at a scale
  measured once.

``screen_point`` refuses a target outside the usable viewport rather than
returning a coordinate that would click a UI panel or nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .mapdata import MapError, MapLayer


class OffScreen(MapError):
    """The target is not inside the viewport at this camera."""


class CameraModelError(MapError):
    """The calibration measurements contradict the camera model."""


@dataclass(frozen=True)
class Viewport:
    """The screen rectangle the map is drawn in and safe to click: the whole
    screen minus the top bar, side panels and bottom bar. Margins in screen px."""

    width: int
    height: int
    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    def contains(self, x: float, y: float) -> bool:
        return (self.left <= x < self.width - self.right
                and self.top <= y < self.height - self.bottom)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.left + self.width - self.right) / 2,
                (self.top + self.height - self.bottom) / 2)


@dataclass(frozen=True)
class Camera:
    scale: float                      # screen px per map px
    anchor: tuple[float, float]       # a map pixel ...
    origin: tuple[float, float]       # ... and where it is on screen
    viewport: Viewport

    def to_screen(self, mx: float, my: float) -> tuple[float, float]:
        return (self.origin[0] + (mx - self.anchor[0]) * self.scale,
                self.origin[1] + (my - self.anchor[1]) * self.scale)

    def to_map(self, sx: float, sy: float) -> tuple[float, float]:
        return (self.anchor[0] + (sx - self.origin[0]) / self.scale,
                self.anchor[1] + (sy - self.origin[1]) / self.scale)

    @classmethod
    def centred_on(cls, map_point: tuple[float, float], scale: float,
                   viewport: Viewport) -> Camera:
        """The camera after the map search jumped to ``map_point``: that pixel
        is at the viewport's centre."""
        if scale <= 0:
            raise CameraModelError("scale must be positive")
        return cls(scale=scale, anchor=map_point, origin=viewport.centre, viewport=viewport)

    @classmethod
    def from_references(cls, a: tuple[tuple[float, float], tuple[float, float]],
                        b: tuple[tuple[float, float], tuple[float, float]],
                        viewport: Viewport, tolerance: float = 0.03) -> Camera:
        """Fit from two ``(map_pixel, screen_point)`` pairs.

        Refuses when the pairs are too close on either axis to give a scale,
        when the map and screen vectors run in opposite directions (an
        orientation mix-up), or when the x and y scales differ by more than
        ``tolerance`` (a relative fraction).
        """
        (m1, s1), (m2, s2) = a, b
        dmx, dmy = m2[0] - m1[0], m2[1] - m1[1]
        dsx, dsy = s2[0] - s1[0], s2[1] - s1[1]
        if abs(dmx) < 50 or abs(dmy) < 50:
            raise CameraModelError(
                "the reference points are less than 50 map pixels apart on an axis; "
                "pick two far apart, not on the same row or column")
        if dmx * dsx <= 0 or dmy * dsy <= 0:
            raise CameraModelError(
                "a map axis runs against its screen axis between the references; "
                "both grow right and down, so an axis was swapped or flipped")
        sx, sy = dsx / dmx, dsy / dmy
        if abs(sx - sy) > tolerance * max(sx, sy):
            raise CameraModelError(
                f"x scale {sx:.4f} and y scale {sy:.4f} disagree by more than "
                f"{tolerance:.0%}: the map is not drawn flat at this zoom, or a reference is off")
        return cls(scale=(sx + sy) / 2, anchor=m1, origin=s1, viewport=viewport)


def map_point(layer: MapLayer, target: str, ident: int) -> tuple[float, float]:
    """The pixel to click for a state or province: a point certainly inside it."""
    if target not in ("state", "province"):
        raise ValueError(f"target must be 'state' or 'province', not {target!r}")
    x, y = layer.interior_point(target, ident)
    return float(x), float(y)


def screen_point(layer: MapLayer, target: str, ident: int, camera: Camera,
                 margin: int = 4) -> tuple[int, int]:
    """Where to click to hit state/province ``ident`` at ``camera``.

    Raises :class:`OffScreen` when that point is outside the viewport (or within
    ``margin`` px of its edge, where a click can land on the frame).
    """
    mx, my = map_point(layer, target, ident)
    sx, sy = camera.to_screen(mx, my)
    vp = camera.viewport
    inner = Viewport(vp.width, vp.height, vp.left + margin, vp.top + margin,
                     vp.right + margin, vp.bottom + margin)
    if not inner.contains(sx, sy):
        raise OffScreen(
            f"{target} {ident} is at screen ({sx:.0f}, {sy:.0f}), outside the "
            f"{vp.width}x{vp.height} viewport; centre the camera on it first")
    return round(sx), round(sy)
