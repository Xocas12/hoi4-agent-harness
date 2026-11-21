"""Reading the open event window and choosing its option (adapters/event_choice.py).

Against a fake screen only (events_helpers.FakeEventScreen): OCR text laid out with the window's
layout, and a mouse that closes the window when a click lands on an option's slot. Nothing here
captures the real screen or moves the real mouse; whether the real game's window looks like the
fake is what docs/events.md's live steps settle."""

from __future__ import annotations

import json

import pytest

from events_helpers import FIX, FakeEventScreen, catalog, playset_layout
from hoi4_harness.actions import catalog as actions
from hoi4_harness.adapters.event_choice import (
    EventExecutor,
    EventLayout,
    EventReader,
    capture_box,
    enable_events,
    layout_for,
    locate_options,
    option_boxes,
    window_box,
)
from hoi4_harness.adapters.event_windows import EventGuard, EventSighting
from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter
from hoi4_harness.adapters.ocr import OcrLine
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import UNCAPPED_ACTIONS, HOI4Env
from hoi4_harness.identifiers import Playset
from hoi4_harness.types import ActionCall, GameState
from hoi4_harness.uilayout import Geometry

GEO = Geometry(1920, 1080)
BIG = Geometry(2560, 1440)


def build(tmp_path, geometry=GEO, *, tag="GER", damage=0, focused=True, dry_run=False,
          ocr_why=None, guard=None, coordinates=None, grow=0.0):
    cat = catalog(tag)
    screen = FakeEventScreen(geometry, cat, grow=grow, damage=damage)
    driver = InputDriverAdapter(
        InputConfig(geometry=geometry, coordinates=dict(coordinates or {}), move_duration=0),
        dry_run=dry_run, gui=screen, sleep=lambda s: None,
        focus_check=lambda title: FocusCheck(focused, "Hearts of Iron IV" if focused else "Notepad"))
    driver.event_guard = guard
    reader = EventReader(cat, screen.backend(ocr_why), driver.geometry, layout=screen.layout,
                         focused=lambda: driver.focus().focused,
                         probe=guard.probe if guard else None,
                         log_path=tmp_path / "events.jsonl", grow=grow)
    executor = enable_events(driver, reader, settle=0)
    return screen, driver, reader, executor


def choose(driver, option):
    return driver.apply(ActionCall("choose_event_option", {"option": str(option)}))


def without(screen, *words):
    """The screen with the lines containing ``words`` unread (OCR missing them)."""
    real = screen.lines
    screen.lines = lambda rect: [ln for ln in real(rect) if not any(w in ln.text for w in words)]


# --- the window layout -----------------------------------------------------------------------


def test_the_layout_is_read_from_the_playsets_eventwindow_gui():
    layout = layout_for(Playset.from_paths(FIX))
    assert layout.source == "interface/eventwindow.gui"
    assert (layout.position, layout.size) == ((-282.0, -310.0), (581.0, 427.0))
    assert (layout.top_height, layout.bottom_y, layout.grid, layout.slot) == (
        121.0, 221.0, (215.0, -1.0), (300.0, 47.0))


def test_a_file_without_the_window_keeps_the_built_in_layout():
    assert EventLayout.from_gui("guiTypes = { }").source.startswith("built in")
    assert EventLayout.from_gui("not a gui file {{{").source.startswith("built in")


def test_a_mods_different_window_moves_the_options():
    mod = EventLayout.from_gui(
        'guiTypes = { containerWindowType = { name = "EventWindow" position = { x=-100 y=-200 } '
        'size = { width = 500 height = 400 } Orientation = CENTER containerWindowType = { '
        'name = "bottom_Window" position = { x=0 y=250 } gridBoxType = { name = "options_grid" '
        "position = { x = 10 y = 0 } slotsize = { width = 280 height = 50 } } } } }")
    assert (mod.position, mod.bottom_y, mod.grid, mod.slot) == (
        (-100.0, -200.0), 250.0, (10.0, 0.0), (280.0, 50.0))


@pytest.mark.parametrize("geometry", [GEO, BIG])
def test_the_window_is_centred_by_the_layout_at_each_resolution(geometry):
    box = window_box(playset_layout(), geometry)
    s = geometry.scale
    gui_w, gui_h = geometry.gui_size
    assert box.x == round((gui_w / 2 - 282) * s) and box.y == round((gui_h / 2 - 310) * s)
    assert box.w == round(581 * s)
    # at 1080p, UI scale 1: 0.5 * 1920 - 282 * 1.25 = 607.5 -> the window's left edge
    if geometry is GEO:
        assert (box.x, box.y) == (608, 152)


