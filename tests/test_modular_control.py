"""Modular control (control.py, auto/): the map, the presets, the wake rules and the script
controllers. Simulators and plain states only: nothing here touches the game or a model."""

from __future__ import annotations

import json

import pytest

from hoi4_harness.actions import catalog
from hoi4_harness.agent.hybrid import actions_for_mode, describe
from hoi4_harness.agent.policy import Policy
from hoi4_harness.agent.prompts import build_system, system_for
from hoi4_harness.auto.base import AutoContext
from hoi4_harness.auto.construction import ConstructionScript
from hoi4_harness.auto.focus import FocusScript
from hoi4_harness.auto.politics import PoliticsScript
from hoi4_harness.auto.production import ProductionScript
from hoi4_harness.auto.research import ResearchScript
from hoi4_harness.cli import _config_from_args, build_parser, main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.control import (
    DELEGATE_ACTION,
    DOMAIN_ACTIONS,
    DOMAINS,
    STEER_ACTIONS,
    ControlError,
    preset,
    resolve,
)
from hoi4_harness.tactics import registry
from hoi4_harness.types import (
    ActionResult,
    ConstructionItem,
    Front,
    GameEvent,
    GameState,
    ProductionLine,
    ResearchSlot,
    War,
)

ALL = set(catalog.names())


def ctx(**options) -> AutoContext:
    return AutoContext(config=HarnessConfig(country="SWE"), options=options)


def state(**fields) -> GameState:
    base = dict(country="SWE", date="1936-03-01", national_focus="x", civilian_factories=10,
                military_factories=5)
    base.update(fields)
    return GameState(**base)


# --- the domain table is the catalog ----------------------------------------------------------


def test_every_catalog_action_has_one_owner_or_is_always_available():
    owned = [a for names in DOMAIN_ACTIONS.values() for a in names]
    assert len(owned) == len(set(owned))                       # no action in two domains
    steered = set(STEER_ACTIONS) | {DELEGATE_ACTION}
    free = {"note", "advance_time", "list_decisions", "describe_action", "list_gui_buttons",
            "inspect_world",
            "set_game_speed"}
    assert ALL == set(owned) | steered | free


# --- presets: the old levels, exactly ----------------------------------------------------------


@pytest.mark.parametrize("mode", ["llm", "ai", "game_ai"])
def test_a_preset_map_gives_the_old_tool_list_exactly(mode):
    """The preset's map, run through the new narrowing, equals ``actions_for_mode``."""
    assert preset(mode).actions_for_model(set(ALL)) == actions_for_mode(set(ALL), mode)


@pytest.mark.parametrize("mode", ["llm", "ai", "game_ai"])
def test_a_preset_config_offers_the_old_tools_and_the_old_prompt(mode):
    config = HarnessConfig(operational_control=mode)
    assert config.allowed_actions(set(ALL)) == actions_for_mode(set(ALL), mode)
    assert config.control_map().preset == mode and not config.control_map().explicit
    assert system_for(config) == build_system(operational_control=mode)
    assert describe(mode) in system_for(config)


def test_the_preset_maps_say_what_the_levels_meant():
    llm, ai, game_ai = preset("llm"), preset("ai"), preset("game_ai")
    assert not llm.human_ai and llm.steer == "off" and llm.owned_by("llm") == list(DOMAINS)
    assert not ai.human_ai and ai.delegated and ai.steer == "llm"
    assert game_ai.human_ai and game_ai.steer == "llm" and game_ai.owned_by("llm") == ["politics"]


def test_an_unknown_operational_control_still_plays_as_llm():
    assert preset("nonsense").preset == "llm"


# --- the human_ai constraint -------------------------------------------------------------------


def test_game_on_a_non_army_domain_means_human_ai_and_army_game_alone_does_not():
    assert resolve({"research": "game", "default": "game"}).human_ai
    only_research = {"research": "game", "default": "off"}
    assert resolve(only_research).human_ai
    delegated = resolve({"army": "game", "default": "llm"})
    assert not delegated.human_ai and delegated.delegated


def test_a_contested_map_is_refused_where_the_ai_is_known_to_overwrite():
    with pytest.raises(ControlError, match="focus=llm is contested.*production"):
        resolve({"focus": "llm", "production": "game", "default": "game"})
    with pytest.raises(ControlError, match="army=script is contested"):
        resolve({"research": "game", "army": "script", "default": "game"})
    # the example from the brief mixes llm and script with a game production line: refused too
    with pytest.raises(ControlError, match="contested"):
        resolve({"focus": "llm", "research": "script", "construction": "script",
                 "production": "game", "diplomacy": "llm", "army": "script", "default": "off"})


