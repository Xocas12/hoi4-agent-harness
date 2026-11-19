"""The fake game, on its own: its keyboard, its console, its effects, its log.

Everything reaches it the way the real stack reaches HOI4: hardware scan codes
through ``ScanCodeKeys(send=game.send)``, a run file in the user directory, and a
log read back with the real ``LogTailAdapter``. No test here sends a real key.
"""

from __future__ import annotations

import pytest

from hoi4_harness.adapters.keys import ScanCodeKeys
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.fakegame import (
    FakeGame,
    ScriptError,
    demo_index,
    parse_script,
)
from hoi4_harness.types import ProductionLine, ScenarioStart


@pytest.fixture()
def game(tmp_path):
    return FakeGame(tmp_path / "user", index=demo_index(tmp_path / "playset"), variance=0)


@pytest.fixture()
def keys(game):
    return ScanCodeKeys(send=game.send, sleep=game.sleep)


def run_lines(game, keys, *lines):
    """What the console channel does: write the file, open the console, run it."""
    (game.user_dir / "llmb_turn.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    keys.chord("grave")
    keys.type_text("run llmb_turn.txt")
    keys.chord("enter")
    keys.chord("grave")


def build(building="industrial_complex", state="10", guard=True):
    block = f"add_building_construction = {{ type = {building} level = 1 instant_build = no }}"
    if guard:
        block = (f"if = {{ limit = {{ is_owned_by = SWE free_building_slots = {{ building = "
                 f"{building} size > 0 }} }} {block} }}")
    return f"eval_effect SWE = {{ {state} = {{ {block} }} }}"


# --- the parser ---------------------------------------------------------------


def test_the_script_parser_nests_blocks_and_keeps_comparisons():
    (entry,) = parse_script("SWE = { if = { limit = { has_political_power > 24.99 } x = -3 } }")
    inner = entry.value[0].value
    assert inner[0].key == "limit" and inner[0].value[0].op == ">"
    assert inner[1].value == "-3"


@pytest.mark.parametrize("text", ["SWE = { a = 1", "SWE = 1 }", "= 3", "SWE"])
def test_the_script_parser_refuses_what_is_not_balanced(text):
    with pytest.raises(ScriptError):
        parse_script(text)


# --- the keyboard ---------------------------------------------------------------


def test_the_console_key_opens_it_and_typed_text_runs_a_file(game, keys):
    run_lines(game, keys, "gamespeed 5")
    assert game.runs == [["gamespeed 5"]]
    assert (game.paused, game.speed) == (False, 5)
    assert not game.console_open


def test_text_typed_with_the_console_closed_hits_hotkeys(game, keys):
    """The hazard the channel's key sequence exists to avoid: 'run' is trade, ...
    and the space in it is the pause key."""
    assert game.paused
    keys.type_text("run x")
    assert game.panel == "trade"          # r: trade (then u, n do nothing)
    assert not game.paused                # the space toggled the pause
    assert game.runs == []


def test_escape_with_no_panel_opens_the_menu_and_the_next_key_is_swallowed(game, keys):
    keys.chord("escape")
    assert game.menu_open
    keys.chord("space")
    assert game.paused and not game.menu_open      # the space was eaten, not a pause toggle
    keys.chord("space")
    assert not game.paused


def test_escape_closes_an_open_panel_instead(game, keys):
    keys.chord("alt+q")
    assert game.panel == "focus"
    keys.chord("escape")
    assert game.panel is None and not game.menu_open


def test_the_speed_keys_move_the_speed_one_step(game, keys):
    keys.chord("kp_plus")
    keys.chord("kp_plus")
    assert game.speed == 5
    keys.chord("kp_minus")
    assert game.speed == 4


def test_the_focus_search_starts_the_matching_focus(game, keys):
    keys.chord("alt+q")
    keys.chord("ctrl+f")
    keys.type_text("Industrial")
    keys.chord("shift+enter")
    assert game.world.state.national_focus == "industrial_effort"
    assert game.panel is None


def test_a_focus_whose_prerequisite_is_missing_is_not_started(game, keys):
    keys.chord("alt+q")
    keys.chord("ctrl+f")
    keys.type_text("Expand the Army")
    keys.chord("shift+enter")
    assert game.world.state.national_focus is None


def test_a_second_focus_is_declined_while_one_runs(game, keys):
    for text in ("Industrial", "Defense"):
        keys.chord("alt+q")
        keys.chord("ctrl+f")
        keys.type_text(text)
        keys.chord("shift+enter")
    assert game.world.state.national_focus == "industrial_effort"


# --- effects --------------------------------------------------------------------


def test_construction_is_queued_in_an_owned_state_with_a_free_slot(game, keys):
    run_lines(game, keys, build())
    assert [(i.building, i.state) for i in game.world.state.construction] == [
        ("civilian_factory", "Svealand")]


def test_construction_in_a_state_the_country_does_not_own_does_nothing_when_guarded(game, keys):
    run_lines(game, keys, build(state="20"))
    assert game.world.state.construction == []
    assert game.errors == []              # the limit failed: the game says nothing


def test_without_the_guard_the_game_itself_declines_a_foreign_state(game, keys):
    run_lines(game, keys, build(state="20", guard=False))
    assert game.world.state.construction == []
    assert "not owned by SWE" in game.log_path.read_text(encoding="utf-8")


def test_a_full_state_declines_the_next_building(game, keys):
    run_lines(game, keys, *[build(guard=False)] * 3)           # two slots are free
    assert len(game.world.state.construction) == 2
    assert "no free industrial_complex slot" in game.log_path.read_text(encoding="utf-8")


def test_a_guarded_building_in_a_full_state_is_a_silent_no_op(game, keys):
    run_lines(game, keys, *[build()] * 3)
    assert len(game.world.state.construction) == 2


def test_capital_scope_is_the_lowest_owned_state(game, keys):
    run_lines(game, keys, "eval_effect SWE = { capital_scope = { add_building_construction = "
                          "{ type = arms_factory level = 1 instant_build = no } } }")
    assert game.world.state.construction[0].state == "Svealand"


ARTILLERY = ("eval_effect SWE = { add_equipment_production = { equipment = "
             "{ type = artillery_equipment_1 } requested_factories = 9 } }")


def test_production_takes_what_is_free(tmp_path):
    g = FakeGame(tmp_path / "u", index=demo_index(tmp_path / "p"), variance=0,
                 start_state=ScenarioStart(production=[ProductionLine("infantry_equipment_1", 3)]))
    run_lines(g, ScanCodeKeys(send=g.send, sleep=g.sleep), ARTILLERY)
    assert {x.equipment: x.factories for x in g.world.state.production} == {
        "infantry_equipment_1": 3, "artillery_equipment_1": 2}


def test_production_with_no_free_factory_opens_no_line(game, keys):
    run_lines(game, keys, ARTILLERY)          # the start has every factory on one line
    assert [x.equipment for x in game.world.state.production] == ["infantry_equipment_1"]
    assert "no free military factory" in game.log_path.read_text(encoding="utf-8")


def test_an_unknown_equipment_is_an_error(game, keys):
    run_lines(game, keys, "eval_effect SWE = { add_equipment_production = { equipment = "
                          "{ type = nonsense_1 } requested_factories = 1 } }")
    assert game.errors == ["Unknown equipment: nonsense_1"]


def advisor(character="SWE_demo_minister", cost=25):
    return (f"eval_effect SWE = {{ if = {{ limit = {{ has_political_power > {cost - 0.01:g} }} "
            f"activate_advisor = {character} add_political_power = -{cost} }} }}")


def test_an_advisor_costs_political_power_and_takes_a_slot(game, keys):
    before = game.world.state.political_power
    run_lines(game, keys, advisor())
    assert game.advisors == {"political_advisor": ["SWE_demo_minister"]}
    assert game.world.state.political_power == before - 25


def test_a_taken_advisor_slot_declines_the_second_hire(game, keys):
    run_lines(game, keys, advisor(), advisor("SWE_demo_second_minister"))
    assert game.advisors == {"political_advisor": ["SWE_demo_minister"]}


def test_the_power_guard_leaves_a_hire_the_country_cannot_pay_undone(tmp_path):
    poor = FakeGame(tmp_path / "u", index=demo_index(tmp_path / "p"), variance=0,
                    start_state=ScenarioStart(political_power=10.0))
    keys = ScanCodeKeys(send=poor.send, sleep=poor.sleep)
    run_lines(poor, keys, advisor())
    assert poor.advisors == {} and poor.world.state.political_power == 10.0


def test_the_console_splits_quoted_text_as_the_real_one_does(game, keys):
    run_lines(game, keys, 'eval_effect SWE = { log = "two words" }')
    assert game.errors == ["Unknown effect-type: words"]


def test_an_effect_for_another_country_is_refused(game, keys):
    run_lines(game, keys, "eval_effect GER = { add_political_power = 5 }")
    assert "only SWE is modelled" in game.errors[0]


def test_an_unknown_effect_is_named(game, keys):
    run_lines(game, keys, "eval_effect SWE = { win_the_war = yes }")
    assert game.errors == ["Unknown effect-type: win_the_war"]


def test_a_missing_run_file_is_an_error(game, keys):
    keys.chord("grave")
    keys.type_text("run nothere.txt")
    keys.chord("enter")
    keys.chord("grave")
    assert game.errors == ["Could not open file: nothere.txt"]


# --- directives -------------------------------------------------------------------


def test_a_directive_needs_a_target_first(game, keys):
    run_lines(game, keys, "effect SWE llm_bridge_raise_protect")
    assert game.directives == []
    run_lines(game, keys, "effect SWE llm_bridge_select_POL", "effect SWE llm_bridge_raise_protect")
    assert game.directives == ["protect POL"]


def test_theater_posture_and_global_posture_and_clearing(game, keys):
    run_lines(game, keys, "effect SWE llm_bridge_select_GER",
              "effect SWE llm_bridge_raise_theater_offensive",
              "effect SWE llm_bridge_set_posture_defensive")
    assert game.theater_postures == {"GER": "offensive"}
    assert game.global_posture == "defensive"
    run_lines(game, keys, "effect SWE llm_bridge_clear_directives")
    assert (game.theater_postures, game.global_posture, game.directives) == ({}, None, [])


def test_a_target_the_mod_has_no_plumbing_for_is_an_error(game, keys):
    run_lines(game, keys, "effect SWE llm_bridge_select_XYZ")
    assert game.errors == ["Unknown scripted effect: llm_bridge_select_XYZ"]


def test_the_decision_route_reaches_the_same_effects(game, keys):
    run_lines(game, keys,
              "eval_effect SWE = { activate_decision = llmb_target_POL }",
              "eval_effect SWE = { activate_decision = llmb_directive_contain }")
    assert game.directives == ["contain POL"]


# --- time and the log -----------------------------------------------------------


def test_time_only_passes_while_the_game_runs(game):
    game.sleep(100)
    assert game.date == "1936-01-01"
    game.paused = False
    game.sleep(1.0)                              # speed 3: half a second a day
    assert game.date == "1936-01-03"


def test_gamespeed_zero_pauses_and_a_bad_speed_is_an_error(game, keys):
    run_lines(game, keys, "gamespeed 4")
    run_lines(game, keys, "gamespeed 0")
    assert game.paused
    run_lines(game, keys, "gamespeed 9")
    assert game.errors == ["gamespeed <0-5>"]


def test_the_log_is_what_the_real_reader_reads(game, tmp_path):
    game.paused = False
    game.run_days(3)
    state = LogTailAdapter(game.log_path, country="SWE").read_state()
    assert state.date == "1936-01-04"
    assert state.political_power == pytest.approx(123.0)
    assert state.research and state.research[0].technology == "infantry_weapons2"
    assert state.telemetry_ticks == 3


def test_a_started_focus_shows_in_the_log_only_after_a_day_of_progress(game, keys):
    keys.chord("alt+q")
    keys.chord("ctrl+f")
    keys.type_text("Industrial")
    keys.chord("shift+enter")
    game.emit_state()
    assert "|focus|" not in game.log_path.read_text(encoding="utf-8")
    game.tick_day()
    assert "|focus|tag=SWE|id=industrial_effort" in game.log_path.read_text(encoding="utf-8")


def test_a_finished_technology_raises_the_techs_counter(game):
    game.run_days(46)
    state = LogTailAdapter(game.log_path, country="SWE").read_state()
    assert any(e.kind == "research_done" for e in state.events)


def test_a_finished_building_raises_a_built_counter_and_takes_its_slot(game, keys):
    run_lines(game, keys, build())
    game.run_days(80)
    text = game.log_path.read_text(encoding="utf-8")
    assert "built_civ=1" in text
    assert game.states["10"].used == 5


def test_the_scripted_war_is_logged_as_the_game_would(tmp_path):
    g = FakeGame(tmp_path / "u", start="1939-08-30", variance=0)
    g.run_days(3)
    state = LogTailAdapter(g.log_path, country="SWE").read_state()
    assert any(e.kind == "war_declared" and e.severity == "critical" for e in state.events)


def test_keys_from_a_file_play_like_keys_sent_directly(game):
    from hoi4_harness.fakegame import FileKeys

    sender = FileKeys(game.keys_path)
    keys = ScanCodeKeys(send=sender, sleep=lambda s: None)
    keys.chord("space")
    assert game.paused
    assert game.poll_keys() > 0
    assert not game.paused
    assert game.poll_keys() == 0


def test_pause_in_hours_stops_the_game_after_whole_days_and_the_margin(game, keys):
    run_lines(game, keys, "pause_in_hours 180", "gamespeed 3")       # 7 days and 12 hours
    game.sleep(60)
    assert game.date == "1936-01-08" and game.paused


def test_pause_in_hours_needs_a_number(game, keys):
    run_lines(game, keys, "pause_in_hours soon")
    assert game.errors == ["pause_in_hours <hours>"]