@pytest.mark.parametrize("geometry", [GEO, BIG])
@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6])
def test_option_buttons_are_stacked_one_slot_apart_inside_the_window_column(geometry, count):
    layout = playset_layout()
    boxes = option_boxes(layout, geometry, count)
    assert len(boxes) == count
    window = window_box(layout, geometry)
    s = geometry.scale
    for i, box in enumerate(boxes):
        assert box.x == pytest.approx(window.x + 215 * s, abs=1.5)
        assert box.w == pytest.approx(300 * s, abs=1.5) and box.h == pytest.approx(47 * s, abs=1.5)
        if i:
            assert box.y - boxes[i - 1].y == pytest.approx(47 * s, abs=1.5)
    assert boxes[0].y == pytest.approx(window.y + 220 * s, abs=1.5)


def test_options_past_the_fourth_grow_the_window_and_can_recentre_it():
    layout = playset_layout()
    fixed = option_boxes(layout, GEO, 6)
    centred = option_boxes(layout, GEO, 6, centre_growth=True)
    assert fixed[0].y == option_boxes(layout, GEO, 4)[0].y          # top stays: nothing moved
    assert centred[0].y < fixed[0].y                                # a recentred window moves up
    assert fixed[0].y - centred[0].y == pytest.approx(47 * GEO.scale, abs=2)   # half of 2 slots


def test_a_taller_description_pushes_the_options_down():
    layout = playset_layout()
    assert option_boxes(layout, GEO, 2, grow=40)[0].y - option_boxes(layout, GEO, 2)[0].y == \
        pytest.approx(40 * GEO.scale, abs=1.5)


def test_the_ui_scale_scales_the_boxes():
    wide = Geometry(1920, 1080, gui_scale=1.25)
    assert option_boxes(playset_layout(), wide, 1)[0].w == round(300 * wide.scale)


def test_the_capture_rectangle_stays_on_the_screen_and_covers_the_window():
    for geometry in (GEO, BIG, Geometry(1280, 720)):
        cap, win = capture_box(playset_layout(), geometry), window_box(playset_layout(), geometry)
        assert cap.x <= win.x and cap.x + cap.w >= win.x + win.w and cap.y == win.y
        assert cap.y + cap.h <= geometry.height and cap.x >= 0


# --- locating options on the screen -----------------------------------------------------------


def test_an_option_is_found_by_its_text_even_when_it_wraps_onto_two_lines():
    event = catalog("GER").get("germany.75")
    lines = [OcrLine("The Danzig Question", 700, 230, 300, 20),
             OcrLine("Press for the return", 900, 700, 220, 16),
             OcrLine("of Danzig", 900, 718, 100, 16),
             OcrLine("Offer a guarantee of the Polish border", 900, 760, 300, 16)]
    found = locate_options(event, lines)
    assert set(found) == {1, 2}
    assert found[1].y == 700 and found[1].h == 34            # both lines
    assert found[2].centre == (1050, 768)


# --- the reader --------------------------------------------------------------------------------


def test_the_reader_identifies_the_window_and_times_how_long_it_has_been_open(tmp_path):
    clock = iter([100.0, 130.0, 160.0, 170.0])
    screen, driver, _, _ = build(tmp_path)
    reader = EventReader(catalog("GER"), screen.backend(), driver.geometry, layout=screen.layout,
                         clock=lambda: next(clock))
    screen.open("germany.75")
    reader.date_now = "1936-03-07"
    first = reader.look()
    later = reader.look()
    assert first.status == "identified" and first.event.id == "germany.75"
    assert (first.open_seconds, first.first_date) == (0.0, "1936-03-07")
    assert later.open_seconds == 30.0
    screen.close()
    assert reader.look().status == "none"
    screen.open("germany.75")
    assert reader.look().open_seconds == 0.0                  # a new window starts a new count


def test_text_that_is_not_an_event_is_none_without_a_probe_and_unidentified_with_one(tmp_path):
    screen, _, reader, _ = build(tmp_path)
    screen.noise = [("Berlin", 900, 300), ("Prussia 12th Army", 900, 400)]
    assert reader.look().status == "none"
    reader._probe = lambda: EventSighting("country")          # the pixel probe says one is open
    view = reader.look()
    assert view.status == "unidentified" and "Berlin" in view.page.text


