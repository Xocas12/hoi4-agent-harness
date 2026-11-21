"""Full observation: every panel a player opens, from a synthetic text save, gated by intel.

The save is written in the layout a 1.20 text save has (key paths checked against a real 1936
autosave and a 1941 save, see docs/observation.md). A small serializer turns dicts into
Clausewitz text so each test states only the facts it is about: ``Items`` is a ``{ a b c }``
list, ``Anon`` a list of bare ``{ ... }`` entries, a Python list a repeated key.
"""

from __future__ import annotations

import json

import pytest

from hoi4_harness import world as W
from hoi4_harness import world_full as F
from hoi4_harness.actions.catalog import ACTIONS
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall
from orders_helpers import load_layer, write_grid_game


class Items(list):
    """``key={ a b c }``."""


class Anon(list):
    """``key={ { ... } { ... } }``: bare entries, which the parser files under ``_items``."""


def emit(value, depth: int = 0) -> str:
    pad = "\t" * depth
    out = []
    for key, item in value.items():
        for one in item if isinstance(item, list) and not isinstance(item, (Items, Anon)) else [item]:
            if isinstance(one, Items):
                out.append(f"{pad}{key}={{ {' '.join(str(x) for x in one)} }}\n")
            elif isinstance(one, Anon):
                out.append(f"{pad}{key}={{\n")
                for entry in one:
                    out.append(f"{pad}\t{{\n{emit(entry, depth + 2)}{pad}\t}}\n")
                out.append(f"{pad}}}\n")
            elif isinstance(one, dict):
                out.append(f"{pad}{key}={{\n{emit(one, depth + 1)}{pad}}}\n")
            else:
                out.append(f"{pad}{key}={one}\n")
    return "".join(out)


def ref(ident, kind=70) -> dict:
    return {"id": ident, "type": kind}


def inline(ident, kind=70) -> str:
    return f"{{ id={ident} type={kind} }}"


def character(ident, token, country, **extra) -> str:
    body = {"id": inline(ident, 73), "token": f'"{token}"', "country": f'"{country}"', **extra}
    return "\t\tcharacter={\n" + emit(body, 3) + "\t\t}\n"


def division(ident, province, template=1, supply=100):
    return {"id": inline(ident, 51), "location": province,
            "division_template_id": inline(template, 52), "organisation": 50, "dig_in": 2,
            "army_current_supply_ratio": supply,
            "equipment": {"equipment": [{"id": inline(5), "amount": 30}], "allow_zero_entries": "no"}}


def ship(ident, kind, org=40):
    return {"id": inline(ident, 51), "definition": kind, "organisation": org, "strength": 100}


def wing(ident, name, count, mission=None):
    active = mission is not None
    return {"id": inline(ident, 69), "count": count, "name": f'"{name}"',
            "mission": {"type": 1, "active": "yes" if active else "no",
                        **({"executing_mission": mission, "strategic_region": 39} if active else {})}}


def war(first, second, mine_first, since="1939.9.1.1"):
    ours = {"province_capture": 120.5, "equipment_damage": 40, "casualties": 3}
    return {"war_relation": {
        "first": f'"{first}"', "second": f'"{second}"', "start_date": f'"{since}"',
        "first_casualties": 1000, "second_casualties": 400, "first_was_instigator": "yes",
        "war_score_first_vs_second": {"first": f'"{first}"', "second": f'"{second}"', **(
            ours if mine_first else {"province_capture": 5})},
        "war_score_second_vs_first": {"first": f'"{second}"', "second": f'"{first}"', **(
            {"province_capture": 5} if mine_first else ours)}}}


