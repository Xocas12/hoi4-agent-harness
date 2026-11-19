# ruff: noqa: E501 -- the log lines are verbatim from the game and stay unwrapped.
"""The brief built from a real 1.19.5.0 game.log, captured in the second live session.

Every line below is from the game, Sweden, April 1937 -- daily state, the focus
and research probes, a target, and two theater postures -- verbatim except the
schema version, bumped from the v2 the game printed when v3 added a field. The brief it
produced in that session printed "(Noned left)" for a focus whose remaining days
the log reader cannot see, and a bare "defensive GER" for a theater event.
"""

from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.observation import render_full

LIVE_LOG = """\
[12:23:59][1937.04.12.21]: LLMB|v3|state|date= 21:00, 12 April, 1937|tag=SWE|pp=881.1735|stab=82.639|ws=0|civ=20|mil=3|doc=3|mp=32980|techs=32|built_civ=0|built_mil=0|built_dock=0|built_infra=0
[12:23:59][1937.04.12.21]: LLMB|v3|army|tag=SWE|divisions=12|delegated=0
[12:23:59][1937.04.12.21]: LLMB|v3|focus|tag=SWE|id=SWE_defense_act
[12:23:59][1937.04.12.21]: LLMB|v3|research|tag=SWE|id=infantry_weapons2
[12:24:55][1937.04.18.03]: LLMB|v3|evt|tag=SWE|kind=13|detail=GER
[12:25:31][1937.04.18.03]: LLMB|v3|evt|tag=SWE|kind=15|detail=defensive GER
[12:25:46][1937.04.18.03]: LLMB|v3|evt|tag=SWE|kind=13|detail=ETH
[12:26:03][1937.04.18.03]: LLMB|v3|evt|tag=SWE|kind=15|detail=offensive ETH
[12:26:05][1937.04.18.21]: LLMB|v3|state|date= 21:00, 18 April, 1937|tag=SWE|pp=886.7|stab=82.63|ws=0|civ=21|mil=3|doc=3|mp=32980|techs=32|built_civ=0|built_mil=0|built_dock=0|built_infra=0
[12:26:05][1937.04.18.21]: LLMB|v3|army|tag=SWE|divisions=12|delegated=0
[12:26:05][1937.04.18.21]: LLMB|v3|focus|tag=SWE|id=SWE_defense_act
[12:26:05][1937.04.18.21]: LLMB|v3|research|tag=SWE|id=infantry_weapons2
[12:26:06][1937.04.19.21]: LLMB|v3|state|date= 21:00, 19 April, 1937|tag=SWE|pp=887.6|stab=82.63|ws=0|civ=21|mil=3|doc=3|mp=32980|techs=32|built_civ=0|built_mil=0|built_dock=0|built_infra=0
"""


def test_a_focus_and_research_with_unknown_days_render_without_a_day_count(tmp_path):
    log = tmp_path / "game.log"
    log.write_text(LIVE_LOG, encoding="utf-8")
    state = LogTailAdapter(log).read_state()
    brief = render_full(state)
    assert "Focus: SWE_defense_act" in brief
    assert "Research: infantry_weapons2" in brief
    assert "None" not in brief
    assert "Noned" not in brief


def test_two_theaters_hold_different_postures_in_a_real_log(tmp_path):
    log = tmp_path / "game.log"
    log.write_text(LIVE_LOG, encoding="utf-8")
    state = LogTailAdapter(log).read_state()
    assert state.theater_postures == {"GER": "defensive", "ETH": "offensive"}
    assert "ETH: offensive | GER: defensive" in render_full(state)


def test_theater_events_are_labelled(tmp_path):
    from hoi4_harness.adapters.logtail import _event_text

    assert _event_text("theater_posture", "defensive GER") == "theater posture: defensive GER"
