"""The tilted-camera model: a homography fitted from (map, screen) pairs."""

import math
import os
from pathlib import Path

import pytest

from hoi4_harness.identifiers import Playset
from hoi4_harness.mapcamera import (
    Camera,
    CameraModelError,
    OffScreen,
    PerspectiveCamera,
    Viewport,
    fit_camera,
    fit_homography,
    map_point,
    screen_point,
)
from hoi4_harness.mapdata import STACK_TYPES, VP_MARKER, MapError, MapLayer, read_unitstacks
from map_helpers import write_game

VIEW = Viewport(1920, 1080, top=40, bottom=120)
# a known tilt: x stretches with y, as when the camera looks down at an angle
H_TRUE = ((6.6, -3.1, -19000.0), (0.18, 3.4, -1520.0), (8.3e-5, -0.0031, 1.0))
GRID = [(3040 + 12 * i, 296 + 7 * j) for i in range(4) for j in range(4)]
FOUR = [(3040, 296), (3100, 300), (3050, 330), (3095, 335)]


def project(h, x, y):
    w = h[2][0] * x + h[2][1] * y + h[2][2]
    return ((h[0][0] * x + h[0][1] * y + h[0][2]) / w, (h[1][0] * x + h[1][1] * y + h[1][2]) / w)


def pairs_of(points, h=H_TRUE, jitter=None):
    out = []
    for i, (x, y) in enumerate(points):
        sx, sy = project(h, x, y)
        if jitter:
            dx, dy = jitter[i % len(jitter)]
            sx, sy = sx + dx, sy + dy
        out.append(((x, y), (sx, sy)))
    return out


def test_a_known_homography_is_recovered_from_four_points():
    cam, res = fit_homography(pairs_of(FOUR), VIEW)
    assert max(res) < 1e-6
    for x, y in GRID:
        assert cam.to_screen(x, y) == pytest.approx(project(H_TRUE, x, y), abs=1e-4)
    assert cam.matrix[2][2] == pytest.approx(1.0)
    assert cam.matrix[0][0] == pytest.approx(H_TRUE[0][0], rel=1e-6)


def test_more_points_with_noise_average_it_out():
    jitter = [(1.2, -0.8), (-1.0, 1.1), (0.7, 0.9), (-1.3, -0.6), (0.4, -1.0)]
    cam, res = fit_homography(pairs_of(GRID, jitter=jitter), VIEW)
    assert 0.1 < max(res) < 3.0          # honest, non-zero residuals
    for x, y in [(3060, 310), (3080, 320)]:
        assert cam.to_screen(x, y) == pytest.approx(project(H_TRUE, x, y), abs=4.0)


def test_round_trip_map_screen_map():
    cam, _ = fit_homography(pairs_of(GRID), VIEW)
    for x, y in GRID:
        sx, sy = cam.to_screen(x, y)
        assert cam.to_map(sx, sy) == pytest.approx((x, y), abs=1e-6)


def test_four_points_are_flagged_as_proving_nothing():
    fit = fit_camera(pairs_of(FOUR), VIEW)
    assert fit.model == "perspective" and fit.exactly_determined
    assert "proves nothing" in fit.note
    assert not fit_camera(pairs_of(GRID), VIEW).exactly_determined


def test_conditioning_of_a_good_fit_is_modest():
    cam, _ = fit_homography(pairs_of(GRID), VIEW)
    assert 1.0 <= cam.conditioning < 1e3


@pytest.mark.parametrize("points", [
    [(3040, 300), (3060, 300), (3080, 300), (3100, 300)],                    # one row
    [(3040, 296), (3050, 306), (3060, 316), (3070, 326), (3080, 336)],       # one diagonal
    [(3040, 296), (3040, 296), (3100, 330), (3050, 335)],                    # repeated
])
def test_degenerate_sets_are_refused(points):
    with pytest.raises(CameraModelError):
        fit_homography(pairs_of(points), VIEW)


def test_collinear_screen_points_are_refused():
    pairs = [((3040, 296), (100, 100)), ((3100, 300), (200, 200)),
             ((3050, 330), (300, 300)), ((3095, 335), (400, 400))]
    with pytest.raises(CameraModelError, match="collinear"):
        fit_homography(pairs, VIEW)


def test_fewer_than_four_pairs_are_refused():
    with pytest.raises(CameraModelError, match="at least 4"):
        fit_homography(pairs_of(GRID[:3]), VIEW)


