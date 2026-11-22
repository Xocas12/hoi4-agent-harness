"""Steer ids the model wrote in the overnight campaign (2026-11-22), mapped to the game's own."""

from hoi4_harness import steering as S


def test_equipment_names_and_production_line_ids_map_to_archetypes():
    eq = lambda ident: S.normalise("production", "equipment", ident)  # noqa: E731
    assert eq("motorized_equipment") == "motorized"
    assert eq("infantry_equipment") == "infantry"
    assert eq("infantry_equipment_2") == "infantry"          # the brief's Industry line id
    assert eq("medium_tank_chassis") == "armor"
    assert eq("light_tank_chassis_1") == "armor"
    assert eq("small_plane_airframe_2") == "small_plane_airframe_2"   # fighter or CAS: unsaid


def test_building_aliases():
    assert S.normalise("production", "building", "oil_refinery") == "synthetic_refinery"
    assert S.normalise("production", "building", "military_factory") == "arms_factory"
