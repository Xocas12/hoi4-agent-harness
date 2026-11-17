"""The map layer (#85): provinces.bmp, adjacency, states and interior points."""

import os
from pathlib import Path

import pytest

from hoi4_harness.cli import main
from hoi4_harness.identifiers import Mod, Playset
from hoi4_harness.mapdata import Bmp24, MapError, MapLayer, read_adjacencies
from map_helpers import ART, COLOURS, IDS, bmp_bytes, write_game

INSTALL = Path(os.environ.get(
    "HOI4_GAME_DIR", "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"))
needs_install = pytest.mark.skipif(not (INSTALL / "map" / "provinces.bmp").is_file(),
                                   reason="no HOI4 install with map files")


@pytest.fixture
def game(tmp_path):
    return write_game(tmp_path / "game")


@pytest.fixture
def layer(game):
    return MapLayer.load(Playset.from_paths(game), cache_dir=False)


def _packed(letter):
    r, g, b = COLOURS[letter]
    return (r << 16) | (g << 8) | b


# --- the picture ---------------------------------------------------------------


@pytest.mark.parametrize("bottom_up", [True, False])
def test_rows_come_back_top_first_whatever_the_storage_order(bottom_up):
    bmp = Bmp24(bmp_bytes(bottom_up=bottom_up))
    assert (bmp.width, bmp.height, bmp.bottom_up) == (11, 8, bottom_up)
    for y, line in enumerate(ART):
        assert bmp.row(y).tolist() == [_packed(c) for c in line]


def test_padding_bytes_are_not_read_as_pixels():
    bmp = Bmp24(bmp_bytes())
    assert bmp.stride == 36  # 33 bytes of pixels + 3 of padding
    assert len(bmp.row(0)) == 11


def test_a_bmp_that_is_not_24_bit_is_refused():
    data = bytearray(bmp_bytes())
    data[28] = 8  # bits per pixel
    with pytest.raises(MapError, match="24-bit"):
        Bmp24(bytes(data))
    with pytest.raises(MapError, match="not a BMP"):
        Bmp24(b"PNG" + bytes(100))


def test_a_truncated_bmp_is_refused():
    with pytest.raises(MapError, match="truncated"):
        Bmp24(bmp_bytes()[:-40])


# --- provinces -----------------------------------------------------------------


def test_province_stats_and_orientation(layer):
    a, b, d = layer.provinces[1], layer.provinces[3], layer.provinces[4]
    assert (layer.width, layer.height) == (11, 8)
    assert a.pixels == sum(line.count("A") for line in ART)
    # y is the picture's y, top row 0 -- not the file's bottom-up row.
    assert a.bbox == (0, 0, 9, 7)
    assert b.bbox == (9, 0, 10, 7)
    assert d.bbox == (6, 3, 6, 3) and d.centroid == (6.0, 3.0)
    assert layer.provinces[2].kind == "sea" and a.terrain == "plains" and a.coastal
    assert b.kind == "land" and not b.coastal


def test_the_top_row_has_the_smaller_y(tmp_path):
    art = ["AAAA", "SSSS", "SSSS"]  # A only on the top row
    game = write_game(tmp_path / "g")
    (game / "map" / "provinces.bmp").write_bytes(bmp_bytes(art))
    layer = MapLayer.load(Playset.from_paths(game), cache_dir=False)
    assert layer.provinces[1].bbox == (0, 0, 3, 0)
    assert layer.provinces[2].bbox == (0, 1, 3, 2)


def test_a_top_down_bmp_gives_the_same_layer(tmp_path):
    up = MapLayer.load(Playset.from_paths(write_game(tmp_path / "u")), cache_dir=False)
    down = MapLayer.load(Playset.from_paths(write_game(tmp_path / "d", bottom_up=False)),
                         cache_dir=False)
    assert {i: (p.pixels, p.centroid, p.bbox) for i, p in up.provinces.items()} == \
           {i: (p.pixels, p.centroid, p.bbox) for i, p in down.provinces.items()}


def test_province_at_reads_image_coordinates(layer):
    assert layer.province_at(0, 0) == IDS["A"]
    assert layer.province_at(10, 0) == IDS["B"]
    assert layer.province_at(6, 3) == IDS["D"]
    assert layer.province_at(11, 0) is None and layer.province_at(0, -1) is None


# --- centroid vs interior point ---------------------------------------------------