def swe_block() -> dict:
    return {
        "politics": {
            "parties": {
                "democratic": {"default": "yes", "popularity": 55, "country_leader": Anon(
                    [{"ideology": '"conservatism"', "character": inline(2001, 73)}])},
                "fascism": {"default": "yes", "popularity": 12, "country_leader": Anon(
                    [{"ideology": '"nazism"', "character": inline(2002, 73)}])}},
            "ideas": Items(["limited_exports", "SWE_neutrality_spirit", "SWE_per_albin_hansson"]),
            "ruling_party": "democratic", "elections_allowed": "yes", "last_election": '"1936.9.1.1"',
            "election_frequency": 48, "political_power": 80.5},
        "diplomacy": {"active_relations": {
            "SWE": {"cached_sum": -100},
            "GER": {"cached_sum": -10, "attitude": '"attitude_hostile"', **war("SWE", "GER", True)},
            "NOR": {"cached_sum": 60, "attitude": '"attitude_friendly"',
                    "guarantee": {"first": '"SWE"', "second": '"NOR"', "start_date": '"1937.1.1.1"'}},
            "FIN": {"cached_sum": 40, "puppet": {"autonomy_state": '"autonomy_dominion"',
                                                  "first": '"SWE"', "second": '"FIN"',
                                                  "start_date": '"1938.1.1.1"'}},
            "USA": {"cached_sum": 20, "lend_lease": {"first": '"USA"', "second": '"SWE"',
                                                      "start_date": '"1939.5.1.1"'}},
            "DEN": {"cached_sum": 5, "attitude": '"attitude_neutral"'}},
            "wargoals": {"take_state": {"id": inline(9, 4713), "wargoaldata_recipient": '"GER"',
                                        "states": Items([12]), "type": "take_state"}}},
        "intelligence_agency": {
            "name": '"Karlsborg"', "is_created": "yes", "upgrade_progress": 40,
            "upgrade": "upgrade_portable_radios",
            "upgrades": {"upgrade_army_department": 1, "upgrade_decryption_boost": 2},
            "operative": [
                {"id": inline(31, 4713), "name": '"Anna Berg"', "skill": 2,
                 "traits": Items(["operative_linguist"]),
                 "mission": {"build_intel_network": {"target": '"GER"', "state": 12}},
                 "state": "on_mission", "nationalities": Items(["SWE"])},
                {"id": inline(32, 4713), "name": '"Nils Dal"', "skill": 1, "traits": Items([]),
                 "state": "available"}],
            "captured": Anon([{"country": '"SWE"', "operative": inline(40, 4713),
                               "intel": {"civilian": 12.5, "army": 12.5}}]),
            "cryptology": {"targets": Anon([{"target": '"GER"', "amount": 9000}])},
            "defense": 4.5, "max_operative_count": 3, "usable_operative_slots": 2},
        "operations": {"finished": {"operation_infiltrate_armed_forces_army": Items(['"GER"', 1])}},
        "program_status": {
            "project_pool": {"project": {
                "sp_land_flamethrower_tank": {"id": ref(1, 86), "start": "no", "completed": "yes"},
                "sp_naval_midget_submarine": {"id": ref(2, 86), "start": "yes", "completed": "no",
                                              "progress": {"current_state": 1, "prototype_state": {
                                                  "project_progress": 41.5, "iterations": 3}}},
                "sp_air_radar": {"id": ref(3, 86), "start": "no", "completed": "no"}}},
            "program": {"id": ref(2, 83), "country": '"SWE"', "province": 6, "building": '"land_facility"',
                        "scientist": {"character": inline(2010, 73)}},
            "breakthrough": {"specialization_land": 100, "specialization_naval": 20}},
        "characters": {"character_status": [
            {"character": inline(2010, 73), "country_leader": "no", "advisor": "no",
             "unit_leader": "no", "scientist": "yes"},
            {"character": inline(2011, 73), "country_leader": "no", "advisor": "yes",
             "unit_leader": "no", "scientist": "no"},
            {"character": inline(2020, 73), "country_leader": "no", "advisor": "no",
             "unit_leader": "yes", "scientist": "no"},
            {"character": inline(2021, 73), "country_leader": "no", "advisor": "no",
             "unit_leader": "yes", "scientist": "no"}]},
        "research_slot": 3,
        "technology": {
            "technologies": {
                "basic_train": {"level": 1, "research_points": 165, "date": '"1936.1.1.12"'},
                "gw_artillery": {"level": 1, "date": '"1937.3.1.1"'},
                "infantry_weapons1": {"research_points": 23.5, "date": '"1.1.1.1"',
                                      "locked_design_team": '"SWE_bofors_organization"'}},
            "slots": {"infantry_weapons1": {"points_factor": 1.2},
                      "empty": [{"points": 30}, {"points": 30}]}},
        "production": {
            "equipments": {"equipment": [{"id": inline(5), "amount": 3200}, {"id": inline(6), "amount": 40}],
                           "allow_zero_entries": "no"},
            "industrial_organisations": {"SWE_bofors_organization": {"id": ref(1, 79), "name": '""'}},
            "military_lines": {"id": ref(5, 56), "equipment_variant_index": ref(5, 70),
                               "requested_factories": 3, "active_factories": 3}},
        "units": {"division": [division(1, 6), division(2, 6, supply=40), division(3, 1, supply=55)],
                  "fleet": [{"id": ref(1, 61), "name": '"Flottan"', "home_base": 241, "task_force": [
                      {"id": ref(43, 51), "name": '"Kustflottan"', "location": 6,
                       "last_combat_date": '"1.1.1.1"',
                       "mission": {"mission": 1}, "ship": [ship(31, "destroyer"), ship(32, "destroyer"),
                                                           ship(33, "battleship")]},
                      {"id": ref(44, 51), "name": '"Ubatar"', "location": 2, "last_combat_date": '"1.1.1.1"',
                       "mission": {"mission": 3}, "ship": [ship(34, "submarine")]}]}]},
        "fuel_status": {"fuel": 1200.5, "max_fuel": 5000, "fuel_gain": 2, "fuel_cost": 0.5},
        "command_power": 14.25, "stability": 0.7, "war_support": 0.4, "num_ships": 4,
        "convoys": {"equipment": {"equipment": {"id": inline(1), "amount": 40}}},
        "nukes": Anon([{"amount": 0, "nukes_ready": 0}]),
        "focus_tree": '"swedish_focus"', "focus": {"progress": 6, "current": '"SWE_rearm"',
                                                    "completed": ['"SWE_a"', '"SWE_b"']},
        "decision_status": {"active_timed_decision": {"decision": '"SWE_rearm_mission"', "days": 30,
                                                        "state": "active"},
                            "active_targeted_decision": [{"decision": '"SWE_press"', "target": "NOR",
                                                          "state": "available"}]},
        "occupation_status": {"default_law": '"military_governor_occupation"'},
        "resources": {"delivery_routes": {
            "SWE": {"sender": '"SWE"', "receiver": '"SWE"'},
            "GER": {"type": 2, "from_state": 10, "to_state": 12, "sender": '"SWE"', "receiver": '"GER"'}}},
    }


