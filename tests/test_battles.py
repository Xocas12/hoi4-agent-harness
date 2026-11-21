"""Battles: the save's ``combat`` block read the way the attack bubbles show it.

The save is synthetic (tests/battle_helpers.py) in the layout the real 1941 save has; the 4x3 grid map
puts SWE on provinces 1, 2, 5, 6, 9, 10 (and 11, which it took) and GER on 3, 4, 7, 8, 12.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from battle_helpers import (
    division,
    land_combat,
    naval_combat,
    naval_member,
    ref,
    save_text,
    side,
)
from hoi4_harness import battles as B
from hoi4_harness import world as W
from orders_helpers import load_layer, write_grid_game


@pytest.fixture
def grid(tmp_path):
    return load_layer(write_grid_game(tmp_path / "game"))


def combat_block() -> str:
    # A: GER attacks SWE at province 6. GER front is div 10 (org 40, losing 2.0 an hour), div 11 is
    # in reserve; SWE's 1 and 2 hold (org 50 + 20, losing 1.0 an hour).
    a = land_combat(
        100, 6, 30, "plains",
        side([(10, 51), (11, 4713)], [(10, 51)], reserves=[(11, 4713)], tag="GER", tactic=1,
             taken_str=20.0, taken_org=30.0, rate=2.0),
        side([(1, 51), (2, 51)], [(1, 51), (2, 51)], tag="SWE", tactic=2, taken_str=5.0,
             taken_org=8.0, rate=1.0, air=12, flanked=True))
    # B: GER against France at province 12, which SWE sees (it holds 11); neither side is SWE's.
    b = land_combat(
        101, 12, 10, "hills",
        side([(12, 51), (99, 51)], [], tag="GER", tactic=1, rate=1.0, taken_org=3.0),
        side([(77, 51)], [], tag="FRA", tactic=2, rate=3.0, taken_org=9.0))
    # C: deep in Germany, at province 4: not seen.
    c = land_combat(102, 4, 10, "plains",
                    side([(12, 51)], [], tag="GER"), side([(78, 51)], [], tag="FRA"))
    # D: a sea fight at province 2, next to SWE's 6; one SWE destroyer and two convoys against subs.
    d = naval_combat(
        103, 2, 40,
        naval_member("GER", "submarine", "U-1") + naval_member("GER", "submarine", "U-2"),
        naval_member("SWE", "destroyer", "HMS Psilander") + naval_member("SWE", convoy=True)
        + naval_member("SWE", convoy=True),
        att_dealt=5.0, def_dealt=0.0, att_compare=2.0, def_compare=0.5)
    return a + b + c + d


def make_save(tmp_path, combat=None, **kw) -> Path:
    swe = (division(1, 6, 1, org=50.0, strength=200.0) + division(2, 6, 1, org=20.0, strength=100.0)
           + division(3, 1, 1, org=60.0, strength=210.0) + division(11, 1, 1, org=55.0))
    ger = (division(10, 7, 3, org=40.0, strength=190.0, supply=20) + division(11, 7, 3, type_=4713,
                                                                             org=60.0, strength=200.0)
           + division(12, 4, 3, org=30.0))
    path = tmp_path / "save games" / "autosave.hoi4"
    path.parent.mkdir(exist_ok=True)
    path.write_text(save_text(swe, ger, combat_block() if combat is None else combat,
                              controllers={11: "SWE"}, **kw), encoding="utf-8")
    return path


def picture(tmp_path, grid, combat=None):
    world = W.read_world(make_save(tmp_path, combat), "SWE", layer=grid)
    return world, W.picture(world, "SWE", {}, grid)


# --- reading the block ------------------------------------------------------------------------------


def test_the_combat_block_is_read_with_both_sides_the_terrain_and_the_hours(tmp_path, grid):
    world, _ = picture(tmp_path, grid)
    assert [(r.kind, r.ident, r.province, r.hours, r.terrain) for r in world.combat] == [
        ("land", "100", 6, 30, "plains"), ("land", "101", 12, 10, "hills"),
        ("land", "102", 4, 10, "plains"), ("naval", "103", 2, 40, "water_shallow_sea")]
    a = world.combat[0]
    assert a.attacker.tags == ["GER"] and a.defender.tags == ["SWE"]
    assert a.attacker.keys == ["10:51", "11:4713"] and a.attacker.reserves == ["11:4713"]
    assert (a.attacker.taken_str, a.attacker.taken_org) == (20.0, 30.0)
    assert a.attacker.rate_org == pytest.approx(2.0) and a.defender.planes == 12
    assert a.defender.flanked is True and a.attacker.flanked is False
    assert world.enemies == {"GER"}


def test_no_combat_block_is_no_battles(tmp_path, grid):
    world, pic = picture(tmp_path, grid, combat="")
    assert world.combat == [] and pic.battles == []
    assert B.brief_lines(pic.battles, str) == []


def test_the_tactic_numbers_follow_the_games_own_file():
    assert B.tactic_name(12) == "shock" and B.tactic_name(20) == "ambush"
    assert B.tactic_name(1) == "basic attack" and B.tactic_name("x") is None
    game = Path(os.environ.get("HOI4_GAME_DIR")
                or "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV")
    path = game / "common" / "combat_tactics.txt"
    if not path.exists():
        pytest.skip("HOI4 is not installed here")
    import re

    names = re.findall(r"^(tactic_[a-z_0-9]+) = \{", path.read_text(encoding="utf-8-sig"), re.M)
    assert len(names) == len(B.TACTICS)
    assert names[11] == "tactic_shock" and names[19] == "tactic_ambush"
    assert names[0] == "tactic_basic_attack" and names[1] == "tactic_basic_defend"


# --- what the observer sees --------------------------------------------------------------------------


def test_own_battles_come_first_and_the_far_one_is_not_seen(tmp_path, grid):
    _, pic = picture(tmp_path, grid)
    assert [(b.ident, b.own_side) for b in pic.battles] == [
        ("100", "defender"), ("103", "defender"), ("101", None)]     # 102 (province 4) is hidden


def test_a_division_is_named_by_id_and_type(tmp_path, grid):
    """SWE's division 11 (type 51) and GER's division 11 (type 4713) share an id."""
    _, pic = picture(tmp_path, grid)
    a = pic.battles[0]
    rows = {u["id"] + ":" + u["country"]: u for u in a.attacker.units}
    assert set(rows) == {"10:GER", "11:GER"} and rows["11:GER"]["role"] == "reserve"
    assert a.defender.countries == ["SWE"] and {u["country"] for u in a.defender.units} == {"SWE"}


def test_own_divisions_show_their_numbers_and_the_enemys_only_its_bars(tmp_path, grid):
    _, pic = picture(tmp_path, grid)
    a = pic.battles[0]
    mine = next(u for u in a.defender.units if u["id"] == "1")
    assert mine["org"] == 50.0 and mine["strength"] == 200.0
    assert mine["org_pct"] == pytest.approx(0.83, abs=0.01)
    enemy = next(u for u in a.attacker.units if u["id"] == "10")
    # a share of the best GER copy of its template (div 11, org 60): 40 / 60
    assert enemy["org_pct"] == pytest.approx(0.67, abs=0.01)
    assert set(enemy) == {"id", "country", "template", "org_pct", "strength_pct", "role"}


def test_the_damage_each_side_dealt_is_what_the_other_took(tmp_path, grid):
    _, pic = picture(tmp_path, grid)
    a = pic.battles[0]
    assert a.attacker.taken == (20.0, 30.0) and a.defender.dealt == (20.0, 30.0)
    assert a.attacker.dealt == (5.0, 8.0) and a.defender.taken == (5.0, 8.0)
    assert (a.attacker.tactic, a.defender.tactic) == ("basic attack", "basic defend")
    assert a.hours == 30 and a.terrain == "plains" and a.state == 11


def test_who_is_winning_is_hours_to_break_and_the_formula_is_the_documented_one(tmp_path, grid):
    _, pic = picture(tmp_path, grid)
    a = pic.battles[0]
    # attacker line: div 10, org 40, losing 2.0 an hour -> 20 h; defender: 50 + 20 = 70 over 1.0 -> 70 h
    assert a.attacker.hours_to_break == pytest.approx(20.0)
    assert a.defender.hours_to_break == pytest.approx(70.0)
    assert a.share == pytest.approx(20 / 90)
    assert a.verdict == "you are clearly winning" and "hours to break" in a.basis


def test_a_battle_the_country_has_no_part_in_shows_counts_and_the_bar_only(tmp_path, grid):
    _, pic = picture(tmp_path, grid)
    b = pic.battles[2]
    assert (b.attacker.countries, b.defender.countries) == (["GER"], ["FRA"])
    assert (b.attacker.divisions, b.defender.divisions) == (2, 1)
    assert b.attacker.units == [] and b.defender.units == [] and b.attacker.tactic is None
    assert b.attacker.templates == {}                    # not even which templates
    # no organisation is shown, so the bar is the loss rates: 3.0 / (1.0 + 3.0)
    assert b.share == pytest.approx(0.75) and b.verdict == "attacker clearly ahead"
    row = B.battle_row(b, str)
    assert set(row["attacker"]) == {"countries", "divisions"} and row["yours"] is False


def test_a_naval_fight_reads_ships_convoys_and_the_damage_dealt(tmp_path, grid):
    _, pic = picture(tmp_path, grid)
    d = pic.battles[1]
    assert d.kind == "naval" and d.province == 2 and d.hours == 40
    assert d.attacker.ships == {"submarine": 2} and d.attacker.countries == ["GER"]
    assert d.defender.ships == {"destroyer": 1} and d.defender.convoys == 2
    assert d.share == pytest.approx(1.0) and d.basis == "damage dealt"
    assert d.verdict == "you are clearly losing"


def test_when_nothing_is_dealt_a_naval_fight_falls_back_to_the_forces_compared():
    raw_a, raw_d = B.RawSide(forces_compare=2.0), B.RawSide(forces_compare=0.5)
    share, basis = B.estimate(B.Side([]), B.Side([]), (raw_a, raw_d))
    assert share == pytest.approx(0.8) and basis == "forces compared"
    assert B.estimate(B.Side([]), B.Side([]), (B.RawSide(), B.RawSide())) == (None, "")


def test_the_damage_race_is_the_last_resort():
    att, dfn = B.Side([], taken=(0.0, 10.0)), B.Side([], taken=(0.0, 30.0))
    share, basis = B.estimate(att, dfn)
    assert share == pytest.approx(0.75) and basis == "damage taken so far"
    assert B.share_words(None) == "no reading"
    assert B.share_words(0.5, "attacker") == "even"
    assert B.share_words(0.6, "attacker") == "you are ahead"
    assert B.share_words(0.6, "defender") == "you are behind"


# --- in the brief and the tool ---------------------------------------------------------------------------


def test_the_brief_has_a_battles_part_with_own_first(tmp_path, grid):
    _, pic = picture(tmp_path, grid)
    text = "\n".join(W.brief_lines(pic, layer=grid))
    assert "Battles (2 yours, 1 seen; the bar is an estimate" in text
    assert ("* state 11 [plains]: GER attack 2 div (2 Infanterie) vs SWE 2 div (2 Infantry), 30h; "
            "damage dealt 5 vs 20; tactics basic attack/basic defend; you are clearly winning "
            "(attacker 22%)") in text
    assert "naval, GER attack 2 submarine vs SWE 1 destroyer, 2 convoys, 40h" in text
    # the third-party battle is a dash, with no templates and no tactics
    line = next(x for x in text.splitlines() if "[hills]" in x)
    assert line.strip().startswith("- ") and "Infanterie" not in line and "tactics" not in line


def test_the_brief_keeps_to_five_battles(tmp_path, grid):
    many = "".join(land_combat(200 + i, 6, 5, "plains", side([(10, 51)], [(10, 51)], tag="GER"),
                               side([(1, 51)], [(1, 51)], tag="SWE")) for i in range(8))
    _, pic = picture(tmp_path, grid, combat=many)
    lines = B.brief_lines(pic.battles, str)
    assert len(lines) == 1 + 5 + 1 and "(+3 more: inspect_world view=battles)" in lines[-1]


def test_inspect_world_battles_view(tmp_path, grid):
    _, pic = picture(tmp_path, grid)
    data = W.query(pic, "battles", None, layer=grid)
    assert data["count"] == 3 and data["yours"] == 2
    first = data["battles"][0]
    assert first["yours"] == "defender" and first["duration_days"] == 1.2
    assert first["who_is_winning"]["estimate"] is True
    assert first["attacker"]["tactic"] == "basic attack"
    assert first["attacker"]["damage_dealt"] == {"str": 5.0, "org": 8.0}
    assert first["defender"]["air_support_planes"] == 12 and first["defender"]["flanked_the_enemy"]
    assert "estimate" in data["note"]
    assert W.query(pic, "battles", None, layer=grid, limit=1)["shown"] == 1
    assert "battles" in W.query(pic, "nonsense", None)["error"]


def test_enemy_divisions_in_contact_show_bars_only_when_in_a_battle_with_yours(tmp_path, grid):
    _, pic = picture(tmp_path, grid)
    seen = W.query(pic, "state", "12", layer=grid)["enemy_seen"]    # GER 10 and 11 stand in province 7
    by_id = {d["id"]: d for d in seen}
    assert by_id["10"]["in_battle_with_yours"] is True
    for row in by_id.values():
        assert not {"org", "supply", "dug_in", "manpower", "experience"} & set(row)
    assert "org_pct" in by_id["10"]
    quiet = picture(tmp_path, grid, combat="")[1]
    rows = W.query(quiet, "state", "12", layer=grid)["enemy_seen"]
    assert rows and all("org_pct" not in r and "in_battle_with_yours" not in r for r in rows)


def test_a_malformed_combat_block_costs_no_battles_not_the_army(tmp_path, grid):
    path = make_save(tmp_path, combat="\tland_combat={\n\t\tlocation=abc\n\t}\n"
                                      "\tland_combat={\n\t\tid={ id=1 type=62 }\n\t}\n")
    world = W.read_world(path, "SWE", layer=grid)
    assert world.combat == [] and len(world.countries["SWE"].divisions) == 4
    assert B.read_combat("no combat here") == []
    assert ref(1) == "{ id=1 type=51 }"