def test_an_unavailable_ocr_or_an_unfocused_game_is_reported_not_read(tmp_path):
    _, _, reader, _ = build(tmp_path, ocr_why="Windows OCR needs Windows")
    assert reader.look().status == "unavailable" and "needs Windows" in reader.look().why
    _, _, reader, _ = build(tmp_path, focused=False)
    view = reader.look()
    assert view.status == "unavailable" and "not in front" in view.why


def test_the_brief_gives_title_description_options_and_their_effects(tmp_path):
    screen, _, reader, _ = build(tmp_path)
    screen.open("germany.75")
    text = "\n".join(reader.brief_lines(GameState(date="1936-03-07")))
    assert 'EVENT OPEN: germany.75 "The Danzig Question"' in text and "matched 1.00" in text
    assert "first seen 1936-03-07" in text and "Public opinion at home is split" in text
    assert "Option 1: Press for the return of Danzig -> add_political_power 50" in text
    assert "Option 3 [never shown to this country]" in text
    assert "choose_event_option" in text and "option 1 is taken blind" in text


def test_the_brief_is_silent_with_no_window_and_says_once_when_it_cannot_read(tmp_path):
    screen, _, reader, _ = build(tmp_path)
    assert reader.brief_lines(GameState()) == []
    _, _, off, _ = build(tmp_path, ocr_why="no OCR language")
    assert "not read (no OCR language)" in off.brief_lines(GameState())[0]
    assert off.brief_lines(GameState()) == []


def test_an_unmatched_open_window_is_in_the_brief_with_the_blind_fallback_told(tmp_path):
    screen, _, reader, _ = build(tmp_path)
    reader._probe = lambda: EventSighting("country")
    screen.noise = [("Some other text", 900, 300)]
    line = reader.brief_lines(GameState())[0]
    assert "not matched to any event" in line and "answers it blind" in line


# --- choosing ----------------------------------------------------------------------------------


@pytest.mark.parametrize("geometry", [GEO, BIG])
@pytest.mark.parametrize("option,index", [("1", 1), ("2", 2), ("germany.75.b", 2),
                                          ("Mobilise the reserve", 4)])
def test_choosing_clicks_the_option_where_its_text_is_and_the_window_closes(tmp_path, geometry,
                                                                          option, index):
    screen, driver, _, _ = build(tmp_path, geometry)
    screen.open("germany.75")
    result = choose(driver, option)
    assert result.ok, result.message
    # the fake window shows options 1, 2 and 4 (3 is POL's): the click lands in the slot of the
    # option's TEXT, so the fake closes on the option it drew there
    assert screen.chosen == [("germany.75", index)]
    assert "the window closed" in result.message
    assert result.changed["event"] == "germany.75" and result.changed["channel"] == "mouse"
    assert "text was read" in result.changed["located_by"]
    record = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert record[-1]["event"] == "germany.75" and record[-1]["closed"] is True
    assert "model chose option" in record[-1]["answer"]


def test_choosing_survives_ocr_typos(tmp_path):
    screen, driver, _, _ = build(tmp_path, damage=7)
    screen.open("germany.76")
    result = choose(driver, 2)
    assert result.ok, result.message
    assert screen.chosen == [("germany.76", 2)]


def test_the_result_says_what_the_option_does(tmp_path):
    screen, driver, _, _ = build(tmp_path)
    screen.open("germany.76")
    assert "add_political_power -25" in choose(driver, 1).message


def test_the_layout_model_is_compared_with_where_the_text_was_and_the_gap_is_reported(tmp_path):
    screen, driver, reader, _ = build(tmp_path, grow=30)  # the real window taller than the model
    screen.open("germany.75")
    reader.grow = 0.0                                       # the reader's model knows nothing of it
    result = choose(driver, 1)
    assert result.ok and screen.chosen == [("germany.75", 1)]
    note = result.changed["layout_note"]
    assert "layout model predicted" in note
    off = int(note.split("off by 0, ")[1].split(" ")[0])
    assert abs(off - 30 * GEO.scale) <= 2


def test_an_option_whose_text_was_not_read_is_clicked_by_the_layout_when_the_slot_is_known(tmp_path):
    screen, driver, _, _ = build(tmp_path)
    screen.open("germany.76")
    without(screen, "Answer with force")                    # the OCR misses the second option
    result = choose(driver, 2)
    assert result.ok, result.message
    assert screen.chosen == [("germany.76", 2)]
    assert "computed from the window layout" in result.changed["located_by"]
    assert "was not found on the screen" in result.changed["layout_note"]