def ger_block() -> dict:
    return {
        "politics": {"parties": {"fascism": {"default": "yes", "popularity": 62, "country_leader": Anon(
            [{"ideology": '"nazism"', "character": inline(2003, 73)}])},
            "democratic": {"default": "yes", "popularity": 23, "country_leader": Anon([])}},
            "ideas": Items(["war_economy", "GER_four_year_plan"]), "ruling_party": "fascism"},
        "diplomacy": {"active_relations": {
            "SWE": {"cached_sum": -10, **war("GER", "SWE", False)},
            "AUS": {"cached_sum": 30, "guarantee": {"first": '"GER"', "second": '"AUS"',
                                                     "start_date": '"1937.1.1.1"'}}}},
        "production": {"equipments": {"equipment": [{"id": inline(5), "amount": 1234}]}},
        "units": {"division": [division(10, 7, 3), division(11, 7, 3)],
                  "fleet": [{"id": ref(7, 61), "name": '"Kriegsmarine"', "task_force": [
                      {"id": ref(70, 51), "name": '"Hochseeflotte"', "location": 5,
                       "mission": {"mission": 1}, "ship": [ship(90 + i, "destroyer") for i in range(7)]
                       + [ship(100, "battleship")]}]}]},
        # present in every real country block, and must never reach the model for another country
        "intelligence_agency": {"name": '"Abwehr"', "is_created": "yes",
                                "operative": [{"id": inline(77, 4713), "name": '"Secret"', "skill": 3}]},
        "program_status": {"project_pool": {"project": {
            "sp_air_radar": {"id": ref(3, 86), "start": "yes", "completed": "no"}}}},
        "focus": {"current": '"GER_secret_focus"'},
    }


def provinces_block() -> str:
    body = {
        "3": {"controller": '"SWE"'},
        "7": {"controller": '"SWE"', "victory_points": 5},
        "4": {"victory_points": 3},
        "6": {"buildings": {"supply_node": {"level": 2}, "rail_way": {"level": 3}}},
        "2": {"buildings": {"rail_way": {"level": 1}}},
        "10": {"controller": '"GER"', "victory_points": 2},
    }
    return "provinces={\n" + emit(body, 1) + "}\n"


def states_block() -> str:
    def state(owner, civ=1, controller=None, resistance=None, mp=1000):
        body = {"buildings": {"industrial_complex": {"level": civ}}, "owner": f'"{owner}"',
                "manpower_pool": {"available": mp, "locked": mp * 10, "total": mp * 11},
                "resistance": resistance or {}}
        if controller:
            body["controller"] = f'"{controller}"'
        return body

    body = {"10": state("SWE", mp=500), "11": state("SWE", controller="GER", resistance={
        "resistance": 20, "resistance_target": 25, "compliance": 5, "occupied_country_tag": '"SWE"'}),
        "12": state("GER", controller="SWE", resistance={
            "resistance": 70.3, "resistance_speed": -0.1, "resistance_target": 70, "compliance": 10.2,
            "occupied_country_tag": '"GER"', "operational_status": "yes"}),
        "13": state("GER")}
    return "states={\n" + emit(body, 1) + "}\n"


def faction(members, template, leader_change="1936.1.1.12", **extra) -> str:
    body = {"id": ref(len(members), 88), "ideology": "democratic", "members": Anon([]),
            "template": f'"faction_template_{template}"', "faction_leader_change_date": f'"{leader_change}"',
            "rule_status": {"rules": Items(["joining_rule_non_fascist", "faction_peace_rule_x"])},
            "goal_status": {"manifest": "faction_manifest_defense",
                            "goals": Anon([{"goal": "faction_manifest_defense", "status": "active"},
                                           {"goal": "faction_goal_rule_the_waves", "status": "active"}])},
            "upgrades": {"slots": Anon([{"spymaster": {"name": '"Head of Cryptology"'}}]),
                         "tech_sharing_group": {"countries": Items([f'"{m}"' for m in members])}},
            "research": "yes", "manpower": "no", **extra}
    text = emit({k: v for k, v in body.items() if k != "members"}, 1)
    return "faction={\n" + text + "\tmembers={\n" + "".join(f'\t\t"{m}"\n' for m in members) + "\t}\n}\n"


