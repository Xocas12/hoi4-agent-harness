"""Unit condition: what the save holds per division, supply, equipment fill and encirclement.

The save and the grid map are synthetic (tests/battle_helpers.py). Province 6 touches 2, 5, 7 and 10;
SWE's war with GER is in its diplomacy block, so GER-held provinces are the enemy's.
"""

from __future__ import annotations

import pytest

from battle_helpers import division, land_combat, save_text, side
from hoi4_harness import condition as C
from hoi4_harness import world as W
from orders_helpers import load_layer, write_grid_game


@pytest.fixture
def grid(tmp_path):
    return load_layer(write_grid_game(tmp_path / "game"))


def swe_units() -> str:
    return (
        division(1, 6, 1, org=50.0, strength=200.0, supply=100.0)
        # cut off for 8 days: 2 of 108 supply, short of 250 items, hurt, half adapted to the climate
        + division(2, 6, 1, org=10.0, strength=60.0, supply=2.0, oos=8, held=750, missing=250,
                   str_damage=3.5, org_damage=6.0, acclimatised=500, dig=2.0, dig_cap=9.0)
        + division(3, 1, 1, org=60.0, strength=210.0, supply=108.0)
        + division(4, 9, 2, org=12.0, strength=100.0, supply=90.0)         # the only Cavalry
        + division(5, 10, 1, org=55.0, strength=205.0, supply=5.0, oos=2))


def build(tmp_path, grid, combat="", controllers=None):
    path = tmp_path / "autosave.hoi4"
    path.write_text(save_text(swe_units(), division(10, 7, 3), combat,
                              controllers=controllers or {2: "GER", 5: "GER"}), encoding="utf-8")
    world = W.read_world(path, "SWE", layer=grid)
    return world, W.picture(world, "SWE", {}, grid)


def by_id(pic):
    return {c.id: c for c in pic.conditions}


# --- the fields -------------------------------------------------------------------------------------------


def test_the_division_fields_are_read_from_the_save(tmp_path, grid):
    world, _ = build(tmp_path, grid)
    d = next(x for x in world.countries["SWE"].divisions if x.id == "2")
    assert d.key == "2:51" and d.strength == 60.0 and d.max_supply == 108.0
    assert d.out_of_supply_days == 8 and d.experience == 500.0 and d.dig_in_cap == 9.0
    assert (d.equipment_held, d.equipment_missing) == (750, 250)
    assert (d.str_damage, d.org_damage) == (3.5, 6.0) and d.acclimatisation == pytest.approx(0.5)
    assert d.last_combat == "1941.8.1.12"
    # the key only exists while the division is out of supply; no requests means nothing lacking
    fine = next(x for x in world.countries["SWE"].divisions if x.id == "1")
    assert fine.out_of_supply_days is None and fine.equipment_missing == 0
    assert fine.acclimatisation is None


def test_shares_are_against_the_best_copy_of_the_template_and_supply_against_its_ceiling(tmp_path, grid):
    _, pic = build(tmp_path, grid)
    c = by_id(pic)
    assert c["2"].org_share == pytest.approx(10 / 60) and c["2"].strength_share == pytest.approx(60 / 210)
    assert c["2"].supply_share == pytest.approx(2 / 108)
    assert c["1"].org_share == pytest.approx(50 / 60) and c["3"].org_share == pytest.approx(1.0)
    # one Cavalry copy: no second to be a maximum, so no share (the brief uses the absolute number)
    assert c["4"].org_share is None and c["4"].strength_share is None


def test_equipment_fill_is_held_over_held_plus_still_requested(tmp_path, grid):
    _, pic = build(tmp_path, grid)
    assert by_id(pic)["2"].equipment_fill == pytest.approx(0.75)
    assert by_id(pic)["1"].equipment_fill == pytest.approx(1.0)


# --- cut off ----------------------------------------------------------------------------------------------


def test_a_division_out_of_supply_in_a_small_pocket_is_encircled(tmp_path, grid):
    _, pic = build(tmp_path, grid)
    c = by_id(pic)
    # 6 touches 2 and 5 (GER-held now) and 7 (GER's); 11 is GER's too: the pocket is 6, 10 and 9
    assert c["2"].encircled is True and c["2"].pocket == 3
    assert c["5"].encircled is True
    # in the same pocket but not out of supply: not encircled, the flag needs both
    assert c["1"].encircled is False and c["4"].encircled is False


