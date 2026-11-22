"""The net political power trend: measured from the daily state lines, so a model need not
guess one (it invented a "-13/day bleed from occupation", overnight campaign 2026-11-22)."""

from __future__ import annotations

from hoi4_harness.adapters.logtail import SCHEMA_VERSION, LogTailAdapter
from hoi4_harness.observation.render import render_full


def _line(day: int, pp: float) -> str:
    return (f"[messagehandler.cpp:290]: LLMB|v{SCHEMA_VERSION}|state|date=1941.5.{day}|tag=GER|"
            f"pp={pp}|stab=100|ws=100|civ=93|mil=126|doc=14|mp=500000\n")


def test_the_trend_is_net_pp_a_day_and_the_brief_says_so(tmp_path):
    log = tmp_path / "game.log"
    log.write_text("".join(_line(d, 40 - 2 * (d - 1)) for d in range(1, 12)), encoding="utf-8")
    state = LogTailAdapter(log, poll_seconds=0, country="GER").read_state()
    assert state.pp_trend_days == 10 and abs(state.pp_per_day + 2.0) < 1e-9
    assert "PP 20 (net -2.0/day over 10 days, after what the AI spent)" in render_full(state)


def test_too_few_days_give_no_trend(tmp_path):
    log = tmp_path / "game.log"
    log.write_text(_line(1, 10) + _line(2, 11), encoding="utf-8")
    state = LogTailAdapter(log, poll_seconds=0, country="GER").read_state()
    assert state.pp_per_day is None and "net" not in render_full(state).splitlines()[1]