def test_a_fit_that_misses_by_more_than_the_tolerance_is_refused():
    pairs = pairs_of(GRID)
    pairs[5] = (pairs[5][0], (pairs[5][1][0] + 40, pairs[5][1][1] - 30))   # one misread marker
    with pytest.raises(CameraModelError, match="residual"):
        fit_homography(pairs, VIEW)
    fit_homography(pairs, VIEW, tolerance=60)     # the tolerance can be widened on purpose


def test_a_tilted_camera_is_not_taken_for_a_flat_one():
    fit = fit_camera(pairs_of(GRID), VIEW)
    assert fit.model == "perspective"
    assert max(fit.flat_residuals) > 20           # the flat model would be wrong by that much


def test_a_flat_view_picks_the_flat_camera():
    flat = Camera(scale=15.0, anchor=(3050, 300), origin=(600, 300), viewport=VIEW)
    pairs = [((x, y), flat.to_screen(x, y)) for x, y in GRID]
    fit = fit_camera(pairs, VIEW)
    assert fit.model == "flat"
    assert fit.camera.scale == pytest.approx(15.0)
    assert fit.max_residual < 1e-6


def test_perspective_camera_works_with_screen_point(tmp_path):
    layer = MapLayer.load(Playset.from_paths(write_game(tmp_path / "g")), cache_dir=False)
    mx, my = map_point(layer, "province", 1)
    cam = PerspectiveCamera(((10.0, 0.0, 900 - 10 * mx), (0.0, 10.0, 500 - 10 * my), (0.0, 0.0, 1.0)), VIEW)
    assert screen_point(layer, "province", 1, cam) == (900, 500)
    far = PerspectiveCamera(((10.0, 0.0, 9000.0), (0.0, 10.0, 500 - 10 * my), (0.0, 0.0, 1.0)), VIEW)
    with pytest.raises(OffScreen):
        screen_point(layer, "province", 1, far)


# --- unitstacks.txt -----------------------------------------------------------

STACKS = """\
6050;0;3086.00;9.65;1723.00;0.00;0.24
6050;38;3085.48;9.90;1723.84;-1.57;0.24
9149;38;3085.52;9.70;1732.00;-1.57;0.24
# a comment
broken row
9149;x;1;2;3;4;5
156;38;3041.84;10.56;1752.00;1.57;0.24
"""


def test_unitstacks_are_read_per_province_and_type_with_y_down():
    stacks = read_unitstacks(STACKS, map_height=2048)
    assert set(stacks) == {6050, 9149, 156}
    assert set(stacks[6050]) == {0, 38}
    vp = stacks[6050][VP_MARKER]
    assert (vp.x, vp.y) == (3085.48, pytest.approx(2048 - 1723.84))
    assert vp.height == 9.9 and vp.rotation == -1.57 and vp.scale == 0.24
    assert stacks[9149][38].y == 316.0
    assert VP_MARKER in STACK_TYPES


def test_the_layer_reads_unitstacks_from_the_playset(tmp_path):
    root = write_game(tmp_path / "g")
    (root / "map" / "unitstacks.txt").write_text("1;38;5.50;9.0;6.00;0.00;0.2\n", encoding="utf-8")
    layer = MapLayer.load(Playset.from_paths(root), cache_dir=False)
    assert layer.anchor(1) == (5.5, layer.height - 6.0)
    with pytest.raises(MapError):
        layer.anchor(3)
    (root / "map" / "unitstacks.txt").unlink()
    assert MapLayer.load(Playset.from_paths(root), cache_dir=False).stacks(1) == {}


# --- the live measurement (1.19.5, 1920x1080, after a map search for Sodermanland) ---

INSTALL = Path(os.environ.get(
    "HOI4_GAME_DIR", "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"))
needs_install = pytest.mark.skipif(not (INSTALL / "map" / "unitstacks.txt").is_file(),
                                   reason="no HOI4 install with map files")
LIVE = {6050: (1488, 579), 9149: (1441.5, 418.5), 11091: (1047, 171), 156: (483, 108)}


@needs_install
def test_the_four_live_markers_fit_a_well_conditioned_homography():
    stacks = read_unitstacks((INSTALL / "map" / "unitstacks.txt").read_text(encoding="utf-8-sig"))
    pairs = [((stacks[p][VP_MARKER].x, stacks[p][VP_MARKER].y), s) for p, s in LIVE.items()]
    fit = fit_camera(pairs, VIEW)
    assert fit.model == "perspective" and fit.exactly_determined
    assert max(fit.flat_residuals) > 40            # the flat camera cannot explain these
    assert fit.camera.conditioning < 1e3
    assert all(math.isfinite(v) for row in fit.camera.matrix for v in row)
    for m, s in pairs:                             # exact only because 4 points fix 8 numbers
        assert fit.camera.to_screen(*m) == pytest.approx(s, abs=1e-6)