def test_a_division_whose_every_land_neighbour_is_enemy_held_is_the_pocket_of_one(tmp_path, grid):
    _, pic = build(tmp_path, grid, controllers={2: "GER", 5: "GER", 10: "GER"})
    assert by_id(pic)["2"].encircled is True and by_id(pic)["2"].pocket == 1


def test_a_pocket_that_reaches_home_is_not_encircled(tmp_path, grid, monkeypatch):
    monkeypatch.setattr(C, "POCKET", 2)      # the map is 12 provinces: stand in for a big one
    _, pic = build(tmp_path, grid)
    assert by_id(pic)["2"].encircled is False and by_id(pic)["2"].pocket is None


def test_a_pocket_with_no_enemy_on_its_edge_is_not_encircled(tmp_path, grid):
    size, touches = C.pocket_of(6, grid, lambda p: "SWE", set())
    assert touches is False and size is not None


# --- what is wrong with it ---------------------------------------------------------------------------------


def test_problems_are_named_and_worst_first(tmp_path, grid):
    _, pic = build(tmp_path, grid)
    c = by_id(pic)
    assert c["2"].problems == ["encircled", "out of supply 8d", "low organisation", "low strength"]
    assert c["5"].problems == ["encircled", "out of supply"]          # 2 days is not yet "6+"
    assert c["4"].problems == ["low organisation"]                    # 12 < 18, no share to use
    assert c["1"].problems == [] and c["3"].problems == []
    assert [x.id for x in pic.conditions][:3] == ["2", "5", "4"]


def test_the_brief_line_counts_the_problems_by_place(tmp_path, grid):
    combat = land_combat(100, 6, 30, "plains",
                         side([(10, 51)], [(10, 51)], tag="GER"),
                         side([(1, 51), (2, 51)], [(1, 51)], reserves=[(2, 51)], tag="SWE"))
    _, pic = build(tmp_path, grid, combat)
    lines = C.brief_lines(pic.conditions, lambda s: f"state {s}")
    assert lines == ["  Army condition: 1 out of supply 6+ days in state 11, 2 encircled in state 11, "
                     "2 under 30% org, 1 under 40% strength; 2 in battle (inspect_world view=divisions)"]
    assert {c.id: c.in_battle for c in pic.conditions if c.in_battle} == {"1": "line", "2": "reserve"}
    text = "\n".join(W.brief_lines(pic, layer=grid))
    assert "Army condition: 1 out of supply 6+ days" in text


def test_a_healthy_army_says_so(tmp_path, grid):
    path = tmp_path / "ok.hoi4"
    path.write_text(save_text(division(1, 6, 1) + division(2, 6, 1), division(10, 7, 3)),
                    encoding="utf-8")
    pic = W.picture(W.read_world(path, "SWE", layer=grid), "SWE", {}, grid)
    assert C.brief_lines(pic.conditions, str)[0].startswith(
        "  Army condition: no division out of supply, cut off or broken")
    assert C.brief_lines([], str) == []


# --- the tool ----------------------------------------------------------------------------


def test_inspect_world_divisions_lists_every_field_worst_first(tmp_path, grid):
    _, pic = build(tmp_path, grid)
    data = W.query(pic, "divisions", None, layer=grid)
    assert data["count"] == 5 and data["divisions"][0]["id"] == "2"
    worst = data["divisions"][0]
    assert worst["org"] == 10.0 and worst["org_share_est"] == pytest.approx(0.17, abs=0.01)
    assert worst["supply"] == "2%" and worst["out_of_supply_days"] == 8
    assert worst["equipment_fill"] == "75%" and worst["acclimatised"] == "50%"
    assert worst["dug_in"] == 2.0 and worst["dig_in_cap"] == 9.0 and worst["experience_points"] == 500
    assert worst["recent_damage"] == {"str": 3.5, "org": 6.0}
    assert worst["encircled"] is True and worst["pocket_provinces"] == 3 and worst["in_battle"] is False
    assert worst["problems"][0] == "encircled" and "attrition" in data["note"]
    assert W.query(pic, "divisions", None, layer=grid, limit=2)["shown"] == 2
