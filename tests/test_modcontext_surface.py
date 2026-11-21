"""The action surface of a playset (modcontext): decisions, focuses, techs, ideas.

Read from tests/fixtures/modctx, a small invented playset: a base game with
decisions, a focus tree, techs, ideas and a scripted GUI, mod ``alpha`` that
overrides one decisions file and adds techs and an ideology, and mod ``beta``
that replaces folders. Ids are invented, not copied from HOI4.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoi4_harness.cli import main
from hoi4_harness.identifiers import IdentifierIndex, Mod, Playset
from hoi4_harness.modcontext import script as S

FIX = Path(__file__).parent / "fixtures" / "modctx"
GAME, ALPHA, BETA = FIX / "game", FIX / "mods" / "alpha", FIX / "mods" / "beta"


@pytest.fixture(scope="module")
def base():
    return IdentifierIndex.build(Playset.from_paths(GAME)).mod_context()


@pytest.fixture(scope="module")
def layered():
    return IdentifierIndex.build(Playset.from_paths(GAME, [ALPHA, BETA])).mod_context()


# --- the script reader -----------------------------------------------------------


def test_the_reader_keeps_order_operators_and_repeated_keys():
    nodes = S.parse('a = { x > 5 x < 9 b = yes # note "with a quote\n list = { P Q } s = "two words" }')
    (_, _, body), = nodes
    assert [(k, o) for k, o, _ in body[:3]] == [("x", ">"), ("x", "<"), ("b", "=")]
    assert S.bare(S.block(body, "list")) == ["P", "Q"]
    assert S.text(body, "s") == "two words"
    assert S.inline(body) == 'x > 5 x < 9 b = yes list = { P Q } s = "two words"'


def test_a_condition_reads_as_a_short_line():
    block = S.block(S.parse("a = { has_war = no OR = { tag = GER tag = ITA } num > 3 "
                            "is_ai = yes c = 1 d = 2 e = 3 }"), "a")
    line = S.readable(block, limit=4)
    assert line == "not has_war, (tag GER or tag ITA), num > 3, is_ai, +3 more"


def test_tag_tests_decide_only_when_they_can():
    def truth(text, tag):
        return S.tag_truth(S.parse(text), tag)
    assert truth("tag = SWE", "SWE") is True and truth("original_tag = SWE", "GER") is False
    assert truth("OR = { tag = SWE tag = FIN }", "FIN") is True
    assert truth("OR = { tag = SWE has_war = yes }", "GER") is None       # the war could decide it
    assert truth("NOT = { tag = SWE }", "SWE") is False
    assert truth("has_war = yes", "SWE") is None


# --- decisions -------------------------------------------------------------------


def test_decisions_carry_category_cost_duration_and_kind(base):
    cheap = base.find_decision("swe_cheap")
    assert (cheap.category, cheap.cost, cheap.days_remove, cheap.days_re_enable) == ("swe_cat", 25, 30, 60)
    assert cheap.kind == "decision" and not cheap.fire_only_once
    assert base.find_decision("swe_once").fire_only_once
    assert base.find_decision("swe_mission").kind == "mission"
    assert base.find_decision("swe_aid").kind == "targeted"
    assert base.find_decision("swe_aid").targets == ["1", "FIN"]
    assert base.find_decision("swe_var_cost").cost_raw == "swe_cost_var"


def test_a_decision_that_cannot_be_taken_is_marked(base):
    assert base.find_decision("swe_spacer").blocked
    assert base.find_decision("swe_no_effect").inert
    assert base.find_decision("swe_cheap").takeable and not base.find_decision("swe_mission").takeable


def test_a_country_sees_the_decisions_its_tag_tests_allow(base):
    swe = {d.id for d in base.decision_for("SWE")}
    ger = {d.id for d in base.decision_for("GER")}
    assert "swe_cheap" in swe and "ger_thing" not in swe
    assert ger == {"ger_thing"}


def test_a_decision_reads_back_as_conditions_a_person_can_follow(base):
    out = base.find_decision("swe_cheap").describe(base.names({"swe_cheap", "swe_cheap_desc"}))
    assert out["visible"] == "has_country_flag swe_open"
    assert out["available"] == "not has_war, (not is_in_faction or num_of_factories > 5)"
    assert out["name"] == "Cheap Reform"
    assert out["description"] == "A small reform, cheaply done. Twice."     # colour codes and \n gone


def test_localised_names_lose_the_text_the_game_fills_in(base):
    assert base.names({"swe_aid"})["swe_aid"] == "Aid"          # "[FROM.GetName]" is draw-time


def test_scripted_guis_are_read_and_counted_beside_the_decisions(base):
    gui = [c for c in base.surface("GER").categories if c["id"] == "ger_cat"]
    assert gui and gui[0]["custom_gui"] == "ger_cat_window"      # flagged, decisions still listed
    surface = base.surface("SWE")
    assert surface.excluded == []                                  # nothing left out since #136
    assert surface.counts["gui_windows"] == 3 and surface.counts["gui_buttons_pressable"] >= 2
    assert not any("scripted_gui" in d.id for d in base.decisions().values())


def test_a_mod_file_at_the_same_path_replaces_the_game_file(layered):
    ids = set(layered.decisions())
    assert ids == {"swe_cheap", "alpha_new"}                     # swe_once etc. are gone
    cheap = layered.find_decision("swe_cheap")
    assert (cheap.cost, cheap.days_remove, cheap.source) == (40, 10, "Alpha World")
    assert layered.names({"alpha_new"})["alpha_new"] == "Brand New Thing"


def test_replace_path_drops_earlier_categories_and_ideas(layered):
    assert layered.categories()["swe_cat"].priority == 99 and set(layered.categories()) == {"swe_cat"}
    spirits = [i["id"] for i in layered.ideas()["spirits"]]
    assert spirits == ["SWE_beta_spirit"] and layered.ideas()["laws"] == {}


# --- focus tree --------------------------------------------------------------------


def test_focus_details_hold_prerequisites_exclusions_and_conditions(base):
    focus = base.focus_details("SWE")
    assert focus["SWE_root"].available == "not has_war" and focus["SWE_root"].cost == 10
    assert focus["SWE_left"].mutually_exclusive == ["SWE_right"]
    assert focus["SWE_left"].bypass == "has_country_flag swe_skip"
    assert focus["SWE_end"].prerequisites == [["SWE_left", "SWE_right"], ["SWE_root"]]


def test_available_now_follows_prerequisites_and_exclusions(base):
    assert base.available_focuses("SWE") == ["SWE_root"]
    assert base.available_focuses("SWE", ["SWE_root"]) == ["SWE_left", "SWE_right"]
    assert base.available_focuses("SWE", ["SWE_root", "SWE_left"]) == ["SWE_end"]   # right is excluded


# --- technologies, ideas, advisors, mechanics -------------------------------------


def test_only_technologies_the_base_game_lacks_are_new(layered, base):
    assert base.new_technologies() == []
    (tech,) = layered.new_technologies()
    assert (tech["id"], tech["folder"], tech["start_year"]) == ("alpha_rifle", "infantry_folder", "1950")


def test_laws_spirits_advisors_and_starting_ideas_are_per_country(base):
    swe = base.surface("SWE")
    assert [s["id"] for s in swe.spirits] == ["SWE_neutrality"]
    (law,) = swe.laws
    assert [o["id"] for o in law["options"]] == ["civilian_economy", "war_economy"]
    assert [a["token"] for a in swe.advisors] == ["SWE_minister_adv"]
    assert swe.advisors[0]["name"] == "A Minister" and swe.advisors[0]["cost"] == 50
    fin = base.surface("FIN")
    assert [o["id"] for o in fin.laws[0]["options"]] == ["civilian_economy"]      # war_economy: NOT tag FIN
    assert fin.spirits == [] and fin.advisors == []


def test_starting_ideas_stop_at_the_default_bookmark(base):
    assert base.start_date() == "1936.1.1.12"
    assert base.starting_ideas("SWE") == ["SWE_neutrality", "civilian_economy", "late_one"]


def test_mods_add_ideologies_and_resources_the_base_lacks(layered, base):
    mech = layered.mechanics()
    assert mech["ideologies"]["new"] == ["neutrality"] and mech["resources"]["new"] == ["uranium"]
    assert mech["replaced_folders"] == ["common/decisions/categories", "common/ideas"]
    assert base.mechanics()["ideologies"] == {"new": [], "dropped": 0, "total": 2}


def test_events_are_counted_by_namespace(layered):
    events = layered.event_chains("SWE")
    assert events["events"] == 4 and events["namespaces"] == 3
    assert events["country"] == {"swe": 2}


# --- the library and command line ----------------------------------------------------


def test_a_surface_serialises_and_names_what_it_excluded(base):
    data = json.loads(json.dumps(base.surface("SWE").to_dict()))
    assert data["counts"]["takeable_decisions"] == 7 and data["counts"]["missions"] == 1
    assert data["focus"]["available_now"] == [{"id": "SWE_root", "name": "Root Focus"}]


def test_a_mod_knows_its_workshop_id():
    assert Mod.load(ALPHA).remote_file_id == "2438003901"
    assert Mod.load(BETA).remote_file_id is None
    assert Mod.load(Path("/x/steamapps/workshop/content/394360/123456")).remote_file_id == "123456"


def test_mod_actions_json_on_the_fixture(capsys):
    code = main(["mod-actions", "--game-dir", str(GAME), "--mod", str(ALPHA), "--country", "swe",
                 "--offline", "--json", "--limit", "5"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["tag"] == "SWE"
    assert [d["id"] for d in out["decisions"]] == ["alpha_new", "swe_cheap"]
    assert out["world"][0]["source"] == "descriptor"            # offline, nothing cached
    assert out["new_technologies"][0]["id"] == "alpha_rifle"


def test_mod_actions_text_and_unknown_country(capsys, tmp_path):
    assert main(["mod-actions", "--game-dir", str(GAME), "--country", "SWE", "--offline"]) == 0
    text = capsys.readouterr().out
    assert "takeable decisions        7" in text and "gui windows               3" in text
    assert main(["mod-actions", "--game-dir", str(GAME), "--country", "ZZZ", "--offline"]) == 1
    assert main(["mod-actions", "--game-dir", str(GAME), "--offline"]) == 2
