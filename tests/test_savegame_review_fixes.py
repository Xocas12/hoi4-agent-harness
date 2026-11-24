"""Review findings on the hardened save parser (#86), one test group each."""

from pathlib import Path
from types import SimpleNamespace

from hoi4_harness.adapters.console import ConsoleChannel
from hoi4_harness.adapters.savegame import (
    SaveGameAdapter,
    _equipment_names,
    parse_clausewitz,
    top_level_spans,
)
from hoi4_harness.adapters.ui_scripts import verify
from hoi4_harness.observation.delta import render_delta
from hoi4_harness.types import ActionCall, GameState, ProductionLine


def mapped(raw):
    return SaveGameAdapter(Path("."))._to_state(raw)


# a. an empty assignment closes its own block once, not its parent as well

def test_an_empty_assignment_in_a_block_does_not_close_the_parent():
    parsed = parse_clausewitz("country={ politics={ name= } stability=0.9 } date=1")
    assert parsed == {"country": {"politics": {}, "stability": "0.9"}, "date": "1"}


def test_an_empty_assignment_at_the_end_of_input_still_returns():
    assert parse_clausewitz("a={ b=") == {"a": {}}


# b. a production or units block with nothing listed is known and empty

def test_production_without_lines_reads_as_known_and_empty():
    state = mapped({"country": {"production": {"efficiency_loss": "0"}}})
    assert state.known("production") and state.production == []
    assert state.known("construction") and state.construction == []


def test_units_without_a_division_read_as_known_and_empty():
    state = mapped({"country": {"units": {"navy": {}}}})
    assert state.known("divisions") and state.divisions == []


def test_the_console_opens_the_first_line_of_a_country_with_no_production():
    state = mapped({"country": {"production": {"efficiency_loss": "0"}}})
    state.country, state.military_factories = "SWE", 3
    state.unknown_fields = [n for n in state.unknown_fields if n != "military_factories"]
    index = SimpleNamespace(tags={"SWE"}, equipment_types=lambda: {"new_equipment"},
                            equipment_unlock=lambda _equipment: (True, []))
    console = ConsoleChannel(SimpleNamespace(_sleep=lambda _seconds: None), index=index)
    call = ActionCall("set_production", {"equipment": "new_equipment", "factories": 1})
    assert "requested_factories = 1" in console.plan(call, state).lines[0]


# c. one bad equipment entry does not cost the others their names

def test_a_malformed_equipment_entry_is_skipped_not_fatal():
    names = _equipment_names({
        "infantry_equipment_1": {"id": {"id": "7", "type": "70"}},
        "stray_value": "3",
        "no_id": {"max_version": "0"},
        "untyped": {"id": {"id": "9"}},
    })
    assert names == {("7", "70"): "infantry_equipment_1"}


def test_only_a_duplicated_equipment_reference_is_withheld():
    names = _equipment_names({
        "infantry_equipment_1": {"id": {"id": "7", "type": "70"}},
        "twin_a": {"id": {"id": "8", "type": "70"}},
        "twin_b": {"id": {"id": "8", "type": "70"}},
    })
    assert names == {("7", "70"): "infantry_equipment_1"}


def write_save(tmp_path, equipments, lines, divisions="", templates=None):
    templates = templates or (
        ' division_template={ id={ id=5 type=52 } name="Infantry" country=SWE }\n')
    path = tmp_path / "s.hoi4"
    path.write_text(
        "date=1936.1.1\nplayer=SWE\n"
        f"equipments={{\n{equipments}}}\n"
        f"division_templates={{\n{templates}}}\n"
        "countries={\n\tSWE={\n"
        f"\t\tproduction={{ military_lines={{ {lines} }} }}\n"
        f"\t\tunits={{ {divisions} }}\n\t}}\n}}\n")
    return path


