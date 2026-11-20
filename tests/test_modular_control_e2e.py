"""Modular control end to end, on the fake game, the fake army and the mock: a mixed map, a
no-model run, and what a campaign sends under each map. No real game, no input, no model."""

from __future__ import annotations

import json

import pytest

from campaign_helpers import build_env, campaign_config
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.auto.army import ArmyScript
from hoi4_harness.auto.base import AutoContext
from hoi4_harness.campaign.runner import CampaignStartError, run_campaign
from hoi4_harness.config import HarnessConfig
from hoi4_harness.control import ControlError
from hoi4_harness.env import HOI4Env
from orders_helpers import stack
from test_independent_hands import Recorder, make_world, play, say

OFF = {"navy": "off", "air": "off", "diplomacy": "off"}
MIXED = {"focus": "llm", "research": "script", "army": "script", "construction": "off",
         "production": "off", "politics": "off", **OFF}


def records(tmp_path):
    lines = (tmp_path / "run" / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(x) for x in lines]


class NeverCalled(LLMClient):
    name = "never"

    def __init__(self):
        self.calls = 0

    def complete(self, system, messages, tools=None, max_tokens=None):
        self.calls += 1
        raise AssertionError("a no-model run called the model")


# --- the mixed map: focus llm, research script, army script ------------------------------------


def mixed_world(tmp_path):
    return make_world(tmp_path, control=dict(MIXED), army_controller="vanilla_like",
                      auto_options={"research_list": ["infantry_weapons1"]})


def test_a_mixed_map_gives_the_model_only_its_own_domain(tmp_path):
    world = mixed_world(tmp_path)
    focus = [say("focus", ("set_national_focus", {"focus_id": "industrial_effort"}),
                 ("advance_time", {"days": 30, "reason": "x"}))]
    client = Recorder(focus)
    play(world, client, until="1936-03-15")
    assert client.offered == {"set_national_focus", "advance_time", "note"}
    assert world.config.control_map().explicit and not world.config.campaign_human_ai
    assert "human_ai is not sent" in world.log


def test_the_research_script_fills_the_slot_through_the_mouse_path_and_the_model_is_not_woken(tmp_path):
    world = mixed_world(tmp_path)
    client = Recorder([say("focus", ("set_national_focus", {"focus_id": "industrial_effort"}),
                           ("advance_time", {"days": 30, "reason": "x"}))])
    play(world, client, until="1936-03-15")
    assert "infantry_weapons1" in {s.technology for s in world.fake.world.state.research}
    auto = [r for r in records(world.tmp_path) if r["kind"] == "auto"]
    started = [a for r in auto for a in r["actions"] if a["name"] == "start_research"]
    assert started and started[0]["arguments"] == {"technology": "infantry_weapons1"}
    assert started[0]["rationale"].startswith("auto:")
    # idle research slots remain (two slots, one list entry) and still never wake the model
    assert "research slot" not in world.log


def test_the_army_controller_issues_orders_through_the_bridge_on_the_fake_army(tmp_path):
    world = mixed_world(tmp_path)
    client = Recorder([say("focus", ("set_national_focus", {"focus_id": "industrial_effort"}),
                           ("advance_time", {"days": 7, "reason": "x"}))], nap=7)
    play(world, client, until="1936-02-20")
    orders = [a for r in records(world.tmp_path) if r["kind"] == "auto"
              for a in r["actions"] if a["name"] in ("attack_province", "move_division")]
    assert orders and orders[0]["arguments"] == {"from_state": 11, "to_state": 12}
    assert "vanilla" in orders[0]["rationale"]
    assert any("order to province" in line for line in world.fake.army.log)
    assert any("Order: #1" in brief for brief in client.briefs)


def test_the_brief_reports_each_automated_domain_in_one_line(tmp_path):
    world = mixed_world(tmp_path)
    client = Recorder([say("focus", ("advance_time", {"days": 7, "reason": "x"}))], nap=7)
    play(world, client, until="1936-02-20")
    brief = next(b for b in client.briefs if "Automated research" in b)
    assert "Automated research (research priority list): " in brief
    assert "Automated army (vanilla_like tactical controller): " in brief
    assert "Automated focus" not in brief and "Automated construction" not in brief


