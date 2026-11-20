"""The fake game's save has the layout of the real one.

``fakesave.py`` writes what the headless stack reads when there is no game, so a
key it invents (or a nesting it gets wrong) would make every test of the
save-reading half pass against a format that does not exist. These compare it
with ``fixtures/swe_1938_02_04.hoi4``, a trimmed save the real game wrote, key by
key, and read it back through the same mapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness.adapters.savegame import (
    SaveGameAdapter,
    _as_list,
    country_block,
    parse_clausewitz,
    read_save,
)
from hoi4_harness.fakegame import FakeGame, demo_index
from hoi4_harness.types import ActionCall, ProductionLine

REAL = Path(__file__).parent / "fixtures" / "swe_1938_02_04.hoi4"


@pytest.fixture()
def fake(tmp_path):
    game = FakeGame(tmp_path / "user", index=demo_index(tmp_path / "playset"))
    game.world.state.production = [
        ProductionLine("infantry_equipment_1", 2, 0.5, 4.0),
        ProductionLine("artillery_equipment_1", 1, 0.0, 0.0),        # a line only just opened
    ]
    game.world.apply(ActionCall("queue_construction", {
        "building": "civilian_factory", "state": "Svealand", "count": 1}))
    game.world.state.completed_focuses = ["SWE_defense_act"]
    return game


@pytest.fixture()
def block(fake):
    path = fake.write_save()
    return country_block(path.read_text(encoding="utf-8"), "SWE")


@pytest.fixture(scope="module")
def real():
    return country_block(REAL.read_text(encoding="utf-8"), "SWE")


def keys(entries) -> set[str]:
    return {k for entry in _as_list(entries) for k in entry}


def test_every_key_the_fake_writes_in_a_production_line_exists_in_the_real_one(block, real):
    assert keys(block["production"]["military_lines"]) <= keys(real["production"]["military_lines"])


def test_the_queue_entry_has_the_real_entrys_keys(block, real):
    fake_entry = block["production"]["general_lines"]["building"]
    real_entry = next(e for e in _as_list(real["production"]["general_lines"]["building"]))
    assert set(fake_entry) <= set(real_entry)
    assert set(fake_entry["building"]) == set(real_entry["building"]) == {"template", "location"}


def test_the_country_blocks_top_level_keys_are_the_real_ones(block, real):
    assert set(block) <= set(real)


def test_the_header_and_top_level_blocks_are_the_real_ones(fake):
    text = fake.write_save().read_text(encoding="utf-8")
    # gameplaysettings (#102) is only in fixtures trimmed after trim_save kept it: the
    # Germany 1936 one. The Swedish ones predate it, and their value was never recorded.
    real_top = {line.split("=")[0]
                for path in (REAL, REAL.with_name("ger_1936_01_01.hoi4"))
                for line in path.read_text(encoding="utf-8").splitlines()
                if line and not line[0].isspace() and "=" in line}
    fake_top = {line.split("=")[0] for line in text.splitlines()
                if line and not line[0].isspace() and "=" in line}
    assert fake_top <= real_top
    assert text.startswith("HOI4txt\n")


def test_what_goes_in_comes_out_through_the_same_mapping(fake):
    path = fake.write_save()
    state = SaveGameAdapter(path.parent)._to_state(read_save(path))
    assert state.country == "SWE" and state.date == "1936-01-01"
    assert [(x.equipment, x.factories) for x in state.production] == [
        ("infantry_equipment_1", 2), ("artillery_equipment_1", 1)]
    assert [(c.building, c.state) for c in state.construction] == [("civilian_factory", "10")]
    assert state.completed_focuses == ["SWE_defense_act"]
    assert state.known("divisions") and sum(g.count for g in state.divisions) == 12
    assert state.civilian_factories == 12 and state.military_factories == 5


def test_a_binary_save_is_what_save_as_binary_yes_writes(fake):
    fake.save_mode = "binary"
    path = fake.write_save()
    with pytest.raises(ValueError, match="save_as_binary=no"):
        read_save(path)


def test_the_text_parses_as_clausewitz_script(fake):
    text = fake.write_save().read_text(encoding="utf-8")
    assert text.count("{") == text.count("}")
    assert "countries" in parse_clausewitz(text)
