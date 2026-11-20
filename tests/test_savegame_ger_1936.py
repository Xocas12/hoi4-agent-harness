"""A 1 January 1936 Germany save maps to a brief with the game's own numbers (#6).

``fixtures/ger_1936_01_01.hoi4`` is a ``scripts/trim_save.py`` cut of a text save
from a live HOI4 1.20 game as Germany on the 1936 bookmark, LLM Bridge mod loaded.
The account name is replaced with "player".

Every expected number below was read off the running game at that moment, not
off the save: the top bar (PP 2, stability 81%, war support 35%, manpower 1.31M,
73 factories) and the mod's log line

    LLMB|v5|state|... pp=2.324|stab=81|ws=0|civ=35|mil=28|doc=10|owned=73|mp=1316296|ai=1|techs=55
    army divisions=30

The war support the mod logged was 0 because it read the bare ``war_support``
variable, which is 0 on 1.20 (see the telemetry fix and #97). The fixture
keeps that 0 as logged, so the war-support test below is a known failure until a
save is taken after the fix is live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness.adapters.savegame import SaveGameAdapter, read_save
from hoi4_harness.observation.render import render_full

FIXTURE = Path(__file__).parent / "fixtures" / "ger_1936_01_01.hoi4"


@pytest.fixture(scope="module")
def raw() -> dict:
    return read_save(FIXTURE)


@pytest.fixture()
def state(raw):
    return SaveGameAdapter(FIXTURE.parent)._to_state(raw)


def test_6_a_1936_save_gives_a_brief_with_real_numbers(state):
    brief = render_full(state)
    assert brief.startswith("## GER -- 1936-01-01")
    assert "PP 2 " in brief and "stability 81%" in brief and "manpower 1,316,296" in brief
    assert "35 civ / 28 mil / 10 dockyards" in brief
    assert "4 SLOT(S) FREE" in brief
    assert "30 divisions" in brief
    assert "24x Infanterie-Division" in brief and "3x Panzer-Division" in brief
    assert "construction: queue empty" in brief.lower()
    for line in ("infantry_equipment_1 x10", "support_equipment_1 x2", "artillery_equipment_1 x1",
                 "light_tank_chassis_2 x2", "motorized_equipment_1 x2", "small_plane_airframe_1 x1"):
        assert line in brief, line


def test_6_date_and_tag(state):
    assert state.date == "1936-01-01" and state.country == "GER"


def test_6_political_power_and_stability_are_the_games_figures(state):
    assert state.political_power == pytest.approx(2.324, abs=0.01)   # log pp=2.324, bar "2"
    assert state.stability == pytest.approx(0.81, abs=0.001)         # bar 81%; the save's base is 0.7
    assert state.known("political_power") and state.known("stability")


def test_6_manpower_matches_the_top_bar(state):
    assert state.manpower == 1_316_296


def test_6_factories_sum_to_the_top_bars_73(state):
    assert (state.civilian_factories, state.military_factories, state.dockyards) == (35, 28, 10)
    assert state.civilian_factories + state.military_factories + state.dockyards == 73


def test_6_thirty_divisions_with_the_template_mix(state):
    assert sum(d.count for d in state.divisions) == 30
    assert {d.template: d.count for d in state.divisions} == {
        "Infanterie-Division": 24, "Panzer-Division": 3, "Infanterie-Division (mot.)": 1,
        "Kavallerie-Brigade": 1, "Gebirgs-Brigade": 1,
    }


def test_6_the_six_production_lines(state):
    lines = {line.equipment: line for line in state.production}
    six = {
        "infantry_equipment_1": 10, "support_equipment_1": 2, "artillery_equipment_1": 1,
        "light_tank_chassis_2": 2, "motorized_equipment_1": 2, "small_plane_airframe_1": 1,
    }
    assert {k: lines[k].factories for k in six} == six
    assert all(lines[k].efficiency == pytest.approx(0.50, abs=0.01) for k in six)
    # The save also holds a CAS and a medium-bomber line, one factory each; the rest
    # of the 28 military factories (28 - 20 = 8) is what the brief calls idle.
    assert set(lines) - set(six) == {"small_plane_cas_airframe_1", "medium_plane_airframe_1"}
    assert 28 - sum(line.factories for line in state.production) == 8


def test_6_research_is_idle_with_four_free_slots(state):
    assert [s.technology for s in state.research] == [None] * 4


def test_6_the_construction_queue_is_known_and_empty(state):
    assert state.known("construction") and state.construction == []


def test_6_the_player_block_is_not_the_decoy(raw):
    # The trim plants a GER-less decoy host; the player's own politics must win.
    assert raw["country"]["politics"]["political_power"] != "999"
    assert "opinion" not in raw["country"]


def test_6_war_support_reports_what_the_mod_logged(state, raw):
    # llmb_ws is 0 in this save (the telemetry bug), and the mapping reports it faithfully.
    logged = float(raw["country"]["variables"]["llmb_ws"])
    assert state.known("war_support")
    assert state.war_support == pytest.approx(logged / 100.0)


@pytest.mark.xfail(strict=True, reason="telemetry logged war support as 0 (bare war_support reads 0 "
                   "on 1.20); fixed in the mod, needs a new save after the fix is live-verified "
                   "(issue #97)")
def test_war_support_matches_the_top_bar(state):
    assert state.war_support == pytest.approx(0.35, abs=0.005)


@pytest.mark.parametrize("player", ["SWE", "GER", "ENG"])
def test_6_trim_decoys_never_duplicate_the_players_tag(player):
    """trim_save planted a decoy GER block, so a GER save got two GER blocks and the
    adapter read the decoy (PP 999, no stability, no divisions)."""
    import importlib.util
    import re

    spec = importlib.util.spec_from_file_location(
        "trim_save", Path(__file__).resolve().parents[1] / "scripts" / "trim_save.py")
    trim_save = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trim_save)
    text = trim_save.decoys(player)
    assert len(re.findall(rf"^\t{player}=\{{", text, re.M)) == 0
    assert f"\t\t{player}={{" in text      # the nested opinion decoy still names the player
