"""The fake game under ``human_ai``: the telemetry gate, the flag, and a crude AI.

The fake is only as good as what it models, so the thing worth pinning is that it
models the *hazard*: with ``human_ai`` on and no ``llmb_harness`` flag the daily log
goes silent, exactly what the mod's old ``is_ai = no`` limits would have done in the
game, and that the flag (set the way the harness sets it) brings it back.
"""

from __future__ import annotations

import pytest

from hoi4_harness.adapters.keys import ScanCodeKeys
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.fakegame import FakeGame, demo_index


@pytest.fixture()
def game(tmp_path):
    return FakeGame(tmp_path / "user", country="GER", index=demo_index(tmp_path / "playset"),
                    variance=0)


@pytest.fixture()
def keys(game):
    return ScanCodeKeys(send=game.send, sleep=game.sleep)


def run_lines(game, keys, *lines):
    (game.user_dir / "llmb_turn.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    keys.chord("grave")
    keys.type_text("run llmb_turn.txt")
    keys.chord("enter")
    keys.chord("grave")


def llmb_lines(game, kind):
    return [ln for ln in game.log_path.read_text(encoding="utf-8").splitlines()
            if f"LLMB|v5|{kind}|" in ln]


def test_human_ai_is_a_toggle(game, keys):
    run_lines(game, keys, "human_ai")
    assert game.human_ai
    run_lines(game, keys, "human_ai")
    assert not game.human_ai


def test_under_human_ai_the_log_goes_silent_without_the_flag(game, keys):
    run_lines(game, keys, "human_ai")
    before = len(llmb_lines(game, "state"))
    game.run_days(5)
    assert len(llmb_lines(game, "state")) == before


def test_the_flag_brings_the_daily_lines_back(game, keys):
    run_lines(game, keys, "effect GER llm_bridge_mark_harness", "human_ai")
    before = len(llmb_lines(game, "state"))
    game.run_days(5)
    assert len(llmb_lines(game, "state")) == before + 5
    assert all("|ai=1|" in ln for ln in llmb_lines(game, "state")[-5:])


def test_the_flag_can_be_set_with_an_eval_effect_too(game, keys):
    run_lines(game, keys, "eval_effect GER = { set_country_flag = llmb_harness }", "human_ai")
    assert game.visible


def test_a_human_country_reports_with_no_flag(game):
    before = len(llmb_lines(game, "state"))
    game.run_days(3)
    assert not game.human_ai and len(llmb_lines(game, "state")) == before + 3
    assert "|ai=0|" in llmb_lines(game, "state")[-1]


def test_the_campaign_line_comes_weekly_and_on_demand(game, keys):
    game.run_days(14)
    assert len(llmb_lines(game, "camp")) == 2
    run_lines(game, keys, "effect GER llm_bridge_emit_campaign")
    assert len(llmb_lines(game, "camp")) == 3
    state = LogTailAdapter(game.log_path, country="GER").read_state()
    assert state.campaign is not None and state.campaign.owned_states == 30
    assert state.campaign.world_states > state.campaign.owned_states


def test_the_ai_declares_the_war_a_directive_asks_for_and_wins_it(game, keys):
    run_lines(game, keys, "effect GER llm_bridge_mark_harness", "human_ai",
              "effect GER llm_bridge_select_POL", "effect GER llm_bridge_raise_invade")
    game.run_days(10)
    assert not game.world.state.wars, "the AI waits before it declares"
    game.run_days(10)
    assert [w.against for w in game.world.state.wars] == ["POL"]
    game.run_days(400)
    assert game.ai.capitulated and game.ai.capitulated[0][1] == "POL"
    assert not game.world.state.wars
    assert game.ai.player_states > 30
    assert "POL" not in game.ai.countries
    assert any("kind=2" in ln for ln in llmb_lines(game, "evt"))


def test_without_a_directive_the_ai_stays_at_peace(game, keys):
    run_lines(game, keys, "effect GER llm_bridge_mark_harness", "human_ai")
    game.run_days(120)
    assert not game.world.state.wars and not game.ai.capitulated


def test_an_offensive_posture_wins_faster(tmp_path):
    def days_to_win(posture):
        g = FakeGame(tmp_path / str(posture) / "user", country="GER", variance=0)
        g.human_ai, g.flags = True, {"llmb_harness"}
        g.directives, g.global_posture = ["invade POL"], posture
        for day in range(1, 2000):
            g.tick_day()
            if g.ai.capitulated:
                return day
        raise AssertionError("never won")

    assert days_to_win("offensive") < days_to_win(None) < days_to_win("defensive")


def test_the_ai_researches_and_builds_for_itself(game, keys):
    run_lines(game, keys, "human_ai")
    game.run_days(3)
    assert all(slot.technology for slot in game.world.state.research)


def test_a_dead_game_writes_nothing_and_does_not_tick(game):
    game.run_days(2)
    size = game.log_path.stat().st_size
    game.kill()
    game.paused = False
    game.sleep(50)
    assert game.log_path.stat().st_size == size


def test_focus_comes_back_after_a_while(game):
    game.lose_focus(60)
    assert not game.focus_check("x").focused
    game.sleep(30)
    assert not game.focus_check("x").focused
    game.sleep(31)
    assert game.focus_check("x").focused


def test_the_harness_flag_can_be_cleared_again(game, keys):
    run_lines(game, keys, "effect GER llm_bridge_mark_harness", "human_ai")
    assert game.visible
    run_lines(game, keys, "effect GER llm_bridge_unmark_harness")
    assert not game.visible and "llmb_harness" not in game.flags