def full_save(date="1936.3.1.12", player="GER") -> str:
    countries = {"SWE": swe_block(), "GER": ger_block()}
    body = "".join(f"\t{tag}={{\n{emit(block, 2)}\t}}\n" for tag, block in countries.items())
    characters = "".join([
        character(2001, "SWE_per_albin_hansson", "SWE"),
        character(2002, "SWE_sven_olov_lindholm", "SWE"),
        character(2003, "GER_adolf_hitler", "GER"),
        character(2010, "SWE_hannes_alfven", "SWE", scientist={
            "traits": Items(['"scientist_trait_artillery_specialist"']),
            "skills": {"specialization_land": {"level": 2}, "specialization_naval": {}}}),
        character(2020, "SWE_archibald_douglas", "SWE", corps_commander={
            "id": inline(500, 4713), "skill": 3, "attack_skill": 4, "defense_skill": 2,
            "planning_skill": 3, "logistics_skill": 1, "traits": Items(["armor_officer", "trickster"])}),
        character(2021, "SWE_helge_jung", "SWE", field_marshal={
            "id": inline(501, 4713), "skill": 4, "attack_skill": 3, "defense_skill": 5,
            "planning_skill": 4, "logistics_skill": 2, "traits": Items([])}),
        character(2011, "SWE_per_albin_hansson_advisor", "SWE", advisors={"advisor": {
            "slot": '"political_advisor"', "idea_token": '"SWE_per_albin_hansson"',
            "traits": Items(['"SWE_social_democrat"'])}}),
    ])
    air = {
        "air_base": [{"id": ref(9, 65), "state": 10, "capacity": 100},
                     {"id": ref(8, 65), "state": 13, "capacity": 100}],
        "SWE": {"air_wing_pool": [
            {"definition": "fighter", "air_base": inline(9, 65),
             "air_wings": [wing(1, "Jaktflottilj 1", 60, "air_superiority"), wing(2, "Jaktflottilj 2", 40)]},
            {"definition": "tac_bomber", "air_base": inline(9, 65),
             "air_wings": [wing(3, "Bomb 1", 30, "cas")]}]},
        "GER": {"air_wing_pool": {"definition": "fighter", "air_base": inline(8, 65),
                                  "air_wings": wing(20, "JG 1", 130, "air_superiority")}},
    }
    top = emit({"supply_system_2": {"countries": {"SWE": {
        "truck": Anon([{"id": ref(1), "count": 12}]), "buffer": 1.5, "wanted_supply_trucks": 30,
        "lost_railways": Items([0, 2, 1]), "lost_trains": Items([1, 0]),
        "lost_trucks_attrition": Items([4]), "lost_trucks_killed": Items([0])}}}}, 0)
    doctrine = emit({"doctrine": {"countries": Anon([
        {"folder": Anon([{"folder": "land"}])},
        {"folder": Anon([{"grand_doctrine": "new_grand_battleplan", "folder": "land", "tracks": Anon([
            {"sub_doctrine": "fire_concentration", "rewards": 2, "mastery": 245.1},
            {"rewards": 0, "mastery": 0}])}])},
        {"folder": Anon([{"folder": "land"}])}])}}, 0)
    operatives = emit({"strategic_operatives": {"strategic_operative": {
        "0": {}, "1": {"intel_network": [
            {"target": '"GER"', "intel_network": {
                "vertices": 4, "state_id": Items([12, 13, 11, 10]),
                "strength": Items([100, 50, 0, 0])}},
            {"target": '"NOR"', "intel_network": {"vertices": 1, "state_id": Items([20]),
                                                   "strength": Items([20])}}]}}}}, 0)
    return (
        f'HOI4txt\ndate="{date}"\nplayer="{player}"\n'
        "equipments={\n\tinfantry_equipment_1={\n\t\tid={ id=5 type=70 }\n\t}\n"
        "\tartillery_equipment_1={\n\t\tid={ id=6 type=70 }\n\t}\n}\n"
        + states_block() + provinces_block()
        + "division_templates={\n"
        + "".join(f'\tdivision_template={{\n\t\tid={{ id={i} type=52 }}\n\t\tname="{n}"\n'
                  f'\t\tcountry="{c}"\n\t}}\n'
                  for i, n, c in ((1, "Infantry", "SWE"), (3, "Infanterie", "GER")))
        + "}\n"
        "character_manager={\n\thistorical={\n" + characters + "\t}\n}\n"
        "countries={\n" + body + "}\n"
        + faction(["SWE", "NOR", "FIN"], "nordic", ideology="democratic")
        + faction(["GER", "ITA"], "axis").replace("ideology=democratic", "ideology=fascism")
        + "strategic_air={\n" + emit(air, 1) + "}\n"
        + operatives + doctrine + top)


