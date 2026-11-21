"""Event and news windows in an independent campaign (issue #125), against the fake game only.

The fake puts a window over the screen that takes every click until its answer is clicked: what an
event does in the real game. Nothing here touches the real game or sends real input. The probe
the guard uses is the fake's (the real one reads a screen pixel against a colour nobody has
measured yet), and the points are the test's own numbers."""

from __future__ import annotations

import json

import pytest

from campaign_helpers import say
from hoi4_harness.adapters.event_windows import EventGuard, EventOpen, EventSighting, PixelProbe
from hoi4_harness.types import ActionCall
from test_independent_hands import Recorder, make_world, play, results

OPTION1, NEWS_CLOSE = (960, 620), (1500, 300)
POINTS = {"event.option1": OPTION1, "event.news_close": NEWS_CLOSE}
GUARDED = {"event_windows": {"settle": 0}}
RESEARCH = [say("research", ("start_research", {"technology": "infantry_at"}),
                ("advance_time", {"days": 30, "reason": "x"}))]


def world_with(tmp_path, *, guarded=True, points=True, **options):
    world = make_world(tmp_path, points=POINTS if points else None,
                       **(GUARDED if guarded else {}), **options)
    world.fake.event_ui = {"option1": OPTION1, "news_close": NEWS_CLOSE}
    return world


def driver_of(world):
    return world.env.adapter.writer.router.driver


def events_record(tmp_path):
    path = tmp_path / "run" / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_an_open_news_window_eats_the_research_click_when_nothing_guards(tmp_path):
    """The bug as it was seen live: the click went to the event and the research was rejected."""
    world = world_with(tmp_path, guarded=False)
    world.fake.open_event("news", "Rhineland")
    play(world, Recorder(RESEARCH))
    result = results(tmp_path)["start_research"]
    assert result["is_error"] and "not in any research slot" in result["content"]
    assert world.fake.event_swallowed
    assert "event windows: NOT guarded" in world.log


def test_a_news_window_is_closed_before_the_research_runs_and_the_record_says_so(tmp_path):
    world = world_with(tmp_path)
    world.fake.open_event("news", "Rhineland")
    play(world, Recorder(RESEARCH))
    result = results(tmp_path)["start_research"]
    assert not result["is_error"], result
    assert "infantry_at" in {s.technology for s in world.fake.world.state.research}
    assert world.fake.event_swallowed == []                  # no research click met the window
    assert [(a["kind"], a["title"], a["answered"]) for a in world.fake.event_answers] == [
        ("news", "Rhineland", "closed")]
    assert "news event window was open" in result["content"]
    assert "events_answered" in result["content"]
    assert [(r["tool"], r["kind"], r["closed"]) for r in events_record(tmp_path)] == [
        ("start_research", "news", True)]
    assert "event windows: guarded" in world.log


def test_a_country_event_gets_its_first_option(tmp_path):
    world = world_with(tmp_path)
    world.fake.open_event("country", "Some decision")
    play(world, Recorder(RESEARCH))
    assert not results(tmp_path)["start_research"]["is_error"]
    assert [a["answered"] for a in world.fake.event_answers] == ["option 1"]
    assert events_record(tmp_path)[0]["answer"].startswith("first option")


def test_windows_stacked_on_each_other_are_all_closed(tmp_path):
    world = world_with(tmp_path)
    world.fake.open_event("news", "a")
    world.fake.open_event("country", "b")
    play(world, Recorder(RESEARCH))
    assert not results(tmp_path)["start_research"]["is_error"]
    assert [a["kind"] for a in world.fake.event_answers] == ["country", "news"]


def test_a_window_that_opens_during_the_run_is_closed_before_the_next_tool(tmp_path):
    world = world_with(tmp_path)
    world.fake.event_script = {"1936-01-15": ("news", "Rhineland")}
    script = [say("wait", ("advance_time", {"days": 20, "reason": "x"})),
              say("research", ("start_research", {"technology": "infantry_at"}),
                  ("advance_time", {"days": 10, "reason": "x"}))]
    play(world, Recorder(script))
    assert not results(tmp_path)["start_research"]["is_error"]
    assert [a["title"] for a in world.fake.event_answers] == ["Rhineland"]
    assert world.fake.event_swallowed == []


