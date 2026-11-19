"""Theater posture on the mod side and the log reader (#9)."""

import re

from hoi4_harness import modgen
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.observation import render_full
from hoi4_harness.types import ActionCall, Front, GameState, ScenarioStart, War


def _files(tags=("FIN", "POL")):
    return {item.path: item.text for item in modgen.generate(tags)}


def _block(text, name):
    return re.search(name + r" = \{(.*?)\n\}", text, flags=re.S).group(1)


def test_a_theater_posture_block_scopes_front_requests_to_its_own_tag():
    strategies = _files()["common/ai_strategy/llm_bridge_targets.txt"]
    for posture in modgen.THEATER_POSTURES:
        fin = _block(strategies, f"llmb_posture_{posture}_FIN")
        assert f"has_country_flag = llmb_posture_{posture}_FIN" in fin
        assert "type = front_unit_request tag = FIN" in fin and "POL" not in fin


def test_raising_a_theater_posture_sets_one_flag_and_logs_it_for_the_selected_tag():
    effects = _files()["common/scripted_effects/llm_bridge_targets.txt"]
    body = _block(effects, "llm_bridge_raise_theater_offensive")
    pol = next(line for line in body.splitlines() if "llmb_target_POL" in line)
    assert "clr_country_flag = llmb_posture_defensive_POL" in pol
    assert "set_country_flag = llmb_posture_offensive_POL" in pol
    assert f"kind={modgen.EVENT_THEATER_POSTURE}|detail=offensive POL" in pol
    balanced = _block(effects, "llm_bridge_raise_theater_balanced")
    assert "set_country_flag" not in balanced
    assert "clr_country_flag = llmb_posture_offensive_FIN" in balanced


def test_standing_down_clears_theater_posture_flags_too():
    effects = _files()["common/scripted_effects/llm_bridge_targets.txt"]
    clear = _block(effects, "llm_bridge_clear_target_directives")
    assert "llm_bridge_clear_theater_postures = yes" in clear
    assert "llmb_posture_offensive_POL" in _block(effects, "llm_bridge_clear_theater_postures")


def test_the_theater_posture_decisions_are_localised():
    files = _files()
    for posture in modgen.THEATER_POSTURE_CHOICES:
        assert f"llmb_theater_{posture} = {{" in files["common/decisions/llm_bridge_target_decisions.txt"]
        assert f"llmb_theater_{posture}:0" in files["localisation/english/llm_bridge_targets_l_english.yml"]


def test_the_log_reader_learns_theater_postures_from_the_mods_events(tmp_path):
    log = tmp_path / "game.log"
    log.write_text(
        "[x]: LLMB|v3|state|date=1939.9.1|tag=GER|pp=1|stab=50|ws=10\n"
        "[x]: LLMB|v3|evt|tag=GER|kind=15|detail=offensive POL\n"
        "[x]: LLMB|v3|evt|tag=GER|kind=15|detail=defensive FRA\n"
        "[x]: LLMB|v3|evt|tag=GER|kind=15|detail=balanced POL\n"
    )
    state = LogTailAdapter(log).read_state()
    assert state.theater_postures == {"POL": "balanced", "FRA": "defensive"}
    assert state.known("theater_postures")
    log.write_text(log.read_text() + "[x]: LLMB|v3|evt|tag=GER|kind=12\n")
    assert LogTailAdapter(log).read_state().theater_postures == {}


def test_the_brief_shows_global_and_theater_postures_together():
    state = GameState(posture="defensive", theater_postures={"SOV": "defensive", "GER": "offensive"})
    assert "AI control: posture defensive (global) | GER: offensive | SOV: defensive" in render_full(state)


def test_a_theater_tag_is_case_insensitive_and_must_be_faced_by_a_front():
    adapter = MockAdapter(
        country="GER", start="1939-09-01",
        start_state=ScenarioStart(wars=[War(against="POL")], fronts=[Front(name="east", enemy="POL")]),
    )
    adapter.apply(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    assert adapter.apply(ActionCall("set_ai_posture", {"posture": "offensive", "theater": "pol"})).ok
    assert adapter.read_state().theater_postures == {"POL": "offensive"}