@pytest.fixture
def grid(tmp_path):
    return load_layer(write_grid_game(tmp_path / "game"))


@pytest.fixture
def save(tmp_path):
    folder = tmp_path / "save games"
    folder.mkdir()
    path = folder / "autosave.hoi4"
    path.write_text(full_save(), encoding="utf-8")
    return path


def world_of(save, grid):
    world = W.read_world(save, "SWE", layer=grid)
    assert world.full_error is None, world.full_error
    return world


def ask(world, grid, view, ident=None, intel=None, me="SWE"):
    pic = W.picture(world, me, intel if intel is not None else {}, grid)
    return W.query(pic, view, ident, None, grid, 60)


EXACT = {"GER": W.Intel(civilian=0.9, army=0.9, navy=0.9, air=0.9)}
MIDDLE = {"GER": W.Intel(civilian=0.5, army=0.5, navy=0.5, air=0.5)}
BLIND = {"GER": W.Intel(civilian=0.1, army=0.1, navy=0.1, air=0.1)}


# --- the shared parts ------------------------------------------------------------------------------


def test_country_index_and_the_per_country_arrays(save, grid):
    world = world_of(save, grid)
    assert world.full.order == ["SWE", "GER"]
    # SWE is country 1: its doctrine entry and its intelligence networks are entry 1
    own = world.countries["SWE"].full
    assert [(d["folder"], d["doctrine"]) for d in own.doctrines] == [("land", "new_grand_battleplan")]
    assert own.doctrines[0]["tracks"] == [
        {"sub_doctrine": "fire_concentration", "mastery": 245, "rewards": 2}]
    assert [(n["target"], n["nodes"], n["nodes_active"], n["strength"]) for n in world.full.networks] == [
        ("GER", 4, 2, 37.5), ("NOR", 1, 1, 20.0)]
    assert world.full.networks[0]["states_covered"] == [12, 13]


# --- faction and diplomacy -------------------------------------------------------------------------


def test_faction_members_goals_rules_and_what_a_non_member_does_not_see(save, grid):
    world = world_of(save, grid)
    mine = ask(world, grid, "faction")
    assert mine["template"] == "nordic" and mine["leader"] == "SWE"
    assert mine["members"] == ["SWE", "NOR", "FIN"]
    assert mine["manifest"] == "Defense" and [g["goal"] for g in mine["goals"]] == ["Rule the waves"]
    assert "Joining rule non fascist" in mine["rules"] and mine["upgrade_slots"] == ["Head of Cryptology"]
    assert mine["research_sharing"] is True
    other = ask(world, grid, "faction", "GER")
    assert other["template"] == "axis" and other["members"] == ["GER", "ITA"]
    assert "upgrade_slots" not in other and "research_sharing" not in other     # members only
    assert ask(world, grid, "faction", "FRA")["faction"] is None


def test_diplomacy_wars_with_score_guarantees_puppets_and_lend_lease(save, grid):
    world = world_of(save, grid)
    mine = ask(world, grid, "diplomacy")
    war = mine["wars"]["GER"]
    assert war["since"] == "1939.9.1.1" and war["casualties_ours"] == 1000 and war["casualties_theirs"] == 400
    assert war["score_ours"] == {"province_capture": 120.5, "equipment_damage": 40.0, "casualties": 3.0}
    assert war["score_theirs"] == {"province_capture": 5.0}
    assert mine["treaties"]["NOR"]["guarantee"]["direction"] == "to"
    assert mine["treaties"]["FIN"]["puppet"] == {
        "direction": "to", "since": "1938.1.1.1", "autonomy": "dominion"}
    assert mine["treaties"]["USA"]["lend_lease"]["direction"] == "from"
    assert "DEN" not in mine["treaties"]                                       # opinion only
    assert mine["war_goals"] == [{"type": "take_state", "target": "GER", "states": [12]}]
    # the other side's war reads from its own block: the score is flipped to its point of view
    ger = ask(world, grid, "diplomacy", "GER", EXACT)
    assert ger["wars"]["SWE"]["score_ours"] == {"province_capture": 5.0}
    assert ger["treaties"]["AUS"]["guarantee"]["direction"] == "to"
    assert ger["faction"] == ["GER", "ITA"] and ger["towards_you"] == {"opinion": -10, "attitude": "hostile"}


# --- politics --------------------------------------------------------------------------------------