def test_a_domain_the_ai_leaves_room_in_is_allowed_and_an_unknown_one_warns():
    ok = resolve({"default": "game", "politics": "llm"})
    assert ok.human_ai and not ok.warnings
    unknown = resolve({"default": "game", "navy": "llm"})
    assert any("navy=llm under human_ai" in w for w in unknown.warnings)


def test_allow_contested_turns_the_refusal_into_a_warning():
    cmap = resolve({"focus": "llm", "research": "game", "default": "off"}, allow_contested=True)
    assert any("focus=llm is contested" in w and "allow_contested" in w for w in cmap.warnings)


def test_off_is_never_contested():
    assert resolve({"research": "game", "default": "off"}).human_ai


def test_steer_exists_only_while_the_game_ai_owns_something():
    assert resolve({"default": "llm"}).steer == "off"
    assert resolve({"default": "game", "politics": "llm"}).steer == "llm"
    assert resolve({"army": "game", "default": "llm"}).steer == "llm"
    with pytest.raises(ControlError, match="steer=llm needs the game's AI"):
        resolve({"default": "llm"}, steer="llm")
    assert resolve({"army": "game", "default": "llm"}, steer="off").steer == "off"


def test_steer_with_delegated_armies_only_is_marked_unverified():
    warned = resolve({"army": "game", "default": "llm"})
    assert any("unverified whether ai_strategy affects a human country's delegated armies" in w
               for w in warned.warnings)
    assert not resolve({"default": "game", "politics": "llm"}).warnings


def test_the_steer_tools_follow_the_switch():
    steered = resolve({"army": "game", "default": "llm"}).actions_for_model(set(ALL))
    assert STEER_ACTIONS <= steered and DELEGATE_ACTION in steered
    unsteered = resolve({"army": "game", "default": "llm"}, steer="off").actions_for_model(set(ALL))
    assert not unsteered & (STEER_ACTIONS | {DELEGATE_ACTION})
    plain = resolve({"default": "llm"}).actions_for_model(set(ALL))
    assert not plain & (STEER_ACTIONS | {DELEGATE_ACTION})


def test_human_ai_takes_the_clock_and_delegation_away_from_the_model():
    offered = resolve({"default": "game", "politics": "llm"}).actions_for_model(set(ALL))
    assert "set_game_speed" not in offered and DELEGATE_ACTION not in offered
    assert offered >= {"take_decision", "hire_advisor", "set_ai_directive", "advance_time", "note"}


def test_each_domain_gives_the_model_only_its_own_actions():
    cmap = resolve({"focus": "llm", "research": "script", "default": "off"})
    offered = cmap.actions_for_model(set(ALL))
    assert "set_national_focus" in offered and "start_research" not in offered
    assert not offered & DOMAIN_ACTIONS["construction"]


def test_bad_maps_say_why():
    with pytest.raises(ControlError, match="unknown control domain 'wings'"):
        resolve({"wings": "llm"})
    with pytest.raises(ControlError, match="unknown controller 'robot'"):
        resolve({"focus": "robot"})
    with pytest.raises(ControlError, match="diplomacy: no deterministic controller"):
        resolve({"diplomacy": "script"})
    with pytest.raises(ControlError, match="navy: no deterministic controller"):
        resolve({"navy": "script"})
    with pytest.raises(ControlError, match="army_controller 'nope'"):
        resolve({"army": "script"}, army_controller="nope")


def test_a_no_model_map_warns_about_llm_domains_left_idle():
    cmap = resolve({"focus": "llm", "default": "script", "diplomacy": "off", "navy": "off",
                    "air": "off"}, planner_enabled=False)
    assert any("no model is running" in w and "focus" in w for w in cmap.warnings)


def test_the_string_and_preset_forms_parse():
    assert resolve("focus=llm,research=script,default=off").domains["research"] == "script"
    assert resolve("game_ai").preset == "game_ai"
    with pytest.raises(ControlError):
        resolve("focus")
    with pytest.raises(ControlError, match="unknown control preset"):
        resolve("fastest")


def test_uses_model_is_llm_domains_or_steering():
    assert resolve({"default": "script", "diplomacy": "off", "navy": "off", "air": "off"}
                   ).uses_model is False
    assert resolve({"focus": "llm", "default": "off"}).uses_model
    assert resolve({"default": "game", "politics": "off"}).uses_model       # steering


