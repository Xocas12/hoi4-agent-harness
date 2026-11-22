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


def test_the_identifier_check_takes_the_alias_too():
    # The check before the console refused "motorized_equipment" while the planner would have
    # mapped it (live, 2026-11-22): the call now goes on with "motorized".
    from pathlib import Path

    from hoi4_harness.identifiers import IdentifierIndex, Playset
    from hoi4_harness.types import ActionCall

    index = IdentifierIndex.build(Playset.from_paths(Path(__file__).parent / "fixtures" / "playset" / "game"))
    call = ActionCall("steer_production", {"kind": "equipment", "id": "motorized_equipment",
                                           "level": "more"})
    assert index.check(call, "GER") is None
    assert call.arguments["id"] == "motorized"


def test_region_names_with_underscores_and_suggestions():
    from hoi4_harness import regions as R

    rs = {206: R.Region(206, "STRATEGICREGION_206", sea=True, name="Lower Baltic Sea"),
          37: R.Region(37, "STRATEGICREGION_37", name="Baltic States"),
          68: R.Region(68, "STRATEGICREGION_68", sea=True, name="Western Mediterranean Sea")}
    assert R.resolve(rs, "baltic_sea").id == 206          # live guess, 2026-11-22
    assert R.resolve(rs, "baltic") is None                 # two regions: ambiguous
    assert R.near(rs, "baltic") == ["Baltic States (37)", "Lower Baltic Sea (206)"]