def test_the_centroid_of_a_c_shape_is_outside_it_and_the_interior_point_is_not(layer):
    cx, cy = layer.provinces[1].centroid
    assert layer.province_at(round(cx), round(cy)) == IDS["S"]  # in the hole
    ix, iy = layer.interior_point("province", 1)
    assert layer.province_at(ix, iy) == IDS["A"]
    assert 1 <= ix <= 2 and 1 <= iy <= 6  # inside the 4-wide bar, off its edge


def test_state_interior_point_maps_back_to_the_state(layer):
    for sid in (1, 2):
        ix, iy = layer.interior_point("state", sid)
        assert layer.state_of(layer.province_at(ix, iy)) == sid


def test_a_state_of_two_separate_pieces_gets_a_point_inside_one(layer):
    ix, iy = layer.interior_point("state", 2)
    assert layer.province_at(ix, iy) == IDS["B"]  # the larger, deeper piece


def test_a_one_pixel_province_is_its_own_interior_point(layer):
    assert layer.interior_point("province", 4) == (6, 3)


# --- adjacency -----------------------------------------------------------------


def test_borders_come_from_shared_pixel_edges(layer):
    assert layer.adjacent[1] == {2, 3}
    assert layer.adjacent[2] == {1, 3, 4}
    assert layer.adjacent[4] == {2}


def test_a_corner_touch_is_not_a_border(tmp_path):
    art = ["AS", "SB"]  # A and B meet only at a corner
    game = write_game(tmp_path / "g")
    (game / "map" / "provinces.bmp").write_bytes(bmp_bytes(art))
    layer = MapLayer.load(Playset.from_paths(game), cache_dir=False)
    assert layer.adjacent[1] == {2} and layer.adjacent[3] == {2}


def test_straits_come_from_adjacencies_csv_and_skip_real_borders(layer):
    assert layer.straits == {1: {4}, 4: {1}}  # 1;3 already touches
    assert layer.neighbours(1) == [2, 3, 4]
    assert layer.neighbours(1, straits=False) == [2, 3]


def test_adjacencies_reader_skips_comments_header_and_end_marker():
    text = "From;To;Type\n#a comment\n5;6;sea;7\n-1;-1;;-1\n"
    assert read_adjacencies(text) == [(5, 6, "sea")]


# --- states --------------------------------------------------------------------


def test_states_list_their_provinces(layer):
    assert layer.states[1].provinces == [1] and layer.states[1].owner == "SWE"
    assert layer.states[2].provinces == [3, 4]
    assert layer.state_of(4) == 2 and layer.state_of(2) is None
    assert layer.state_bbox(2) == (6, 0, 10, 7)
    with pytest.raises(MapError, match="no state 9"):
        layer.interior_point("state", 9)


# --- the playset layers the map like everything else ------------------------------


def test_a_mod_that_replaces_the_map_folder_wins(game, tmp_path):
    mod = tmp_path / "mod"
    (mod / "map").mkdir(parents=True)
    (mod / "map" / "provinces.bmp").write_bytes(bmp_bytes(["AB", "AB"]))
    (mod / "map" / "definition.csv").write_text(
        "1;200;30;40;land;false;plains;1\n3;30;200;60;land;false;hills;1\n", encoding="utf-8")
    (mod / "descriptor.mod").write_text('name = "Tiny"\nreplace_path = "map"\n', encoding="utf-8")
    layer = MapLayer.load(Playset.from_paths(game, [mod]), cache_dir=False)
    assert (layer.width, layer.height) == (2, 2)
    assert layer.straits == {}  # the game's adjacencies.csv went with the folder
    assert Mod.load(mod).replace_paths == ["map"]


def test_a_mod_file_without_replace_path_overrides_just_that_file(game, tmp_path):
    mod = tmp_path / "mod"
    (mod / "map").mkdir(parents=True)
    (mod / "map" / "adjacencies.csv").write_text("From;To;Type\n", encoding="utf-8")
    layer = MapLayer.load(Playset.from_paths(game, [mod]), cache_dir=False)
    assert layer.straits == {} and layer.width == 11


def test_default_map_can_rename_the_files(game):
    (game / "map" / "default.map").write_text(
        'definitions = "defs.csv"\nprovinces = "provinces.bmp"  # same\n', encoding="utf-8")
    (game / "map" / "definition.csv").rename(game / "map" / "defs.csv")
    layer = MapLayer.load(Playset.from_paths(game), cache_dir=False)
    assert len(layer.provinces) == 5