# --- the config --------------------------------------------------------------------------------


def test_a_config_map_replaces_the_old_setting_and_a_no_model_run_needs_no_planner():
    config = HarnessConfig(control={"default": "script", "diplomacy": "off", "navy": "off",
                                    "air": "off"})
    assert config.control_map().explicit and not config.planner_active
    assert HarnessConfig().planner_active            # the old default is untouched


def test_the_operator_lists_still_narrow_the_model_and_the_script_offers():
    config = HarnessConfig(control={"focus": "llm", "research": "script", "default": "off"},
                           disabled_actions=["start_research"])
    assert config.allowed_actions(set(ALL)) & {"set_national_focus"} == {"set_national_focus"}
    assert "start_research" not in config.script_offered(set(ALL))
    assert "queue_construction" in config.script_offered(set(ALL))      # not narrowed by the map


def test_profile_and_cli_forms(tmp_path):
    profile = tmp_path / "p.json"
    profile.write_text(json.dumps({"control": {"focus": "llm", "research": "script",
                                               "default": "off"},
                                   "army_controller": "learned"}), encoding="utf-8")
    config = HarnessConfig.from_file(profile)
    assert config.control_map().domains["research"] == "script" and config.army_controller == "learned"

    parser = build_parser()
    args = parser.parse_args(["play", "--control", "focus=llm,research=script,default=off"])
    assert _config_from_args(args).control_map().domains["focus"] == "llm"
    # the old --control values still mean the campaign's levels
    legacy = _config_from_args(parser.parse_args(["play", "--control", "llm", "--campaign"]))
    assert legacy.campaign_control == "llm" and not legacy.control_map().explicit
    # a file, inline JSON, pairs
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"research": "script", "default": "off"}), encoding="utf-8")
    for value in (str(path), '{"research": "script", "default": "off"}',
                  "research=script,default=off"):
        got = _config_from_args(parser.parse_args(["play", "--control-map", value, "--steer", "off"]))
        assert got.control_map().domains["research"] == "script" and got.steer == "off"


def test_the_cli_refuses_a_contested_map_with_the_reason(capsys):
    code = main(["play", "--control", "focus=llm,research=game,default=off", "--turns", "1"])
    assert code == 2
    assert "contested" in capsys.readouterr().err


# --- the model is told who runs what -----------------------------------------------------------


def test_the_prompt_is_generated_from_the_map():
    config = HarnessConfig(
        control={"focus": "llm", "research": "script", "army": "game", "default": "off"})
    text = system_for(config)
    assert "You run: the national focus." in text
    assert "Deterministic scripts run: research" in text
    assert "no tools for them" in text
    assert "handed to it" in text
    assert "delegate_army_to_ai hands it an army" in text
    assert "You command directly" not in text


def test_a_game_ai_map_says_human_ai_and_an_unsteered_one_says_so():
    config = HarnessConfig(control={"default": "game", "politics": "llm"})
    text = system_for(config)
    assert "handed the whole country (human_ai)" in text and "You run: advisors and decisions" in text
    quiet = system_for(HarnessConfig(control={"default": "llm"}))
    assert "do not steer the game's AI" in quiet


# --- wakes follow ownership ---------------------------------------------------------------------


def idle_state(**fields):
    s = state(national_focus=None, research=[ResearchSlot(index=0)], **fields)
    s.unknown_fields = []
    return s


def test_a_domain_wake_fires_only_when_the_model_owns_the_domain():
    s = idle_state()
    owns = Policy(owned=frozenset({"focus", "research"}), days_per_turn=999)
    assert owns.should_wake(s, 1).wake
    for owned in (frozenset({"production"}), frozenset()):
        policy = Policy(owned=owned, days_per_turn=999)
        assert not policy.should_wake(s, 1).wake
    only_research = Policy(owned=frozenset({"research"}), days_per_turn=999)
    assert "research slot" in only_research.should_wake(s, 1).reason
    # no map (the old levels): every rule on its own flag, as before
    assert Policy(days_per_turn=999).should_wake(s, 1).wake


def front_state():
    war = War(against="GER")
    f = Front(name="east", enemy="GER", pressure="losing_ground", pocket_divisions=3)
    return state(national_focus="x", wars=[war], fronts=[f])


