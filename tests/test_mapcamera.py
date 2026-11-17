"""The map -> screen model (#85): calibratable, y-down, and refuses what is off screen."""

import pytest

from hoi4_harness.identifiers import Playset
from hoi4_harness.mapcamera import (
    Camera,
    CameraModelError,
    OffScreen,
    Viewport,
    map_point,
    screen_point,
)
from hoi4_harness.mapdata import MapLayer
from map_helpers import bmp_bytes, write_game

VIEW = Viewport(1920, 1080, top=40, bottom=120)


@pytest.fixture
def layer(tmp_path):
    return MapLayer.load(Playset.from_paths(write_game(tmp_path / "g")), cache_dir=False)


def test_both_axes_grow_right_and_down():
    cam = Camera(scale=2.0, anchor=(100, 100), origin=(500, 400), viewport=VIEW)
    assert cam.to_screen(100, 100) == (500, 400)
    assert cam.to_screen(110, 130) == (520, 460)  # more x: right; more y: lower
    assert cam.to_map(520, 460) == (110, 130)


def test_centring_on_a_point_puts_it_at_the_viewport_centre():
    cam = Camera.centred_on((3068, 324), 1.5, VIEW)
    assert VIEW.centre == (960, 500)
    assert cam.to_screen(3068, 324) == (960, 500)
    assert cam.to_screen(3068 + 100, 324) == (960 + 150, 500)


def test_two_references_fix_scale_and_offset():
    # a landmark at map (1000, 500) is under the mouse at (300, 200); another at (1400, 800) at (900, 650)
    cam = Camera.from_references(((1000, 500), (300, 200)), ((1400, 800), (900, 650)), VIEW)
    assert cam.scale == pytest.approx(1.5)
    assert cam.to_screen(1000, 500) == pytest.approx((300, 200))
    assert cam.to_screen(1400, 800) == pytest.approx((900, 650))
    assert cam.to_screen(1200, 650) == pytest.approx((600, 425))


def test_references_that_flip_an_axis_are_refused():
    # the same pair with screen y read bottom-up, as a BMP stores it
    with pytest.raises(CameraModelError, match="against its screen axis"):
        Camera.from_references(((1000, 500), (300, 650)), ((1400, 800), (900, 200)), VIEW)


def test_references_with_different_x_and_y_scales_are_refused():
    with pytest.raises(CameraModelError, match="disagree"):
        Camera.from_references(((1000, 500), (300, 200)), ((1400, 800), (900, 900)), VIEW)


def test_references_too_close_to_give_a_scale_are_refused():
    with pytest.raises(CameraModelError, match="far apart"):
        Camera.from_references(((1000, 500), (300, 200)), ((1010, 800), (315, 650)), VIEW)


def test_the_province_higher_in_the_picture_is_higher_on_screen(layer):
    # province 1's click point is in the left bar (x 1..2); province 3's is at the right edge.
    # Put the camera so the whole 11x8 picture is visible and compare rows within one state.
    cam = Camera(scale=40.0, anchor=(0, 0), origin=(200, 100), viewport=VIEW)
    top = layer.province_at(0, 0)
    assert top == 1
    sx, sy = cam.to_screen(0, 0)
    bx, by = cam.to_screen(0, 7)
    assert by > sy  # row 7 is lower on screen than row 0
    # the interior point of state 1 sits on the picture's left bar, left of the hole
    px, py = screen_point(layer, "state", 1, cam, margin=0)
    mx, my = map_point(layer, "state", 1)
    assert (px, py) == (round(200 + mx * 40), round(100 + my * 40))
    assert layer.province_at(*[round(v) for v in cam.to_map(px, py)]) == 1


def test_screen_point_clicks_the_province_it_was_asked_for(layer):
    cam = Camera.centred_on(map_point(layer, "state", 2), 30.0, VIEW)
    sx, sy = screen_point(layer, "state", 2, cam)
    assert (sx, sy) == (round(VIEW.centre[0]), round(VIEW.centre[1]))
    mx, my = cam.to_map(sx, sy)
    assert layer.state_of(layer.province_at(round(mx), round(my))) == 2


def test_off_screen_targets_are_refused_not_clamped(layer):
    cam = Camera.centred_on((5, 4), 40.0, VIEW)
    screen_point(layer, "province", 1, cam)  # near the centre: fine
    far = Camera(scale=40.0, anchor=(0, 0), origin=(5000, 5000), viewport=VIEW)
    with pytest.raises(OffScreen, match="outside"):
        screen_point(layer, "province", 1, far)


def test_a_point_under_the_ui_frame_is_off_screen(layer):
    mx, my = map_point(layer, "province", 4)  # (6, 3)
    # land it 10 px above the bottom bar's edge, then 10 px into it
    inside = Camera(scale=1.0, anchor=(mx, my), origin=(500, 1080 - 120 - 10), viewport=VIEW)
    assert screen_point(layer, "province", 4, inside) == (500, 950)
    under = Camera(scale=1.0, anchor=(mx, my), origin=(500, 1080 - 120 + 10), viewport=VIEW)
    with pytest.raises(OffScreen):
        screen_point(layer, "province", 4, under)
    near_edge = Camera(scale=1.0, anchor=(mx, my), origin=(500, 1080 - 120 - 2), viewport=VIEW)
    with pytest.raises(OffScreen):  # inside the 4 px safety margin
        screen_point(layer, "province", 4, near_edge)


def test_the_target_must_be_a_state_or_a_province(layer):
    with pytest.raises(ValueError):
        map_point(layer, "country", 1)


def test_orientation_follows_the_picture_not_the_file(tmp_path):
    # a province drawn at the top of the picture ends up above one drawn below it
    game = write_game(tmp_path / "g")
    (game / "map" / "provinces.bmp").write_bytes(bmp_bytes(["AAAA", "AAAA", "BBBB", "BBBB"]))
    layer = MapLayer.load(Playset.from_paths(game), cache_dir=False)
    cam = Camera(scale=10.0, anchor=(0, 0), origin=(100, 100), viewport=VIEW)
    _, ya = cam.to_screen(*map_point(layer, "province", 1))
    _, yb = cam.to_screen(*map_point(layer, "province", 3))
    assert ya < yb