def test_a_window_with_no_recorded_answer_waits_and_nothing_is_clicked(tmp_path):
    world = world_with(tmp_path, points=False)
    world.fake.open_event("news", "Rhineland")
    clicks = len(world.fake.gui.calls)
    play(world, Recorder(RESEARCH))
    result = results(tmp_path)["start_research"]
    assert result["is_error"]
    assert "event.news_close" in result["content"] and "Nothing was clicked" in result["content"]
    assert len(world.fake.gui.calls) == clicks and world.fake.event_swallowed == []
    assert world.fake.events_open                           # still waiting for a person
    assert "cannot be closed until these points are recorded" in world.log
    assert "event.news_close" in world.log


def test_a_window_that_will_not_close_stops_the_tool_after_a_few_answers(tmp_path):
    world = world_with(tmp_path)
    world.fake.event_ui = {"option1": (5, 5), "news_close": (5, 5)}      # the answer misses it
    world.fake.open_event("news", "stuck")
    play(world, Recorder(RESEARCH))
    result = results(tmp_path)["start_research"]
    assert result["is_error"] and "still open" in result["content"]
    assert not any(line.startswith("research:") for line in world.fake.ui_log)
    assert len(world.fake.event_swallowed) == 3             # the three answers, nothing else


def test_a_click_is_refused_while_a_window_is_open_even_mid_path(tmp_path):
    world = world_with(tmp_path)
    driver = driver_of(world)
    world.fake.open_event("country", "mid-path")
    with pytest.raises(EventOpen, match="country event window is open"):
        driver.click_at((100, 200), label="slot")
    with pytest.raises(EventOpen):
        driver.click("event.option1")
    assert world.fake.event_swallowed == []
    result = driver.apply(ActionCall("start_research", {"technology": "infantry_at"}))
    assert [a["kind"] for a in world.fake.event_answers] == ["country"]   # cleared first
    assert result.message.startswith("Before this, a country event window was open")


def test_no_window_means_no_extra_click(tmp_path):
    world = world_with(tmp_path)
    play(world, Recorder(RESEARCH))
    assert not results(tmp_path)["start_research"]["is_error"]
    assert world.fake.event_answers == []
    assert not (tmp_path / "run" / "events.jsonl").exists()


def test_keyboard_only_tools_do_not_need_the_guard(tmp_path):
    world = world_with(tmp_path)
    world.fake.open_event("news", "Rhineland")
    play(world, Recorder([say("go", ("advance_time", {"days": 5, "reason": "x"}))]))
    assert world.fake.events_open and world.fake.event_answers == []


# --- the probe -----------------------------------------------------------------------------------


def test_the_pixel_probe_matches_a_colour_within_its_tolerance():
    pixels = {(10, 10): (30, 40, 50), (20, 20): (0, 0, 0)}
    probe = PixelProbe({"news": {"x": 10, "y": 10, "rgb": [32, 38, 55], "tolerance": 8},
                        "country": {"x": 20, "y": 20, "rgb": [0, 0, 200]}},
                       grab=lambda x, y: pixels[(x, y)])
    assert probe() == EventSighting("news")
    pixels[(20, 20)] = (5, 0, 190)
    assert probe() == EventSighting("country")      # a country event sits over a news window
    pixels[(10, 10)] = (90, 40, 50)
    pixels[(20, 20)] = (0, 0, 0)
    assert probe() is None


def test_a_bad_probe_spec_is_refused_naming_the_field():
    with pytest.raises(ValueError, match="event_windows.probes.news"):
        PixelProbe({"news": {"x": 1}})
    with pytest.raises(ValueError, match="unknown kind"):
        PixelProbe({"menu": {"x": 1, "y": 1, "rgb": [0, 0, 0]}})


def test_a_profile_with_no_probes_starts_unguarded(tmp_path):
    world = make_world(tmp_path)
    play(world, Recorder(RESEARCH))
    assert "event windows: NOT guarded: no event_windows probes" in world.log
    assert driver_of(world).event_guard is None


def test_the_guard_lists_the_points_its_answers_click():
    guard = EventGuard(lambda: None)
    assert guard.needed_points() == ["event.news_close", "event.option1"]
    assert guard.missing({"event.option1": (1, 1)}) == ["event.news_close"]
