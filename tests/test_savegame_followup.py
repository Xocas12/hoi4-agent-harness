"""Additional save/consumer checks on top of the live-session mapping in #84."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from hoi4_harness.adapters.console import ConsoleChannel, Refused
from hoi4_harness.adapters.savegame import SaveGameAdapter, read_save
from hoi4_harness.adapters.ui_scripts import verify
from hoi4_harness.agent.policy import Policy
from hoi4_harness.handover import stale_reason
from hoi4_harness.observation.delta import render_delta
from hoi4_harness.observation.render import render_full
from hoi4_harness.types import ActionCall, GameState, ProductionLine, ResearchSlot

FIXTURE = Path(__file__).parent / "fixtures/savegame/swe_1936.hoi4"
LIVE_FIXTURE = Path(__file__).parent / "fixtures/swe_1937_04_23.hoi4"


@pytest.fixture
def raw():
    return read_save(FIXTURE)


def mapped(raw):
    return SaveGameAdapter(FIXTURE.parent)._to_state(raw)


@pytest.mark.parametrize("value", [None, "unknown", "nan", "1.5", "-1"])
def test_incomplete_manpower_pool_is_unknown(raw, value):
    pool = raw["owned_states"]["38"]["manpower_pool"]
    if value is None:
        pool.pop("available")
    else:
        pool["available"] = value
    assert not mapped(raw).known("manpower")


@pytest.mark.parametrize("change", ["missing_buildings", "invalid_level", "fractional_level"])
def test_incomplete_building_levels_are_unknown(raw, change):
    state = raw["owned_states"]["38"]
    if change == "missing_buildings":
        state.pop("buildings")
    else:
        state["buildings"]["industrial_complex"]["level"] = "bad" if change == "invalid_level" else "1.5"
    result = mapped(raw)
    assert not result.known("civilian_factories")
    assert result.manpower == 31153 and result.known("manpower")


def test_naval_lines_keep_unknown_assignments_without_claiming_military_capacity(raw):
    state = mapped(raw)
    assert len(state.production) == 6 and state.known("production")
    assert [line.kind for line in state.production] == ["military"] * 2 + ["naval"] * 4
    assert [line.factories for line in state.production] == [1, 1, 1, 1, 1, None]
    assert state.production[-1].efficiency is None
    assert all(line.output_per_day is None for line in state.production)
    assert "1 MIL FACTORIES IDLE" in render_full(state)


def test_missing_efficiency_and_daily_output_stay_unknown(raw):
    line = raw["country"]["production"]["military_lines"][0]
    line.pop("factory_efficiencies")
    state = mapped(raw)
    assert state.production[0].efficiency is None
    assert state.production[0].output_per_day is None
    assert "unknown eff" in render_full(state)


def test_unresolved_equipment_does_not_silently_remove_a_line(raw):
    raw["equipment_names"] = {}
    state = mapped(raw)
    assert not state.known("production") and state.production == []


def test_missing_template_reference_keeps_divisions_unknown(raw):
    raw["country"]["units"]["division"][0].pop("division_template_id")
    state = mapped(raw)
    assert not state.known("divisions") and state.divisions == []


def test_unrecognized_collection_shape_does_not_become_known_empty(raw):
    raw["country"]["production"] = {"unrecognized_lines": {}}
    raw["country"]["units"] = {"unrecognized_units": {}}
    state = mapped(raw)
    assert not state.known("production") and not state.known("divisions")


def test_known_military_metrics_and_active_research_from_84_are_preserved():
    state = mapped(read_save(LIVE_FIXTURE))
    infantry = next(line for line in state.production if line.equipment == "infantry_equipment_1")
    assert infantry.efficiency == pytest.approx(0.53)
    assert infantry.output_per_day == pytest.approx(2.935 / 0.5)
    assert state.national_focus == "SWE_defense_act"
    assert [slot.technology for slot in state.research] == ["infantry_weapons2", None, None]


def test_unknown_division_strength_and_grouped_location_are_explicit(raw):
    state = mapped(raw)
    assert sum(group.count for group in state.divisions) == 12
    assert all(group.strength is None and group.location == "unknown" for group in state.divisions)


def military_and_naval():
    military = ProductionLine("infantry_equipment_1", 1)
    military.kind = "military"
    naval = ProductionLine("ship_hull_submarine_2", None)
    naval.kind = "naval"
    return GameState(military_factories=3, production=[military, naval], unknown_fields=["construction"])


def test_reflexes_and_handover_do_not_count_naval_dockyards_as_military_factories():
    state = military_and_naval()
    calls = Policy().reflex_actions(state)
    assert len(calls) == 1 and calls[0].arguments == {"equipment": "infantry_equipment_1", "factories": 3}
    call = ActionCall("set_production", {"equipment": "infantry_equipment_1", "factories": 3})
    assert stale_reason(call, state) is None


def test_unknown_military_assignments_do_not_trigger_reflexes_or_stale_rejection():
    state = military_and_naval()
    state.production[0].factories = None
    assert Policy().reflex_actions(state) == []
    call = ActionCall("set_production", {"equipment": "infantry_equipment_1", "factories": 3})
    assert stale_reason(call, state) is None


def test_delta_sums_repeated_lines_and_displays_unknown_assignments():
    before = GameState(production=[ProductionLine("submarine", 1), ProductionLine("submarine", None)])
    after = deepcopy(before)
    after.production[1].factories = 2
    assert "Production submarine: unknown -> 3 factories" in render_delta(before, after)


def test_delta_reports_collection_visibility_changes_without_invented_zeros():
    before = GameState(research=[ResearchSlot(1)], production=[ProductionLine("infantry", 1)])
    after = GameState(unknown_fields=["research", "production"])
    delta = render_delta(before, after)
    assert "Research slots free: 1 -> unknown" in delta
    assert "Production: unknown" in delta and "-> 0 factories" not in delta
    delta = render_delta(after, before)
    assert "Research slots free: unknown -> 1" in delta
    assert "Production infantry: unknown -> 1 factories" in delta


def test_unknown_production_assignment_is_unverified_rather_than_rejected():
    state = GameState(production=[ProductionLine("infantry", None)])
    result = verify(
        ActionCall("set_production", {"equipment": "infantry", "factories": 2}),
        state,
        lambda: state,
        timeout=0,
    )
    assert not result.ok and result.kind == "unverified"


def test_equipment_and_template_references_include_the_type(tmp_path):
    path = tmp_path / "typed.hoi4"
    path.write_text("""date=1936.1.1
