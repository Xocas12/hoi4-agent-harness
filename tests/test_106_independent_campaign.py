"""#106: the independent campaign (`--control llm`): the model plays the country itself.

Fake game only: nothing here touches the real game, sends input or calls a model. The mode
is not verified against the live game."""

from __future__ import annotations

import json

import pytest

from campaign_helpers import LazyClient, build_env, campaign_config, say
from hoi4_harness.agent.hybrid import describe
from hoi4_harness.agent.policy import CampaignPacing, Policy
from hoi4_harness.agent.prompts import system_for
from hoi4_harness.campaign.runner import CampaignStartError, run_campaign
from hoi4_harness.cli import _config_from_args, build_parser
from hoi4_harness.types import GameState, ResearchSlot


class Recorder(LazyClient):
    def __init__(self, script=None, nap=30):
        super().__init__(script=script, nap=nap)
        self.briefs, self.tools = [], []

    def _policy(self, messages, tools):
        self.briefs.append(next((m.text for m in reversed(messages) if m.role == "user" and m.text), ""))
        self.tools.append({t.name for t in (tools or [])})
        return super()._policy(messages, tools)

    def complete(self, system, messages, tools=None, max_tokens=None):
        self.tools.append({t.name for t in (tools or [])})
        return super().complete(system, messages, tools, max_tokens)


def play(tmp_path, *, until="1936-04-01", client=None, fake_hook=None, **options):
    config = campaign_config(tmp_path, campaign_until=until, **options)
    env = build_env(config)
    if fake_hook:
        fake_hook(env.adapter.fake_game)
    client = client or Recorder()
    _, summary = run_campaign(config, planner=client, env=env)
    return summary, client, env.adapter.fake_game, config


def records(tmp_path):
    return [json.loads(x) for x in
            (tmp_path / "run" / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]


def test_106_the_default_is_still_the_game_ai(tmp_path):
    _, client, fake, config = play(tmp_path)
    assert config.campaign_control == "game_ai"
    assert config.operational_control == "game_ai" and config.campaign_human_ai
    assert fake.human_ai is True
    assert any("set_ai_directive" in t for t in client.tools)


def test_106_llm_mode_does_not_hand_the_country_to_the_game_ai(tmp_path):
    summary, _, fake, config = play(tmp_path, campaign_control="llm")
    assert summary["reached_end_date"]
    assert config.operational_control == "llm" and not config.campaign_human_ai
    assert config.reflex_enabled and config.guidance == "world-conquest-llm"
    assert fake.human_ai is False                      # human_ai was never toggled on
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "independent mode" in log and "handing GER" not in log


def test_106_the_model_gets_the_hands_on_actions_and_not_the_directives(tmp_path):
    script = [say("try both", ("set_ai_directive", {"directive": "invade", "target": "POL"}),
                  ("set_national_focus", {"focus_id": "industrial_effort"}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    _, client, _, _ = play(tmp_path, client=Recorder(script=script), campaign_control="llm")
    offered = set().union(*client.tools)
    assert {"set_national_focus", "queue_construction", "take_decision",
            "hire_advisor"} <= offered
    assert not offered & {"set_ai_directive", "set_ai_posture", "clear_ai_directives",
                          "delegate_army_to_ai", "set_game_speed"}
    results = {}
    for record in records(tmp_path):
        for result in record.get("results", []):
            results[result["name"]] = result
    assert results["set_ai_directive"]["is_error"]                   # refused, not applied
    assert "set_national_focus" in results


def test_106_the_model_is_woken_when_the_focus_is_idle(tmp_path):
    _, client, _, _ = play(tmp_path, until="1936-04-01", campaign_control="llm")
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "wake: no national focus is running" in log
    # the game-AI campaign never wakes for it: the AI picks the focus
    other = tmp_path / "other"
    other.mkdir()
    play(other, until="1936-04-01")
    assert "no national focus" not in (other / "run" / "campaign.log").read_text(encoding="utf-8")


def idle_state(**fields):
    base = dict(country="GER", date="1936-03-01", national_focus="x",
                research=[ResearchSlot(index=1, technology=None)])
    base.update(fields)
    state = GameState(**base)
    state.unknown_fields = []
    return state


PACING = CampaignPacing(peace_days=30, war_days=14, wake_gap_days=7)


def test_106_an_idle_research_slot_wakes_the_model_only_when_it_can_fill_it():
    can = Policy(campaign=PACING, wake_on_free_research_slot=True,
                 hands_on=frozenset({"start_research", "set_national_focus"}))
    decision = can.should_wake(idle_state(), days_since_planner=8)
    assert decision.wake and "research slot" in decision.reason
    cannot = Policy(campaign=PACING, wake_on_free_research_slot=True,
                    hands_on=frozenset({"set_national_focus"}))
    assert not cannot.should_wake(idle_state(), days_since_planner=8).wake
    # not inside the wake gap, and never in the game-AI campaign (hands_on None)
    assert not can.should_wake(idle_state(), days_since_planner=3).wake
    assert not Policy(campaign=PACING, wake_on_free_research_slot=True).should_wake(
        idle_state(), days_since_planner=8).wake


def test_106_a_finished_focus_and_empty_production_wake_the_model():
    policy = Policy(campaign=PACING, wake_on_no_focus=True,
                    hands_on=frozenset({"set_national_focus", "set_production"}))
    state = idle_state(national_focus=None, research=[])
    assert "no national focus" in policy.should_wake(state, days_since_planner=8).reason
    state = idle_state(research=[], military_factories=5, production=[])
    assert "production" in policy.should_wake(state, days_since_planner=8).reason


def test_106_the_reflex_layer_is_back_on(tmp_path):
    config = campaign_config(tmp_path, campaign_control="llm")
    assert config.reflex_enabled
    assert not campaign_config(tmp_path).reflex_enabled


def test_106_the_prompt_describes_the_mode_it_is(tmp_path):
    system = system_for(campaign_config(tmp_path, campaign_control="llm"))
    assert "NOT running your country" in system and "stand where they are" in system
    assert "set_ai_directive" not in system.split("Your objective")[0]
    assert "you command directly" not in system.lower()
    assert "NOT running it" in describe("llm", campaign=True)
    game = system_for(campaign_config(tmp_path))
    assert "set_ai_directive" in game and "NOT running your country" not in game


def test_106_a_country_the_ai_already_runs_is_refused(tmp_path):
    config = campaign_config(tmp_path, campaign_control="llm")
    env = build_env(config)
    env.adapter.fake_game.human_ai = True               # an earlier game_ai run left it on
    env.adapter.fake_game.flags.add("llmb_harness")
    env.adapter.fake_game.emit_state()
    with pytest.raises(CampaignStartError, match="ai=1"):
        run_campaign(config, planner=Recorder(), env=env)
    assert env.adapter.fake_game.human_ai is True      # and nothing was toggled


def test_106_an_unknown_control_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="campaign_control"):
        campaign_config(tmp_path, campaign_control="auto")


def test_106_the_flag_sets_it():
    args = build_parser().parse_args(["play", "--campaign", "--control", "llm"])
    config = _config_from_args(args)
    assert config.campaign_control == "llm" and not config.campaign_human_ai