def test_the_recorded_first_option_point_anchors_the_computed_slots(tmp_path):
    screen, driver, _, _ = build(tmp_path, coordinates={"event.option1": (1000, 700)})
    screen.open("germany.76")
    without(screen, "Answer with force")
    # the recorded point is elsewhere than the fake's slot: the click lands there, the window stays
    # open, and the result says so instead of claiming success
    result = choose(driver, 2)
    assert (1000, 700 + round(47 * GEO.scale)) in screen.clicks
    assert not result.ok and "still open" in result.message


def test_an_option_behind_one_with_an_unknown_trigger_cannot_be_placed_when_unread(tmp_path):
    # option 4 follows 2 (shown by tag) and 3 (never shown): its slot is 2, computable
    screen, driver, _, _ = build(tmp_path)
    screen.open("germany.75", shown=[1, 2, 4])
    without(screen, "Mobilise")
    assert choose(driver, 4).ok and screen.chosen == [("germany.75", 4)]
    # for a country with no tag known, option 2's trigger is unknown: its slot cannot be computed
    screen, driver, _, _ = build(tmp_path, tag=None)
    screen.open("germany.75", shown=[1, 2, 4])
    without(screen, "Mobilise", "guarantee")
    result = choose(driver, 4)
    assert not result.ok and "cannot be computed" in result.message and screen.clicks == []


def test_choosing_refuses_without_clicking_when_no_window_is_open(tmp_path):
    screen, driver, _, _ = build(tmp_path)
    result = choose(driver, 1)
    assert not result.ok and result.error_kind == "rejected"
    assert "no event window is open" in result.message and screen.clicks == []


def test_choosing_refuses_an_option_that_does_not_exist_or_is_not_shown(tmp_path):
    screen, driver, _, _ = build(tmp_path)
    screen.open("germany.75")
    assert "options 1 to 4, not 9" in choose(driver, 9).message
    assert "no option of germany.75 is called 'nope'" in choose(driver, "nope").message
    hidden = choose(driver, 3)
    assert not hidden.ok and "not shown to this country" in hidden.message
    assert screen.clicks == [] and choose(driver, "").error_kind == "invalid_args"


def test_choosing_refuses_a_window_it_could_not_identify(tmp_path):
    screen, driver, reader, _ = build(tmp_path)
    reader._probe = lambda: EventSighting("country")
    screen.noise = [("Unknown words here", 900, 300)]
    result = choose(driver, 1)
    assert not result.ok and "could not be matched" in result.message
    assert "answer it blind" in result.message and screen.clicks == []


def test_choosing_refuses_when_the_game_is_not_in_front_and_when_ocr_is_off(tmp_path):
    screen, driver, _, _ = build(tmp_path, focused=False)
    screen.open("germany.75")
    assert "not in front" in choose(driver, 1).message and screen.clicks == []
    screen, driver, _, _ = build(tmp_path, ocr_why="no OCR language")
    screen.open("germany.75")
    assert "events cannot be read" in choose(driver, 1).message and screen.clicks == []


def test_a_click_that_does_not_close_the_window_is_a_rejection_not_a_success(tmp_path):
    screen, driver, _, _ = build(tmp_path)
    screen.open("germany.75")
    screen.dead_zone = True
    result = choose(driver, 1)
    assert not result.ok and result.error_kind == "rejected" and "still open" in result.message
    assert len(screen.clicks) == 1                            # one click, never a retry storm


def test_a_dry_run_says_where_it_would_click_and_sends_nothing(tmp_path):
    screen, driver, _, _ = build(tmp_path, dry_run=True)
    screen.open("germany.75")
    result = choose(driver, 2)
    assert result.error_kind == "not_executed" and "would click germany.75 option 2" in result.message
    assert screen.clicks == [] and screen.event_id == "germany.75"


def test_a_chained_event_that_opens_at_once_is_named(tmp_path):
    screen, driver, _, _ = build(tmp_path)
    screen.open("germany.75")
    screen.on_close = lambda: screen.open("germany.76")
    result = choose(driver, 1)
    assert result.ok and result.changed["next_event"] == "germany.76"
    assert "next event is already open: germany.76" in result.message