def test_politics_laws_spirits_advisors_and_leaders(save, grid):
    politics = ask(load := world_of(save, grid), grid, "politics")
    assert load.full is not None
    assert politics["ruling_party"] == "democratic" and politics["parties"] == {
        "democratic": 55.0, "fascism": 12.0}
    assert politics["leaders"]["democratic"] == "Per Albin Hansson (conservatism)"
    assert politics["laws"] == ["limited_exports"]
    assert politics["spirits"] == ["SWE_neutrality_spirit"]
    assert politics["advisors"] == [{"name": "Per Albin Hansson Advisor", "slot": "political_advisor",
                                     "traits": ["Social democrat"]}]
    assert politics["elections_allowed"] is True and politics["political_power"] == 80.5


def test_politics_of_another_country_follows_civilian_intel(save, grid):
    world = world_of(save, grid)
    exact = ask(world, grid, "politics", "GER", EXACT)
    assert exact["parties"]["fascism"] == 62.0 and exact["laws"] == ["war_economy"]
    assert exact["spirits"] == ["GER_four_year_plan"]
    estimate = ask(world, grid, "politics", "GER", MIDDLE)
    assert estimate["parties"] == {"fascism": 60, "democratic": 25} and "spirits" not in estimate
    blind = ask(world, grid, "politics", "GER", BLIND)
    assert blind["ruling_party"] == "fascism" and "parties" not in blind and "laws" not in blind


# --- intelligence agency, projects -----------------------------------------------------------------


def test_agency_upgrades_operatives_networks_and_the_intel_ledger(save, grid):
    world = world_of(save, grid)
    intel = ask(world, grid, "intel", None, {"GER": W.Intel(0.725, 0.4, None, 0.1)})
    agency = intel["agency"]
    assert agency["name"] == "Karlsborg" and agency["created"] and agency["operative_slots"] == 3
    assert agency["upgrades"] == {"army_department": 1, "decryption_boost": 2}
    assert agency["upgrade_in_progress"] == "upgrade_portable_radios"
    anna = agency["operatives"][0]
    assert (anna["name"], anna["skill"], anna["state"]) == ("Anna Berg", 2, "on_mission")
    assert anna["mission"] == {"type": "build_intel_network", "target": "GER", "state": "12"}
    assert agency["captured"][0]["intel_gained"] == {"civilian": 12.5, "army": 12.5}
    assert agency["decrypting"] == [{"target": "GER", "amount": 9000}]
    assert agency["operations_done"] == {"Infiltrate armed forces army": 1}
    assert intel["networks"][0]["target"] == "GER"
    assert intel["intel_levels_percent"] == {"GER": {"civilian": 72, "army": 40, "air": 10}}


def test_special_projects_facilities_and_scientists(save, grid):
    projects = ask(world_of(save, grid), grid, "projects")
    assert projects["completed"] == ["sp_land_flamethrower_tank"]
    assert projects["active"] == [{"project": "sp_naval_midget_submarine", "phase": "1",
                                   "progress": 41.5, "iterations": 3}]
    assert projects["not_started"] == 1
    facility = projects["facilities"][0]
    assert facility["type"] == "land_facility" and facility["state"] == 11
    assert facility["scientist"]["name"] == "Hannes Alfven"
    assert projects["scientists"] == [{"name": "Hannes Alfven", "traits": ["Artillery specialist"],
                                       "skills": {"land": 2}}]
    assert projects["breakthrough"] == {"land": 100, "naval": 20}


def test_another_countrys_agency_projects_and_decisions_are_never_shown(save, grid):
    world = world_of(save, grid)
    # views that exist only for the own country answer from the own block, whatever id is asked
    for view in ("projects", "intel", "research"):
        text = json.dumps(ask(world, grid, view, "GER", EXACT))
        assert "Secret" not in text and "Abwehr" not in text and "GER_secret_focus" not in text
    assert ask(world, grid, "research")["focus_current"] == "SWE_rearm"


# --- research, decisions ---------------------------------------------------------------------------


def test_research_slots_points_doctrines_decisions_and_focus(save, grid):
    research = ask(world_of(save, grid), grid, "research")
    assert research["slots_total"] == 3 and research["idle_slots"] == 2
    assert research["researching"] == [{"technology": "infantry_weapons1", "points": 23.5,
                                        "design_team": "Bofors organization"}]
    assert research["researched"] == 2 and research["latest"][0] == "gw_artillery"
    assert research["timed_active"] == [{"decision": "SWE_rearm_mission", "days_left": 30}]
    assert research["targeted_available"] == ["SWE_press"]
    assert research["focus_current"] == "SWE_rearm" and research["focuses_completed"] == 2
    assert research["doctrines"][0]["doctrine"] == "new_grand_battleplan"


# --- navy and air, by intel ------------------------------------------------------------------------