def test_a_junk_equipment_entry_in_a_saved_file_leaves_the_production_readable(tmp_path):
    write_save(
        tmp_path,
        " infantry_equipment_1={ id={ id=7 type=70 } }\n junk=3\n",
        "equipment_variant_index={ id=7 type=70 } active_factories=2")
    state = SaveGameAdapter(tmp_path).read_state()
    assert [(x.equipment, x.factories) for x in state.production] == [("infantry_equipment_1", 2)]


# d. a bare id is a reference when exactly one record carries it

def test_a_bare_scalar_reference_matches_a_unique_id(tmp_path):
    write_save(
        tmp_path,
        " infantry_equipment_1={ id={ id=7 type=70 } }\n",
        "equipment_variant_index=7 active_factories=2",
        divisions="division={ division_template_id=5 }")
    state = SaveGameAdapter(tmp_path).read_state()
    assert [(x.equipment, x.factories) for x in state.production] == [("infantry_equipment_1", 2)]
    assert [(g.template, g.count) for g in state.divisions] == [("Infantry", 1)]


def test_a_bare_scalar_reference_is_not_guessed_when_the_id_is_shared(tmp_path):
    write_save(
        tmp_path,
        " infantry_equipment_1={ id={ id=7 type=70 } }\n wrong_type={ id={ id=7 type=71 } }\n",
        "equipment_variant_index=7 active_factories=2",
        divisions="division={ division_template_id=5 }",
        templates=(' division_template={ id={ id=5 type=52 } name="Infantry" country=SWE }\n'
                   ' division_template={ id={ id=5 type=53 } name="Other" country=SWE }\n'))
    state = SaveGameAdapter(tmp_path).read_state()
    assert not state.known("production") and not state.known("divisions")


def test_a_duplicated_reference_does_not_let_the_bare_id_pick_one(tmp_path):
    write_save(
        tmp_path,
        " infantry_equipment_1={ id={ id=7 type=70 } }\n twin={ id={ id=7 type=70 } }\n",
        "equipment_variant_index=7 active_factories=2")
    assert not SaveGameAdapter(tmp_path).read_state().known("production")


# e. a stray closing brace at the root does not end the index

def test_a_stray_closing_brace_at_the_root_does_not_end_indexing():
    spans = top_level_spans("a=1 } b=2 c=3")
    assert set(spans) == {"a", "b", "c"}


def test_a_nested_containers_own_closing_brace_still_ends_that_container():
    text = "root={ a=1 b={ c=2 } d=3 } tail=4"
    assert set(top_level_spans(text)) == {"root", "tail"}


# f. verifying set_production counts every line of the equipment

def test_verify_production_sums_every_line_of_the_equipment():
    state = GameState(production=[ProductionLine("infantry", 2), ProductionLine("artillery", 1),
                                  ProductionLine("infantry", 3)])
    result = verify(ActionCall("set_production", {"equipment": "infantry", "factories": 5}),
                    state, lambda: state, timeout=0)
    assert result.ok, result.message


def test_verify_production_reports_the_total_when_it_differs():
    state = GameState(production=[ProductionLine("infantry", 2), ProductionLine("infantry", 3)])
    result = verify(ActionCall("set_production", {"equipment": "infantry", "factories": 4}),
                    state, lambda: state, timeout=0)
    assert not result.ok and "5" in result.message


# g. production turning known or unknown is one line, not one per equipment

def lines_about_production(text):
    return [line for line in text.splitlines() if line.lstrip("- ").startswith("Production")]


def test_production_turning_unknown_is_one_line():
    before = GameState(production=[ProductionLine("infantry", 1), ProductionLine("artillery", 2)])
    after = GameState(unknown_fields=["production"])
    assert lines_about_production(render_delta(before, after)) == ["Production: unknown"]


def test_production_turning_known_is_one_line():
    before = GameState(unknown_fields=["production"])
    after = GameState(production=[ProductionLine("infantry", 1), ProductionLine("artillery", 2)])
    lines = lines_about_production(render_delta(before, after))
    assert len(lines) == 1 and lines[0].startswith("Production: unknown ->")

