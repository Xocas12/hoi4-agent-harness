"""The construction queue, production lines and focuses a 1.20.0 text save holds.

``fixtures/swe_1938_02_04.hoi4`` is Sweden on 4 February 1938 as the console's
``savegame`` command wrote it, trimmed by ``scripts/trim_save.py`` (the account
name is "player", seeds are gone). A civilian factory had just been queued in
state 38; the numbers below were read off that save, and the queue's location is
the one the player saw in the game.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness.adapters.savegame import SaveGameAdapter, country_block, read_save

FIXTURE = Path(__file__).parent / "fixtures" / "swe_1938_02_04.hoi4"


@pytest.fixture(scope="module")
def state():
    return SaveGameAdapter(FIXTURE.parent)._to_state(read_save(FIXTURE))


def test_the_queued_civilian_factory_is_in_the_save(state):
    assert state.known("construction")
    first = state.construction[0]
    assert (first.building, first.state) == ("civilian_factory", "38")
    # produced=4076.4 of cost=10800 at speed=120.75 a day.
    assert first.progress == pytest.approx(0.3774, abs=0.001)
    assert first.days_remaining == 56


def test_each_queued_level_is_an_item(state):
    # The line says amount=2: two levels, the first carrying the progress.
    assert len(state.construction) == 2
    assert state.construction[1].progress == 0.0


def test_a_line_that_only_asked_for_factories_is_still_a_line(state):
    # The fourth military line has queued_factories=1 and nothing else: it is the
    # second infantry line a set_production leaves behind, and it must show.
    lines = [line for line in state.production if line.equipment == "infantry_equipment_1"]
    assert len(lines) == 3
    assert [line.factories for line in lines] == [1, 1, 1]
    assert lines[-1].output_per_day == 0.0 and lines[-1].efficiency == 0.0


def test_completed_focuses_and_the_running_one(state):
    assert state.completed_focuses == ["SWE_defense_act", "SWE_urbanization"]
    assert state.national_focus == "SWE_work_projects_for_the_unemployed"
    assert state.known("completed_focuses")


def test_fuel_is_the_stockpile(state):
    assert state.fuel == pytest.approx(55912.75)


def test_the_decoy_blocks_do_not_shadow_the_players_country():
    # GER={ SWE={ opinion } } sits in front of the real one, ENG has a block at column 0.
    text = FIXTURE.read_text(encoding="utf-8")
    block = country_block(text, "SWE")
    assert "opinion" not in block and "production" in block


def test_the_fixture_is_scrubbed():
    text = FIXTURE.read_text(encoding="utf-8")
    assert 'user="player"' in text
    assert "seed=" not in text and "game_unique" not in text
