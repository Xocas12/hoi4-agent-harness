"""The front report (#159): the line's movement across saves, the battles over a period, the force
picture in contact, and the computed reading of why a front is stuck.

The saves are the synthetic ones of tests/battle_helpers.py on the 4x3 grid map (SWE holds
provinces 1, 2, 5, 6, 9, 10; GER 3, 4, 7, 8, 11, 12; state 12 is German and holds provinces 3, 7
and 11). SWE is the observed country at war with GER.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from battle_helpers import division, land_combat, save_text, side
from hoi4_harness import frontreport as FR
from hoi4_harness import world as W
from hoi4_harness.types import GameState
from orders_helpers import load_layer, write_grid_game


@pytest.fixture
def grid(tmp_path):
    return load_layer(write_grid_game(tmp_path / "game"))


def attack(ident, province, att_units, def_units):
    """SWE attacks the German-held province with ``att_units`` against ``def_units`` divisions."""
    return land_combat(
        ident, province, 30, "plains",
        side([(900 + ident + i, 51) for i in range(att_units)], [], tag="SWE", rate=1.0),
        side([(800 + ident + i, 51) for i in range(def_units)], [], tag="GER", rate=1.0))


def write_save(tmp_path, name, date, controllers, combat="", ger_divisions=4, panzers=0, mtime=None) -> Path:
    swe = (division(1, 6, 1, org=50.0, strength=200.0) + division(2, 6, 1, org=50.0, strength=200.0)
           + division(3, 1, 1, org=60.0, strength=210.0))
    ger = "".join(division(10 + i, 7, 4 if i < panzers else 3)
                  for i in range(ger_divisions))
    path = tmp_path / "save games" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(save_text(swe, ger, combat, controllers=controllers, date=date), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def feed(history, path, grid):
    world = W.read_world(path, "SWE", layer=grid)
    assert history.record(world, "SWE", grid)
    return W.picture(world, "SWE", {}, grid)


def lines_for(history, pic, grid):
    return "\n".join(FR.brief_lines(FR.report(history, pic, grid)))


SEVEN_ATTACKS = attack(200, 7, 1, 3) + attack(201, 8, 1, 3) + attack(202, 12, 1, 3)


def test_two_saves_give_the_brief_a_line_with_movement_and_days_unchanged(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.31.12", {11: "SWE"}), grid)
    text = lines_for(history, pic, grid)
    assert ("Front vs GER: line since the save of 1941-06-30: +0/-0 provinces; "
            "62 days: +0/-0 provinces; unchanged for 62 days") in text


def test_a_province_taken_between_saves_shows_as_gained_and_resets_the_days(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.31.12", {11: "SWE", 7: "SWE"}), grid)
    text = lines_for(history, pic, grid)
    assert "+1/-0 provinces (1/0 states); 62 days: +1/-0 provinces (1/0 states)" in text
    assert "unchanged" not in text
    # and a third save with the same line: unchanged since the second
    pic = feed(history, write_save(tmp_path, "c.hoi4", "1941.9.30.12", {11: "SWE", 7: "SWE"}), grid)
    assert "unchanged for 30 days" in lines_for(history, pic, grid)


def test_a_province_the_enemy_retakes_shows_as_lost(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE", 7: "SWE"}), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.7.31.12", {11: "SWE"}), grid)
    assert "+0/-1 provinces (0/1 states)" in lines_for(history, pic, grid)


def test_an_annexed_province_is_not_a_loss(tmp_path, grid):
    # the province leaves the controllers table when its owner changes to the holder
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE", 7: "SWE"}), grid)
    world = W.read_world(write_save(tmp_path, "b.hoi4", "1941.7.31.12", {11: "SWE"}), "SWE", layer=grid)
    world.states[12].owner = "SWE"
    history.record(world, "SWE", grid)
    assert history.snapshots[-1]["fronts"]["GER"]["lost"] == 0


def test_the_first_save_has_nothing_to_compare_with(tmp_path, grid):
    history = FR.FrontHistory()
    pic = feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}), grid)
    assert "first look, no earlier save" in lines_for(history, pic, grid)
    assert FR.report(history, pic, grid)[0]["reading"] is None


def test_an_older_or_repeated_save_is_not_recorded(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.8.31.12", {11: "SWE"}), grid)
    world = W.read_world(write_save(tmp_path, "b.hoi4", "1941.6.30.12", {11: "SWE"}), "SWE", layer=grid)
    assert history.record(world, "SWE", grid) is False
    assert len(history.snapshots) == 1


def test_fronts_view_has_the_battles_over_the_period_and_the_force_picture(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}, combat=SEVEN_ATTACKS,
                             ger_divisions=4, panzers=1), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.31.12", {11: "SWE"},
                                   ger_divisions=4, panzers=1), grid)
    data = W.query(pic, "fronts", None, layer=grid, history=history)
    assert data["view"] == "fronts" and data["count"] == 1
    front = data["fronts"][0]
    assert front["enemy"] == "GER"
    mine = front["battles"]["your_attacks"]
    # the three attacks were on in the first save and gone in the second, GER still holds each province
    assert (mine["total"], mine["attacker_won"], mine["attacker_lost"], mine["piecemeal"]) == (3, 0, 3, 3)
    assert front["battles"]["their_attacks"]["total"] == 0
    assert front["battles"]["attacker_success"] == {"won": 0, "decided": 3, "share": 0.0}
    force = front["force"]
    assert (force["own_in_contact"], force["foe_in_contact"]) == (2, 4)
    assert force["ratio"] == "1 to 2.0" and force["armour_share"] == 0.0
    assert [b["outcome"] for b in front["recent_battles"]] == ["defender"] * 3
    assert W.query(pic, "fronts", None, layer=grid)["fronts"][0]["battles"]["started"] == 0


def test_an_attack_that_took_the_province_counts_as_won(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"},
                             combat=attack(200, 7, 3, 2)), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.7.31.12", {11: "SWE", 7: "SWE"}), grid)
    mine = FR.report(history, pic, grid)[0]["battles"]["your_attacks"]
    assert (mine["attacker_won"], mine["attacker_lost"], mine["piecemeal"]) == (1, 0, 0)


def test_the_stuck_reading_fires_and_names_its_numbers_and_the_tools(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}, combat=SEVEN_ATTACKS,
                             ger_divisions=4), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.31.12", {11: "SWE"}, ger_divisions=4),
               grid)
    stuck = FR.report(history, pic, grid)[0]["reading"]
    assert stuck["kind"] == "stuck"
    assert "line unchanged for 62 days" in stuck["numbers"]
    assert "outnumbered 1 to 2.0 in contact (2 against 4)" in stuck["numbers"]
    assert "3 of 3 attacks lost" in stuck["numbers"]
    assert "3 of 3 attacks went in at under 50% of the defender's divisions" in stuck["numbers"]
    joined = " ".join(stuck["tools"])
    assert "set_front_order order=push" in joined and "order=hold" in joined
    text = lines_for(history, pic, grid)
    assert "STUCK: line unchanged for 62 days; outnumbered 1 to 2.0 in contact (2 against 4)" in text
    assert "Try: set_front_order push one sector, hold elsewhere" in text
    # a thin-armour reading needs enough divisions in contact: two of them is not enough
    assert "armour" not in stuck["numbers"]


def test_the_stuck_reading_names_thin_armour_when_enough_divisions_are_in_contact(tmp_path, grid):
    history = FR.FrontHistory()
    path = tmp_path / "save games"
    path.mkdir()
    for name, date in (("a.hoi4", "1941.6.30.12"), ("b.hoi4", "1941.8.31.12")):
        swe = "".join(division(i, 6, 1) for i in range(1, 6))
        ger = division(10, 7, 3) + division(11, 7, 3)
        (path / name).write_text(save_text(swe, ger, "", controllers={11: "SWE"}, date=date),
                                 encoding="utf-8")
        pic = feed(history, path / name, grid)
    stuck = FR.report(history, pic, grid)[0]["reading"]
    assert stuck and "0% armour" in stuck["numbers"]
    assert any("steer_army" in t and "steer_production" in t for t in stuck["tools"])


def test_the_stuck_reading_does_not_fire_when_the_line_moved(tmp_path, grid):
    history = FR.FrontHistory()
    won = attack(200, 7, 3, 2) + attack(201, 3, 3, 2) + attack(202, 8, 3, 2)
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}, combat=won), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.31.12",
                                   {11: "SWE", 7: "SWE", 3: "SWE", 8: "SWE"}), grid)
    row = FR.report(history, pic, grid)[0]
    assert row["reading"] is None
    assert "STUCK" not in lines_for(history, pic, grid)


def test_a_short_pause_is_not_stuck(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.7.20.12", {11: "SWE"}), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.10.12", {11: "SWE"}), grid)
    row = FR.report(history, pic, grid)[0]
    assert row["movement"]["unchanged_days"] == 21 and row["reading"] is None


def test_the_brief_keeps_to_a_few_fronts():
    rows = [{"enemy": t, "movement": {"known": False},
             "battles": {"started": 0, "period_days": 90, "your_attacks": {"total": 0},
                         "their_attacks": {"total": 0}},
             "force": {"own_in_contact": 1, "foe_in_contact": 1, "ratio": "1 to 1.0",
                       "armour_share": None},
             "reading": None} for t in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    lines = FR.brief_lines(rows)
    assert sum(1 for x in lines if "Front vs" in x) == FR.MAX_FRONTS
    assert "+2 more fronts" in lines[-1]


def test_the_history_survives_a_restart_and_the_observer_brief_carries_the_report(tmp_path, grid):
    run = tmp_path / "run"
    saves = tmp_path / "save games"
    first = write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}, combat=SEVEN_ATTACKS, mtime=1_000_000)
    W.WorldObserver("SWE", saves, layer=grid, run_dir=run).brief_lines(
        GameState(country="SWE", date="1941-06-30"))
    assert (run / FR.HISTORY_FILE).is_file() and first.is_file()
    write_save(tmp_path, "b.hoi4", "1941.8.31.12", {11: "SWE"}, mtime=2_000_000)
    observer = W.WorldObserver("SWE", saves, layer=grid, run_dir=run)
    state = GameState(country="SWE", date="1941-08-31")
    text = "\n".join(observer.brief_lines(state))
    assert "Front vs GER: line since the save of 1941-06-30" in text and "unchanged for 62 days" in text
    assert "STUCK" in text
    data = observer.query(state, "fronts", None)
    assert data["saves_read"] == 2 and data["fronts"][0]["battles"]["your_attacks"]["attacker_lost"] == 3


def test_template_armour_share_comes_from_the_battalions_of_the_template():
    text = ('HOI4txt\ndivision_templates={\n'
            '\tdivision_template={\n\t\tid={ id=7 type=52 }\n\t\tname="Mixed"\n\t\tcountry="GER"\n'
            '\t\tregiments={\n\t\t\tmedium_armor={ x=0 y=0 }\n\t\t\tmedium_armor={ x=0 y=1 }\n'
            '\t\t\tmotorized={ x=1 y=0 }\n\t\t\tmotorized={ x=1 y=1 }\n\t\t}\n'
            '\t\tsupport={\n\t\t\tanti_tank={ x=0 y=0 }\n\t\t}\n\t}\n'
            '\tdivision_template={\n\t\tid={ id=8 type=52 }\n\t\tname="Foot"\n\t\tcountry="GER"\n'
            '\t\tregiments={\n\t\t\tinfantry={ x=0 y=0 }\n\t\t}\n\t}\n}\n')
    assert W.read_template_armour(text) == {"GER": {"7": 0.5, "8": 0.0}}


def test_a_front_where_most_attacks_fail_is_stuck_even_when_a_few_provinces_changed_hands(tmp_path, grid):
    history = FR.FrontHistory()
    far = attack(200, 12, 1, 3) + attack(201, 4, 1, 3) + attack(202, 12, 1, 4)
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}, combat=far), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.31.12",
                                   {11: "SWE", 7: "SWE", 3: "SWE", 8: "SWE"}), grid)
    reading = FR.report(history, pic, grid)[0]["reading"]
    assert reading["kind"] == "stuck"
    assert "line barely moved (3 taken in 62 days)" in reading["numbers"]
    assert "3 of 3 attacks lost" in reading["numbers"]


def test_a_front_that_gives_ground_gets_a_losing_reading_with_defensive_tools(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.31.12", {
        11: "SWE", 1: "GER", 2: "GER", 5: "GER", 6: "GER", 9: "GER", 10: "GER"}), grid)
    reading = FR.report(history, pic, grid)[0]["reading"]
    assert reading["kind"] == "losing"
    assert "lost 6 provinces and took 0 in 62 days" in reading["numbers"]
    joined = " ".join(reading["tools"])
    assert "set_ai_posture defensive theater=GER" in joined and "order=hold" in joined
    text = lines_for(history, pic, grid)
    assert "LOSING: lost 6 provinces and took 0 in 62 days" in text
    assert "set_ai_posture defensive GER" in text


def test_a_front_that_is_winning_ground_has_no_reading(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE", 1: "GER"}), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.31.12", {
        11: "SWE", 7: "SWE", 3: "SWE", 8: "SWE", 12: "SWE", 4: "SWE", 1: "GER"}), grid)
    assert FR.report(history, pic, grid)[0]["reading"] is None


def test_the_brief_reading_is_short_and_names_two_tools_at_most(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}, combat=SEVEN_ATTACKS), grid)
    pic = feed(history, write_save(tmp_path, "b.hoi4", "1941.8.31.12", {
        11: "SWE", 1: "GER", 2: "GER", 5: "GER", 6: "GER", 9: "GER", 10: "GER"}), grid)
    lines = FR.brief_lines(FR.report(history, pic, grid))
    reading = next(x for x in lines if "LOSING" in x)
    assert len(reading) < 300
    assert reading.split("Try: ")[1].count("; ") <= 1


def test_a_capitulated_enemy_is_not_a_front(tmp_path, grid):
    history = FR.FrontHistory()
    feed(history, write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"}), grid)
    world = W.read_world(write_save(tmp_path, "b.hoi4", "1941.8.31.12", {11: "SWE"}), "SWE", layer=grid)
    history.record(world, "SWE", grid)
    assert [r["enemy"] for r in FR.report(history, W.picture(world, "SWE", {}, grid), grid)] == ["GER"]
    world.countries["GER"].capitulated = True
    assert FR.report(history, W.picture(world, "SWE", {}, grid), grid) == []


def test_the_capitulated_flag_is_read_from_the_countrys_diplomacy_block(tmp_path, grid):
    path = write_save(tmp_path, "a.hoi4", "1941.6.30.12", {11: "SWE"})
    block = "\n\tGER={\n\t\tdiplomacy={\n\t\t\tcapitulated=yes\n\t\t}\n"
    text = path.read_text(encoding="utf-8").replace("\n\tGER={\n", block, 1)
    path.write_text(text, encoding="utf-8")
    assert W.read_world(path, "SWE", layer=grid).countries["GER"].capitulated is True


def test_fronts_rank_by_enemy_divisions_in_contact():
    rows = [{"enemy": t, "date": None, "movement": {}, "battles": {"started": 0},
             "force": {"foe_in_contact": n}} for t, n in (("HOL", 2), ("SOV", 30), ("NOR", 1))]
    rows.sort(key=lambda r: (-r["force"]["foe_in_contact"], -r["battles"]["started"], r["enemy"]))
    assert [r["enemy"] for r in rows] == ["SOV", "HOL", "NOR"]


REAL_TEMPLATE = (
    "HOI4txt\ndivision_templates={\n\tdivision_template={\n\t\tid={ id=1730 type=52 }\n"
    "\t\tname=\"Medium Tank 1941 A\"\n\t\tobsolete=yes\n\t\tobsolete_change_date=\"1942.6.3.9\"\n"
    "\t\tdivision_names_group=\"SOV_MEC_01\"\n\t\tcountry=\"SOV\"\n\t\toriginal_tag=\"SOV\"\n"
    "\t\tforeign_template_tag=\"---\"\n\t\tpriority=1\n\t\tallow_new_equipment=yes\n"
    "\t\tallow_foreign_equipment=yes\n\t\tregimental_support={\n\t\t\tanti_air_battery={ x=1 y=0 }\n"
    "\t\t\tanti_air_battery={ x=2 y=0 }\n\t\t}\n\t\tsupport={\n\t\t\tengineer={ x=0 y=0 }\n"
    "\t\t\tartillery={ x=0 y=1 }\n\t\t\tmot_recon={ x=0 y=2 }\n\t\t}\n\t\tregiments={\n"
    "\t\t\tmedium_armor={ x=0 y=0 }\n\t\t\tmedium_armor={ x=0 y=1 }\n\t\t\tmedium_armor={ x=0 y=2 }\n"
    "\t\t\tmotorized={ x=1 y=0 }\n\t\t\tmotorized={ x=1 y=1 }\n\t\t\tmotorized={ x=1 y=2 }\n"
    "\t\t\tmotorized={ x=2 y=0 }\n\t\t\tmotorized={ x=2 y=1 }\n\t\t\tmotorized={ x=2 y=2 }\n"
    "\t\t\tmotorized={ x=2 y=3 }\n\t\t}\n\t\ttemplate_counter=-1\n"
    "\t\tingame_set_template_counter=no\n\t\trole=\"armor\"\n\t\torigin_type=master_host\n\t}\n}\n")


def test_a_medium_tank_template_from_a_real_save_counts_as_armoured():
    # the real save's Medium Tank 1941 A: 3 tank battalions of 10, so 30% of its battalions
    share = W.read_template_armour(REAL_TEMPLATE)["SOV"]["1730"]
    assert share == pytest.approx(0.3)

    class D:
        armour = share
        template = "Medium Tank 1941 A"

    assert FR._is_armoured(D())

    class Foot:
        armour = 0.0
        template = "Infantry 1941"

    assert not FR._is_armoured(Foot())
