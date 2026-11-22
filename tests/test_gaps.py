"""The gaps an audit found in what a player can do (docs/interaction.md), closed faithfully.

Weak guards that now copy the game's own conditions (an advisor's allowed/visible/available, the
unlock an equipment needs, a decision's custom cost), province buildings, division templates, a
per-state occupation law, and the steers for experience, market, army and supply spending. Each
runs on the fake game through the inbox, which evaluates the few triggers and effects the harness
writes; the refusals that never reach the game are checked on the plan alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness import modgen, steering
from hoi4_harness.actions import catalog
from hoi4_harness.adapters.console import CONSOLE_ACTIONS, MARKED_ACTIONS, ConsoleChannel
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.control import DOMAIN_ACTIONS, STEER_ACTIONS
from hoi4_harness.fakegame import FakeGame, write_demo_playset
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.modcontext import script as S
from hoi4_harness.modcontext.execute import plan_decision
from hoi4_harness.modcontext.surface import ModContext
from hoi4_harness.steering import Standing
from hoi4_harness.types import ActionCall, GameState, ProductionLine

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"

EXTRA = {
    "common/characters/gated.txt": """characters = {
	SWE_gated = { advisor = { slot = political_advisor idea_token = SWE_gated_tok cost = 25
		allowed = { original_tag = SWE } visible = { has_war_support > 0.05 }
		available = { has_war_support > 0.6 } } }
	SWE_foreign = { advisor = { slot = political_advisor idea_token = SWE_foreign_tok cost = 25
		allowed = { original_tag = GER } } }
	SWE_flagged = { advisor = { slot = political_advisor idea_token = SWE_flagged_tok cost = 25
		visible = { NOT = { has_character_flag = hidden_one } } } }
}""",
    "common/technologies/gaps.txt": """technologies = {
	demo_rockets = { enable_equipments = { rocket_equipment_1 } }
	demo_rockets_b = { enable_equipments = { rocket_equipment_1 } }
	infantry_tech = { enable_subunits = { infantry } }
	engineers_tech = { enable_subunits = { engineer } }
}""",
    "common/units/gaps.txt": """sub_units = {
	infantry = { group = infantry active = no }
	artillery_brigade = { group = combat_support active = yes }
	engineer = { group = support active = no }
	recon = { group = support active = no }
	destroyer = { }
}""",
    "history/states/13-Coast.txt":
        'state = { id = 13 name = "STATE_13" provinces = { 301 302 303 } history = { owner = SWE } }',
    "history/states/21-Abroad.txt":
        'state = { id = 21 name = "STATE_21" provinces = { 401 402 } history = { owner = POL } }',
}


def _rig(tmp_path, political_power=200.0):
    playset = write_demo_playset(tmp_path / "playset")
    for relative, text in EXTRA.items():
        path = playset / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    index = IdentifierIndex.build(Playset.from_paths(playset))
    game = FakeGame(tmp_path / "user", index=index, variance=0)
    game.paused = False
    game.world.state.political_power = political_power
    reader = LogTailAdapter(game.log_path, country=game.country)
    game.emit_state()

    class Driver:
        dry_run = False

        class config:
            settle_seconds = 0.0

        def _sleep(self, seconds):
            pass

    channel = ConsoleChannel(Driver(), index=index, read_state=reader.read_state,
                             sleep=game.sleep, verify_timeout=1.0)
    channel.config.archive_dir = tmp_path / "run"
    channel.steer_standing = Standing(tmp_path / "run")
    channel.inbox = game.inbox()
    channel.config.inbox_ack_seconds = 30.0
    return game, channel, index


def _do(channel, action, /, **arguments):
    (result,) = channel.execute([ActionCall(action, arguments)])
    return result


# --- the vocabulary ------------------------------------------------------------------------

NEW_STEERS = {"steer_spending", "steer_market", "steer_army"}


def test_the_new_actions_are_in_the_catalog_the_console_the_control_map_and_are_marked():
    new = NEW_STEERS | {"queue_province_building", "design_division_template"}
    assert new <= set(catalog.names()) and new <= CONSOLE_ACTIONS and new <= MARKED_ACTIONS
    assert NEW_STEERS <= STEER_ACTIONS
    assert "queue_province_building" in DOMAIN_ACTIONS["construction"]
    assert "hire_advisor" in MARKED_ACTIONS


def test_the_tools_a_live_game_has_no_route_for_say_so_and_the_real_ones_do_not():
    for name in catalog.MOCK_ONLY:
        assert "Simulator only" in catalog.get(name).description, name
    for name in ("design_division_template", "queue_province_building", "steer_army", "hire_advisor"):
        assert "Simulator only" not in catalog.get(name).description, name
    # one place says it, and the mock is the only adapter that carries them
    from hoi4_harness.adapters.mock import MockAdapter
    assert catalog.MOCK_ONLY <= MockAdapter.supported_actions | {"set_army_order", "set_air_mission",
                                                                  "set_naval_mission", "diplomacy"}
    assert "design_division_template" not in catalog.MOCK_ONLY


# --- hire_advisor copies the advisor's own conditions --------------------------------------


def test_an_advisors_allowed_visible_and_available_are_in_the_guard_and_the_game_applies_them(tmp_path):
    game, channel, index = _rig(tmp_path)
    plan = channel.plan(ActionCall("hire_advisor", {"advisor_id": "SWE_gated"}),
                        channel._read_state())
    (line,) = plan.lines
    assert "original_tag = SWE" in line and "has_war_support > 0.05" in line
    assert "has_war_support > 0.6" in line and "NOT = { has_idea = SWE_gated_tok }" in line
    assert "has_political_power > 24.99" in line and "LLMB|v5|click|" in line
    game.war_support = 0.3                                        # available needs more than 0.6
    declined = _do(channel, "hire_advisor", advisor_id="SWE_gated")
    assert not declined.ok and declined.error_kind == "rejected" and "Nothing changed" in declined.message
    assert game.advisors.get("political_advisor", []) == []
    game.war_support = 0.9
    hired = _do(channel, "hire_advisor", advisor_id="SWE_gated")
    assert hired.ok and "SWE_gated hired" in hired.message
    assert game.advisors["political_advisor"] == ["SWE_gated"]


def test_an_advisor_the_country_never_has_or_one_the_guard_cannot_copy_is_refused_on_the_plan(tmp_path):
    _game, channel, _ = _rig(tmp_path)
    foreign = _do(channel, "hire_advisor", advisor_id="SWE_foreign")
    assert not foreign.ok and "never has this advisor" in foreign.message
    flagged = _do(channel, "hire_advisor", advisor_id="SWE_flagged")
    assert not flagged.ok and "character itself" in flagged.message
    assert channel.inbox.pending == []


def test_the_hire_result_names_the_cost_modifiers_it_does_not_apply(tmp_path):
    game, channel, _ = _rig(tmp_path)
    game.war_support = 0.9
    plan = channel.plan(ActionCall("hire_advisor", {"advisor_id": "SWE_gated"}), channel._read_state())
    assert any("army_advisor_cost_factor" in note for note in plan.unchecked)


# --- set_production needs the equipment unlocked --------------------------------------------


def _produce(channel, equipment="rocket_equipment_1"):
    return _do(channel, "set_production", equipment=equipment, factories=1)


@pytest.fixture()
def free_factory(tmp_path):
    game, channel, index = _rig(tmp_path)
    game.world.state.production = [ProductionLine("infantry_equipment_1", 2, 0.5, 4.0)]
    game.world.state.military_factories = 5

    def read():
        # What a text save shows: the lines and the factories (the log alone does not).
        return GameState(country="SWE", political_power=game.world.state.political_power,
                         military_factories=5, production=list(game.world.state.production),
                         date=game.date)

    channel.attach_reader(read)
    return game, channel, index


def test_a_line_for_a_locked_equipment_is_declined_by_the_game_until_a_technology_unlocks_it(
        free_factory):
    game, channel, _ = free_factory
    plan = channel.plan(ActionCall("set_production", {"equipment": "rocket_equipment_1",
                                                      "factories": 1}), channel._read_state())
    assert ("limit = { OR = { has_tech = demo_rockets has_tech = demo_rockets_b } }"
            in plan.lines[0])
    declined = _produce(channel)
    assert declined.error_kind is not None and not declined.ok
    assert [line.equipment for line in game.world.state.production] == ["infantry_equipment_1"]
    game.researched.add("demo_rockets_b")                         # either technology will do
    assert _produce(channel).ok
    assert "rocket_equipment_1" in [line.equipment for line in game.world.state.production]


def test_an_equipment_nothing_unlocks_is_refused_and_a_starting_one_is_not_guarded(free_factory):
    _game, channel, _ = free_factory
    sealed = _produce(channel, "sealed_equipment_1")
    assert not sealed.ok and "no technology of this playset unlocks" in sealed.message
    plan = channel.plan(ActionCall("set_production", {"equipment": "artillery_equipment_1",
                                                      "factories": 1}), channel._read_state())
    assert "has_tech" not in plan.lines[0]                        # active = yes: from the start


def test_the_index_reads_what_unlocks_equipment_and_units(tmp_path):
    _game, _channel, index = _rig(tmp_path)
    assert index.equipment_unlock("rocket_equipment_1") == (False, ["demo_rockets", "demo_rockets_b"])
    assert index.equipment_unlock("infantry_equipment_1") == (True, [])
    assert index.subunit_unlock("infantry") == (False, ["infantry_tech"])
    assert index.states["13"].provinces == frozenset({"301", "302", "303"})


# --- take_decision: costs the effect pays ---------------------------------------------------


def _decision(body):
    return ModContext._decision("x_decision", "x_cat", "test", S.parse(body))


def test_a_cost_the_effect_pays_is_guarded_by_its_custom_trigger_and_said_aloud():
    decision = _decision("""cost = 10 custom_cost_text = X_cost custom_cost_trigger = { command_power > 24.9 }
        complete_effect = { add_command_power = -25 }""")
    assert decision.pays == ("command power",)
    plan = plan_decision(decision, "SWE")
    assert plan.executable and "command_power > 24.9" in plan.line
    assert any("custom cost (text X_cost)" in note and "command power" in note for note in plan.unchecked)
    assert any("cost modifiers" in note and "political_power_cost" in note for note in plan.unchecked)


def test_a_cost_the_effect_pays_with_nothing_to_test_it_is_refused():
    decision = _decision("""cost = 0 complete_effect = { hidden_effect = { add_manpower = -1000 } }""")
    plan = plan_decision(decision, "SWE")
    assert not plan.executable and "manpower" in plan.reason and "custom_cost_trigger" in plan.reason
    free = _decision("""cost = 5 complete_effect = { add_stability = 0.01 add_political_power = 5 }""")
    assert plan_decision(free, "SWE").executable                  # a gain is not a cost


# --- province buildings -------------------------------------------------------------------


def test_a_province_building_is_queued_under_the_games_slot_rule_and_marked(tmp_path):
    game, channel, _ = _rig(tmp_path)
    plan = channel.plan(ActionCall("queue_province_building",
                                   {"building": "fortification", "province": 302, "count": 2}),
                        channel._read_state())
    (line,) = plan.lines
    assert line.startswith("eval_effect SWE = { 13 = {")
    assert ("free_building_slots = { building = bunker province = 302 size > 0 "
            "include_constructions = yes }") in line
    assert "add_building_construction = { type = bunker level = 1 province = 302" in line
    assert line.count("LLMB|v5|click|") == 2 and "instant_build = no" in line
    result = _do(channel, "queue_province_building", building="fortification", province=302, count=2)
    assert result.ok and "queued in province 302" in result.message
    held = game.states["13"].province_levels.get("302:bunker", 0)
    assert held + len([q for q in game.province_queue if q[1:] == ("302", "bunker")]) == 2


def test_a_province_the_game_has_no_room_in_is_queued_only_as_far_as_the_slot_test_allows(tmp_path):
    game, channel, _ = _rig(tmp_path)
    game.states["13"].province_levels["303:supply_node"] = 1      # a hub is there: cap is one
    result = _do(channel, "queue_province_building", building="supply_hub", province=303)
    assert not result.ok and result.error_kind == "rejected"
    assert game.province_queue == []
    full = _do(channel, "queue_province_building", building="fortification", province=301, count=10)
    assert full.ok and len(game.province_queue) + game.states["13"].province_levels.get(
        "301:bunker", 0) == 10
    over = _do(channel, "queue_province_building", building="fortification", province=301, count=2)
    assert not over.ok                                            # ten levels already queued or built


def test_province_buildings_a_state_you_do_not_own_and_unknown_ones_are_declined_or_refused(tmp_path):
    game, channel, _ = _rig(tmp_path)
    abroad = _do(channel, "queue_province_building", building="fortification", province=401)
    assert not abroad.ok and abroad.error_kind == "rejected" and game.province_queue == []
    ghost = _do(channel, "queue_province_building", building="fortification", province=999)
    assert not ghost.ok and ("no province" in ghost.message or "no state" in ghost.message)
    game.states["13"].coastal = False
    inland = _do(channel, "queue_province_building", building="naval_base", province=301)
    assert not inland.ok and inland.error_kind == "rejected"      # a naval base needs a coast
    assert game.province_queue == []
    game.states["13"].coastal = True
    assert _do(channel, "queue_province_building", building="naval_base", province=301).ok


def test_railways_are_not_offered_and_queue_construction_points_at_the_province_action(tmp_path):
    _game, channel, _ = _rig(tmp_path)
    assert "rail" not in catalog.get("queue_province_building").parameters["properties"][
        "building"]["enum"]
    forts = _do(channel, "queue_construction", building="fortification", state="13")
    assert not forts.ok and "queue_province_building" in forts.message


# --- division templates ---------------------------------------------------------------------


def test_a_template_is_created_when_every_unit_is_unlocked_and_never_edited(tmp_path):
    game, channel, _ = _rig(tmp_path)
    arguments = {"name": "Standard_Inf", "battalions": [{"type": "infantry", "count": 7},
                                                         {"type": "artillery_brigade", "count": 2}],
                 "support": ["engineer"]}
    plan = channel.plan(ActionCall("design_division_template", arguments), channel._read_state())
    (line,) = plan.lines
    assert 'NOT = { has_template = "Standard_Inf" }' in line and "has_tech = infantry_tech" in line
    assert "has_tech = engineers_tech" in line and "artillery_brigade" in line
    assert "infantry = { x = 1 y = 1 }" in line and "artillery_brigade = { x = 2 y = 0 }" in line
    assert "engineer = { x = 0 y = 0 }" in line
    declined = _do(channel, "design_division_template", **arguments)
    assert not declined.ok and declined.error_kind == "rejected"        # the technologies are missing
    assert game.templates == {}
    game.researched |= {"infantry_tech", "engineers_tech"}
    made = _do(channel, "design_division_template", **arguments)
    assert made.ok and "template Standard_Inf created" in made.message
    assert game.templates["Standard_Inf"]["regiments"].count("infantry") == 7
    edited = _do(channel, "design_division_template", name="Standard_Inf",
                 battalions=[{"type": "infantry", "count": 3}])
    assert not edited.ok and edited.error_kind == "rejected"             # a name in use is declined
    assert len(game.templates["Standard_Inf"]["regiments"]) == 9


@pytest.mark.parametrize("arguments, why", [
    ({"name": "has space", "battalions": [{"type": "infantry", "count": 1}]}, "1 to 40 letters"),
    ({"name": "t", "battalions": [{"type": "engineer", "count": 1}]}, "not a battalion type"),
    ({"name": "t", "battalions": [{"type": "infantry", "count": 1}], "support": ["infantry"]},
     "not a support company"),
    ({"name": "t", "battalions": [{"type": "infantry", "count": 1}], "support": ["recon"]},
     "nothing in this playset unlocks recon"),
    ({"name": "t", "battalions": [{"type": "infantry", "count": 26}]}, "five columns"),
    ({"name": "t", "battalions": [{"type": "infantry", "count": 1}], "support": ["engineer"] * 2},
     "each type once"),
    ({"name": "t", "battalions": []}, "at least one battalion"),
])
def test_a_template_the_designer_would_not_accept_is_refused_before_anything_is_posted(
        tmp_path, arguments, why):
    _game, channel, _ = _rig(tmp_path)
    result = _do(channel, "design_division_template", **arguments)
    assert not result.ok and why in result.message and "nothing was sent" in result.message
    assert channel.inbox.pending == []


# --- occupation law per state ---------------------------------------------------------------


def test_an_occupation_law_can_be_set_for_one_state_you_control_and_do_not_own(tmp_path):
    game, channel, _ = _rig(tmp_path)
    plan = channel.plan(ActionCall("set_occupation_law", {"law": "martial_law_occupation",
                                                          "state": "20"}), channel._read_state())
    assert "20 = { is_controlled_by = SWE NOT = { is_owned_by = SWE } }" in plan.lines[0]
    assert "20 = { set_occupation_law = martial_law_occupation }" in plan.lines[0]
    own = _do(channel, "set_occupation_law", law="martial_law_occupation", state="10")
    assert not own.ok and own.error_kind == "rejected"                # you own it: nothing to occupy
    not_held = _do(channel, "set_occupation_law", law="martial_law_occupation", state="20")
    assert not not_held.ok and game.state_occupation_laws == {}       # you do not control it yet
    game.states["20"].controller = "SWE"
    done = _do(channel, "set_occupation_law", law="martial_law_occupation", state="20")
    assert done.ok and "state 20" in done.message
    assert game.state_occupation_laws == {"20": "martial_law_occupation"}
    both = _do(channel, "set_occupation_law", law="martial_law_occupation", state="20",
               occupied="POL")
    assert not both.ok and "not both" in both.message
    ghost = _do(channel, "set_occupation_law", law="martial_law_occupation", state="9999")
    assert not ghost.ok and "no state" in ghost.message


# --- the steers -----------------------------------------------------------------------------


def test_experience_and_political_power_spending_is_steered_by_level(tmp_path):
    game, channel, _ = _rig(tmp_path)
    assert _do(channel, "steer_spending", kind="land_xp", id="division_template", level="more").ok
    assert _do(channel, "steer_spending", kind="navy_xp", id="navy_spirit", level="much_more").ok
    assert _do(channel, "steer_spending", kind="pp", id="relation", level="less").ok
    assert game.flags >= {"llmb_steer_spending_land_xp_division_template_more",
                          "llmb_steer_spending_navy_xp_navy_spirit_much_more",
                          "llmb_steer_spending_pp_relation_less"}
    assert _do(channel, "steer_spending", kind="land_xp", id="division_template", level="much_more").ok
    assert "llmb_steer_spending_land_xp_division_template_more" not in game.flags
    for arguments, why in (({"kind": "air_xp", "id": "navy_spirit", "level": "more"}, "not a air_xp id"),
                           ({"kind": "land_xp", "level": "more"}, "needs an id"),
                           ({"kind": "pp", "id": "relation", "level": "first"}, "no level")):
        result = _do(channel, "steer_spending", **arguments)
        assert not result.ok and why in result.message, (arguments, result.message)


def test_the_market_steers_take_an_archetype_a_country_or_nothing(tmp_path):
    game, channel, _ = _rig(tmp_path)
    assert _do(channel, "steer_market", kind="buy", id="infantry_equipment", level="more").ok
    assert _do(channel, "steer_market", kind="buy_from", id="GER", level="much_more").ok
    assert _do(channel, "steer_market", kind="trade_desire", id="POL", level="less").ok
    assert _do(channel, "steer_market", kind="spend", level="more").ok
    # sell was dropped after the first live run: equipment_market_for_sale_factor takes the game's
    # own category tokens (armor, train), not archetypes, and 138 lines failed to load.
    assert not _do(channel, "steer_market", kind="sell", id="artillery_equipment", level="less").ok
    assert game.flags >= {"llmb_steer_market_buy_infantry_equipment_more",
                          "llmb_steer_market_buy_from_GER_much_more",
                          "llmb_steer_market_trade_desire_POL_less", "llmb_steer_market_spend_any_more"}
    for arguments, why in (({"kind": "buy", "id": "nonsense_equipment", "level": "more"},
                            "not an equipment archetype id"),
                           ({"kind": "buy_from", "id": "gerx", "level": "more"}, "country tag"),
                           ({"kind": "buy", "id": "infantry_equipment", "level": "less"}, "no level"),
                           ({"kind": "spend", "id": "GER", "level": "more"}, "takes no id")):
        result = _do(channel, "steer_market", **arguments)
        assert not result.ok and why in result.message, (arguments, result.message)


def test_army_and_supply_steers_including_the_fuel_thresholds(tmp_path):
    game, channel, _ = _rig(tmp_path)
    assert _do(channel, "steer_army", kind="template", id="light_armor", level="more").ok
    assert _do(channel, "steer_army", kind="roles", id="garrison", level="less").ok
    assert _do(channel, "steer_army", kind="fuel", level="much_more").ok
    assert _do(channel, "steer_army", kind="trucks", level="more").ok
    assert game.flags >= {"llmb_steer_army_template_light_armor_more",
                          "llmb_steer_army_roles_garrison_less",
                          "llmb_steer_army_fuel_any_much_more", "llmb_steer_army_trucks_any_more"}
    assert not _do(channel, "steer_army", kind="fuel", level="less").ok          # no such level
    assert not _do(channel, "steer_army", kind="roles", id="cavalry", level="more").ok


def test_new_production_and_intelligence_kinds_ride_the_existing_actions(tmp_path):
    game, channel, _ = _rig(tmp_path)
    assert _do(channel, "steer_production", kind="min_factories", id="motorized", level="much_more").ok
    assert _do(channel, "steer_production", kind="military_share", level="more").ok
    assert _do(channel, "steer_production", kind="surplus", level="less").ok
    assert _do(channel, "steer_intelligence", kind="spymaster", level="more").ok
    assert game.flags >= {"llmb_steer_production_min_factories_motorized_much_more",
                          "llmb_steer_production_military_share_any_more",
                          "llmb_steer_production_surplus_any_less",
                          "llmb_steer_intelligence_spymaster_any_more"}


def test_every_new_steer_has_documented_syntax_and_is_in_the_shipped_mod():
    text = (MOD / steering.FILE).read_text(encoding="utf-8")
    regions = (MOD / steering.REGIONS_FILE).read_text(encoding="utf-8")
    for line in (
        "type = land_xp_spend_priority id = unlock_doctrine value = 100",
        "type = air_xp_spend_priority id = air_spirit value = 300",
        "type = navy_xp_spend_priority id = equipment_variant value = -100",
        "type = pp_spend_priority id = political_advisor value = 200",
        "type = pp_spend_amount id = idea value = 500",
        "type = template_prio id = medium_armor value = 150",
        "type = role_ratio id = marines value = -50",
        "type = ai_wanted_divisions_factor value = 240",
        "type = force_build_armies value = 100",
        "type = stockpile_fuel stop_above = 90",
        "type = stockpile_fuel start_below = 40 stop_above = 90",
        "type = min_wanted_supply_trucks value = 300",
        "type = min_wanted_supply_trains value = 30",
        "type = equipment_market_spend_factories value = 20",
        "type = equipment_production_min_factories id = motorized value = 10",
        "type = added_military_to_civilian_factory_ratio value = 50",
        "type = dockyard_to_military_factory_ratio value = 20",
        "type = air_factory_balance value = 15",
        "type = equipment_stockpile_surplus_ratio value = -25",
        "type = become_spymaster value = 100",
    ):
        assert line in text, line
    for line in ("type = equipment_market_buy equipment_type = infantry_equipment value = 100",
                 "type = equipment_market_buy seller = GER value = 200",
                 "type = equipment_market_trade_desire id = POL value = 30"):
        assert line in regions, line
    assert "equipment_market_for_sale_factor" not in regions        # dropped after the live run
    assert modgen.drift(MOD) == []


def test_tokens_without_documented_syntax_are_not_offered():
    used = {spec.strategy for _a, _k, spec in steering.specs() + steering.specs(dynamic=True)}
    for token in ("fuel_buffer", "wanted_supply_trucks", "wanted_supply_trains", "occupation_policy",
                  "decrypt_target", "activate_crypto"):
        assert token not in used