def test_own_fleets_by_type_location_and_mission(save, grid):
    navy = ask(world_of(save, grid), grid, "navy")
    assert navy["ships"] == 4 and navy["by_type"] == {"destroyer": 2, "battleship": 1, "submarine": 1}
    first, second = navy["task_forces"]
    assert (first["task_force"], first["location"], first["state"], first["mission"]) == (
        "Kustflottan", 6, 11, "patrol")
    assert first["ships"] == {"destroyer": 2, "battleship": 1}
    assert second["mission"] == "convoy raiding"


def test_foreign_fleet_and_wings_show_as_the_matching_intel_allows(save, grid):
    world = world_of(save, grid)
    exact = ask(world, grid, "navy", "GER", EXACT)
    assert exact["ships"] == 8 and exact["by_type"] == {"destroyer": 7, "battleship": 1}
    assert exact["task_forces"][0]["state"] is None or exact["task_forces"][0]["location"] == 5
    estimate = ask(world, grid, "navy", "GER", MIDDLE)
    assert estimate["ships"] == "~8" and estimate["by_type"] == {"destroyer": "~7", "battleship": "~1"}
    assert "task_forces" not in estimate
    assert ask(world, grid, "navy", "GER", BLIND)["ships"] == "unknown"
    # each branch has its own intel: army intel high, air low shows the army but not the wings
    split = {"GER": W.Intel(civilian=0.9, army=0.9, navy=0.1, air=0.9)}
    assert ask(world, grid, "navy", "GER", split)["ships"] == "unknown"
    assert ask(world, grid, "air", "GER", split)["planes"] == 130
    assert ask(world, grid, "air", "GER", BLIND)["planes"] == "unknown"
    assert ask(world, grid, "air", "GER", MIDDLE)["planes"] == "~130"


def test_own_air_wings_by_type_base_and_mission(save, grid):
    air = ask(world_of(save, grid), grid, "air")
    assert air["planes"] == 130 and air["by_type"] == {"fighter": 100, "tactical bomber": 30}
    first = air["wings"][0]
    assert (first["wing"], first["planes"], first["state"], first["mission"], first["region"]) == (
        "Jaktflottilj 1", 60, 10, "air_superiority", 39)
    assert air["wings"][1]["mission"] == "idle"


# --- economy, equipment, supply, occupation --------------------------------------------------------


def test_economy_stockpile_what_divisions_hold_and_trade(save, grid):
    economy = ask(world_of(save, grid), grid, "economy")
    assert economy["fuel"] == {"stock": 1200, "max": 5000, "gain": 2.0, "cost": 0.5}
    assert economy["manpower"] == {"available": 1500, "population_not_yet_recruitable": 15000}
    assert economy["convoys"] == 40 and economy["command_power"] == 14.2
    assert economy["stockpile"] == {"infantry_equipment_1": 3200, "artillery_equipment_1": 40}
    assert economy["in_service"] == {"infantry_equipment_1": 90}
    assert economy["industrial_organisations"] == ["Bofors organization"]
    assert economy["trade_routes"] == [{"partner": "GER", "direction": "export", "sea": True,
                                        "from_state": 10, "to_state": 12}]
    assert economy["occupation_law"] == "military_governor_occupation"


def test_a_foreign_stockpile_follows_army_intel(save, grid):
    world = world_of(save, grid)
    assert ask(world, grid, "economy", "GER", EXACT)["stockpile"] == {"infantry_equipment_1": 1234}
    assert ask(world, grid, "economy", "GER", MIDDLE)["stockpile"] == {"infantry_equipment_1": "~1230"}
    assert ask(world, grid, "economy", "GER", BLIND)["stockpile"] == "unknown"


def test_supply_hubs_railways_trucks_and_divisions_short_of_supply(save, grid):
    supply = ask(world_of(save, grid), grid, "supply")
    assert supply["system"] == {"trucks": 12, "trucks_wanted": 30, "buffer": 1.5, "railways_lost_recent": 3,
                                "trains_lost_recent": 1, "trucks_lost_attrition_recent": 4,
                                "trucks_lost_killed_recent": 0}
    assert supply["hubs"] == [{"province": 6, "state": 11, "hub_level": 2}]
    assert supply["provinces_with_railway"] == 2 and supply["average_railway_level"] == 2.0
    assert supply["divisions"]["count"] == 3 and supply["divisions"]["average_supply_percent"] == 65
    assert [d["supply"] for d in supply["divisions"]["below_70_percent"]] == [40, 55]