def test_the_probe_still_seeing_a_window_after_the_text_is_gone_is_unverified(tmp_path):
    guard = EventGuard(lambda: EventSighting("country"), sleep=lambda s: None)
    screen, driver, _, _ = build(tmp_path, guard=guard)
    screen.open("germany.75")
    result = choose(driver, 1)
    assert result.error_kind == "unverified" and not result.ok
    assert screen.chosen == [("germany.75", 1)]               # the click was allowed past the gate


def test_the_click_goes_through_the_drivers_focus_guard(tmp_path):
    screen, driver, reader, _ = build(tmp_path)
    screen.open("germany.75")
    reader._focused = None                                    # the reader reads, the driver guards
    driver._focus_check = lambda title: FocusCheck(False, "Notepad")
    result = choose(driver, 1)
    assert not result.ok and screen.clicks == []


def test_a_point_off_the_screen_is_refused(tmp_path):
    screen, driver, _, _ = build(tmp_path, coordinates={"event.option1": (1000, 5000)})
    screen.open("germany.76")
    without(screen, "Refuse", "Answer with force")            # nothing read: only the layout is left
    result = choose(driver, 1)
    assert not result.ok and "outside" in result.message and screen.clicks == []


def test_choosing_is_never_cleared_first_by_the_blind_guard(tmp_path):
    answered = []
    guard = EventGuard(lambda: EventSighting("country"), sleep=lambda s: None)
    screen, driver, _, _ = build(tmp_path, guard=guard)
    screen.open("germany.75")
    driver.run_event_steps = lambda steps: answered.append(steps)
    choose(driver, 1)
    assert answered == [] and guard.record == []


def test_the_blind_answer_names_the_event_the_reader_knows(tmp_path):
    screen, driver, reader, _ = build(tmp_path)
    guard = EventGuard(lambda: EventSighting("country"), sleep=lambda s: None)
    guard.namer = reader.open_title
    screen.open("germany.75")
    reader.look()
    assert guard.namer() == "The Danzig Question"


# --- the env: the query, the brief, the catalog -------------------------------------------------


def make_env(tmp_path):
    from hoi4_harness.adapters.mock import MockAdapter

    screen, _, reader, _ = build(tmp_path)
    adapter = MockAdapter()
    adapter.event_reader = reader
    return HOI4Env(adapter, HarnessConfig()), screen


def test_read_event_answers_from_the_screen_with_the_match_and_the_options(tmp_path):
    env, screen = make_env(tmp_path)
    assert "read_event" in env.available_queries
    screen.open("germany.75")
    result = env.act(ActionCall("read_event", {}))
    data = json.loads(result.message)
    assert result.ok and data["status"] == "open" and data["id"] == "germany.75"
    assert data["match"]["identified"] and data["match"]["confidence"] > 0.95
    assert [o["on_screen"] for o in data["options"]] == [True, True, False, True]
    assert data["options"][1]["shown"] == "yes" and "open_for_seconds" in data


def test_read_event_with_no_window_and_by_id(tmp_path):
    env, _ = make_env(tmp_path)
    assert json.loads(env.act(ActionCall("read_event", {})).message)["status"] == "none"
    data = json.loads(env.act(ActionCall("read_event", {"id": "poland.1"})).message)
    assert data["status"] == "from_files" and len(data["description"]) == 2
    missing = env.act(ActionCall("read_event", {"id": "nope.1"}))
    assert not missing.ok and missing.error_kind == "invalid_args"


def test_read_event_is_not_offered_where_nothing_reads_the_screen():
    from hoi4_harness.adapters.mock import MockAdapter

    env = HOI4Env(MockAdapter(), HarnessConfig())
    assert "read_event" not in env.available_queries
    assert not env.act(ActionCall("read_event", {})).ok


def test_an_open_event_is_in_the_brief_of_every_observation(tmp_path):
    env, screen = make_env(tmp_path)
    screen.open("germany.76")
    assert "EVENT OPEN: germany.76" in env.observe().brief
    screen.close()
    assert "EVENT OPEN" not in env.observe().brief


def test_the_two_actions_are_in_the_catalog_and_the_choice_does_not_count_against_the_cap():
    assert actions.get("read_event").category == "query" and "read_event" in actions.QUERY_ACTIONS
    spec = actions.get("choose_event_option")
    assert spec.parameters["required"] == ["option"] and not spec.requires_confirmation
    assert "choose_event_option" in UNCAPPED_ACTIONS


def test_the_executor_names_its_one_action():
    assert EventExecutor.actions == frozenset({"choose_event_option"})