def test_front_wakes_need_the_army_or_the_steering():
    s = front_state()
    assert Policy(owned=frozenset({"army"}), steer=False, days_per_turn=999).should_wake(s, 1).wake
    assert Policy(owned=frozenset(), steer=True, days_per_turn=999).should_wake(s, 1).wake
    assert not Policy(owned=frozenset({"focus"}), steer=False, days_per_turn=999).should_wake(s, 1).wake


def test_the_players_own_capitulation_still_wakes_a_model_that_owns_nothing_else():
    s = state(events=[GameEvent(kind="capitulation", text="capitulation: SWE (to GER)",
                                severity="critical", date="1936-03-01")])
    assert Policy(owned=frozenset({"focus"}), steer=False, days_per_turn=999).should_wake(s, 1).wake


# --- the reflexes ARE the script controllers -----------------------------------------------------


def test_reflex_actions_are_the_script_controllers_reflex_rules():
    s = state()
    s.production = [ProductionLine("infantry_equipment_1", 3)]
    policy = Policy()
    got = [(c.name, c.arguments, c.rationale) for c in policy.reflex_actions(s)]
    want = [(c.name, c.arguments, c.rationale)
            for c in ConstructionScript().reflex(s) + ProductionScript().reflex(s)]
    assert got == want and len(got) == 2
    assert got[0][1] == {"building": "civilian_factory", "state": "capital", "count": 2}
    assert got[1][1] == {"equipment": "infantry_equipment_1", "factories": 5}


# --- script controllers --------------------------------------------------------------------------


def test_research_fills_idle_slots_from_the_list_and_skips_what_is_busy():
    script = ResearchScript(ctx(research_list=["a", "b", "c", "d"]))
    s = state(research=[ResearchSlot(0, "b", 30), ResearchSlot(1), ResearchSlot(2)])
    calls = script.decide(s, "", None)
    assert [c.arguments["technology"] for c in calls] == ["a", "c"]
    assert all(c.name == "start_research" for c in calls)
    for c in calls:
        script.feedback(c, ActionResult(ok=True, message="", action=c.name), s)
    s2 = state(research=[ResearchSlot(0, "b", 30), ResearchSlot(1, "a", 10), ResearchSlot(2)])
    assert [c.arguments["technology"] for c in script.decide(s2, "", None)] == ["d"]


def test_research_leaves_a_refused_technology_alone_for_a_while():
    script = ResearchScript(ctx(research_list=["bad", "good"]))
    s = state(research=[ResearchSlot(0)])
    (c,) = script.decide(s, "", None)
    script.feedback(c, ActionResult(ok=False, message="no", action=c.name, error_kind="rejected"), s)
    assert [x.arguments["technology"] for x in script.decide(s, "", None)] == ["good"]
    later = state(date="1936-06-01", research=[ResearchSlot(0)])
    assert script.decide(later, "", None)[0].arguments["technology"] == "bad"


def test_research_uses_the_named_list_from_the_data_file_and_does_nothing_when_unseen():
    assert ResearchScript(ctx(research_list="industry")).order[0] == "basic_machine_tools"
    assert ResearchScript(ctx()).order[0] == "infantry_weapons"
    with pytest.raises(KeyError, match="no research list"):
        ResearchScript(ctx(research_list="nope"))
    unseen = state(research=[ResearchSlot(0)])
    unseen.unknown_fields = ["research"]
    assert ResearchScript(ctx()).decide(unseen, "", None) == []


def test_construction_keeps_the_queue_full_and_builds_civilian_factories_early():
    script = ConstructionScript(ctx())
    (c,) = script.decide(state(), "", None)
    assert c.arguments == {"building": "civilian_factory", "state": "capital", "count": 2}
    full = state(construction=[ConstructionItem("civilian_factory", "x")] * 3)
    assert script.decide(full, "", None) == []


def test_construction_turns_to_military_factories_at_war_or_from_the_date():
    script = ConstructionScript(ctx())
    war = state(wars=[War(against="GER")], military_factories=2)
    assert script.decide(war, "", None)[0].arguments["building"] == "military_factory"
    late = state(date="1938-01-01", military_factories=2)
    assert script.decide(late, "", None)[0].arguments["building"] == "military_factory"
    enough = state(date="1938-01-01", military_factories=9)
    assert script.decide(enough, "", None)[0].arguments["building"] == "civilian_factory"