def test_the_model_cannot_use_a_scripts_domain_even_by_asking(tmp_path):
    world = mixed_world(tmp_path)
    script = [say("greedy", ("start_research", {"technology": "infantry_weapons1"}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    play(world, Recorder(script), until="1936-03-15")
    asked = {r["name"]: r for rec in records(world.tmp_path) if rec["kind"] == "actions"
             for r in rec["results"]}
    assert asked["start_research"]["is_error"]


def test_a_script_cannot_step_outside_its_domain_or_the_adapters_offer(tmp_path):
    from hoi4_harness.auto.layer import AutoLayer
    from hoi4_harness.auto.research import ResearchScript
    from hoi4_harness.types import ActionCall

    world = mixed_world(tmp_path)

    class Greedy(ResearchScript):
        def decide(self, state, objective, memory):
            return [ActionCall("set_national_focus", {"focus_id": "x"}),
                    ActionCall("start_research", {"technology": "infantry_weapons1"})]

    layer = AutoLayer({"research": Greedy(AutoContext(config=world.config))})
    ran = layer.run(world.env, world.env.read_state())
    assert [c.name for c, _ in ran] == ["start_research"]


# --- the army script on its own ------------------------------------------------------------------


def test_the_army_script_builds_its_controller_from_the_registry_and_waits_between_repeats(tmp_path):
    s = stack(tmp_path)
    config = HarnessConfig(country="SWE", army_controller="vanilla_like")
    script = ArmyScript(AutoContext(config=config, layer=lambda: s.layer))
    state = s.state()
    (order,) = script.decide(state, "", None)
    assert (order.name, order.arguments) == ("attack_province", {"from_state": 11, "to_state": 12})
    result = s.adapter.apply(order)
    script.feedback(order, result, state)
    assert script.decide(state, "", None) == []                       # inside the cooldown
    assert script.label == "vanilla_like tactical controller"


def test_the_army_script_says_why_it_is_idle_without_a_map():
    script = ArmyScript(AutoContext(config=HarnessConfig(country="SWE")))
    assert script.decide(__import__("hoi4_harness.types", fromlist=["GameState"]).GameState(),
                         "", None) == []
    assert "no map" in script.idle_note(None)


# --- no model at all -----------------------------------------------------------------------------


def test_an_all_script_run_plays_turns_with_zero_model_calls_on_the_mock(tmp_path):
    config = HarnessConfig(
        control={"default": "script", "diplomacy": "off", "navy": "off", "air": "off",
                 "army": "off"},
        dry_run=False, require_confirmation=False, run_dir=tmp_path / "run",
        auto_options={"focus_path": ["industrial_effort"], "research_list": ["a", "b", "c"]})
    assert not config.planner_active
    env = HOI4Env(MockAdapter(), config)
    env.reset()
    client = NeverCalled()
    loop = AgentLoop(env, planner=None, config=config, transcript_path=tmp_path / "run" / "t.jsonl")
    report = loop.run(12)
    assert report.turns == 12 and report.planner_calls == 0 and client.calls == 0
    state = env.read_state()
    assert [s.technology for s in state.research] == ["a", "b", "c"]
    records_ = [json.loads(x) for x in (tmp_path / "run" / "t.jsonl").read_text(
        encoding="utf-8").splitlines()]
    done = [a["name"] for r in records_ if r["kind"] == "auto" for a in r["actions"]]
    assert done.count("start_research") == 3 and "set_national_focus" in done
    assert report.actions_ok >= 4
    assert not {"llm", "observe"} & {r["kind"] for r in records_}


def test_a_planner_handed_to_a_no_model_map_is_never_called(tmp_path):
    config = HarnessConfig(control={"default": "script", "diplomacy": "off", "navy": "off",
                                    "air": "off", "army": "off"},
                           dry_run=False, require_confirmation=False, run_dir=tmp_path / "run")
    env = HOI4Env(MockAdapter(), config)
    env.reset()
    client = NeverCalled()
    report = AgentLoop(env, planner=client, config=config).run(6)
    assert client.calls == 0 and report.planner_calls == 0


def test_an_all_script_campaign_runs_to_the_end_date_with_no_planner_calls(tmp_path):
    world = make_world(tmp_path, control={"default": "script", **OFF, "army": "off"},
                       auto_options={"research_list": ["infantry_weapons1"],
                                     "focus_path": ["industrial_effort"]})
    world.config.campaign_until = "1936-04-01"
    report, summary = run_campaign(world.config, planner=None, env=world.env)
    assert summary["reached_end_date"] and report.planner_calls == 0
    assert "infantry_weapons1" in {s.technology for s in world.fake.world.state.research}
    assert not any(r["kind"] in ("llm", "observe") for r in records(world.tmp_path))
    assert world.fake.human_ai is False


def test_the_lazy_planner_is_not_built_for_a_no_model_campaign(tmp_path):
    config = campaign_config(tmp_path, control={"default": "script", **OFF, "army": "off"})
    assert not config.planner_active
    assert config.control_map().explicit and config.campaign_control == "llm"


# --- human_ai is sent iff a non-army domain is game ----------------------------------------------


def run_with(tmp_path, control, **options):
    config = campaign_config(tmp_path, control=control, **options)
    env = build_env(config)
    client = Recorder([say("go", ("advance_time", {"days": 30, "reason": "x"}))])
    run_campaign(config, planner=client, env=env)
    return config, env.adapter.fake_game, client


def test_a_non_army_game_domain_sends_human_ai_and_the_model_steers(tmp_path):
    config, fake, client = run_with(tmp_path, {"default": "game", "politics": "llm"})
    assert config.campaign_control == "game_ai" and config.campaign_human_ai
    assert fake.human_ai is True
    assert {"set_ai_directive", "set_ai_posture", "take_decision"} <= client.offered
    assert not client.offered & {"set_national_focus", "start_research", "queue_construction"}


def test_army_game_alone_delegates_armies_and_never_sends_human_ai(tmp_path):
    config, fake, client = run_with(tmp_path, {"army": "game", "default": "llm"})
    assert not config.campaign_human_ai and fake.human_ai is False
    assert "human_ai is not sent" in (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert not client.offered & {"move_division", "attack_province"}


def test_a_contested_map_cannot_start_a_campaign(tmp_path):
    with pytest.raises(ControlError, match="contested"):
        campaign_config(tmp_path, control={"focus": "llm", "research": "game", "default": "off"})


def test_the_ai_already_running_the_country_refuses_a_map_that_gives_it_domains_to_us(tmp_path):
    config = campaign_config(tmp_path, control={"focus": "llm", "default": "off"})
    env = build_env(config)
    env.adapter.fake_game.human_ai = True               # an earlier game_ai run left ai=1
    env.adapter.fake_game.flags.add("llmb_harness")
    env.adapter.fake_game.emit_state()
    with pytest.raises(CampaignStartError, match="ai=1.*focus"):
        run_campaign(config, planner=Recorder(), env=env)


# --- Expert AI's strong weights follow the map: steer on and human_ai on --------------------------


def test_the_strong_weights_go_out_iff_the_map_steers_a_country_the_ai_runs(tmp_path):
    from test_expert_ai_base import EAI_ENTRY, start

    fake, log, _ = start(tmp_path / "a", EAI_ENTRY, control={"default": "game", "politics": "llm"})
    assert "llmb_strong_directives" in fake.global_flags and "strong directive weights on" in log
    fake, log, _ = start(tmp_path / "b", EAI_ENTRY, control={"army": "game", "default": "llm"})
    assert "llmb_strong_directives" not in fake.global_flags      # armies only: no human_ai
    fake, log, _ = start(tmp_path / "c", EAI_ENTRY,
                         control={"default": "game", "politics": "llm"}, steer="off")
    assert "llmb_strong_directives" not in fake.global_flags      # nobody steers