player=SWE
equipments={
 infantry_equipment_1={ id={ id=7 type=70 } }
 wrong_type={ id={ id=7 type=71 } }
}
division_templates={
 division_template={ id={ id=5 type=52 } name="Infantry" country=SWE }
 division_template={ id={ id=5 type=53 } name="Wrong type" country=SWE }
}
countries={
\tSWE={
\t\tproduction={ military_lines={ equipment_variant_index={ id=7 type=70 } active_factories=1 } }
\t\tunits={ division={ division_template_id={ id=5 type=52 } } }
\t}
}
""")
    state = SaveGameAdapter(tmp_path).read_state()
    assert [line.equipment for line in state.production] == ["infantry_equipment_1"]
    assert [(group.template, group.count) for group in state.divisions] == [("Infantry", 1)]


def production_console():
    index = SimpleNamespace(tags={"SWE"}, equipment_types=lambda: {"new_equipment"})
    return ConsoleChannel(SimpleNamespace(_sleep=lambda _seconds: None), index=index)


@pytest.mark.parametrize("naval_factories", [3, None])
def test_console_production_counts_only_military_assignments(naval_factories):
    state = GameState(
        country="SWE",
        military_factories=3,
        production=[ProductionLine("submarine", naval_factories, kind="naval")],
    )
    call = ActionCall("set_production", {"equipment": "new_equipment", "factories": 1})
    planned = production_console().plan(call, state)
    assert len(planned.lines) == 1 and "requested_factories = 1" in planned.lines[0]


def test_console_production_refuses_unknown_military_assignments():
    state = GameState(
        country="SWE",
        military_factories=3,
        production=[ProductionLine("infantry", None, kind="military")],
    )
    call = ActionCall("set_production", {"equipment": "new_equipment", "factories": 1})
    with pytest.raises(Refused):
        production_console().plan(call, state)


@pytest.mark.parametrize(
    "slots",
    [
        {"empty": "unrecognized"},
        {"metadata": "unrecognized"},
        {"empty": [{"points": "1"}, "unrecognized"]},
        {"infantry_weapons2": [{"points": "1"}, "unrecognized"]},
    ],
)
def test_invalid_research_slot_records_do_not_claim_idle_capacity(slots):
    state = mapped({"country": {"technology": {"slots": slots}}})
    assert not state.known("research") and state.research == []
    assert "SLOT(S) FREE" not in render_full(state)


def test_repeated_busy_research_records_keep_their_slot_count():
    state = mapped(
        {
            "country": {
                "technology": {
                    "slots": {
                        "infantry_weapons2": [{"points": "1"}, {"points": "2"}],
                        "empty": {"points": "1"},
                    }
                }
            }
        }
    )
    assert state.known("research")
    assert [slot.technology for slot in state.research] == ["infantry_weapons2", "infantry_weapons2", None]
