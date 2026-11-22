"""Observation (#139): the map, every division, and other countries through intel, from a text save.

The map is the 4x3 grid of the unit-order tests (orders_helpers): provinces 1..12 row by row,
states are the columns (10, 11 Swedish; 12, 13 German). The save is written here, in the layout a
1.20 text save has (the same keys and nesting the real autosave was checked against).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from hoi4_harness import world as W
from hoi4_harness.actions.catalog import QUERY_ACTIONS
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.config import HarnessConfig
from hoi4_harness.control import ALWAYS
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall, GameState
from orders_helpers import load_layer, write_grid_game

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"


def _division(ident, province, template, org=50.0, have=9000, need=10000, dig=4.0):
    return (f"\t\t\tdivision={{\n\t\t\t\tid={{ id={ident} type=51 }}\n\t\t\t\tlocation={province}\n"
            f"\t\t\t\tid={{ id={ident} type=51 }}\n"
            f"\t\t\t\tdivision_template_id={{ id={template} type=52 }}\n"
            f"\t\t\t\torganisation={org}\n\t\t\t\tstrength=200\n"
            f"\t\t\t\tarmy_manpower={{\n\t\t\t\t\tarmy_manpower_value={{\n"
            f"\t\t\t\t\t\tvalue={{ tag=\"X\" value={have} }}\n\t\t\t\t\t}}\n"
            f"\t\t\t\t\tarmy_manpower_need={{\n\t\t\t\t\t\tvalue={{ tag=\"X\" value={need} }}\n"
            f"\t\t\t\t\t}}\n\t\t\t\t}}\n\t\t\t\tdig_in={dig}\n\t\t\t\tarmy_current_supply_ratio=100\n"
            f"\t\t\t}}\n")


def _line(equipment, factories):
    return (f"\t\t\tmilitary_lines={{\n\t\t\t\tid={{ id={equipment} type=56 }}\n"
            f"\t\t\t\tequipment_variant_index={{ id={equipment} type=70 }}\n"
            f"\t\t\t\trequested_factories={factories}\n\t\t\t\tactive_factories={factories}\n"
            f"\t\t\t}}\n")


def save_text(date="1936.3.1.12", player="GER") -> str:
    states = "".join(
        f"\t{sid}={{\n\t\tbuildings={{\n\t\t\tindustrial_complex={{\n\t\t\t\tlevel={civ}\n\t\t\t}}\n"
        f"\t\t\tarms_factory={{\n\t\t\t\tlevel={mil}\n\t\t\t}}\n\t\t}}\n\t\towner=\"{owner}\"\n\t}}\n"
        for sid, owner, civ, mil in ((10, "SWE", 4, 2), (11, "SWE", 3, 1), (12, "GER", 12, 9),
                                     (13, "GER", 11, 8)))
    swe_units = _division(1, 6, 1, org=60, dig=8) + _division(2, 6, 1) + _division(3, 1, 2)
    ger_units = (_division(10, 7, 3, org=40, have=5000) + _division(11, 7, 3)
                 + _division(12, 4, 4) + _division(13, 8, 4) + _division(14, 12, 3))
    return (
        f'HOI4txt\ndate="{date}"\nplayer="{player}"\n'
        "equipments={\n\tinfantry_equipment_1={\n\t\tid={ id=5 type=70 }\n\t}\n"
        "\tartillery_equipment_1={\n\t\tid={ id=6 type=70 }\n\t}\n}\n"
        f"states={{\n{states}}}\n"
        'provinces={\n\t11={\n\t\tcontroller="SWE"\n\t}\n}\n'
        "division_templates={\n"
        + "".join(f'\tdivision_template={{\n\t\tid={{ id={i} type=52 }}\n\t\tname="{n}"\n'
                  f'\t\tcountry="{c}"\n\t}}\n'
                  for i, n, c in ((1, "Infantry", "SWE"), (2, "Cavalry", "SWE"),
                                  (3, "Infanterie", "GER"), (4, "Panzer", "GER")))
        + "}\n"
        "countries={\n"
        f"\tSWE={{\n\t\tunits={{\n{swe_units}\t\t}}\n\t\tproduction={{\n{_line(5, 3)}\t\t}}\n\t}}\n"
        f"\tGER={{\n\t\tunits={{\n{ger_units}\t\t}}\n\t\tproduction={{\n{_line(5, 10)}{_line(6, 4)}"
        "\t\t}\n\t}\n}\n")


@pytest.fixture
def grid(tmp_path):
    root = write_grid_game(tmp_path / "game")
    return load_layer(root)


@pytest.fixture
def save(tmp_path):
    folder = tmp_path / "save games"
    folder.mkdir()
    path = folder / "autosave.hoi4"
    path.write_text(save_text(), encoding="utf-8")
    return path


# --- the save ----------------------------------------------------------------------------------


def test_the_save_is_read_into_divisions_states_controllers_and_production(save, grid):
    world = W.read_world(save, "SWE", layer=grid)
    assert world.date == "1936-03-01" and world.player == "GER"
    assert set(world.countries) == {"SWE", "GER"}
    swe = world.countries["SWE"].divisions
    first = next(d for d in swe if d.id == "1")
    assert (first.template, first.province, first.state, first.terrain) == ("Infantry", 6, 11, "plains")
    assert first.organisation == 60 and first.dig_in == 8 and first.manpower == pytest.approx(0.9)
    assert world.states[12].owner == "GER" and world.states[12].civ == 12
    assert world.controllers == {11: "SWE"}
    assert world.controller_of(11, grid) == "SWE" and world.owner_of(11, grid) == "GER"
    assert [(p.equipment, p.factories) for p in world.countries["GER"].production] == [
        ("infantry_equipment_1", 10), ("artillery_equipment_1", 4)]
    assert world.factories("GER") == {"civ": 23, "mil": 17, "dock": 0}


# --- what the country can see -------------------------------------------------------------------


def test_foreign_divisions_show_where_they_touch_the_country_and_nowhere_else(save, grid):
    pic = W.picture(W.read_world(save, "SWE", layer=grid), "SWE", {}, grid)
    assert len(pic.own) == 3
    # 7 borders SWE's 6; 12 borders 11, which SWE holds; 4 and 8 are deep in Germany
    assert sorted(d.province for d in pic.contact["GER"]) == [7, 7, 12]
    assert 12 in pic.borders and pic.borders[12][0] == "GER"


def test_intel_decides_how_much_of_a_foreign_country_shows(save, grid):
    world = W.read_world(save, "SWE", layer=grid)

    def ger(**intel):
        return W.picture(world, "SWE", {"GER": W.Intel(**intel)}, grid).foreign["GER"]

    exact = ger(civilian=0.9, army=0.9)
    assert exact["divisions"] == "5" and exact["templates"] == {"Infanterie": 3, "Panzer": 2}
    assert exact["factories"] == {"civ": 23, "mil": 17, "dock": 0}
    assert exact["production"][0] == {"equipment": "infantry_equipment_1", "factories": 10}
    estimate = ger(civilian=0.5, army=0.5)
    assert estimate["divisions"] == "~5" and estimate["factories"]["civ"] == "~25"
    assert estimate["production"] == ["artillery_equipment_1", "infantry_equipment_1"]
    blind = ger(civilian=0.1, army=0.1)
    assert blind["divisions"] == "at least 3 seen" and blind["factories"] == "unknown"
    assert blind["production"] == "unknown"
    # no reading at all: the default band, and it says so
    assumed = W.picture(world, "SWE", {}, grid).foreign["GER"]
    assert assumed["intel"]["assumed"] is True and assumed["divisions"].startswith("~")


def test_the_brief_section_and_the_query_views(save, grid):
    pic = W.picture(W.read_world(save, "SWE", layer=grid), "SWE", {"GER": W.Intel(0.9, 0.9)}, grid)
    text = "\n".join(W.brief_lines(pic, layer=grid))
    assert text.startswith("Forces and map (save of 1936-03-01")
    assert "Army: 3 divisions" in text
    assert "Industry: 7 civ / 3 mil / 0 dock; producing infantry_equipment_1 x3" in text
    assert "state 11 [plains]: 2 (2 Infantry) org 55, manpower 90%, dug in 6 | facing 2" in text
    # SWE and GER are at peace in this save: neighbours seen are "at the border", not "in
    # contact" (a model read "in contact" as a war, overnight campaign 2026-11-22)
    assert "GER: divisions 5 (3 at the border, at peace); factories 23 civ / 17 mil / 0 dock" in text
    pic.world.enemies = {"GER"}
    assert "GER: divisions 5 (3 in contact, at war)" in "\n".join(W.brief_lines(pic, layer=grid))
    divisions = W.query(pic, "divisions", None)
    assert divisions["count"] == 3 and divisions["divisions"][0]["province"] in (1, 6)
    state = W.query(pic, "state", "12")         # provinces 3, 7, 11 (11 held by SWE)
    assert state["held_by"] == "GER" and [d["province"] for d in state["enemy_seen"]] == [7, 7]
    assert [d["province"] for d in W.query(pic, "state", "13")["enemy_seen"]] == [12]
    assert W.query(pic, "country", "GER")["divisions"] == "5"
    assert W.query(pic, "country", "ITA")["known"] is False
    assert "error" in W.query(pic, "nonsense", None)


# --- finding this campaign's save -----------------------------------------------------------------


def test_the_newest_text_save_of_this_moment_is_used(tmp_path):
    folder = tmp_path / "saves"
    folder.mkdir()
    old = folder / "old.hoi4"
    old.write_text(save_text(date="1936.1.1.1"), encoding="utf-8")
    binary = folder / "bin.hoi4"
    binary.write_bytes(b"HOI4bin\x00\x01")
    newer = folder / "autosave.hoi4"
    newer.write_text(save_text(date="1936.3.1.12"), encoding="utf-8")
    now = time.time()
    os.utime(old, (now - 30, now - 30))
    os.utime(newer, (now - 20, now - 20))
    os.utime(binary, (now, now))                       # newest, but binary: skipped
    assert W.newest_save(folder, "1936-03-10") == newer
    assert W.newest_save(folder, "1936-01-15") == old  # a save after the game's date is skipped
    assert W.newest_save(folder, "1937-06-01") is None  # too old for this moment


# --- the intel line -------------------------------------------------------------------------------


def test_the_mod_logs_intel_weekly_and_the_reader_keeps_it(tmp_path):
    telemetry = (MOD / "common" / "scripted_effects" / "llm_bridge_telemetry.txt").read_text(encoding="utf-8")
    assert "llm_bridge_emit_intel = {" in telemetry
    assert "set_temp_variable = { llmb_ia = ROOT.army_intel@THIS }" in telemetry
    hooks = (MOD / "common" / "on_actions" / "llm_bridge_on_actions.txt").read_text(encoding="utf-8")
    weekly = hooks[hooks.index("on_weekly"):]
    assert "llm_bridge_emit_intel = yes" in weekly[:400]
    log = tmp_path / "game.log"
    log.write_text(
        "[11:04:29][1936.01.02.21]: LLMB|v5|state|date= 21:00, 2 January, 1936|tag=SWE|pp=1|stab=80"
        "|ws=0|civ=9|mil=3|doc=3|mp=5\n"
        "[11:04:29][1936.01.02.21]: LLMB|v5|intel|tag=SWE|of=GER|civ=72.5|army=40|navy=[?llmb_in]|air=10\n",
        encoding="utf-8")
    state = LogTailAdapter(log).read_state()
    assert state.intel == {"GER": {"civilian": 0.725, "army": 0.4, "air": 0.1}}
    assert W.intel_from(state)["GER"].navy is None


# --- in a run ----------------------------------------------------------------------------------------


def test_a_run_with_the_observer_gets_the_section_and_the_tool(save, grid):
    adapter = MockAdapter()
    adapter.state.country = "SWE"
    env = HOI4Env(adapter, HarnessConfig())
    assert "inspect_world" not in env.allowed_actions
    env.attach_world(W.WorldObserver("SWE", save.parent, layer=grid))
    assert "inspect_world" in env.allowed_actions
    assert "Forces and map" in env.observe().brief
    result = env.query(ActionCall("inspect_world", {"view": "country", "id": "GER"}))
    assert result.ok and json.loads(result.message)["tag"] == "GER"
    assert "inspect_world" in QUERY_ACTIONS and "inspect_world" in ALWAYS


def test_no_save_yet_is_said_not_guessed(tmp_path):
    observer = W.WorldObserver("SWE", tmp_path)
    assert observer.brief_lines(GameState(country="SWE")) == []
    assert "no text save" in observer.query(GameState(country="SWE"), "divisions", None)["error"]
