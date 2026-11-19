"""Map pixel <-> screen point, as an explicit model that a live check settles.

The harness cannot see HOI4's camera: there is no file for it and the game was
not running when this was written. So the transform is a small, honest model
with every free number named, and a way to fit them from two measurements:

    screen = origin + scale * (map_pixel - anchor)

``scale`` is screen pixels per map pixel at the current zoom, the same on both
axes (the map is drawn flat; HOI4 tilts it a little at some zoom levels, which
this ignores; the tilted case is :class:`PerspectiveCamera` below). Both axes grow right and DOWN, matching
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

import math
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


# --- the tilted camera: a planar homography -----------------------------------
#
# Zoomed in, HOI4 tilts the camera, so the flat model above fails: measured live
# (1.19.5, 1920x1080, after a map search) the y scale held near 16 px per map px
# while the x scale grew from about 20 at the top of the screen to about 28 lower
# down. The map is a plane, so a pinhole camera maps it to the screen by a 3x3
# homography H: (sx, sy) = (h1 x + h2 y + h3, h4 x + h5 y + h6) / (h7 x + h8 y + h9).
# H has 8 degrees of freedom, so four (map, screen) pairs fix it exactly and more
# pairs are fitted by least squares, which is the only way to learn whether the
# model is right: with four points the residual is zero by construction.


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting; CameraModelError if singular."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[piv][c]) < 1e-12:
            raise CameraModelError("the reference points do not determine a camera "
                                   "(collinear, repeated or otherwise degenerate)")
        m[c], m[piv] = m[piv], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _inv3(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-18:
        raise CameraModelError("the camera matrix is singular")
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def _normaliser(pts):
    """Similarity moving the centroid to the origin and the mean distance to sqrt 2."""
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    d = sum(math.hypot(p[0] - cx, p[1] - cy) for p in pts) / len(pts)
    if d < 1e-9:
        raise CameraModelError("all the reference points coincide")
    k = math.sqrt(2) / d
    return [[k, 0.0, -k * cx], [0.0, k, -k * cy], [0.0, 0.0, 1.0]]


def _apply(h, x, y):
    w = h[2][0] * x + h[2][1] * y + h[2][2]
    if abs(w) < 1e-12:
        raise OffScreen("the point is on the camera's horizon")
    return ((h[0][0] * x + h[0][1] * y + h[0][2]) / w,
            (h[1][0] * x + h[1][1] * y + h[1][2]) / w)


def _condition(h) -> float:
    """sigma_max / sigma_min of a 3x3, from the eigenvalues of H^T H (Jacobi)."""
    a = [[sum(h[k][i] * h[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    for _ in range(60):
        off = max((abs(a[i][j]), i, j) for i in range(3) for j in range(i + 1, 3))
        if off[0] < 1e-15 * max(1.0, a[0][0] + a[1][1] + a[2][2]):
            break
        _, p, q = off
        theta = 0.5 * math.atan2(2 * a[p][q], a[q][q] - a[p][p])
        c, s_ = math.cos(theta), math.sin(theta)
        for k in range(3):
            a[k][p], a[k][q] = c * a[k][p] - s_ * a[k][q], s_ * a[k][p] + c * a[k][q]
        for k in range(3):
            a[p][k], a[q][k] = c * a[p][k] - s_ * a[q][k], s_ * a[p][k] + c * a[q][k]
    ev = sorted(max(a[i][i], 0.0) for i in range(3))
    return math.inf if ev[0] <= 0 else math.sqrt(ev[2] / ev[0])


Pair = tuple[tuple[float, float], tuple[float, float]]


@dataclass(frozen=True)
class PerspectiveCamera:
    """A planar homography map pixel -> screen px. Same interface as Camera, so
    :func:`screen_point` takes either."""

    matrix: tuple[tuple[float, float, float], ...]   # H, h33 = 1 where possible
    viewport: Viewport
    # sigma_max / sigma_min of the matrix in the fit's normalised coordinates (the
    # raw pixel matrix always looks ill-conditioned: its units differ by 1000x).
    # 0 when the camera was built from a bare matrix. A healthy fit is well under 1e3.
    conditioning: float = 0.0

    def to_screen(self, mx: float, my: float) -> tuple[float, float]:
        return _apply(self.matrix, mx, my)

    def to_map(self, sx: float, sy: float) -> tuple[float, float]:
        return _apply(_inv3(self.matrix), sx, sy)


@dataclass(frozen=True)
class CameraFit:
    camera: object                    # Camera (flat) or PerspectiveCamera
    model: str                        # "flat" or "perspective"
    residuals: tuple[float, ...]      # screen px, one per input pair, in order
    flat_residuals: tuple[float, ...]
    points: int
    note: str = ""

    @property
    def max_residual(self) -> float:
        return max(self.residuals)

    @property
    def rms_residual(self) -> float:
        return math.sqrt(sum(r * r for r in self.residuals) / len(self.residuals))

    @property
    def exactly_determined(self) -> bool:
        """True when a homography was fitted to four points: its zero residual
        proves nothing; hold cities out and predict them."""
        return self.model == "perspective" and self.points <= 4


def _check_pairs(pairs: list[Pair], need: int) -> None:
    if len(pairs) < need:
        raise CameraModelError(f"need at least {need} (map, screen) pairs, got {len(pairs)}")
    for i, (m1, _) in enumerate(pairs):
        for m2, _ in pairs[i + 1:]:
            if math.hypot(m1[0] - m2[0], m1[1] - m2[1]) < 1e-6:
                raise CameraModelError("two reference points share a map position")


def _collinear(pts, rel: float = 0.02) -> bool:
    """True when the points' minor spread is under ``rel`` of the major one."""
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    sxx = sum((p[0] - cx) ** 2 for p in pts)
    syy = sum((p[1] - cy) ** 2 for p in pts)
    sxy = sum((p[0] - cx) * (p[1] - cy) for p in pts)
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    disc = math.sqrt(max(tr * tr / 4 - det, 0.0))
    big, small = tr / 2 + disc, tr / 2 - disc
    return big <= 0 or math.sqrt(max(small, 0.0) / big) < rel