def test_occupation_resistance_compliance_victory_points_and_lost_land(save, grid):
    occupation = ask(world_of(save, grid), grid, "occupation")
    assert occupation["occupation_law"] == "military_governor_occupation"
    held = occupation["occupied_by_you_resisting"]
    assert [(e["state"], e["owner"], e["resistance"], e["of_nation"]) for e in held] == [
        (12, "GER", 70.3, "GER")]
    assert held[0]["compliance"] == 10.2 and held[0]["active"] is True
    lost = occupation["your_states_held_by_others"]
    assert [(e["state"], e["held_by"], e["resistance"]) for e in lost] == [(11, "GER", 20.0)]
    assert occupation["victory_points"]["held"] == [{"province": 7, "points": 5, "of": "GER"}]
    assert occupation["victory_points"]["lost"] == [{"province": 10, "points": 2, "held_by": "GER"}]
    assert occupation["provinces_held_by_others_in_your_states"] == {"11": {"GER": 1}}
    assert occupation["foreign_states_you_hold"] == {"12": {"owner": "GER", "provinces_held": 2, "of": 3}}


# --- the brief, the tool, the catalog --------------------------------------------------------------


def test_the_brief_stays_short_and_names_faction_wars_agency_fleet_air(save, grid):
    pic = W.picture(world_of(save, grid), "SWE", EXACT, grid)
    full_lines = F.brief_lines(pic.world.full, "SWE", pic.intel)
    assert 2 <= len(full_lines) <= 4
    text = "\n".join(full_lines)
    assert "faction nordic (leader SWE, 3 members)" in text and "at war with GER" in text
    assert "guarantees NOR" in text and "overlord of FIN" in text
    assert "agency Karlsborg, 2 operatives (1 on missions); networks GER 38%" in text
    assert "4 ships in 2 task forces, 130 planes in 3 wings; stockpile infantry_equipment_1 3200" in text
    assert text in "\n".join(W.brief_lines(pic, layer=grid))


def test_inspect_world_serves_every_new_view_and_the_catalog_lists_them(save, grid):
    adapter = MockAdapter()
    adapter.state.country = "SWE"
    env = HOI4Env(adapter, HarnessConfig())
    env.attach_world(W.WorldObserver("SWE", save.parent, layer=grid))
    spec = next(a for a in ACTIONS if a.name == "inspect_world")
    enum = spec.parameters["properties"]["view"]["enum"]
    assert set(F.NEW_VIEWS) <= set(enum) and {"divisions", "state", "country", "borders"} <= set(enum)
    for view in F.NEW_VIEWS:
        result = env.query(ActionCall("inspect_world", {"view": view}))
        assert result.ok, (view, result.message)
        assert json.loads(result.message)["view"] == view
    bad = env.query(ActionCall("inspect_world", {"view": "nonsense"}))
    assert not bad.ok


def test_a_panel_that_cannot_be_read_costs_the_panel_not_the_army(tmp_path, grid):
    text = full_save().replace("politics={", "politics=1\npoliticsX={", 1)
    path = tmp_path / "save games"
    path.mkdir()
    (path / "autosave.hoi4").write_text(text, encoding="utf-8")
    world = W.read_world(path / "autosave.hoi4", "SWE", layer=grid)
    assert len(world.countries["SWE"].divisions) == 3
    assert ask(world, grid, "diplomacy")["wars"]["GER"]


def test_the_empty_save_has_no_panels_to_show(tmp_path):
    observer = W.WorldObserver("SWE", tmp_path)
    from hoi4_harness.types import GameState

    assert "no text save" in observer.query(GameState(country="SWE"), "navy", None)["error"]


def test_a_block_written_at_column_zero_inside_a_country_does_not_cut_the_order_short(tmp_path, grid):
    # a real save writes some nested blocks (CAN={ inside ENG's) at column 0; the layout scan reads
    # those as top-level keys, so the country order must not rely on it
    text = full_save().replace("\tSWE={\n", "\tSWE={\nNOR={\n\tx=1\n}\n", 1)
    folder = tmp_path / "save games"
    folder.mkdir()
    (folder / "autosave.hoi4").write_text(text, encoding="utf-8")
    world = W.read_world(folder / "autosave.hoi4", "GER", layer=grid)
    assert world.full_error is None and world.full.order == ["SWE", "GER"]
    assert [d["wing"] for d in world.countries["GER"].full.air] == ["JG 1"]
    assert F.country_order(text) == ["SWE", "GER"]


def test_commanders_with_rank_skills_and_traits(save, grid):
    result = ask(world_of(save, grid), grid, "commanders")
    assert result["count"] == 2 and result["by_rank"] == {"field marshal": 1, "corps commander": 1}
    marshal, general = result["commanders"]
    assert (marshal["name"], marshal["rank"], marshal["skill"]) == ("Helge Jung", "field marshal", 4)
    assert (marshal["attack"], marshal["defense"], marshal["planning"], marshal["logistics"]) == (3, 5, 4, 2)
    assert general["traits"] == ["Armor officer", "Trickster"] and general["name"] == "Archibald Douglas"
    # another country's officers are not on your screen
    assert ask(world_of(save, grid), grid, "commanders", "GER", EXACT)["count"] == 2