def test_missing_map_files_say_so(tmp_path):
    (tmp_path / "common").mkdir()
    with pytest.raises(MapError, match="provinces.bmp not found"):
        MapLayer.load(Playset.from_paths(tmp_path), cache_dir=False)


# --- cache ---------------------------------------------------------------------


def test_the_second_load_comes_from_the_cache_and_matches(game, tmp_path):
    cache = tmp_path / "cache"
    first = MapLayer.load(Playset.from_paths(game), cache)
    second = MapLayer.load(Playset.from_paths(game), cache)
    assert not first.from_cache and second.from_cache
    assert second.provinces[1].centroid == first.provinces[1].centroid
    assert second.adjacent == first.adjacent and second.straits == first.straits
    assert second.interior_point("province", 1) == first.interior_point("province", 1)
    assert len(list(cache.glob("map-*.json"))) == 1


def test_changing_the_picture_invalidates_the_cache(game, tmp_path):
    cache = tmp_path / "cache"
    MapLayer.load(Playset.from_paths(game), cache)
    (game / "map" / "provinces.bmp").write_bytes(bmp_bytes(["AAB", "AAB"]))
    layer = MapLayer.load(Playset.from_paths(game), cache)
    assert not layer.from_cache and layer.width == 3
    assert len(list(cache.glob("map-*.json"))) == 2


def test_a_corrupt_cache_is_rebuilt(game, tmp_path):
    cache = tmp_path / "cache"
    MapLayer.load(Playset.from_paths(game), cache)
    for f in cache.glob("map-*.json"):
        f.write_text("{not json", encoding="utf-8")
    assert not MapLayer.load(Playset.from_paths(game), cache).from_cache


# --- the command ---------------------------------------------------------------


def test_map_command_prints_a_state_and_a_province(game, tmp_path, capsys):
    base = ["map", "--game-dir", str(game), "--cache-dir", str(tmp_path / "c")]
    assert main(base + ["--state", "1"]) == 0
    out = capsys.readouterr().out
    assert "STATE_1" in out and "owner SWE" in out and "outside the state" in out
    assert main(base + ["--province", "1"]) == 0
    out = capsys.readouterr().out
    assert "plains" in out and "2 3 4~" in out
    assert main(base + ["--province", "99"]) == 1
    assert main(base) == 2


# --- the real install ----------------------------------------------------------


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    return MapLayer.load(Playset.from_paths(INSTALL), tmp_path_factory.mktemp("mapcache"))


@needs_install
def test_province_counts_match_definition_csv(real):
    lines = [ln for ln in (INSTALL / "map" / "definition.csv").read_text(
        encoding="utf-8-sig", errors="replace").splitlines() if ln.strip()]
    assert len(real.provinces) == len(lines)
    assert (real.width, real.height) == (5632, 2048)
    on_map = [p for p in real.provinces.values() if p.pixels]
    assert len(on_map) >= len(lines) - 1  # all but the unused black id 0
    assert sum(p.pixels for p in real.provinces.values()) <= real.width * real.height
    assert {p.kind for p in real.provinces.values()} == {"land", "sea", "lake"}


@needs_install
def test_stockholms_state_contains_its_interior_point(real):
    state = real.state_of(6050)  # the province of the Stockholm victory point
    assert state is not None
    x, y = real.interior_point("state", state)
    assert real.state_of(real.province_at(x, y)) == state
    x0, y0, x1, y1 = real.state_bbox(state)
    assert x0 <= x <= x1 and y0 <= y <= y1


@needs_install
def test_interior_points_hold_for_every_state_of_sweden(real):
    swedish = [sid for sid, st in real.states.items() if st.owner == "SWE"]
    assert swedish
    for sid in swedish:
        x, y = real.interior_point("state", sid)
        assert real.state_of(real.province_at(x, y)) == sid


@needs_install
def test_a_known_adjacency_and_a_known_strait(real):
    assert 6209 in real.neighbours(6050, straits=False)
    # adjacencies.csv's first row: Andaman, across water with no shared border
    assert 10321 in real.straits.get(12251, set())
    assert 10321 not in real.adjacent.get(12251, set())


@needs_install
def test_north_is_up_in_image_coordinates(real):
    # Stockholm (59 N) sits in the top quarter of the picture, not the bottom.
    assert real.provinces[6050].centroid[1] < real.height / 4
