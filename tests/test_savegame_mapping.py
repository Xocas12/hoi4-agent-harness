"""The save-to-GameState mapping (#6), checked against a real save.

``fixtures/swe_1937_04_23.hoi4`` is a trimmed copy of a text save of Sweden on
23 April 1937 (HOI4 1.19.5, LLM Bridge mod loaded, started from the 1936
bookmark): the player's country block, the states Sweden owns plus two foreign
ones, Sweden's division templates and the equipment names its lines refer to,
and two decoy countries that reproduce what a real save does to naive readers
(a block written at column 0 inside ENG, and a ``SWE={ opinion }`` nested in
GER). The account name is replaced with "player".

Every expected number below was read off the running game at that moment (the
top bar, the production tooltip, the research screen, the mod's telemetry log),
not off the save, so a mapping that read the wrong key fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness.adapters.savegame import (
    SaveGameAdapter,
    country_block,
    matching_brace,
    parse_clausewitz,
    read_save,
)
from hoi4_harness.observation.render import render_full

FIXTURE = Path(__file__).parent / "fixtures" / "swe_1937_04_23.hoi4"


@pytest.fixture(scope="module")
def raw() -> dict:
    return read_save(FIXTURE)


@pytest.fixture()
def state(raw):
    return SaveGameAdapter(FIXTURE.parent)._to_state(raw)


def test_the_date_and_tag(state):
    assert state.date == "1937-04-23" and state.country == "SWE"


def test_political_power_is_the_games_figure(state):
    # The top bar said ~886-891 over those days; the save holds 891.4101.
    assert state.political_power == pytest.approx(891.41, abs=0.01)
    assert state.known("political_power")


def test_stability_and_war_support_come_from_the_computed_figures_not_the_base(state):
    # The country block stores stability=0.47 and war_support=0.05: the base,
    # before modifiers. The game showed 82.6% and 0%.
    assert state.stability == pytest.approx(0.826, abs=0.001)
    assert state.war_support == 0.0
    assert state.known("stability") and state.known("war_support")


def test_without_the_mod_stability_is_unknown_not_the_base_value(raw):
    stripped = dict(raw)
    country = dict(raw["country"])
    country["variables"] = {k: v for k, v in country["variables"].items() if not k.startswith("llmb_")}
    stripped["country"] = country
    state = SaveGameAdapter(FIXTURE.parent)._to_state(stripped)
    assert not state.known("stability") and not state.known("war_support")
    assert state.stability != pytest.approx(0.47)
    assert state.known("political_power") and state.manpower == 32980


def test_manpower_is_the_sum_of_available_across_owned_states(state):
    assert state.manpower == 32980           # the top bar's "32.98K"
    assert state.known("manpower")


def test_factories_are_summed_over_owned_states_only(state):
    # 9 owned civilian factories (the tooltip's 6/20 in use is 9 + 11 from trade).
    assert (state.civilian_factories, state.military_factories, state.dockyards) == (9, 3, 3)
    assert all(state.known(f) for f in ("civilian_factories", "military_factories", "dockyards"))


def test_the_running_focus(state):
    assert state.national_focus == "SWE_defense_act"
    assert state.known("national_focus")


def test_research_has_one_busy_slot_and_two_free(state):
    assert [(s.index, s.technology) for s in state.research] == [
        (1, "infantry_weapons2"), (2, None), (3, None),
    ]
    assert all(s.days_remaining is None for s in state.research)   # not stored, not guessed


def test_production_lines_name_their_equipment(state):
    lines = {line.equipment: line for line in state.production}
    assert set(lines) == {"infantry_equipment_1", "support_equipment_1"}
    infantry = lines["infantry_equipment_1"]
    assert infantry.factories == 1
    assert infantry.efficiency == pytest.approx(0.53)
    assert infantry.output_per_day == pytest.approx(2.935 / 0.5)


def test_twelve_divisions_counted_by_template(state):
    assert sum(d.count for d in state.divisions) == 12
    assert {d.template: d.count for d in state.divisions} == {
        "Arméfördelning": 5, "Militärområde": 6, "Kavalleribrigaden": 1,
    }
    assert state.known("divisions")


def test_what_the_save_does_not_hold_is_still_unknown(state):
    for name in ("construction", "fronts", "wars", "completed_focuses", "delegated_armies",
                 "ai_directives", "posture", "theater_postures", "fuel", "convoys"):
        assert not state.known(name), name


def test_the_brief_carries_the_real_numbers(state):
    brief = render_full(state)
    assert "PP 891" in brief and "stability 83%" in brief and "manpower 32,980" in brief
    assert "9 civ / 3 mil / 3 dockyards" in brief
    assert "SWE_defense_act" in brief and "infantry_weapons2" in brief
    assert "12 divisions" in brief


def test_the_player_block_is_not_another_countrys(raw):
    # ENG writes a block at column 0 and GER nests SWE={ opinion }: both used to
    # hide or replace the player's block.
    assert raw["country"]["politics"]["political_power"] == "891.4101"
    assert "opinion" not in raw["country"]


def test_only_the_players_states_and_templates_are_kept(raw):
    assert {s["owner"] for s in raw["owned_states"].values()} == {"SWE"}
    assert len(raw["owned_states"]) == 13
    assert set(raw["division_templates"].values()) >= {"Arméfördelning", "Militärområde"}


def test_a_division_with_an_unnamed_template_makes_divisions_unknown(raw):
    broken = dict(raw, division_templates={})
    state = SaveGameAdapter(FIXTURE.parent)._to_state(broken)
    assert not state.known("divisions") and state.divisions == []


def test_a_binary_save_is_refused_with_the_setting_that_fixes_it(tmp_path):
    path = tmp_path / "ironman.hoi4"
    path.write_bytes(b"HOI4bin\x00\x01\x02")
    with pytest.raises(ValueError, match="save_as_binary=no"):
        read_save(path)


# --- the parser fixes the mapping depends on ----------------------------------

def test_keyless_blocks_in_a_list_do_not_close_the_parent():
    parsed = parse_clausewitz("a={ leaders={ { id=1 } { id=2 } } after=yes } b=3")
    assert [item["id"] for item in parsed["a"]["leaders"]["_items"]] == ["1", "2"]
    assert parsed["a"]["after"] == "yes" and parsed["b"] == "3"


def test_matching_brace_ignores_braces_in_strings():
    text = 'x={ name="a } b" y={ } } tail'
    assert text[: matching_brace(text, text.index("{"))] == 'x={ name="a } b" y={ } }'


def test_country_block_survives_a_column_zero_block_in_an_earlier_country():
    text = "countries={\n\tAAA={\nBBB={\n}\n\t\tx=1\n\t}\n\tSWE={\n\t\tp=2\n\t}\n}\n"
    assert country_block(text, "SWE") == {"p": "2"}
