"""Structural save selection must not depend on Paradox's indentation."""

import pytest

import hoi4_harness.adapters.savegame as savegame

# Minimized from the real SWE 1936 save: occupation country references and
# state_garrison_data numeric keys can both start in column zero.
UNINDENTED_NESTING = """date="1936.1.1.12"
player="SWE"
countries={
\tGER={
\t\toccupation={
CAN={
\t\t\tstate_garrison_data={
332={ strength_ratio=1 }
\t\t\t}
\t\t}
\t\t}
\t}
\tSWE={ political_power=2.2 }
}
states={ 1={ owner=SWE } }
"""


def test_unindented_nested_entries_do_not_cut_the_countries_container():
    spans = savegame.top_level_spans(UNINDENTED_NESTING)
    assert set(spans) == {"date", "player", "countries", "states"}
    start, end = spans["countries"][0]
    assert "SWE={ political_power=2.2 }" in UNINDENTED_NESTING[start:end]
    assert savegame.country_block(UNINDENTED_NESTING, "SWE") == {"political_power": "2.2"}


@pytest.mark.parametrize(
    "text",
    [
        "date=1936.1.1 player=SWE countries={ SWE={ political_power=7 } }",
        "  date=1936.1.1\n player=SWE\n countries={\nSWE={political_power=7}\n}",
    ],
)
def test_compact_and_indented_saves_use_the_same_structure(text):
    assert set(savegame.top_level_spans(text)) == {"date", "player", "countries"}
    assert savegame.country_block(text, "SWE") == {"political_power": "7"}


def test_country_selection_ignores_nested_tags_at_the_same_indentation():
    text = """countries={
GER={
SWE={ opinion=10 }
}
SWE={ political_power=99 }
}
"""
    assert savegame.country_block(text, "SWE") == {"political_power": "99"}


def test_repeated_top_level_entries_are_preserved_in_order():
    text = "item={ id=1 } item={ id=2 }\nitem={ id=3 }"
    spans = savegame.top_level_spans(text)
    assert len(spans["item"]) == 3
    assert savegame.extract(text, {"item"}) == {"item": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}


def test_braces_and_fake_assignments_in_strings_or_comments_are_not_structure():
    text = r"""# countries={ FAKE={ } } extra=1
date=1936.1.1
player=SWE
note="escaped \" quote { # literal
fake=1 }"
countries={ # } fake=2
GER={ note="SWE={ opinion=3 }" }
SWE={ political_power=4 # } ignored=5
}
}
"""
    assert set(savegame.top_level_spans(text)) == {"date", "player", "note", "countries"}
    assert savegame.country_block(text, "SWE") == {"political_power": "4"}


def test_missing_key_does_not_trigger_tokenizing_unrelated_large_blocks(monkeypatch):
    seen = []
    real_parse = savegame.parse_clausewitz

    def parse_selected(chunk):
        seen.append(chunk)
        return real_parse(chunk)

    monkeypatch.setattr(savegame, "parse_clausewitz", parse_selected)
    assert savegame.extract(UNINDENTED_NESTING, {"player", "missing"}) == {"player": "SWE"}
    assert all("countries" not in chunk and "states" not in chunk for chunk in seen)


@pytest.mark.parametrize("text", ["key=", "country={ political_power=", 'country={ name="unterminated'])
def test_a_partially_written_scalar_does_not_crash_the_parser(text):
    assert isinstance(savegame.parse_clausewitz(text), dict)


def test_anonymous_resource_blocks_do_not_end_the_country_early():
    text = """countries={ SWE={
resources={ produced={ { steel=8 } { aluminium=2 } } }
politics={ political_power=2.2 }
stability=0.9
} }"""
    block = savegame.country_block(text, "SWE")
    assert block["politics"]["political_power"] == "2.2"
    assert block["resources"]["produced"]["_items"] == [{"steel": "8"}, {"aluminium": "2"}]
    assert block["stability"] == "0.9"


def test_escaped_quotes_comments_and_repeated_anonymous_blocks_parse():
    text = r"""name="A \"quoted\" # { name" # ignore=1 }
values={ { id=1 } { id=2 } }
after=3"""
    parsed = savegame.parse_clausewitz(text)
    assert parsed == {
        "name": 'A "quoted" # { name',
        "values": {"_items": [{"id": "1"}, {"id": "2"}]},
        "after": "3",
    }


def test_duplicate_player_blocks_across_containers_are_ambiguous():
    assert savegame.country_block("countries={ SWE={ pp=1 } } countries={ SWE={ pp=2 } }", "SWE") is None


@pytest.mark.parametrize("tail", ["", ' name="} fake=1', ' name="trailing\\'])
def test_unfinished_country_is_not_reported_as_a_complete_snapshot(tail):
    text = "player=SWE countries={ SWE={ politics={political_power=2.2}" + tail
    assert savegame.country_block(text, "SWE") is None


def test_unterminated_quoted_braces_do_not_close_a_country():
    assert savegame.country_block('countries={ SWE={ name="} }', "SWE") is None


def test_quoted_keys_are_selected_without_confusing_hashes_for_comments():
    text = '"player"="SWE" "countries"={ "SWE"={ "name"="A # name" } }'
    assert savegame.extract(text, {"player"}) == {"player": "SWE"}
    assert savegame.country_block(text, "SWE") == {"name": "A # name"}