def test_construction_adds_infrastructure_when_a_front_is_short_of_supply():
    starved = state(wars=[War(against="GER")], military_factories=9,
                    fronts=[Front(name="east", enemy="GER", supply=0.2)])
    buildings = [c.arguments["building"] for c in ConstructionScript(ctx()).decide(starved, "", None)]
    assert "infrastructure" in buildings


def test_construction_options_set_the_state_and_the_queue_target():
    script = ConstructionScript(ctx(construction={"state": "Svealand", "queue_target": 1}))
    (c,) = script.decide(state(), "", None)
    assert c.arguments["state"] == "Svealand" and c.arguments["count"] == 1


def test_production_hands_idle_factories_to_the_line_furthest_under_its_share():
    script = ProductionScript(ctx())
    s = state(military_factories=10, production=[ProductionLine("infantry_equipment_1", 4)])
    calls = script.decide(s, "", None)
    by = {c.arguments["equipment"]: c.arguments["factories"] for c in calls}
    assert by["infantry_equipment_1"] == 6                      # 0.6 of 10
    assert by["support_equipment_1"] == 2 and by["artillery_equipment_1"] == 1
    assert by["anti_air_equipment_1"] == 1
    assert sum(by.values()) - 4 == 6                            # only the 6 idle ones were handed out
    full = state(military_factories=4, production=[ProductionLine("infantry_equipment_1", 4)])
    assert script.decide(full, "", None) == []


def test_production_ratios_are_an_option_and_a_refused_line_is_skipped():
    script = ProductionScript(ctx(production={"ratios": {"a": 0.5, "b": 0.5}}))
    s = state(military_factories=4, production=[])
    calls = script.decide(s, "", None)
    assert {c.arguments["equipment"] for c in calls} == {"a", "b"}
    script.feedback(calls[0], ActionResult(ok=False, message="", action="x", error_kind="rejected"), s)
    again = script.decide(s, "", None)
    assert {c.arguments["equipment"] for c in again} == {calls[1].arguments["equipment"]}


def test_focus_follows_the_path_and_skips_completed_ones():
    script = FocusScript(ctx(focus_path=["f1", "f2", "f3"]))
    s = state(national_focus=None, completed_focuses=["f1"])
    (c,) = script.decide(s, "", None)
    assert c.arguments == {"focus_id": "f2"}
    assert script.decide(state(national_focus="f2"), "", None) == []
    assert script.decide(state(national_focus=None, completed_focuses=["f1", "f2", "f3"]),
                         "", None) == []


def test_focus_without_a_path_picks_the_best_scored_available_focus():
    class Index:
        def available_focuses(self, tag, done):
            return ["SWE_flag_day", "SWE_expand_industry", "SWE_army_reform"]

    script = FocusScript(AutoContext(config=HarnessConfig(country="SWE"), index=Index()))
    (c,) = script.decide(state(national_focus=None), "", None)
    assert c.arguments["focus_id"] == "SWE_expand_industry"
    nothing = FocusScript(ctx())
    assert nothing.decide(state(national_focus=None), "", None) == []


def test_politics_runs_only_the_rules_it_is_given():
    assert PoliticsScript(ctx()).decide(state(political_power=500), "", None) == []
    rules = [{"id": "econ", "when": {"min_pp": 100, "at_war": False, "from": "1936-02-01"},
              "do": {"action": "hire_advisor", "arguments": {"advisor_id": "swe_econ"}}},
             {"id": "war", "when": {"at_war": True},
              "do": {"action": "take_decision", "arguments": {"decision_id": "mobilise"}}}]
    script = PoliticsScript(ctx(politics_rules=rules))
    rich = state(political_power=150)
    (c,) = script.decide(rich, "", None)
    assert c.name == "hire_advisor" and c.arguments == {"advisor_id": "swe_econ"}
    assert script.decide(state(political_power=50), "", None) == []
    script.feedback(c, ActionResult(ok=True, message="", action=c.name), rich)
    assert script.decide(rich, "", None) == []                       # once
    at_war = state(political_power=150, wars=[War(against="GER")])
    assert script.decide(at_war, "", None)[0].name == "take_decision"


# --- the controller registry --------------------------------------------------------------------


def test_the_tactical_registry_names_the_controllers_and_builds_them():
    assert registry.names() == ["learned", "learned_plans", "planner", "scripted", "vanilla_like"]
    for name in registry.names():
        assert registry.make(name).name in (
            "learned", "learned_plans", "planner", "scripted", "vanilla")
    with pytest.raises(KeyError, match="no tactical controller 'gambit'"):
        registry.make("gambit")