def fit_homography(pairs: list[Pair], viewport: Viewport,
                   tolerance: float = 8.0) -> tuple[PerspectiveCamera, tuple[float, ...]]:
    """Least-squares homography from >= 4 ``(map_pixel, screen_point)`` pairs.

    Coordinates are normalised (centroid to origin, mean radius sqrt 2) before the
    8x8 normal equations are solved, then undone. Refuses collinear map or screen
    points, fits whose worst residual exceeds ``tolerance`` screen px, and fits
    that put a reference point behind the camera. Returns the camera and the
    per-pair residuals (screen px).
    """
    _check_pairs(pairs, 4)
    maps = [p[0] for p in pairs]
    screens = [p[1] for p in pairs]
    if _collinear(maps) or _collinear(screens):
        raise CameraModelError("the reference points are (nearly) collinear; spread them "
                               "over the screen in two dimensions")
    tm, ts = _normaliser(maps), _normaliser(screens)
    rows: list[list[float]] = []
    rhs: list[float] = []
    for (mx, my), (sx, sy) in pairs:
        x, y = tm[0][0] * mx + tm[0][2], tm[1][1] * my + tm[1][2]
        u, v = ts[0][0] * sx + ts[0][2], ts[1][1] * sy + ts[1][2]
        rows.append([x, y, 1, 0, 0, 0, -x * u, -y * u])
        rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -x * v, -y * v])
        rhs.append(v)
    ata = [[sum(r[i] * r[j] for r in rows) for j in range(8)] for i in range(8)]
    atb = [sum(r[i] * b for r, b in zip(rows, rhs, strict=True)) for i in range(8)]
    h = _solve(ata, atb)
    hn = [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]]
    full = _mul(_inv3(ts), _mul(hn, tm))
    if abs(full[2][2]) > 1e-12:
        full = [[v / full[2][2] for v in row] for row in full]
    cam = PerspectiveCamera(tuple(tuple(r) for r in full), viewport, _condition(hn))
    res = []
    for m, s in pairs:
        try:
            px, py = cam.to_screen(*m)
        except OffScreen:
            raise CameraModelError("a reference point is on the camera horizon") from None
        res.append(math.hypot(px - s[0], py - s[1]))
    if max(res) > tolerance:
        raise CameraModelError(
            f"the worst residual is {max(res):.1f} px (tolerance {tolerance:g}): a reference "
            "is misread or the map is not a plane under this camera")
    sign = 1 if full[2][2] >= 0 else -1
    for m, _ in pairs:
        if sign * (full[2][0] * m[0] + full[2][1] * m[1] + full[2][2]) <= 0:
            raise CameraModelError("a reference point falls behind the camera: the fit is nonsense")
    return cam, tuple(res)


def fit_flat(pairs: list[Pair], viewport: Viewport) -> tuple[Camera, tuple[float, ...]]:
    """Least-squares flat camera (one scale, one offset) from >= 2 pairs."""
    _check_pairs(pairs, 2)
    n = len(pairs)
    mx0 = sum(p[0][0] for p in pairs) / n
    my0 = sum(p[0][1] for p in pairs) / n
    sx0 = sum(p[1][0] for p in pairs) / n
    sy0 = sum(p[1][1] for p in pairs) / n
    den = sum((m[0] - mx0) ** 2 + (m[1] - my0) ** 2 for m, _ in pairs)
    if den < 1e-9:
        raise CameraModelError("all the reference points coincide")
    num = sum((m[0] - mx0) * (s[0] - sx0) + (m[1] - my0) * (s[1] - sy0) for m, s in pairs)
    if num <= 0:
        raise CameraModelError("the screen runs against the map between the references")
    cam = Camera(scale=num / den, anchor=(mx0, my0), origin=(sx0, sy0), viewport=viewport)
    res = tuple(math.hypot(cam.to_screen(*m)[0] - s[0], cam.to_screen(*m)[1] - s[1])
                for m, s in pairs)
    return cam, res


def fit_camera(pairs: list[Pair], viewport: Viewport, tolerance: float = 8.0,
               flat_tolerance: float = 4.0) -> CameraFit:
    """Choose per fit. A flat camera that explains three or more pairs within
    ``flat_tolerance`` px wins (fewer parameters, so it extrapolates better);
    otherwise a homography is fitted and must itself stay within ``tolerance``.
    Raises CameraModelError when neither does."""
    pairs = [(tuple(m), tuple(s)) for m, s in pairs]
    flat_res: tuple[float, ...] = ()
    try:
        flat, flat_res = fit_flat(pairs, viewport)
        if len(pairs) >= 3 and max(flat_res) <= flat_tolerance:
            return CameraFit(flat, "flat", flat_res, flat_res, len(pairs),
                             "flat camera: the screen is not tilted at this zoom")
    except CameraModelError:
        pass
    cam, res = fit_homography(pairs, viewport, tolerance)
    note = ("homography through exactly four points: the zero residual proves nothing, "
            "check it on held-out cities" if len(pairs) == 4
            else "homography fitted by least squares; hold cities out to validate")
    return CameraFit(cam, "perspective", res, flat_res, len(pairs), note)


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
