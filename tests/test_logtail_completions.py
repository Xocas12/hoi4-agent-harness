"""Completion events derived from the daily state line (#2).

1.19.5.0 has no on_action for a finished focus, research or building, so the
mod prints cumulative counters and the reader turns a rise into the event.
"""

from hoi4_harness.adapters.logtail import EVENT_CODES, LogTailAdapter

LINE = (
    "[11:04:29][1936.01.{day:02d}.21]: LLMB|v3|state|date= 21:00, {day} January, 1936|tag={tag}"
    "|pp=1.9|stab=83.2|ws=0|civ=9|mil=3|doc=3|mp=31153"
    "|techs={techs}|built_civ={civ}|built_mil={mil}|built_dock=0|built_infra={infra}\n"
)


def day(n, tag="SWE", techs=10, civ=0, mil=0, infra=0):
    return LINE.format(day=n, tag=tag, techs=techs, civ=civ, mil=mil, infra=infra)


def log(tmp_path, *lines):
    path = tmp_path / "game.log"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def texts(tmp_path, *lines):
    return [(e.kind, e.text) for e in LogTailAdapter(log(tmp_path, *lines)).read_state().events]


def test_the_first_line_is_a_baseline_not_a_completion(tmp_path):
    assert texts(tmp_path, day(2, techs=10, civ=4)) == []


def test_a_rising_tech_count_is_research_done(tmp_path):
    got = texts(tmp_path, day(2, techs=10), day(3, techs=10), day(4, techs=11))
    assert got == [("research_done", "research complete: 1 new technology (11 researched)")]


def test_construction_rises_are_summed_into_one_event(tmp_path):
    got = texts(tmp_path, day(2, civ=1, infra=3), day(3, civ=2, mil=1, infra=3))
    assert got == [("construction_complete",
                    "construction complete: +1 civilian factory, +1 military factory")]


def test_a_flat_day_emits_nothing(tmp_path):
    assert texts(tmp_path, day(2, techs=10, civ=1), day(3, techs=10, civ=1)) == []


def test_another_country_does_not_complete_the_players_research(tmp_path):
    # The mislabelled-telemetry case: lines for several tags interleaved.
    got = texts(tmp_path, day(2, tag="SWE", techs=10), day(2, tag="POL", techs=3),
                day(3, tag="SWE", techs=10), day(3, tag="POL", techs=3))
    assert got == []


def test_a_counter_that_fell_is_a_new_game_not_an_event(tmp_path):
    assert texts(tmp_path, day(2, techs=10), day(3, techs=2)) == []


def test_events_are_acknowledged_like_any_other(tmp_path):
    adapter = LogTailAdapter(log(tmp_path, day(2), day(3, techs=11)))
    seen = adapter.read_state().events
    adapter.acknowledge_events(seen)
    assert adapter.read_state().events == []


def test_the_codes_the_wake_path_knows_are_registered():
    assert EVENT_CODES["3"][0] == "focus_complete" and EVENT_CODES["4"][0] == "research_done"
    assert EVENT_CODES["5"][0] == "construction_complete"
