"""Politics and steering a player (or a rival) can do from script, faithfully (docs/interaction.md).

Every action here is one guarded effect: the game's own conditions in the ``limit``, the cost
charged inside it, a marker logged inside it. These run on the fake game, which evaluates the
few triggers and effects the harness writes; the refusals that never reach the game are checked
on the plan alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness import modgen, policy_effects, steering
from hoi4_harness.actions import catalog
from hoi4_harness.adapters.console import CONSOLE_ACTIONS, ConsoleChannel
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.control import DOMAIN_ACTIONS, STEER_ACTIONS
from hoi4_harness.fakegame import FakeGame, write_demo_playset
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.steering import Standing
from hoi4_harness.types import ActionCall, GameState
from hoi4_harness.world import WorldObserver

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"


class _Driver:
    dry_run = False

    class config:
        settle_seconds = 0.0

    def _sleep(self, seconds):
        pass


def _rig(tmp_path, political_power=200.0):
    playset = write_demo_playset(tmp_path / "playset")
    techs = playset / "common" / "technologies"
    techs.mkdir(parents=True)
    (techs / "t.txt").write_text("technologies = { radio = { } infantry_weapons1 = { } }",
                                 encoding="utf-8")
    (playset / "common" / "characters" / "locked.txt").write_text(
        "characters = { SWE_locked = { advisor = { slot = political_advisor "
        "idea_token = SWE_locked_tok cost = 25 can_be_fired = no } } }", encoding="utf-8")
    index = IdentifierIndex.build(Playset.from_paths(playset))
    game = FakeGame(tmp_path / "user", index=index, variance=0)
    game.paused = False
    game.world.state.political_power = political_power
    reader = LogTailAdapter(game.log_path, country=game.country)
    game.emit_state()
    channel = ConsoleChannel(_Driver(), index=index, read_state=reader.read_state,
                             sleep=game.sleep, verify_timeout=1.0)
    channel.config.archive_dir = tmp_path / "run"
    channel.steer_standing = Standing(tmp_path / "run")
    channel.inbox = game.inbox()
    channel.config.inbox_ack_seconds = 30.0
    return game, channel, index


def _do(channel, name, **arguments):
    (result,) = channel.execute([ActionCall(name, arguments)])
    return result


# --- the vocabulary ------------------------------------------------------------------------

NEW = {"change_law", "fire_advisor", "steer_navy", "steer_air", "set_occupation_law",
       "diplomacy_act", "steer_production", "steer_intelligence", "steer_research"}


def test_the_new_actions_are_in_the_catalog_the_console_and_the_control_map():
    assert NEW <= set(catalog.names()) and NEW <= CONSOLE_ACTIONS
    assert {"change_law", "set_occupation_law", "diplomacy_act"} <= DOMAIN_ACTIONS["politics"]
    assert {"steer_production", "steer_intelligence", "steer_research", "steer_navy",
            "steer_air"} <= STEER_ACTIONS


def test_the_directives_cover_diplomacy_and_intelligence_the_ai_carries_out_itself():
    assert {"prepare_war", "declare_war", "volunteers", "lend_lease", "ally", "spy_on",
            "sabotage", "steal_tech"} <= set(catalog.AI_DIRECTIVES)
    text = (MOD / "common/ai_strategy/llm_bridge_targets.txt").read_text(encoding="utf-8")
    assert 'type = prepare_for_war id = "GER" value = 150' in text
    assert 'type = send_volunteers_desire id = "POL" value = 200' in text
    assert ("type = operative_mission mission = build_intel_network value = 800 "
            "mission_target = GER") in text
    assert ("type = operative_operation operation = operation_targeted_sabotage_industry "
            "value = 800 operation_target = SOV") in text
    assert modgen.drift(MOD) == []


# --- laws ----------------------------------------------------------------------------------


def test_a_law_change_checks_the_laws_conditions_replaces_the_old_law_and_charges_its_cost(tmp_path):
    game, channel, _ = _rig(tmp_path, political_power=200.0)
    result = _do(channel, "change_law", law="partial_economic_mobilisation")
    assert result.ok, result.message
    assert "law changed to partial_economic_mobilisation" in result.message
    assert game.ideas >= {"partial_economic_mobilisation", "export_focus", "volunteer_only"}
    assert "civilian_economy" not in game.ideas                      # the old law went
    # 100 paid; the fake earns a few points over the days the inbox waited
    assert 100.0 <= game.world.state.political_power < 150.0
    assert game.keylog == []


def test_the_game_declines_a_law_whose_available_block_fails_and_nothing_is_charged(tmp_path):
    game, channel, _ = _rig(tmp_path)
    game.war_support = 0.1                                          # war_economy needs > 0.5
    result = _do(channel, "change_law", law="war_economy")
    assert not result.ok and result.error_kind == "rejected"
    assert "Nothing changed" in result.message
    assert "war_economy" not in game.ideas and "civilian_economy" in game.ideas
    assert game.world.state.political_power >= 200.0                # nothing was spent


def test_the_law_you_leave_must_be_allowed_to_go(tmp_path):
    game, channel, _ = _rig(tmp_path)
    # volunteer_only may only be left above 0.99 war support; the new law's own block holds
    result = _do(channel, "change_law", law="limited_conscription")
    assert not result.ok and result.error_kind == "rejected"
    assert "volunteer_only" in game.ideas and "limited_conscription" not in game.ideas
    game.war_support = 1.0
    assert _do(channel, "change_law", law="limited_conscription").ok
    assert "volunteer_only" not in game.ideas and "limited_conscription" in game.ideas


def test_a_law_a_player_cannot_pick_is_refused_before_anything_is_posted(tmp_path):
    game, channel, _ = _rig(tmp_path)
    for law, why in (("isolation", "never has it"), ("unpriced_law", "names no cost"),
                     ("war_economyy", "no law")):
        result = _do(channel, "change_law", law=law)
        assert not result.ok and why in result.message and "nothing was sent" in result.message
    assert channel.inbox.pending == []


def test_a_law_costing_more_than_you_hold_is_refused(tmp_path):
    _game, channel, _ = _rig(tmp_path, political_power=50.0)
    result = _do(channel, "change_law", law="partial_economic_mobilisation")
    assert not result.ok and "costs 100 political power, 50 held" in result.message


def test_the_law_effect_is_one_guard_with_the_laws_own_blocks_and_the_cost(tmp_path):
    _game, channel, index = _rig(tmp_path)
    plan = index.mod_context().plan_law("war_economy", "SWE")
    assert plan.executable and plan.cost == 120
    line = plan.line
    assert "has_war_support > 0.5" in line                          # its available block, verbatim
    assert "has_political_power > 119.99" in line and "add_political_power = -120" in line
    assert "NOT = { has_idea = war_economy }" in line
    # its own allowed_to_remove is not asked of itself
    assert "OR = { NOT = { has_idea = war_economy }" not in line
    assert f'log = "LLMB|v5|click|{plan.nonce}"' in line


def test_a_law_that_reads_from_cannot_be_guarded_and_is_refused():
    law = {"id": "x_law", "category": "economy", "cost": 10.0, "allowed": [],
           "available": [("FROM", "=", [("tag", "=", "GER")])], "visible": [],
           "allowed_to_remove": []}
    plan = policy_effects.plan_law(law, [law], "SWE")
    assert not plan.executable and "FROM/PREV" in plan.reason


def test_real_laws_are_planned_on_the_inbox_and_the_quoted_dlc_names_refused_on_the_console():
    game_dir = Path("C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV")
    if not game_dir.is_dir():
        pytest.skip("no HOI4 install")
    ctx = IdentifierIndex.build(Playset.from_paths(game_dir)).mod_context()
    inbox = ctx.plan_law("war_economy", "GER", console=False)
    assert inbox.executable and inbox.cost == 150 and "has_war_support" in inbox.line
    console = ctx.plan_law("war_economy", "GER", console=True)
    assert not console.executable and "splits quoted text" in console.reason
    assert not ctx.plan_law("isolation", "GER", console=False).executable   # allowed = always no


# --- advisors ------------------------------------------------------------------------------


def test_a_hired_advisor_can_be_dismissed_and_one_who_is_not_hired_is_declined(tmp_path):
    game, channel, _ = _rig(tmp_path)
    assert _do(channel, "hire_advisor", advisor_id="SWE_demo_minister").ok
    assert game.advisors["political_advisor"] == ["SWE_demo_minister"]
    fired = _do(channel, "fire_advisor", advisor_id="SWE_demo_minister")
    assert fired.ok and "dismissed" in fired.message
    assert game.advisors["political_advisor"] == []
    again = _do(channel, "fire_advisor", advisor_id="SWE_demo_minister")
    assert not again.ok and again.error_kind == "rejected"           # not hired: the guard declines


def test_an_advisor_whose_role_cannot_be_fired_or_who_does_not_exist_is_refused(tmp_path):
    _game, channel, _ = _rig(tmp_path)
    locked = _do(channel, "fire_advisor", advisor_id="SWE_locked")
    assert not locked.ok and "can_be_fired = no" in locked.message
    ghost = _do(channel, "fire_advisor", advisor_id="SWE_nobody")
    assert not ghost.ok and "no advisor" in ghost.message
    assert channel.inbox.pending == []


# --- occupation laws -----------------------------------------------------------------------


def test_the_default_and_a_per_country_occupation_law(tmp_path):
    game, channel, _ = _rig(tmp_path)
    result = _do(channel, "set_occupation_law", law="martial_law_occupation")
    assert result.ok, result.message
    assert game.occupation_law == "martial_law_occupation"
    result = _do(channel, "set_occupation_law", law="no_garrison", occupied="POL")
    assert result.ok and game.occupation_overrides == {"POL": "no_garrison"}


def test_an_occupation_law_the_game_does_not_show_is_declined_or_refused(tmp_path):
    game, channel, _ = _rig(tmp_path)
    game.war_support = 0.0                                          # no_garrison is visible above 0.1
    declined = _do(channel, "set_occupation_law", law="no_garrison")
    assert not declined.ok and declined.error_kind == "rejected"
    assert game.occupation_law == "military_governor_occupation"
    for law, why in (("colonial_police", "read FROM"), ("hidden_fallback", "never shows it"),
                     ("nonesuch", "no occupation law")):
        refused = _do(channel, "set_occupation_law", law=law)
        assert not refused.ok and why in refused.message
    # a country that is not occupied: the guard declines, the override is not set
    assert not _do(channel, "set_occupation_law", law="martial_law_occupation", occupied="FIN").ok
    assert "FIN" not in game.occupation_overrides


# --- diplomacy -----------------------------------------------------------------------------


def test_guarantee_is_guarded_by_war_and_an_existing_guarantee(tmp_path):
    game, channel, _ = _rig(tmp_path)
    ok = _do(channel, "diplomacy_act", action="guarantee", target="POL")
    assert ok.ok and "now guarantees POL" in ok.message and game.guaranteed == {"POL"}
    again = _do(channel, "diplomacy_act", action="guarantee", target="POL")
    assert not again.ok and again.error_kind == "rejected"          # already guaranteed
    game.at_war_with.add("GER")
    at_war = _do(channel, "diplomacy_act", action="guarantee", target="GER")
    assert not at_war.ok and "GER" not in game.guaranteed
    for bad, why in (("SWE", "itself"), ("XXX", "no country")):
        result = _do(channel, "diplomacy_act", action="guarantee", target=bad)
        assert not result.ok and why in result.message


def test_a_member_can_leave_its_faction_and_a_leader_cannot(tmp_path):
    game, channel, _ = _rig(tmp_path)
    game.faction_leader = True
    leader = _do(channel, "diplomacy_act", action="leave_faction")
    assert not leader.ok and game.faction == "demo_faction"          # the guard declined it
    game.faction_leader = False
    member = _do(channel, "diplomacy_act", action="leave_faction")
    assert member.ok and game.faction is None
    nobody = _do(channel, "diplomacy_act", action="leave_faction")
    assert not nobody.ok                                             # not in a faction any more


@pytest.mark.parametrize("act", sorted(policy_effects.DIPLOMACY_REFUSALS))
def test_what_needs_an_answer_or_takes_time_is_refused_with_the_reason_and_a_route(tmp_path, act):
    _game, channel, _ = _rig(tmp_path)
    result = _do(channel, "diplomacy_act", action=act, target="POL")
    assert not result.ok and "no faithful effect" in result.message
    assert "nothing was sent" in result.message and channel.inbox.pending == []
    assert "set_ai_directive" in result.message or "diplomacy action" in result.message


def test_every_act_of_the_enum_is_either_done_or_refused_with_a_reason():
    acts = set(catalog.DIPLOMACY_ACTS)
    assert acts == set(policy_effects.DIPLOMACY_REFUSALS) | {"guarantee", "leave_faction"}


# --- steering ------------------------------------------------------------------------------


def test_a_steer_sets_one_flag_per_kind_and_id_and_replaces_its_level(tmp_path):
    game, channel, _ = _rig(tmp_path)
    result = _do(channel, "steer_production", kind="equipment", id="fighter", level="more")
    assert result.ok, result.message
    assert game.flags >= {"llmb_steer_production_equipment_fighter_more"}
    assert _do(channel, "steer_production", kind="equipment", id="fighter", level="much_more").ok
    mine = {f for f in game.flags if "equipment_fighter" in f}
    assert mine == {"llmb_steer_production_equipment_fighter_much_more"}
    assert _do(channel, "steer_production", kind="garrison", level="less").ok
    assert "llmb_steer_production_garrison_any_less" in game.flags
    assert _do(channel, "steer_production", kind="equipment", id="fighter", level="clear").ok
    assert not {f for f in game.flags if "equipment_fighter" in f}


def test_intelligence_and_research_steers_and_the_brief_lists_what_stands(tmp_path):
    game, channel, index = _rig(tmp_path)
    assert _do(channel, "steer_intelligence", kind="branch", id="branch_crypto", level="more").ok
    assert _do(channel, "steer_intelligence", kind="mission", id="quiet_network",
               level="much_more").ok
    assert _do(channel, "steer_research", technology="radio", level="first").ok
    assert "llmb_research_radio" in game.flags
    observer = WorldObserver("SWE", None, index=index, run_dir=tmp_path / "run")
    (line,) = observer.brief_lines(GameState(country="SWE"))
    assert "branch branch_crypto more" in line and "mission quiet_network much_more" in line
    assert _do(channel, "steer_intelligence", kind="branch", id="branch_crypto", level="clear").ok
    (line,) = observer.brief_lines(GameState(country="SWE"))
    assert "branch_crypto" not in line


def test_navy_and_air_steers_name_a_region_or_a_country(tmp_path):
    game, channel, _ = _rig(tmp_path)
    assert _do(channel, "steer_navy", kind="dominance", id="81", level="more").ok
    assert _do(channel, "steer_navy", kind="raiding", id="GER", level="much_more").ok
    assert _do(channel, "steer_air", region="3", level="more").ok
    assert game.flags >= {"llmb_steer_navy_dominance_81_more", "llmb_steer_navy_raiding_GER_much_more",
                          "llmb_steer_air_importance_3_more"}
    assert _do(channel, "steer_air", region="3", level="much_more").ok
    assert "llmb_steer_air_importance_3_more" not in game.flags
    for name, arguments, why in (
            ("steer_navy", {"kind": "dominance", "id": "Atlantic", "level": "more"}, "sea region id"),
            ("steer_navy", {"kind": "raiding", "id": "XXX", "level": "more"}, "not a country tag"),
            ("steer_air", {"region": "x1", "level": "more"}, "strategic region id"),
            ("steer_navy", {"kind": "raiding", "id": "GER", "level": "less"}, "no level")):
        result = _do(channel, name, **arguments)
        assert not result.ok and why in result.message, (arguments, result.message)


def test_region_blocks_are_generated_per_playset_region_and_the_mod_ships_the_vanilla_ones():
    text = steering.region_strategies({"1", "2", "10"}, {"10"}, ["GER", "bad tag"])
    assert "type = strategic_air_importance id = 10 value = 40000" in text
    assert text.count("type = naval_dominance") == 3                  # one sea region, three levels
    assert "type = convoy_raiding_target id = GER value = 100" in text and "bad tag" not in text
    shipped = (MOD / steering.REGIONS_FILE).read_text(encoding="utf-8")
    assert shipped.count("type = strategic_air_importance") > 500
    assert shipped.count("{") == shipped.count("}")


def test_a_steer_the_ai_has_no_knob_for_is_refused_before_anything_is_posted(tmp_path):
    _game, channel, _ = _rig(tmp_path)
    cases = (
        ("steer_production", {"kind": "equipment", "id": "nonesuch", "level": "more"}, "not a equipment id"),
        ("steer_production", {"kind": "equipment", "level": "more"}, "needs an id"),
        ("steer_production", {"kind": "garrison", "id": "x", "level": "more"}, "takes no id"),
        ("steer_production", {"kind": "building", "id": "dockyard", "level": "less"}, "no level"),
        ("steer_intelligence", {"kind": "decrypt", "level": "more"}, "not a intelligence steer"),
        ("steer_research", {"technology": "no_such_tech", "level": "first"}, "no technology"),
    )
    for name, arguments, why in cases:
        result = _do(channel, name, **arguments)
        assert not result.ok and why in result.message, (name, arguments, result.message)
    assert channel.inbox.pending == []


def test_the_identifier_check_names_the_nearest_real_law_and_steer_ids(tmp_path):
    _game, _channel, index = _rig(tmp_path)
    bad = index.check(ActionCall("change_law", {"law": "war_econmy"}), "SWE")
    assert bad is not None and "war_economy" in bad.message
    assert index.check(ActionCall("change_law", {"law": "war_economy"}), "SWE") is None
    bad = index.check(ActionCall("set_occupation_law", {"law": "no_garison"}), "SWE")
    assert bad is not None and "no_garrison" in bad.message
    bad = index.check(ActionCall("diplomacy_act", {"action": "guarantee", "target": "ZZZ"}), "SWE")
    assert bad is not None and "no country" in bad.message.lower()
    bad = index.check(ActionCall("steer_production", {"kind": "units", "id": "tank", "level": "more"}),
                      "SWE")
    assert bad is not None and "not a units id" in bad.message
    bad = index.check(ActionCall("steer_research", {"technology": "nope", "level": "first"}), "SWE")
    assert bad is not None and "no technology" in bad.message.lower()


def test_the_mod_ships_a_block_per_steer_and_per_technology():
    text = (MOD / steering.FILE).read_text(encoding="utf-8")
    assert modgen.drift(MOD) == []
    for area, kind, spec in steering.specs():
        for ident in spec.ids or (None,):
            for level, value in spec.levels.items():
                name = steering.flag(area, kind, ident, level)
                assert f"{name} = {{" in text, name
                assert f"has_country_flag = {name}" in text
                assert steering.strategy_text(spec, ident, value) in text
    assert ("type = equipment_production_factor id = fighter value = 50") in text
    assert "type = intelligence_agency_usable_factories value = 20" in text
    assert "type = operative_mission mission = quiet_network value = 1000" in text
    research = (MOD / steering.RESEARCH_FILE).read_text(encoding="utf-8")
    assert research.count("type = research_tech") > 400
    assert text.count("{") == text.count("}") and research.count("{") == research.count("}")


def test_research_blocks_are_generated_per_technology():
    text = steering.research_strategies(["radio", "infantry_weapons1", "bad id"])
    assert text.index("llmb_research_infantry_weapons1 =") < text.index("llmb_research_radio =")
    assert "type = research_tech id = radio value = 100" in text and "bad id" not in text
    assert steering.research_effect("radio", "clear") == "clr_country_flag = llmb_research_radio"
