"""Schema v5: the ``ai=`` read-back on the state line and the weekly ``camp`` line."""

from __future__ import annotations

from pathlib import Path

from hoi4_harness.adapters.logtail import LogTailAdapter, parse_line

STAMP = "[10:00:00][1937.03.05.21]"
STATE = f"{STAMP}: LLMB|v5|state|date=x|tag=GER|pp=42|stab=63|ws=18|civ=12|mil=5|doc=4|mp=1|ai={{ai}}\n"
CAMP = (f"{STAMP}: LLMB|v5|camp|tag=GER|ostates=31|cstates=33|divs=48|enemies=2|capit=1|infac=1"
        "|leader=1|fmem=4|exist=71|indep=60|wstates=1081\n")


def _read(tmp_path: Path, text: str):
    log = tmp_path / "game.log"
    log.write_text(text, encoding="utf-8")
    return LogTailAdapter(log, country="GER").read_state()


def test_the_state_line_says_whether_the_game_ai_runs_the_country(tmp_path):
    assert _read(tmp_path, STATE.format(ai=1)).ai_controlled is True
    assert _read(tmp_path, STATE.format(ai=0)).ai_controlled is False


def test_ai_is_unknown_when_the_line_does_not_carry_it(tmp_path):
    line = f"{STAMP}: LLMB|v5|state|date=x|tag=GER|pp=42|stab=63|ws=18|civ=12|mil=5|doc=4|mp=1\n"
    assert _read(tmp_path, line).ai_controlled is None


def test_the_campaign_line_becomes_the_conquest_numbers(tmp_path):
    state = _read(tmp_path, STATE.format(ai=1) + CAMP)
    stats = state.campaign
    assert stats is not None
    assert (stats.owned_states, stats.controlled_states, stats.divisions) == (31, 33, 48)
    assert (stats.enemies, stats.capitulated) == (2, 1)
    assert stats.in_faction is True and stats.faction_leader is True and stats.faction_members == 4
    assert (stats.countries_existing, stats.countries_independent, stats.world_states) == (71, 60, 1081)
    assert stats.date == "1937-03-05"
    assert round(stats.state_share, 4) == round(31 / 1081, 4)


def test_a_token_the_game_did_not_expand_is_unknown_not_zero(tmp_path):
    line = CAMP.replace("ostates=31", "ostates=[?llmb_c_owned]")
    stats = _read(tmp_path, STATE.format(ai=0) + line).campaign
    assert stats.owned_states is None and stats.state_share is None and stats.divisions == 48


def test_a_v4_line_is_refused_not_misread():
    try:
        parse_line("[x]: LLMB|v4|state|tag=GER|pp=1")
    except ValueError as exc:
        assert "v5" in str(exc)
    else:
        raise AssertionError("a v4 line must not be read by a v5 harness")


def test_the_harness_marked_event_is_info(tmp_path):
    state = _read(tmp_path, STATE.format(ai=0)
                  + f"{STAMP}: LLMB|v5|evt|tag=GER|kind=16|detail=0\n")
    assert [e.kind for e in state.events] == ["harness_marked"]
    assert state.events[0].severity == "info"
