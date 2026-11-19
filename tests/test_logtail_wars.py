"""Schema v4 ``war`` lines: who the player is at war with, read from game.log.

The mod prints one line per enemy per day and none on a day of peace, so a war
is "an enemy on the last complete day", it starts when a name appears and ends
when it stops appearing. The reader turns that into ``state.wars`` and, between
two complete days, into the events the wake rule reacts to.
"""

from __future__ import annotations

from pathlib import Path

from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.agent.policy import Policy
from hoi4_harness.observation.render import render_full


def _day(day: int, wars: dict[str, tuple[int, int, int]] | None = None, extra: str = "") -> str:
    stamp = f"[10:00:00][1939.09.{day:02d}.21]"
    lines = [f"{stamp}: LLMB|v4|state|date=x|tag=SWE|pp=42|stab=63|ws=18|civ=12|mil=5|doc=4|mp=1\n"]
    for enemy, (divs, border, surr) in (wars or {}).items():
        lines.append(f"{stamp}: LLMB|v4|war|tag=SWE|enemy={enemy}|divs={divs}|border={border}"
                     f"|surr={surr}\n")
    return "".join(lines) + extra


def _read(tmp_path: Path, *days: str):
    log = tmp_path / "game.log"
    log.write_text("".join(days), encoding="utf-8")
    return LogTailAdapter(log, country="SWE").read_state()


def test_an_enemy_line_becomes_a_war_with_what_the_mod_saw(tmp_path):
    state = _read(tmp_path, _day(1, {"GER": (84, 1, 3)}), _day(2, {"GER": (90, 1, 4)}), _day(3))
    (war,) = state.wars
    assert (war.against, war.enemy_divisions, war.borders) == ("GER", 90, True)
    assert war.surrender_progress == 0.04
    assert state.known("wars")


def test_a_day_of_peace_is_known_and_empty(tmp_path):
    state = _read(tmp_path, _day(1), _day(2), _day(3))
    assert state.wars == [] and state.known("wars")


def test_wars_are_unknown_until_a_day_has_closed(tmp_path):
    state = _read(tmp_path, _day(1, {"GER": (84, 1, 3)}))
    assert not state.known("wars")


def test_a_war_already_on_at_the_start_is_not_news(tmp_path):
    state = _read(tmp_path, _day(1, {"GER": (84, 1, 3)}), _day(2, {"GER": (84, 1, 3)}), _day(3))
    assert [e for e in state.events if e.kind == "war_declared"] == []
    assert state.wars[0].since is None
    assert "already on when telemetry began" in render_full(state)


def test_a_war_that_appears_is_a_critical_event_and_records_its_date(tmp_path):
    state = _read(tmp_path, _day(1), _day(2, {"POL": (40, 1, 0)}), _day(3, {"POL": (40, 1, 0)}),
                  _day(4))
    (event,) = [e for e in state.events if e.kind == "war_declared"]
    assert event.severity == "critical" and "POL" in event.text
    assert state.wars[0].since == "1939-09-02"


def test_the_mods_own_declaration_is_not_repeated_by_the_war_line(tmp_path):
    declared = ("[10:00:00][1939.09.02.10]: LLMB|v4|evt|tag=SWE|other=POL|kind=1|detail=0\n")
    state = _read(tmp_path, _day(1), declared, _day(2, {"POL": (40, 1, 0)}), _day(3))
    assert len([e for e in state.events if e.kind == "war_declared"]) == 1


def test_peace_is_an_event_and_empties_the_list(tmp_path):
    state = _read(tmp_path, _day(1), _day(2, {"POL": (40, 1, 0)}), _day(3), _day(4))
    assert state.wars == []
    assert [e.kind for e in state.events if e.kind.startswith("war")] == ["war_declared",
                                                                         "war_ended"]


def test_the_wake_rule_reacts_to_a_war_the_log_showed(tmp_path):
    state = _read(tmp_path, _day(1), _day(2, {"POL": (40, 1, 0)}), _day(3, {"POL": (40, 1, 0)}))
    decision = Policy().should_wake(state, days_since_planner=1)
    assert decision.wake and decision.urgency == "critical"
    assert "POL" in decision.reason


def test_the_brief_says_what_the_enemy_is(tmp_path):
    state = _read(tmp_path, _day(1), _day(2, {"POL": (40, 0, 12)}), _day(3, {"POL": (40, 0, 12)}))
    brief = render_full(state)
    assert ("AT WAR with POL since 1939-09-02 (enemy 40 divisions, no shared border, "
            "surrender progress 12%)") in brief


def test_a_war_line_that_did_not_expand_is_a_missing_number_not_zero(tmp_path):
    day = _day(2, {"POL": (1, 1, 1)}).replace("divs=1", "divs=[?llmb_war_divs]")
    state = _read(tmp_path, _day(1), day, _day(3, {"POL": (1, 1, 1)}))
    assert state.wars[0].enemy_divisions is None


def test_the_mods_declaration_puts_the_enemy_on_the_list_before_any_war_line(tmp_path):
    declared = "[10:00:00][1939.09.02.10]: LLMB|v4|evt|tag=POL|other=SWE|kind=1|detail=0\n"
    state = _read(tmp_path, _day(1), declared)
    assert [(w.against, w.since) for w in state.wars] == [("POL", "1939-09-02")]
    assert "AT WAR with POL since 1939-09-02 (war score +0)" in render_full(state)
    # The closing of the day before the declaration must not take it off again.
    state = _read(tmp_path, _day(1), declared, _day(2, {"POL": (40, 1, 0)}))
    assert [w.against for w in state.wars] == ["POL"]
    assert state.wars[0].enemy_divisions is None or state.wars[0].enemy_divisions == 40


def test_a_war_line_shows_at_once_but_a_missing_one_waits_for_the_day_to_close(tmp_path):
    state = _read(tmp_path, _day(1), _day(2, {"POL": (40, 1, 0)}))
    assert [w.against for w in state.wars] == ["POL"]          # day 2 is still open
    state = _read(tmp_path, _day(1), _day(2, {"POL": (40, 1, 0)}), _day(3))
    assert [w.against for w in state.wars] == ["POL"]          # day 3 is open: day 2 had it
    state = _read(tmp_path, _day(1), _day(2, {"POL": (40, 1, 0)}), _day(3), _day(4))
    assert state.wars == []
