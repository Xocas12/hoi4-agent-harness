"""A whole campaign against the fake game: the start, the stops, resume, the report.

The fake models ``human_ai`` and the ``llmb_harness`` flag (``fakegame.py``) and a crude
game AI (``fakecampaign.py``), so what is proved here is the *harness* around the AI
doing what it should. What only the real game can prove (does ``human_ai`` flip
``is_ai``, does the AI act on a directive) is in docs/campaign.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from campaign_helpers import LazyClient, build_env, campaign_config, invade, say
from hoi4_harness.agent.llm.base import Usage
from hoi4_harness.campaign.runner import CampaignStartError, run_campaign
from hoi4_harness.campaign.setup import BACKSTOP_TURNS


def play(tmp_path, script=(), *, nap=60, usage=None, resume=False, env=None, **overrides):
    config = campaign_config(tmp_path, **overrides)
    env = env or build_env(config)
    client = LazyClient(script=list(script), nap=nap, usage=usage)
    report, summary = run_campaign(config, planner=client, env=env, resume=resume)
    return config, env, env.adapter.fake_game, report, summary, client


def records(tmp_path, kind):
    path = tmp_path / "run" / "transcript.jsonl"
    return [r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())
            if r["kind"] == kind]


# --- the start -------------------------------------------------------------------


def test_the_start_marks_the_player_toggles_human_ai_once_and_proves_it_took(tmp_path):
    config, env, fake, report, summary, _ = play(tmp_path)
    assert fake.human_ai and "llmb_harness" in fake.flags
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "handing GER to the game's AI" in log and "human_ai took: the state line reads ai=1" in log
    # one toggle only: the file sent has the mark, human_ai and the refresh lines.
    sent = [run for run in fake.runs if "human_ai" in run]
    assert len(sent) == 1 and sent[0][0].endswith("llm_bridge_mark_harness")
    assert sent[0][1] == "human_ai"


def test_telemetry_keeps_flowing_under_human_ai_because_of_the_flag(tmp_path):
    config, env, fake, report, summary, _ = play(tmp_path)
    assert summary["reached_end_date"] and summary["end_date"] >= "1936-09-01"
    rows = [json.loads(ln) for ln in
            (tmp_path / "run" / "campaign_progress.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) > 5 and all(r["ai"] is True for r in rows)
    assert rows[-1]["date"] == summary["end_date"]


def test_a_start_that_cannot_prove_human_ai_took_stops_and_says_so(tmp_path, monkeypatch):
    config = campaign_config(tmp_path)
    env = build_env(config)
    fake = env.adapter.fake_game
    # The mark takes, the toggle silently does not: the state line stays ai=0.
    original = fake._command

    def command(line):
        if line.split()[0] == "human_ai":
            return None
        return original(line)

    monkeypatch.setattr(fake, "_command", command)
    with pytest.raises(CampaignStartError, match="did not take"):
        run_campaign(config, planner=LazyClient(), env=env)
    assert not fake.human_ai


def test_without_an_ai_field_the_harness_refuses_to_toggle_a_toggle(tmp_path, monkeypatch):
    config = campaign_config(tmp_path)
    env = build_env(config)
    fake = env.adapter.fake_game
    monkeypatch.setattr(fake, "emit_state", lambda: fake._write(
        fake._llmb("state", date="x", tag="GER", pp=1, stab=50, ws=10, civ=1, mil=1, doc=0,
                   owned=2, mp=1)))
    fake.log_path.write_text("", encoding="utf-8")      # drop the line the fake wrote on load
    fake.emit_state()
    with pytest.raises(CampaignStartError, match="no ai= field"):
        run_campaign(config, planner=LazyClient(), env=env)
    assert not fake.human_ai


def test_a_campaign_refuses_a_dry_run_and_an_uncapped_priced_model(tmp_path):
    with pytest.raises(CampaignStartError, match="--live"):
        run_campaign(campaign_config(tmp_path, dry_run=True), planner=LazyClient())
    config = campaign_config(tmp_path)
    config.planner.provider = "anthropic"
    with pytest.raises(CampaignStartError, match="--max-usd"):
        run_campaign(config)


def test_a_campaign_never_presses_escape_or_leaves_the_game_menu_open(tmp_path):
    config, env, fake, report, summary, _ = play(tmp_path, [invade("POL")])
    assert not any("escape" in key for key in fake.keylog)
    assert not fake.menu_open and not fake.console_open
    assert set(fake.keylog) <= {"grave", "enter", "space", "backspace"} | {
        k for k in set(fake.keylog) if len(k.split("+")[-1]) == 1 or k.split("+")[-1] in
        ("space", "-")}


# --- what the model can do ----------------------------------------------------------


def test_the_model_is_offered_only_what_the_ai_will_not_undo(tmp_path):
    config = campaign_config(tmp_path)
    env = build_env(config)
    offered = env.allowed_actions
    assert {"set_ai_directive", "set_ai_posture", "clear_ai_directives", "advance_time",
            "note", "take_decision", "hire_advisor"} <= offered
    for gone in ("set_national_focus", "start_research", "queue_construction", "set_production",
                 "delegate_army_to_ai", "set_game_speed", "move_division"):
        assert gone not in offered


def test_a_directive_war_is_fought_and_won_and_the_territory_shows_it(tmp_path):
    config, env, fake, report, summary, client = play(
        tmp_path, [invade("POL", text="Poland is the weakest neighbour; the AI can take it.")],
        campaign_until="1937-06-01")
    assert fake.ai.capitulated and fake.ai.capitulated[0][1] == "POL"
    rows = [json.loads(ln) for ln in
            (tmp_path / "run" / "campaign_progress.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["owned_states"] == 30 and rows[-1]["owned_states"] > 30
    assert rows[-1]["countries_independent"] < rows[0]["countries_independent"]
    events = [json.loads(ln) for ln in
              (tmp_path / "run" / "campaign_events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["kind"] for e in events if e["kind"] != "harness_marked"] == [
        "war_declared", "capitulation", "war_ended"]
    brief = next(r["brief"] for r in records(tmp_path, "observe") if "CAMPAIGN" in r["brief"]
                 and "Since your last review" in r["brief"])
    assert "war declared" in brief and "You hold" in brief


def test_the_models_review_interval_paces_the_wakes(tmp_path):
    config, env, fake, report, summary, client = play(
        tmp_path, [invade("POL", days=60)], nap=90, campaign_until="1936-10-01")
    observes = records(tmp_path, "observe")
    # Day 0, then the war's declaration (critical) ~day 15, then a nap of 90 days at a time.
    dates = [o["date"] for o in observes]
    assert dates[0] == "1936-01-01" and len(dates) < 12
    assert any("critical event" in o["wake_reason"] for o in observes)


def test_a_string_of_critical_events_inside_the_gap_is_one_wake(tmp_path):
    from hoi4_harness.agent.policy import CampaignPacing, Policy
    from hoi4_harness.types import GameEvent, GameState

    policy = Policy(campaign=CampaignPacing(wake_gap_days=7))
    state = GameState(events=[GameEvent("capitulation", "x", "critical")])
    assert policy.should_wake(state, 3).wake is False
    assert policy.should_wake(state, 7).wake is True


# --- the stops ------------------------------------------------------------------------


def test_the_budget_stops_the_run_with_the_game_left_paused(tmp_path):
    config, env, fake, report, summary, _ = play(
        tmp_path, usage=Usage(1_000_000, 0), nap=30, campaign_until="1940-01-01", max_usd=10.0)
    assert summary["stopped_reason"].startswith("budget: spend ceiling")
    assert not summary["reached_end_date"]
    # $4 a call against a $10 cap: the third call would pass it, so there were two.
    assert summary["spend"]["llm_calls"] <= 3 and summary["spend"]["usd"] <= 12.0
    assert fake.paused


def test_the_hard_cap_is_never_passed_when_a_reserve_is_kept(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1940-01-01")
    config.budget.max_usd, config.budget.reserve_usd = 10.0, 4.5
    env = build_env(config)
    client = LazyClient(nap=30, usage=Usage(1_000_000, 0))
    _, summary = run_campaign(config, planner=client, env=env)
    assert summary["spend"]["usd"] <= 10.0 and summary["stopped_reason"].startswith("budget")


def test_coasting_on_a_spent_budget_plays_to_the_end_with_no_more_calls(tmp_path):
    config = campaign_config(tmp_path, campaign_on_budget="coast", campaign_until="1936-10-01")
    config.budget.max_usd = 5.0
    env = build_env(config)
    _, summary = run_campaign(config, planner=LazyClient(nap=30, usage=Usage(1_000_000, 0)),
                              env=env)
    assert summary["reached_end_date"] and summary["spend"]["usd"] <= 8.0
    assert summary["spend"]["llm_calls"] <= 2


def test_the_run_stops_at_the_end_date_give_or_take_the_pause_and_says_it_got_there(tmp_path):
    config, env, fake, report, summary, _ = play(tmp_path, nap=90, campaign_until="1936-08-17")
    # The last turn is cut to the days left; the pause key lands a day late at most.
    assert "1936-08-17" <= summary["end_date"] <= "1936-08-19" and summary["reached_end_date"]
    assert report.turns < BACKSTOP_TURNS


def test_a_dead_game_ends_the_run_cleanly_with_a_reason(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1940-01-01")
    env = build_env(config)
    fake = env.adapter.fake_game
    tick = fake.tick_day

    def tick_then_die():
        tick()
        if fake.date == "1936-04-10":
            fake.kill()

    fake.tick_day = tick_then_die
    _, summary = run_campaign(config, planner=LazyClient(nap=60), env=env)
    assert "process is gone" in summary["stopped_reason"]
    assert not summary["reached_end_date"]
    ends = records(tmp_path, "run_end")
    assert ends and "process is gone" in ends[-1]["stopped_reason"]
    assert records(tmp_path, "game_lost")


def test_a_silent_log_ends_the_run_cleanly_with_a_reason(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1940-01-01", campaign_silence_minutes=10)
    env = build_env(config)
    fake = env.adapter.fake_game
    tick = fake.tick_day

    def tick_then_lose_the_flag():
        tick()
        if fake.date == "1936-03-05":
            fake.flags.discard("llmb_harness")      # the mod stops reporting for the player

    fake.tick_day = tick_then_lose_the_flag
    _, summary = run_campaign(config, planner=LazyClient(nap=90), env=env)
    assert "game.log has not grown" in summary["stopped_reason"]
    assert summary["end_date"] < "1936-06-01" or summary["end_date"]


def test_the_ai_being_switched_off_is_noticed_and_stops_the_run(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1940-01-01")
    env = build_env(config)
    fake = env.adapter.fake_game
    tick = fake.tick_day

    def tick_then_toggle():
        tick()
        if fake.date == "1936-05-01":
            fake.human_ai = False

    fake.tick_day = tick_then_toggle
    _, summary = run_campaign(config, planner=LazyClient(nap=30), env=env)
    assert "no longer running the country" in summary["stopped_reason"]


# --- the focus guard ---------------------------------------------------------------------


def test_a_game_that_loses_focus_is_waited_for_and_nothing_is_typed_meanwhile(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1936-12-01")
    env = build_env(config)
    fake = env.adapter.fake_game
    seen = {}
    tick = fake.tick_day

    def tick_and_lose_focus():
        tick()
        if fake.date == "1936-03-01" and "keys" not in seen:
            seen["keys"] = len(fake.keylog)
            fake.lose_focus(600)

    fake.tick_day = tick_and_lose_focus
    send = fake.send

    def watched_send(scan, up):
        if not fake.focused:
            seen["typed_while_away"] = True
        send(scan, up)

    env.adapter.writer.router.driver._keys()._send = watched_send
    _, summary = run_campaign(config, planner=LazyClient(nap=30), env=env)
    assert summary["reached_end_date"]
    patient = env.adapter.patient_focus
    assert patient.episodes >= 1 and patient.waited >= 60
    assert "typed_while_away" not in seen
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "waiting for the game window to take the focus back" in log
    assert "back in front" in log


def test_a_window_that_never_comes_back_ends_the_run_with_a_reason(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1938-12-01", campaign_focus_wait_minutes=5)
    env = build_env(config)
    fake = env.adapter.fake_game
    tick = fake.tick_day

    def tick_and_lose_focus():
        tick()
        if fake.date == "1936-02-10":
            fake.lose_focus()

    fake.tick_day = tick_and_lose_focus
    _, summary = run_campaign(config, planner=LazyClient(nap=30, script=[invade("POL")]), env=env)
    assert "has not come back to the front" in summary["stopped_reason"]


# --- recovery saves ---------------------------------------------------------------------------


def test_a_recovery_save_is_asked_for_every_n_months(tmp_path):
    config, env, fake, report, summary, _ = play(
        tmp_path, nap=30, campaign_until="1936-08-15", campaign_save_months=2)
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert log.count("recovery save asked for") == 3      # 01-01, 03-01, 05-30
    assert len(fake.saves_written) >= 3


# --- resume -------------------------------------------------------------------------------


def test_resume_after_a_harness_crash_carries_on_without_toggling_human_ai_back_off(
        tmp_path, monkeypatch):
    from hoi4_harness.agent import loop as loop_module

    config = campaign_config(tmp_path, campaign_until="1937-06-01")
    config.budget.max_usd = 100.0
    env = build_env(config)
    fake = env.adapter.fake_game
    calls = {"n": 0}
    real = loop_module.AgentLoop._planner_turn

    def crashy(self, reason):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("the harness died")
        return real(self, reason)

    monkeypatch.setattr(loop_module.AgentLoop, "_planner_turn", crashy)
    with pytest.raises(RuntimeError, match="harness died"):
        run_campaign(config, planner=LazyClient([invade("POL")], nap=30), env=env)
    assert records(tmp_path, "crash")
    spent_before = fake_spend(tmp_path)
    assert fake.human_ai

    monkeypatch.setattr(loop_module.AgentLoop, "_planner_turn", real)
    env2 = build_env(config, fake=fake)
    _, summary = run_campaign(config, planner=LazyClient(nap=30), env=env2, resume=True)
    assert summary["reached_end_date"] and fake.human_ai, "resume must not toggle the AI off"
    assert len([run for run in fake.runs if "human_ai" in run]) == 1
    assert summary["spend"]["usd"] > spent_before > 0, "the spend carried over, not reset"
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "campaign resumed" in log and "human_ai left as it is" in log
    assert "CRASH: RuntimeError" in log
    # The directive raised before the crash still stands: the war is fought and won.
    assert fake.ai.capitulated


def fake_spend(tmp_path) -> float:
    spent = 0.0
    for rec in records(tmp_path, "llm"):
        usage = rec["usage"]
        spent += (usage["input_tokens"] * 4.0 + usage["output_tokens"] * 20.0) / 1e6
    return spent


def test_resume_works_with_the_save_reader_too(tmp_path):
    config = campaign_config(tmp_path, headless_reader="log+save", campaign_until="1936-09-01")
    config.turns = 4        # interrupted by the turn backstop, as a stop would
    env = build_env(config)
    fake = env.adapter.fake_game
    _, first = run_campaign(config, planner=LazyClient([invade("POL")], nap=30), env=env)
    assert not first["reached_end_date"]
    config.turns = 5000
    env2 = build_env(config, fake=fake)
    _, second = run_campaign(config, planner=LazyClient(nap=30), env=env2, resume=True)
    assert second["reached_end_date"] and fake.human_ai
    assert second["turns"] > first["turns"]
    assert len([run for run in fake.runs if "human_ai" in run]) == 1


def test_a_game_restarted_mid_campaign_is_handed_to_the_ai_again(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1936-08-01")
    config.turns = 3
    env = build_env(config)
    fake = env.adapter.fake_game
    run_campaign(config, planner=LazyClient(nap=30), env=env)
    # The game was restarted: a fresh log, the player human again, no flag.
    fake.human_ai = False
    fake.flags.clear()
    fake.log_path.write_text("", encoding="utf-8")
    fake.emit_state()
    config.turns = 5000
    env2 = build_env(config, fake=fake)
    _, summary = run_campaign(config, planner=LazyClient(nap=30), env=env2, resume=True)
    assert fake.human_ai and "llmb_harness" in fake.flags and summary["reached_end_date"]


def test_the_cli_refuses_a_live_campaign_with_no_cap(tmp_path, monkeypatch, capsys):
    from hoi4_harness.cli import main

    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    code = main(["play", "--campaign", "--live", "--provider", "anthropic", "--country", "GER",
                 "--run-dir", str(tmp_path / "x"), "--headless"])
    assert code == 2
    assert "--max-usd" in capsys.readouterr().err


def test_say_is_a_tool_use_response():
    assert say("x", ("note", {"text": "t"})).tool_calls[0].name == "note"
