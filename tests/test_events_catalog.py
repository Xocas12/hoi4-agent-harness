"""The events of a playset and the matcher that says which one a window's text is.

Read from tests/fixtures/eventctx, a small invented playset (ids and texts are not HOI4's):
titles with colour codes and a placeholder, a title two events share, options with tag triggers and
with triggers only the game can answer, a hidden event, a news event."""

from __future__ import annotations

import pytest

from events_helpers import FakeEventScreen, catalog, typo
from hoi4_harness.adapters.event_choice import capture_box, window_box
from hoi4_harness.modcontext.events import fit, fragments, partial
from hoi4_harness.uilayout import Geometry

GEO = Geometry(1920, 1080)


@pytest.fixture(scope="module")
def cat():
    return catalog("GER")


def read(screen: FakeEventScreen):
    box = capture_box(screen.layout, screen.geometry)
    lines = screen.lines((box.x, box.y, box.w, box.h))
    band = window_box(screen.layout, screen.geometry).y + round(121 * screen.geometry.scale)
    title = " ".join(ln.text for ln in lines if ln.centre[1] < band)
    return screen.catalog.identify("\n".join(ln.text for ln in lines), title=title,
                                   lines=[ln.text for ln in lines])


# --- reading the files -----------------------------------------------------------------------


def test_the_events_are_read_with_localised_title_description_and_options(cat):
    event = cat.get("germany.75")
    assert event.kind == "country_event" and event.title == "The Danzig Question"
    assert "Public opinion at home is split" in event.describe()["description"][0]
    assert [o.text for o in event.options] == [
        "Press for the return of Danzig", "Offer a guarantee of the Polish border",
        "Wait for Germany to blink", "Mobilise the reserve"]
    assert cat.get("world.1").kind == "news_event" and cat.get("world.1").major


def test_an_option_reads_its_effects_and_the_events_it_fires(cat):
    first = cat.get("germany.75").options[0].describe()
    assert first["effects"] == ("add_political_power 50, add_stability 0.05, "
                                "country_event{id germany.76, days 3}")
    assert first["fires_events"] == ["germany.76"] and first["ai_chance"] == "base 70"
    # name, trigger and ai_chance are not effects
    assert "name" not in first["effects"] and "trigger" not in first["effects"]


def test_option_triggers_are_resolved_by_tag_where_they_can_be_and_shown_where_they_cannot(cat):
    ger = {o.index: o.describe() for o in cat.get("germany.75").options}
    assert "shown_if" not in ger[1]                         # no trigger: always shown
    assert (ger[2]["shown"], ger[2]["shown_if"]) == ("yes", "tag GER")
    assert ger[3]["shown"] == "no"                          # tag POL: never for GER
    assert (ger[4]["shown"], ger[4]["shown_if"]) == ("unknown", "has_war")
    pol = {o.index: o.shown for o in catalog("POL").get("germany.75").options}
    assert pol == {1: None, 2: False, 3: True, 4: None}


def test_a_description_with_conditions_lists_every_variant_and_its_condition(cat):
    data = cat.get("poland.1").describe()
    assert len(data["description"]) == 2
    assert data["description_when"][0] == "has_war" and "not has_war" in data["description_when"][1]


def test_placeholders_and_colour_codes_are_dropped_from_the_text_people_read(cat):
    assert cat.get("germany.76").title == "demands Danzig"
    assert "§" not in cat.get("germany.75").title
    assert fragments("[FROM.GetNameDef] demands Danzig and [ROOT.GetName] refuses") == [
        "demands danzig and", "refuses"]
    assert fragments("§RHi§! $KEY$ ok") == []              # nothing literal to score


def test_an_unknown_id_is_none_and_a_hidden_event_is_still_described(cat):
    assert cat.get("nope.1") is None and cat.get("germany.99").hidden


# --- the matcher -----------------------------------------------------------------------------


def test_a_clean_window_is_identified_with_full_confidence(cat):
    screen = FakeEventScreen(GEO, cat)
    screen.open("germany.75")
    found = read(screen)
    assert found.event.id == "germany.75" and found.identified
    assert found.confidence > 0.97 and found.margin > 0.3
    assert set(found.option_scores) >= {1, 2, 4}


@pytest.mark.parametrize("damage", [0, 12, 9, 7])
@pytest.mark.parametrize("event_id", ["germany.75", "germany.76", "generic.1", "generic.2",
                                      "poland.1", "world.1"])
def test_ocr_typos_cost_confidence_not_the_match(cat, event_id, damage):
    screen = FakeEventScreen(GEO, cat, damage=damage)
    screen.open(event_id)
    found = read(screen)
    assert found.event.id == event_id and found.identified, found.to_dict()
    if damage:
        assert found.confidence < 1.0                      # the typos are felt


def test_two_events_with_one_title_are_told_apart_by_text_and_options(cat):
    screen = FakeEventScreen(GEO, cat, damage=9)
    screen.open("generic.2")
    found = read(screen)
    assert found.event.id == "generic.2" and found.runner_up == "generic.1"
    assert found.margin >= 0.05


def test_a_title_alone_is_a_weak_identification_of_a_shared_title(cat):
    found = cat.identify("A Quiet Season", title="A Quiet Season", lines=["A Quiet Season"])
    assert found.event.id in {"generic.1", "generic.2"}
    assert not found.identified                             # the margin is too thin to act on


def test_text_of_the_map_behind_is_not_an_event(cat):
    found = cat.identify("Berlin\nPrussia 12th Army\nPoznan", title="Berlin",
                         lines=["Berlin", "Prussia 12th Army", "Poznan"])
    assert not found.identified and found.confidence < 0.6


def test_an_empty_screen_identifies_nothing(cat):
    found = cat.identify("", title="", lines=[])
    assert found.event is None and not found.identified


def test_a_hidden_event_is_never_matched(cat):
    found = cat.identify("Hidden title\nHidden", title="Hidden title", lines=["Hidden title", "Hidden"])
    assert not found.identified or found.event.id != "germany.99"


def test_a_placeholder_title_matches_on_the_words_the_game_does_not_fill_in(cat):
    screen = FakeEventScreen(GEO, cat)
    screen.open("germany.76")
    lines = ["Poland demands Danzig"] + [ln.text for ln in screen.lines((0, 0, 1920, 1080))][1:]
    found = cat.identify("\n".join(lines), title="Poland demands Danzig", lines=lines)
    assert found.event.id == "germany.76" and found.identified


def test_partial_ratio_finds_a_fragment_inside_longer_text_and_scores_typos():
    assert partial("demands danzig", "poland demands danzig at once") == 1.0
    assert 0.75 <= partial("demands danzig", "poland dernands danzlg at once") < 1.0
    assert partial("demands danzig", "weather in the north") < 0.5
    assert fit(["[X] demands Danzig"], "poland demands danzig") == 1.0
    assert fit(["[X]"], "anything") is None


def test_the_typo_helper_really_damages_text():
    assert typo("Offer a guarantee of the Polish border", 7) != "Offer a guarantee of the Polish border"
