# ruff: noqa: E501
"""The tank, ship and plane designers (docs/designers.md): read from the files, priced as the screen
prices them, and run through the console on the fake game.

The fixture is an invented playset (ids exist only here) in the shapes the game's files use:
``module_slots`` with ``allowed_module_categories``, ``module_count_limit``, modules with
``xp_cost`` / ``can_convert_from`` / ``allowed_module_categories`` (a turret opening a gun category),
``enable_equipments`` / ``enable_equipment_modules`` in technologies, ``common/defines``. The
install's own numbers are checked further down when the game is on this machine.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from hoi4_harness import designers as D
from hoi4_harness import dlc, steering
from hoi4_harness.actions import catalog
from hoi4_harness.adapters.console import CONSOLE_ACTIONS, MARKED_ACTIONS, ConsoleChannel
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.control import DOMAIN_ACTIONS
from hoi4_harness.fakegame import FakeGame, write_demo_playset
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.steering import Standing
from hoi4_harness.types import ActionCall

NSB, MTG, BBA = "No Step Back", "Man the Guns", "By Blood Alone"

FILES = {
    "common/defines/00_defines.lua": """NDefines = { NMilitary = {
	EQUIPMENT_MODULE_ADD_XP_COST = 5.0,
	EQUIPMENT_MODULE_REPLACE_XP_COST = 6.0,
	EQUIPMENT_MODULE_CONVERT_XP_COST = 3.0,
	EQUIPMENT_MODULE_REMOVE_XP_COST = 1.0,
	LAND_EQUIPMENT_BASE_COST = 10,
	LAND_EQUIPMENT_RAMP_COST = 5,
	NAVAL_EQUIPMENT_BASE_COST = 25,
	NAVAL_EQUIPMENT_RAMP_COST = 5,
	AIR_EQUIPMENT_BASE_COST = 25,
	AIR_EQUIPMENT_RAMP_COST = 5,
	DESIGN_TEAM_CHANGE_XP_COST = 0,
} }
""",
    "common/units/equipment/demo_tank.txt": """equipments = {
	demo_tank_chassis = {
		is_archetype = yes
		is_buildable = no
		module_slots = {
			turret_type_slot = { required = yes allowed_module_categories = { demo_turret } }
			gun_slot = { required = no allowed_module_categories = { demo_gun_small } }
			engine_slot = { required = yes allowed_module_categories = { demo_engine } }
			special_slot_1 = { required = no allowed_module_categories = { demo_special } }
			special_slot_2 = special_slot_1
		}
		module_count_limit = { module = extra_a count < 2 }
		default_modules = {
			gun_slot = empty
			turret_type_slot = turret_one
			engine_slot = engine_gas
		}
		maximum_speed = 4
		build_cost_ic = 2
	}
	demo_tank_chassis_1 = {
		year = 1936
		archetype = demo_tank_chassis
		module_slots = inherit
		upgrades = { demo_engine_upgrade }
	}
	demo_tank_chassis_2 = {
		year = 1940
		archetype = demo_tank_chassis
		parent = demo_tank_chassis_1
		module_slots = inherit
		maximum_speed = 5
	}
	demo_tank_chassis_locked = {
		year = 1950
		archetype = demo_tank_chassis
		module_slots = inherit
	}
	demo_old_tank = { archetype = demo_tank_chassis }
}
""",
    "common/units/equipment/modules/00_tank_modules.txt": """equipment_modules = {
	limit = { has_dlc = "No Step Back" }
	turret_one = {
		abbreviation = "t1" category = demo_turret
		add_stats = { build_cost_ic = 1 breakthrough = 7 }
		xp_cost = 2
		allowed_module_categories = { gun_slot = { demo_gun_big } }
	}
	turret_two = {
		abbreviation = "t2" category = demo_turret
		add_stats = { build_cost_ic = 2 breakthrough = 10 }
		xp_cost = 2
		allowed_module_categories = { gun_slot = { demo_gun_big } }
	}
	turret_plain = { abbreviation = "tp" category = demo_turret add_stats = { build_cost_ic = 1 } }
	gun_small_1 = { abbreviation = "g1" category = demo_gun_small add_stats = { soft_attack = 4 } xp_cost = 1 }
	gun_small_2 = {
		abbreviation = "g2" category = demo_gun_small add_stats = { soft_attack = 6 } xp_cost = 2
		can_convert_from = { module = gun_small_1 convert_cost_ic = 1 }
	}
	gun_big = {
		abbreviation = "gb" category = demo_gun_big add_stats = { hard_attack = 20 } xp_cost = 3
		build_cost_resources = { tungsten = 1 }
	}
	engine_gas = { abbreviation = "gas" category = demo_engine add_stats = { maximum_speed = 0.5 } }
	engine_diesel = {
		abbreviation = "die" category = demo_engine add_stats = { reliability = 0.25 } xp_cost = 1
		can_convert_from = { module_category = demo_engine convert_cost_ic = 0.5 }
	}
	extra_a = { abbreviation = "ea" category = demo_special add_stats = { defense = 1 } xp_cost = 1 }
	extra_b = { abbreviation = "eb" category = demo_special add_stats = { defense = 2 } xp_cost = 1 }
	orphan_special = { abbreviation = "or" category = demo_special add_stats = { defense = 3 } }
}
""",
    "common/units/equipment/upgrades/land_upgrades.txt": """upgrades = {
	demo_engine_upgrade = {
		max_level = 20
		cost = land
		linear_cost = { cost_by_level = 1 cost_by_level_for_licensed_equipment = 5 }
		level_requirements = { 5 = { has_tech = engine_tech_1 } }
		add_stats = { maximum_speed = 0.1 }
	}
}
""",
    "common/units/equipment/demo_ship.txt": """equipments = {
	demo_hull = {
		is_archetype = yes
		module_slots = {
			fixed_battery_slot = { required = yes allowed_module_categories = { demo_battery } }
			fixed_engine_slot = { required = yes allowed_module_categories = { demo_ship_engine } }
		}
		default_modules = { fixed_battery_slot = empty fixed_engine_slot = ship_engine_1 }
	}
	demo_hull_1 = { archetype = demo_hull module_slots = inherit upgrades = { demo_hull_upgrade } }
}
""",
    "common/units/equipment/modules/00_ship_modules.txt": """equipment_modules = {
	limit = { has_dlc = "Man the Guns" }
	battery_1 = { abbreviation = "b1" category = demo_battery add_stats = { lg_attack = 2 } }
	battery_2 = {
		abbreviation = "b2" category = demo_battery add_stats = { lg_attack = 3 }
		can_convert_from = { module = battery_1 convert_cost_ic = 5 }
	}
	ship_engine_1 = { abbreviation = "e1" category = demo_ship_engine add_stats = { naval_speed = 1 } }
}
""",
    "common/units/equipment/upgrades/naval_upgrades.txt": """upgrades = {
	demo_hull_upgrade = { max_level = 5 cost = naval naval_speed = 0.03 }
}
""",
    "common/units/equipment/demo_plane.txt": """equipments = {
	demo_airframe = {
		is_archetype = yes
		module_slots = {
			fixed_engine_slot = { required = yes allowed_module_categories = { demo_plane_engine } }
			fixed_gun_slot = { required = no allowed_module_categories = { demo_plane_gun } }
		}
		default_modules = { fixed_engine_slot = plane_engine_1 fixed_gun_slot = empty }
	}
	demo_airframe_1 = { archetype = demo_airframe module_slots = inherit upgrades = { demo_air_upgrade } }
}
""",
    "common/units/equipment/modules/00_plane_modules.txt": """equipment_modules = {
	limit = { has_dlc = "By Blood Alone" }
	plane_engine_1 = { abbreviation = "p1" category = demo_plane_engine add_stats = { thrust = 10 } }
	plane_gun_1 = { abbreviation = "g1" category = demo_plane_gun add_stats = { air_attack = 5 } xp_cost = 1 }
}
""",
    "common/units/equipment/upgrades/air_upgrades.txt": """upgrades = {
	demo_air_upgrade = { max_level = 5 cost = air air_attack = 0.1 }
}
""",
    "common/technologies/demo_designers.txt": """technologies = {
	demo_chassis_a = { enable_equipments = { demo_tank_chassis_1 } enable_equipment_modules = { turret_plain } }
	demo_chassis_b = { enable_equipments = { demo_tank_chassis_2 } enable_equipment_modules = { turret_two } }
	demo_guns = { enable_equipment_modules = { gun_small_1 gun_small_2 } }
	demo_big_guns = { enable_equipment_modules = { gun_big } }
	demo_big_guns_b = { enable_equipment_modules = { gun_big } }
	demo_engines = { enable_equipment_modules = { engine_diesel engine_gas } }
	demo_extras = { enable_equipment_modules = { extra_a extra_b } }
	engine_tech_1 = { }
	demo_hull_tech = { enable_equipments = { demo_hull_1 } enable_equipment_modules = { battery_1 battery_2 ship_engine_1 } }
	demo_air_tech = { enable_equipments = { demo_airframe_1 } enable_equipment_modules = { plane_engine_1 plane_gun_1 } }
}
""",
}

TECHS = {"demo_chassis_a", "demo_chassis_b", "demo_guns", "demo_engines", "demo_extras",
         "demo_hull_tech", "demo_air_tech", "engine_tech_1", "demo_big_guns"}


def _playset(tmp_path: Path, active=(NSB, MTG, BBA)) -> tuple[Path, IdentifierIndex]:
    root = write_demo_playset(tmp_path / "playset")
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    index = IdentifierIndex.build(Playset.from_paths(root, dlc=dlc.explicit(active)))
    return root, index


@pytest.fixture
def data(tmp_path):
    return _playset(tmp_path)[1].designers()


def variant(type_, version, modules, name=None, upgrades=None, frame=False, vid=1, parent=None):
    return D.Variant(type=type_, name=name, version=version, parent_id=parent, obsolete=False,
                     is_frame=frame, modules=modules, upgrades=upgrades or {}, id=vid)


def plan(data, base="demo_tank_chassis_1", modules=None, upgrades=None, kind="tank", **kw):
    kw.setdefault("active", frozenset({NSB, MTG, BBA}))
    return D.plan_design(data, kind, base, modules, upgrades, **kw)


# --- reading the files -----------------------------------------------------------------------


def test_chassis_slots_modules_upgrades_unlocks_and_defines_are_read_from_the_files(data):
    chassis = data.chassis["demo_tank_chassis_2"]
    assert chassis.kind == "tank" and not chassis.is_archetype
    assert [s.name for s in chassis.slots.values()] == [
        "turret_type_slot", "gun_slot", "engine_slot", "special_slot_1", "special_slot_2"]
    assert chassis.slots["turret_type_slot"].required and not chassis.slots["gun_slot"].required
    assert not chassis.slots["special_slot_2"].required
    assert chassis.slots["special_slot_2"].categories == ("demo_special",)      # = special_slot_1
    assert chassis.default_modules["turret_type_slot"] == "turret_one"
    assert chassis.unlock_techs == ["demo_chassis_b"]
    assert chassis.stats["maximum_speed"] == 5          # its own over the archetype's 4
    assert chassis.upgrades == ("demo_engine_upgrade",)  # inherited from the parent chassis
    assert [limit.describe() for limit in chassis.limits] == ["extra_a: count < 2"]
    module = data.modules["gun_small_2"]
    assert module.xp_cost == 2 and module.converts_from == [("module", "gun_small_1", 1.0)]
    assert data.modules["gun_big"].resources == {"tungsten": 1}
    assert data.modules["turret_one"].slot_categories == {"gun_slot": {"demo_gun_big"}}
    assert data.modules["engine_diesel"].unlock_techs == ["demo_engines"]
    assert data.modules["gun_big"].unlock_techs == ["demo_big_guns", "demo_big_guns_b"]
    up = data.upgrades["demo_engine_upgrade"]
    assert up.linear == 1 and up.requirements == {5: ["engine_tech_1"]} and up.cost == "land"
    d = data.defines
    assert (d.add, d.replace, d.convert, d.remove, d.team_flat) == (5, 6, 3, 1, 0)
    assert d.base == {"land": 10, "naval": 25, "air": 25} and d.ramp["air"] == 5


def test_a_legacy_type_and_an_archetype_are_not_chassis_to_design_on(data):
    assert "demo_old_tank" not in data.chassis                # no module_slots of its own
    assert data.chassis["demo_tank_chassis"].is_archetype
    assert [c.id for c in data.types("tank")] == [
        "demo_tank_chassis_1", "demo_tank_chassis_2", "demo_tank_chassis_locked"]
    assert [c.id for c in data.types("ship")] == ["demo_hull_1"]
    assert [c.id for c in data.types("plane")] == ["demo_airframe_1"]


def test_a_variant_is_read_from_the_save_with_its_modules_upgrades_and_parent():
    save = '''HOI4txt
equipments={
	demo_tank_chassis_1={
		id={ id=10 type=70 }
		max_version=1
		is_frame=yes
		creator="GER"
		modules={
			engine_slot=engine_gas
			turret_type_slot=turret_one
		}
		upgrades={
			upgrades={
				{ demo_engine_upgrade 0}
			}
		}
	}
	demo_tank_chassis_1={
		id={ id=11 type=70 }
		name="Panzer"
		version=1
		parent_id={ id=10 type=70 }
		obsolete=yes
		is_frame=no
		creator="GER"
		modules={
			gun_slot=gun_small_1
			engine_slot=engine_diesel
		}
		upgrades={
			upgrades={
				{ demo_engine_upgrade 3}
			}
		}
	}
	demo_tank_chassis_1={
		id={ id=12 type=70 }
		creator="FRA"
		modules={ gun_slot=gun_small_1 }
	}
}
countries={
}
'''
    found = D.read_variants(save, "GER")
    assert [(v.name, v.version, v.is_frame, v.obsolete, v.parent_id) for v in found] == [
        (None, 0, True, False, None), ("Panzer", 1, False, True, 10)]
    assert found[1].modules == {"gun_slot": "gun_small_1", "engine_slot": "engine_diesel"}
    assert found[1].upgrades == {"demo_engine_upgrade": 3} and found[1].id == 11


def test_list_designs_shows_only_the_kind_asked_and_who_it_was_made_from(data):
    frame = variant("demo_tank_chassis_1", 0, {"gun_slot": "empty"}, frame=True, vid=10)
    child = variant("demo_tank_chassis_1", 1, {"gun_slot": "gun_small_1"}, name="Panzer", vid=11,
                    parent=10)
    other = variant("demo_hull_1", 1, {"fixed_battery_slot": "battery_1"}, name="Z", vid=12)
    view = D.designs_view(data, [frame, child, other], "tank")
    assert view["count"] == 2 and [r["name"] for r in view["designs"]] == [None, "Panzer"]
    assert view["designs"][1]["parent_version"] == 0 and view["designs"][1]["version"] == 1
    assert D.designs_view(data, [frame, child, other], "ship")["designs"][0]["name"] == "Z"


def test_describe_option_reads_a_chassis_a_module_a_category_an_upgrade_and_says_when_it_is_off(data):
    on = frozenset({NSB, MTG, BBA})
    overview = D.describe_option(data, "tank", None, on)
    assert [c["id"] for c in overview["chassis"]][0] == "demo_tank_chassis_1"
    assert overview["module_categories"]["demo_gun_small"] == 2
    chassis = D.describe_option(data, "tank", "demo_tank_chassis_1", on)
    assert chassis["slots"][1] == {"slot": "gun_slot", "required": False,
                                   "categories": ["demo_gun_small"], "default": "empty"}
    assert chassis["unlock"]["technologies"] == ["demo_chassis_a"]
    module = D.describe_option(data, "tank", "gun_small_2", on)
    assert module["xp_cost"] == 2 and module["converts_from"][0]["id"] == "gun_small_1"
    assert module["fits"] == ["demo_tank_chassis_1", "demo_tank_chassis_2", "demo_tank_chassis_locked"]
    assert D.describe_option(data, "tank", "orphan_special", on)["unlock"]["technologies"] is None
    assert D.describe_option(data, "tank", "demo_gun_small", on)["modules"][0]["id"] == "gun_small_1"
    assert D.describe_option(data, "tank", "demo_engine_upgrade", on)["level_requirements"] == {
        "5": ["engine_tech_1"]}
    typo = D.describe_option(data, "tank", "gun_smal_1", on)
    assert "error" in typo and "gun_small_1" in typo["nearest"]
    off = D.describe_option(data, "tank", None, frozenset({MTG}))
    assert off["exists"] is False and "No Step Back" in off["note"]


# --- the experience a design costs ------------------------------------------------------------


def test_an_empty_slot_filled_costs_the_add_define_plus_the_modules_own_xp(data):
    p = plan(data, modules={"gun_slot": "gun_small_1"})
    (change,) = p.changes
    assert (change.op, change.define, change.module_xp) == ("add", 5, 1)
    assert p.cost == 6                                     # 5 + 1, by hand


def test_a_module_swapped_for_an_unrelated_one_costs_the_replace_define_plus_xp(data):
    p = plan(data, base="demo_tank_chassis_2", modules={"turret_type_slot": "turret_two"})
    (change,) = p.changes
    assert change.op == "replace"           # turret_two lists no can_convert_from
    assert p.cost == 6 + 2


def test_a_module_converted_to_a_related_one_costs_the_convert_define_by_module_or_by_category(data):
    parent = variant("demo_tank_chassis_1", 1, {"turret_type_slot": "turret_one",
                                                "gun_slot": "gun_small_1",
                                                "engine_slot": "engine_gas"}, name="P")
    by_module = plan(data, modules={"gun_slot": "gun_small_2"}, parent=parent)
    assert [c.op for c in by_module.changes] == ["convert"] and by_module.cost == 3 + 2
    by_category = plan(data, modules={"engine_slot": "engine_diesel"}, parent=parent)
    assert [c.op for c in by_category.changes] == ["convert"] and by_category.cost == 3 + 1
    # the relation is one way: gun_small_1 does not list gun_small_2
    back = variant("demo_tank_chassis_1", 2, {"turret_type_slot": "turret_one",
                                              "gun_slot": "gun_small_2",
                                              "engine_slot": "engine_gas"}, name="Q")
    assert [c.op for c in plan(data, modules={"gun_slot": "gun_small_1"}, parent=back).changes] == [
        "replace"]


def test_a_module_removed_costs_the_remove_define_only(data):
    parent = variant("demo_tank_chassis_1", 1, {"turret_type_slot": "turret_one",
                                                "gun_slot": "gun_small_1", "engine_slot": "engine_gas",
                                                "special_slot_1": "extra_a"}, name="P")
    p = plan(data, modules={"special_slot_1": "empty"}, parent=parent)
    (change,) = p.changes
    assert (change.op, change.define, change.module_xp) == ("remove", 1, 0) and p.cost == 1


def test_unchanged_slots_cost_nothing_and_the_total_adds_up(data):
    p = plan(data, modules={"gun_slot": "gun_big", "special_slot_1": "extra_a",
                            "special_slot_2": "extra_b"})
    assert sorted(c.slot for c in p.changes) == ["gun_slot", "special_slot_1", "special_slot_2"]
    assert p.cost == (5 + 3) + (5 + 1) + (5 + 1)           # three adds, each define + its xp_cost
    assert p.breakdown()["exact"] is False                  # always reported as an upper bound
    assert any("UPPER BOUND" in line for line in p.unchecked)


def test_upgrades_cost_the_linear_levels_or_the_defines_and_are_an_upper_bound(data):
    p = plan(data, upgrades={"demo_engine_upgrade": 3})
    assert [(s.old, s.new, s.cost) for s in p.steps] == [(0, 3, 1 + 2 + 3)]
    air = plan(data, base="demo_airframe_1", kind="plane", upgrades={"demo_air_upgrade": 2})
    assert air.cost == 2 * (25 + 2 * 5)                     # (base + total levels * ramp) per level
    parent = variant("demo_tank_chassis_1", 1, {"turret_type_slot": "turret_one",
                                                "gun_slot": "gun_small_1", "engine_slot": "engine_gas"},
                     upgrades={"demo_engine_upgrade": 2}, name="P")
    again = plan(data, upgrades={"demo_engine_upgrade": 4}, parent=parent)
    assert [(s.old, s.new, s.cost) for s in again.steps] == [(2, 4, 3 + 4)]


def test_modules_and_upgrades_add_into_one_charge(data):
    p = plan(data, modules={"gun_slot": "gun_small_1"}, upgrades={"demo_engine_upgrade": 2})
    assert p.cost == 6 + 3


# --- what the designer refuses ----------------------------------------------------------------


@pytest.mark.parametrize("modules, why", [
    ({"gun_slot": "engine_diesel"}, "does not go in slot gun_slot"),
    ({"nope_slot": "gun_small_1"}, "has no slot"),
    ({"gun_slot": "gun_smal_1"}, "nearest: gun_small_1"),
    ({"gun_slot": "gun_big", "turret_type_slot": "turret_plain"}, "does not go in slot gun_slot"),
    ({"turret_type_slot": "empty"}, "required"),
    ({"engine_slot": "empty"}, "required"),
    ({"special_slot_1": "extra_a", "special_slot_2": "extra_a"}, "limits modules"),
])
def test_a_design_the_slots_or_limits_forbid_is_refused_with_the_reason(data, modules, why):
    with pytest.raises(D.DesignRefused, match=why):
        plan(data, modules=modules)


def test_a_gun_needs_the_turret_that_opens_its_category(data):
    assert plan(data, modules={"gun_slot": "gun_big"}).cost == 5 + 3       # turret_one opens demo_gun_big
    parent = variant("demo_tank_chassis_1", 1, {"turret_type_slot": "turret_one", "gun_slot": "gun_big",
                                                "engine_slot": "engine_gas"}, name="Big")
    with pytest.raises(D.DesignRefused, match="no longer allowed"):
        plan(data, modules={"turret_type_slot": "turret_plain"}, parent=parent)
    assert plan(data, modules={"turret_type_slot": "turret_two"}, parent=parent).cost == 6 + 2   # turret_two opens it too


def test_the_chassis_the_kind_and_the_archetype_are_checked(data):
    with pytest.raises(D.DesignRefused, match="not a tank chassis"):
        plan(data, base="demo_hull_1")
    with pytest.raises(D.DesignRefused, match="archetype"):
        plan(data, base="demo_tank_chassis", modules={"gun_slot": "gun_small_1"})
    with pytest.raises(D.DesignRefused, match="nothing in this playset unlocks demo_tank_chassis_locked"):
        plan(data, base="demo_tank_chassis_locked", modules={"gun_slot": "gun_small_1"})
    with pytest.raises(D.DesignRefused, match="unchanged"):
        plan(data, modules={"turret_type_slot": "turret_one"})


def test_a_module_nothing_unlocks_is_refused_but_a_chassis_default_is_not(data):
    with pytest.raises(D.DesignRefused, match="nothing in this playset unlocks orphan_special"):
        plan(data, modules={"special_slot_1": "orphan_special"})
    assert plan(data, modules={"engine_slot": "engine_gas", "gun_slot": "gun_small_1"}).cost == 6


def test_upgrades_are_checked_for_the_chassis_the_range_and_the_direction(data):
    with pytest.raises(D.DesignRefused, match="has no upgrade"):
        plan(data, upgrades={"demo_air_upgrade": 1})
    with pytest.raises(D.DesignRefused, match="0 to 20"):
        plan(data, upgrades={"demo_engine_upgrade": 21})
    parent = variant("demo_tank_chassis_1", 1, {"turret_type_slot": "turret_one", "gun_slot": "gun_small_1",
                                                "engine_slot": "engine_gas"},
                     upgrades={"demo_engine_upgrade": 4})
    with pytest.raises(D.DesignRefused, match="only raises"):
        plan(data, upgrades={"demo_engine_upgrade": 2}, parent=parent)


def test_the_guard_names_the_dlc_every_unlock_and_the_experience(data):
    p = plan(data, base="demo_tank_chassis_2", modules={"gun_slot": "gun_big", "special_slot_1": "extra_a"},
             upgrades={"demo_engine_upgrade": 6})
    guard = p.guard()
    assert guard.startswith('has_dlc = "No Step Back" has_tech = demo_chassis_b')
    assert "OR = { has_tech = demo_big_guns has_tech = demo_big_guns_b }" in guard
    assert "has_tech = demo_extras" in guard
    assert "has_tech = engine_tech_1" in guard                  # level 5 of the upgrade is gated
    assert guard.endswith(f"has_army_experience > {p.cost:g}")
    body = p.body("Test Tank", obsolete_older=True)
    assert body.startswith('create_equipment_variant = { name = "Test Tank" type = demo_tank_chassis_2 '
                           "obsolete = no mark_older_equipment_obsolete = yes modules = {")
    assert "gun_slot = gun_big" in body and "demo_engine_upgrade = 6" in body
    assert body.endswith(f"army_experience = -{p.cost:g}")


def test_a_module_two_technologies_unlock_is_an_or_and_the_chassis_is_its_own_clause(data):
    p = plan(data, modules={"gun_slot": "gun_big"})
    assert ["demo_big_guns", "demo_big_guns_b"] in p.tech_groups and ["demo_chassis_a"] in p.tech_groups


def test_ships_and_planes_use_their_own_experience_and_dlc(data):
    s = plan(data, base="demo_hull_1", kind="ship", modules={"fixed_battery_slot": "battery_1"})
    assert s.cost == 5 and s.experience == "navy"
    assert s.guard() == 'has_dlc = "Man the Guns" has_tech = demo_hull_tech has_navy_experience > 5'
    assert s.body("S").endswith("navy_experience = -5")
    pl = plan(data, base="demo_airframe_1", kind="plane", modules={"fixed_gun_slot": "plane_gun_1"})
    assert pl.cost == 5 + 1 and pl.guard().startswith('has_dlc = "By Blood Alone"')
    assert pl.body("P").endswith("air_experience = -6")
    conv = variant("demo_hull_1", 1, {"fixed_battery_slot": "battery_1", "fixed_engine_slot": "ship_engine_1"},
                   name="S1")
    c = plan(data, base="demo_hull_1", kind="ship", modules={"fixed_battery_slot": "battery_2"}, parent=conv)
    assert c.cost == 3          # ship modules have no xp_cost: the define alone
    naval = plan(data, base="demo_hull_1", kind="ship", modules={"fixed_battery_slot": "battery_1"},
                 upgrades={"demo_hull_upgrade": 1})
    assert naval.steps[0].cost == 25 + 1 * 5 and naval.cost == 5 + 30


@pytest.mark.parametrize("kind, base, active, needs", [
    ("tank", "demo_tank_chassis_1", {MTG, BBA}, "No Step Back"),
    ("ship", "demo_hull_1", {NSB, BBA}, "Man the Guns"),
    ("plane", "demo_airframe_1", {NSB, MTG}, "By Blood Alone"),
])
def test_a_designer_without_its_dlc_is_refused_in_the_dlcs_name(data, kind, base, active, needs):
    with pytest.raises(D.DesignRefused) as caught:
        D.plan_design(data, kind, base, {}, None, active=frozenset(active))
    assert needs in str(caught.value) and "not active" in str(caught.value)


def test_a_playset_without_the_defines_is_refused_rather_than_guessed(tmp_path):
    root, _ = _playset(tmp_path)
    (root / "common/defines/00_defines.lua").unlink()
    data = IdentifierIndex.build(Playset.from_paths(root, dlc=dlc.explicit([NSB]))).designers()
    assert data.defines is None
    with pytest.raises(D.DesignRefused, match="cannot be established"):
        plan(data, modules={"gun_slot": "gun_small_1"})


# --- through the console, on the fake game ---------------------------------------------------


def _rig(tmp_path, experience=100.0, active=(NSB, MTG, BBA)):
    root, index = _playset(tmp_path, active)
    game = FakeGame(tmp_path / "user", index=index, variance=0)
    game.paused = False
    game.dlcs = set(active)
    game.experience = {"army": experience, "navy": experience, "air": experience}
    game.researched |= TECHS
    reader = LogTailAdapter(game.log_path, country=game.country)
    game.emit_state()

    class Driver:
        dry_run = False

        class config:
            settle_seconds = 0.0

        def _sleep(self, seconds):
            pass

    channel = ConsoleChannel(Driver(), index=index, read_state=reader.read_state, sleep=game.sleep,
                             verify_timeout=1.0)
    channel.config.archive_dir = tmp_path / "run"
    channel.steer_standing = Standing(tmp_path / "run")
    channel.inbox = game.inbox()
    channel.config.inbox_ack_seconds = 30.0
    return game, channel, index


def _do(channel, **arguments):
    (result,) = channel.execute([ActionCall("design_equipment", arguments)])
    return result


ARMS = {"kind": "tank", "base": "demo_tank_chassis_1", "name": "Test Tank",
        "modules": {"gun_slot": "gun_small_1"}, "upgrades": {"demo_engine_upgrade": 2}}


def test_the_designer_is_a_marked_console_action_in_the_production_domain():
    assert "design_equipment" in CONSOLE_ACTIONS and "design_equipment" in MARKED_ACTIONS
    assert "design_equipment" in DOMAIN_ACTIONS["production"]
    assert catalog.get("design_equipment") is not None
    assert {"list_designs", "describe_design_option"} <= catalog.QUERY_ACTIONS
    assert "Simulator only" not in catalog.get("design_equipment").description


def test_the_planned_effect_is_one_guarded_if_with_the_charge_and_a_marker_inside(tmp_path):
    _game, channel, _ = _rig(tmp_path)
    planned = channel.plan(ActionCall("design_equipment", ARMS), channel._read_state())
    (line,) = planned.lines
    assert line.startswith("eval_effect SWE = { if = { limit = { has_dlc = \"No Step Back\" ")
    assert "has_tech = demo_chassis_a" in line and "has_tech = demo_guns" in line
    assert "has_army_experience > 9" in line                      # (5 + 1) + (1 + 2)
    assert 'create_equipment_variant = { name = "Test Tank" type = demo_tank_chassis_1 obsolete = no' in line
    assert "modules = { gun_slot = gun_small_1 }" in line and "upgrades = { demo_engine_upgrade = 2 }" in line
    assert line.index("army_experience = -9") > line.index("create_equipment_variant")
    assert line.index("LLMB|v5|click|") > line.index("army_experience = -9")
    assert any("UPPER BOUND" in u for u in planned.unchecked)


def test_on_the_typed_console_the_dlc_test_and_spaced_names_are_left_out_not_mangled(tmp_path):
    _game, channel, _ = _rig(tmp_path)
    channel.inbox = None                                    # the keys transport
    typed = channel.plan(ActionCall("design_equipment", {**ARMS, "name": "Tank_1"}), channel._read_state())
    (line,) = typed.lines
    assert "has_dlc" not in line and "has_tech = demo_chassis_a" in line and "has_army_experience > 9" in line
    assert any("typed console cannot carry" in u for u in typed.unchecked)
    spaced = _do(channel, **ARMS)                           # "Test Tank" has a space
    assert not spaced.ok and "inbox" in spaced.message and "nothing was sent" in spaced.message


def test_the_fake_game_creates_the_variant_and_charges_the_experience(tmp_path):
    game, channel, _ = _rig(tmp_path, experience=100.0)
    made = _do(channel, **ARMS)
    assert made.ok and "design Test Tank created" in made.message and "9 army experience" in made.message
    assert game.experience == {"army": 91.0, "navy": 100.0, "air": 100.0}
    (v,) = game.variants
    assert (v["name"], v["type"], v["version"], v["obsolete"]) == ("Test Tank", "demo_tank_chassis_1", 1, False)
    assert v["modules"] == {"gun_slot": "gun_small_1"} and v["upgrades"] == {"demo_engine_upgrade": "2"}


def test_ships_and_planes_charge_navy_and_air_experience(tmp_path):
    game, channel, _ = _rig(tmp_path)
    assert _do(channel, kind="ship", base="demo_hull_1", name="Boat",
               modules={"fixed_battery_slot": "battery_1"}).ok
    assert _do(channel, kind="plane", base="demo_airframe_1", name="Kite",
               modules={"fixed_gun_slot": "plane_gun_1"}).ok
    assert game.experience == {"army": 100.0, "navy": 95.0, "air": 94.0}
    assert [v["type"] for v in game.variants] == ["demo_hull_1", "demo_airframe_1"]


def test_without_enough_experience_the_game_declines_and_charges_nothing(tmp_path):
    game, channel, _ = _rig(tmp_path, experience=5.0)
    result = _do(channel, **ARMS)
    assert not result.ok and result.error_kind == "rejected" and "Nothing changed" in result.message
    assert game.variants == [] and game.experience["army"] == 5.0


def test_without_the_technology_the_game_declines(tmp_path):
    game, channel, _ = _rig(tmp_path)
    game.researched.discard("demo_guns")
    result = _do(channel, **ARMS)
    assert not result.ok and game.variants == [] and game.experience["army"] == 100.0


def test_without_the_dlc_it_is_refused_before_anything_is_posted(tmp_path):
    _game, channel, _ = _rig(tmp_path, active=(MTG, BBA))
    result = _do(channel, **ARMS)
    assert not result.ok and "needs the No Step Back DLC" in result.message
    assert "nothing was sent" in result.message and channel.inbox.pending == []


def test_the_game_itself_declines_a_dlc_it_does_not_have(tmp_path):
    game, channel, _ = _rig(tmp_path)
    game.dlcs = {MTG, BBA}                  # the harness believed it was on; the game's has_dlc says no
    result = _do(channel, **ARMS)
    assert not result.ok and game.variants == []


@pytest.mark.parametrize("arguments, why", [
    ({"name": "a;b"}, "1 to 40 letters"),
    ({"modules": {"gun_slot": "engine_diesel"}}, "does not go in slot"),
    ({"modules": {"gun_slot": "gun_big"}, "parent": "Nothing"}, "newest text save"),
    ({"modules": {"special_slot_1": "orphan_special"}}, "nothing in this playset unlocks"),
])
def test_a_design_the_designer_would_not_take_is_refused_before_anything_is_posted(tmp_path, arguments, why):
    _game, channel, _ = _rig(tmp_path)
    result = _do(channel, **{**ARMS, "upgrades": {}, **arguments})
    assert not result.ok and why in result.message and "nothing was sent" in result.message
    assert channel.inbox.pending == []


def test_a_parent_is_read_from_the_saved_designs_and_priced_against_it(tmp_path):
    game, channel, _ = _rig(tmp_path)
    saved = [variant("demo_tank_chassis_1", 0, {"turret_type_slot": "turret_one", "gun_slot": "empty",
                                                "engine_slot": "engine_gas"}, frame=True, vid=10),
             variant("demo_tank_chassis_1", 3, {"turret_type_slot": "turret_one", "gun_slot": "gun_small_1",
                                                "engine_slot": "engine_gas"}, name="Mk3", vid=11, parent=10)]
    channel.designs = SimpleNamespace(variants=lambda state: saved)
    by_name = _do(channel, kind="tank", base="demo_tank_chassis_1", name="Mk4",
                  modules={"gun_slot": "gun_small_2"}, parent="mk3")
    assert by_name.ok and game.variants[0]["parent_version"] == 3
    assert game.experience["army"] == 100 - (3 + 2)                  # converted: define 3 + xp 2
    by_version = _do(channel, kind="tank", base="demo_tank_chassis_1", name="Mk5",
                     modules={"engine_slot": "engine_diesel"}, parent="3")
    assert by_version.ok and game.variants[1]["parent_version"] == 3
    missing = _do(channel, kind="tank", base="demo_tank_chassis_1", name="Mk6",
                  modules={"gun_slot": "gun_small_2"}, parent="9")
    assert not missing.ok and "no demo_tank_chassis_1 design '9'" in missing.message


def test_the_default_design_comes_from_the_saves_frame_when_it_has_one(tmp_path):
    game, channel, _ = _rig(tmp_path)
    frame = variant("demo_tank_chassis_1", 0, {"turret_type_slot": "turret_one", "gun_slot": "gun_small_1",
                                               "engine_slot": "engine_gas"}, frame=True)
    channel.designs = SimpleNamespace(variants=lambda state: [frame])
    # the frame already holds gun_small_1: asking for it changes nothing, asking for _2 converts
    assert not _do(channel, kind="tank", base="demo_tank_chassis_1", name="N",
                   modules={"gun_slot": "gun_small_1"}).ok
    ok = _do(channel, kind="tank", base="demo_tank_chassis_1", name="N", modules={"gun_slot": "gun_small_2"})
    assert ok.ok and game.experience["army"] == 95


def test_identifier_validation_names_the_nearest_real_ids(tmp_path):
    _game, _channel, index = _rig(tmp_path)
    bad = index.check(ActionCall("design_equipment", {**ARMS, "base": "demo_tank_chassis_9"}), "SWE")
    assert bad is not None and "demo_tank_chassis_2" in bad.message
    bad = index.check(ActionCall("design_equipment", {**ARMS, "modules": {"gun_slot": "gun_smal_1"}}), "SWE")
    assert bad is not None and "gun_small_1" in bad.message
    bad = index.check(ActionCall("design_equipment", {**ARMS, "modules": {"gun_slotx": "gun_small_1"}}), "SWE")
    assert bad is not None and "no slot" in bad.message
    assert index.check(ActionCall("design_equipment", ARMS), "SWE") is None


def test_mark_older_obsolete_and_the_variant_steer(tmp_path):
    game, channel, index = _rig(tmp_path)
    assert _do(channel, **ARMS).ok
    second = _do(channel, **{**ARMS, "name": "Second", "modules": {"gun_slot": "gun_small_2"},
                             "upgrades": {}, "obsolete_older": True})
    assert second.ok and [v["obsolete"] for v in game.variants] == [True, False]
    spec = steering.spec_for("production", "variant")
    assert spec.strategy == "equipment_variant_production_factor" and spec.source == "archetype"
    assert steering.strategy_text(spec, "medium_tank_chassis", 50) == (
        "type = equipment_variant_production_factor id = medium_tank_chassis value = 50")
    assert steering.refusal("production", "variant", "medium_tank_chassis", "more",
                            {"archetype": {"medium_tank_chassis"}}) is None
    assert "variant" in catalog.STEER_PRODUCTION_KINDS


def test_the_shipped_mod_has_the_variant_steer_blocks_for_the_vanilla_families():
    text = (Path(__file__).resolve().parents[1] / "mod" / "llm_bridge" / steering.REGIONS_FILE).read_text(
        encoding="utf-8")
    for family in ("light_tank_chassis", "medium_tank_chassis", "small_plane_airframe",
                   "large_plane_airframe", "ship_hull_light"):
        block = (f"ai_strategy = {{ type = equipment_variant_production_factor id = {family} "
                 f"value = 50 }}")
        assert block in text, family
    assert text.count("{") == text.count("}")


# --- the queries, through the environment -----------------------------------------------------


def test_the_two_queries_answer_from_the_files_and_the_save(tmp_path):
    from hoi4_harness.config import HarnessConfig
    from hoi4_harness.env import HOI4Env as Environment

    root, index = _playset(tmp_path)
    from hoi4_harness.adapters.mock import MockAdapter

    env = Environment(MockAdapter(), HarnessConfig(run_dir=tmp_path / "r", country="SWE"), index=index)
    ask = lambda name, **a: env.query(ActionCall(name, a))  # noqa: E731
    described = ask("describe_design_option", kind="tank", id="demo_tank_chassis_2")
    assert described.ok and json.loads(described.message)["unlock"]["technologies"] == ["demo_chassis_b"]
    assert not ask("list_designs", kind="tank").ok                  # no save observer attached
    saved = [variant("demo_tank_chassis_1", 1, {"gun_slot": "gun_small_1"}, name="Mk1")]
    env.world = SimpleNamespace(variants=lambda state: saved, brief_lines=lambda s: [])
    listing = json.loads(ask("list_designs", kind="tank").message)
    assert listing["count"] == 1 and listing["designs"][0]["name"] == "Mk1"
    assert {"list_designs", "describe_design_option"} <= env.available_queries


# --- the install's own files, when the game is on this machine --------------------------------

GAME = Path(os.environ.get("HOI4_GAME_DIR")
            or "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV")
needs_game = pytest.mark.skipif(not (GAME / "common" / "units" / "equipment" / "modules").is_dir(),
                                reason="the HOI4 1.20 install is not on this machine")


@pytest.fixture(scope="module")
def real():
    return D.DesignerData.load(Playset.from_paths(GAME))


@needs_game
def test_the_install_prices_a_module_change_with_the_four_defines(real):
    d = real.defines
    assert (d.add, d.replace, d.convert, d.remove, d.team_flat) == (5.0, 6.0, 3.0, 1.0, 0.0)
    assert d.base == {"land": 10.0, "naval": 25.0, "air": 25.0}
    assert d.ramp == {"land": 5.0, "naval": 5.0, "air": 5.0}


@needs_game
def test_the_install_has_the_module_xp_costs_the_guide_quotes(real):
    assert real.modules["tank_riveted_armor"].xp_cost == 8 and real.modules["tank_welded_armor"].xp_cost == 8
    assert real.modules["tank_light_one_man_tank_turret"].xp_cost == 2
    assert real.modules["light_mg_2x"].xp_cost == 1 and real.modules["light_mg_4x"].xp_cost == 2
    assert all(m.xp_cost is None for m in real.modules.values() if m.kind == "ship")
    assert sum(1 for m in real.modules.values() if m.kind == "tank" and m.xp_cost is not None) >= 90


@needs_game
def test_the_install_chassis_slots_unlocks_and_upgrades(real):
    medium = real.chassis["medium_tank_chassis_2"]
    assert medium.unlock_techs == ["improved_medium_tank_chassis"] and medium.kind == "tank"
    assert medium.slots["turret_type_slot"].required
    assert medium.slots["main_armament_slot"].categories == ("tank_small_main_armament", "tank_flamethrower")
    assert medium.default_modules["armor_type_slot"] == "tank_riveted_armor"
    assert medium.upgrades == ("tank_nsb_engine_upgrade", "tank_nsb_armor_upgrade")
    assert real.chassis["ship_hull_light_2"].kind == "ship" and real.chassis["small_plane_airframe_1"].kind == "plane"
    assert real.chassis["medium_tank_chassis"].is_archetype
    up = real.upgrades["tank_nsb_engine_upgrade"]
    assert up.max_level == 20 and up.linear == 1 and up.requirements[5] == ["engine_tech_1"]


@needs_game
def test_the_install_turret_opens_the_gun_category_and_cannons_have_their_techs(real):
    assert real.modules["tank_medium_two_man_tank_turret"].slot_categories == {
        "main_armament_slot": {"tank_medium_main_armament"}}
    assert sorted(real.modules["tank_medium_cannon"].unlock_techs) == ["antitank1", "artillery1"]
    cast = real.modules["tank_cast_armor"]
    assert cast.converts_from == [] and cast.dismantle_cost_ic == 1


@needs_game
def test_the_install_plan_for_a_medium_tank_matches_a_hand_count(real):
    p = D.plan_design(real, "tank", "medium_tank_chassis_2",
                      {"turret_type_slot": "tank_medium_two_man_tank_turret",
                       "main_armament_slot": "tank_medium_cannon"},
                      {"tank_nsb_armor_upgrade": 3}, active=frozenset({NSB}))
    # turret: replace 6 + its xp_cost 2; gun: add 5 + xp_cost 1; armour upgrade 0 -> 3 = 1 + 2 + 3
    assert [(c.slot, c.op, c.cost) for c in p.changes] == [
        ("turret_type_slot", "replace", 8.0), ("main_armament_slot", "add", 6.0)]
    assert p.cost == 8 + 6 + 6
    assert "has_tech = improved_medium_tank_chassis" in p.guard()
    with pytest.raises(D.DesignRefused, match="does not go in slot main_armament_slot"):
        D.plan_design(real, "tank", "light_tank_chassis_2", {"main_armament_slot": "tank_medium_cannon"},
                      None, active=frozenset({NSB}))      # no light turret opens the medium guns


# --- the guide's designer numbers [files] against the install ---------------------------------


def _sheet_part_two() -> str:
    from hoi4_harness import guidance

    return guidance.load("strategy").split("# Part 2: vanilla 1.20")[1]


def test_the_guide_has_a_designer_section_in_part_one_without_vanilla_numbers():
    from hoi4_harness import guidance

    general = guidance.load("strategy").split("# Part 2: vanilla 1.20")[0]
    match = re.search(r"^## Equipment designers: tanks, ships and planes\r?\n(.*?)(?=^#{1,2} |\Z)", general,
                      re.S | re.M)
    assert match is not None
    body = match.group(1)
    for word in ("Tanks: what you trade", "Ships: what you trade", "Planes: what you trade",
                 "How to judge a design", "design_equipment"):
        assert word in body, word
    assert not re.search(r"\d", body), re.search(r".{20}\d.{20}", body, re.S)
    assert "[files]" not in body and "[community]" not in body


@needs_game
def test_the_guides_designer_numbers_match_the_install(real):
    part = _sheet_part_two()
    assert "### Designers" in part and "Designers [community]" in part
    d = real.defines
    assert (d.add, d.replace, d.convert, d.remove) == (5, 6, 3, 1)
    assert ("Adding a module to an empty slot 5, replacing it with an unrelated one 6, converting it to a "
            "related one 3, removing it 1") in part
    assert d.team_flat == 0 and "0 in vanilla" in part
    assert [real.modules[m].xp_cost for m in ("tank_riveted_armor", "tank_cast_armor", "tank_welded_armor",
                                              "sloped_armor")] == [8, 8, 8, 10]
    assert "(riveted, cast, welded) 8, sloped armour 10" in part
    assert {real.modules[m].xp_cost for m in ("tank_light_one_man_tank_turret",
                                              "tank_light_two_man_tank_turret")} == {2}
    plane_xp = {m.xp_cost for m in real.modules.values() if m.kind == "plane" and m.xp_cost is not None}
    assert min(plane_xp) == 1 and max(plane_xp) == 5 and "plane weapons 1 to 5" in part
    assert real.modules["light_mg_2x"].xp_cost == 1 and real.modules["light_mg_4x"].xp_cost == 2
    raw = (GAME / "common/defines/00_defines.lua").read_text(encoding="utf-8-sig")
    for text, kind in (("35 army", "LAND"), ("50 navy", "NAVY"), ("25 air", "AIR")):
        value = re.search(rf"DEFAULT_MODULE_VARIANT_CREATION_XP_CUTOFF_{kind}\s*=\s*(\d+)", raw).group(1)
        assert text.split()[0] == value and text in part
    assert d.base == {"land": 10, "naval": 25, "air": 25} and set(d.ramp.values()) == {5}
    assert "base 25 and ramp 5 for navy and air, base 10 and ramp 5 for land" in part
    engine = real.upgrades["tank_nsb_engine_upgrade"]
    armour = real.upgrades["tank_nsb_armor_upgrade"]
    assert engine.max_level == armour.max_level == 20 and engine.linear == armour.linear == 1
    assert sorted(engine.requirements) == sorted(armour.requirements) == [5, 10, 15, 18]
    assert "levels 5, 10, 15 and 18" in part


@needs_game
def test_the_guides_chassis_and_module_numbers_match_the_install(real):
    part = _sheet_part_two()
    for chassis, (cost, speed, armour, rel) in {
            "light_tank_chassis_2": (2.7, 6, 20, 1.1), "medium_tank_chassis_2": (4.0, 6, 40, 1.0),
            "heavy_tank_chassis_2": (11.0, 5, 60, 1.1), "super_heavy_tank_chassis_1": (28.0, 4, 90, 1.0)}.items():
        s = real.chassis[chassis].stats
        assert (s["build_cost_ic"], s["maximum_speed"], s["armor_value"], s["reliability"]) == (
            cost, speed, armour, rel), chassis
        assert len(real.chassis[chassis].slots) == 9
    assert "Light (second generation, 1936): 2.7 industry, speed 6, armour 20, reliability 1.1" in part
    assert "Medium (1940): 4.0, 6, 40, 1.0" in part and "Heavy (1940): 11.0, 5, 60, 1.1" in part
    assert "Super-heavy (1943): 28, 4, 90" in part
    assert real.chassis["heavy_tank_chassis_2"].resources == {"steel": 1, "chromium": 1}
    assert real.chassis["super_heavy_tank_chassis_1"].resources == {"steel": 3, "chromium": 2}
    m = real.modules
    assert m["tank_gasoline_engine"].add_stats["maximum_speed"] == 0.5
    assert m["tank_gasoline_engine"].multiply_stats["maximum_speed"] == 0.15
    assert m["tank_diesel_engine"].add_stats["reliability"] == 0.25
    pel = m["tank_petrol_electric_engine"]
    assert (pel.add_stats["reliability"], pel.add_stats["breakthrough"], pel.add_stats["defense"]) == (-0.5, 2, 2)
    riveted, cast, welded = m["tank_riveted_armor"], m["tank_cast_armor"], m["tank_welded_armor"]
    assert (riveted.add_stats["defense"], riveted.multiply_stats["build_cost_ic"]) == (2, -0.2)
    assert (cast.add_stats["defense"], cast.multiply_stats["armor_value"], cast.multiply_stats["build_cost_ic"]) \
        == (3, 0.3, 0.2)
    assert (welded.add_stats["defense"], welded.resources) == (4, {"chromium": 1})
    for gun, soft, hard, pierce in (("tank_small_cannon", 10, 6, 25), ("tank_medium_cannon", 20, 15, 60),
                                    ("tank_high_velocity_cannon", 10, 26, 68), ("tank_heavy_cannon", 20, 20, 90)):
        a = m[gun].add_stats
        assert (a["soft_attack"], a["hard_attack"], a["ap_attack"]) == (soft, hard, pierce), gun
    assert "small cannon 10 soft, 6 hard, 25 piercing; medium cannon 20, 15, 60" in part
    assert m["tank_medium_two_man_tank_turret"].slot_categories["main_armament_slot"] == {"tank_medium_main_armament"}
    assert "tank_medium_main_armament" not in m["tank_light_two_man_tank_turret"].slot_categories.get(
        "main_armament_slot", set())
    for hull, (cost, speed, slots) in {"ship_hull_light_3": (550, 32, 9), "ship_hull_cruiser_3": (2000, 32, 12),
                                       "ship_hull_heavy_3": (3200, 25, 12), "ship_hull_carrier_2": (2600, 26, 10),
                                       "ship_hull_submarine_2": (350, 17, 3)}.items():
        h = real.chassis[hull]
        assert (h.stats["build_cost_ic"], h.stats["naval_speed"], len(h.slots)) == (cost, speed, slots), hull
    assert real.chassis["ship_hull_light_3"].stats["naval_range"] == 3000
    assert "Light hull (1940): 550 industry, 32 knots, range 3000, nine slots" in part
    assert real.chassis["ship_hull_heavy_3"].resources == {"steel": 4, "chromium": 1}
    assert m["ship_light_battery_3"].add_stats["lg_attack"] == 2
    assert m["ship_light_battery_3"].add_stats["build_cost_ic"] == 150
    hb = m["ship_heavy_battery_3"]
    assert (hb.add_stats["hg_attack"], hb.average_stats["hg_armor_piercing"], hb.add_stats["build_cost_ic"]) \
        == (22, 40, 1750)
    assert (m["ship_torpedo_1"].add_stats["torpedo_attack"], m["ship_torpedo_2"].add_stats["torpedo_attack"]) \
        == (18, 24)
    assert m["ship_anti_air_2"].add_stats["anti_air_attack"] == 3.5
    assert (m["ship_sonar_1"].add_stats["sub_detection"], m["ship_sonar_1"].add_stats["build_cost_ic"]) == (8, 40)
    radar = m["ship_radar_2"]
    assert (radar.add_stats["surface_detection"], radar.add_stats["naval_light_gun_hit_chance_factor"]) \
        == (7, 0.025)
    for frame, (rng, speed, agility, cost) in {"small_plane_airframe_2": (650, 450, 45, 6),
                                               "medium_plane_airframe_2": (1100, 375, 35, 10),
                                               "large_plane_airframe_2": (2000, 300, 30, 20)}.items():
        s = real.chassis[frame].stats
        assert (s["air_range"], s["maximum_speed"], s["air_agility"], s["build_cost_ic"]) == (rng, speed, agility, cost)
    assert real.chassis["small_plane_airframe_2"].stats["weight"] == 5
    assert "Small (1939): range 650, speed 450, agility 45, 6 industry, 5 weight" in part
    assert [m[e].add_stats["thrust"] for e in ("engine_1_1x", "engine_2_1x", "engine_3_1x")] == [11, 16, 25]
    assert (m["engine_2_1x"].multiply_stats["maximum_speed"], m["engine_3_1x"].multiply_stats["maximum_speed"]) \
        == (0.2, 0.3)
    assert [(m[g].add_stats["air_attack"], m[g].add_stats["weight"]) for g in ("light_mg_2x", "light_mg_4x", "heavy_mg_2x")] \
        == [(5, 1), (10, 2), (9, 2.5)]
    assert not any(x.xp_cost for x in m.values() if x.kind == "ship")
