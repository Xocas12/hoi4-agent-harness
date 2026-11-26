"""Reading the other player's lines off the game's chat box (#91, adapters/mp_chat_reader.py).

Everything here is a fake screen: ``FakeOcr`` rows, or the fake multiplayer game's chat box. Nothing
captures the real screen. Whether the real box looks like the fake is the calibration in
docs/multiplayer.md."""

from __future__ import annotations

import json

from hoi4_harness.adapters.mp_chat import MultiplayerChat, find_driver
from hoi4_harness.adapters.mp_chat_reader import (
    ChatLine,
    ChatReader,
    build_reader,
    new_lines,
    parse_lines,
    window_rect,
)
from hoi4_harness.adapters.ocr import FakeOcr, OcrLine
from hoi4_harness.config import HarnessConfig
from hoi4_harness.uilayout import Geometry
from test_91_multiplayer_fake import FOCUS, Game, host_plays


def screen(rows_holder: list):
    """A backend whose 'chat box' is whatever ``rows_holder`` holds now."""
    def source(rect):
        return [OcrLine(r, rect[0], rect[1] + 20 * i, 8 * len(r), 16)
                for i, r in enumerate(rows_holder)]
    return FakeOcr(source)


def reader_for(rows: list, **kw) -> ChatReader:
    kw.setdefault("baseline", False)
    return ChatReader(screen(rows), lambda: (0, 600, 600, 300), **kw)


# --- parsing --------------------------------------------------------------------------------


def test_91_parse_a_line_has_a_sender_and_text_and_may_start_with_a_time():
    assert parse_lines(["Hans: hello there", "[12:30] Anna: take army", "12:31 Bo: ok"]) == [
        ChatLine("Hans", "hello there"), ChatLine("Anna", "take army"), ChatLine("Bo", "ok")]


def test_91_parse_a_wrapped_row_continues_the_line_above_and_a_headless_one_is_dropped():
    rows = ["end of a line that scrolled out", "Hans: let us talk about the",
            "Baltic and the border", "Anna: sure"]
    assert parse_lines(rows) == [ChatLine("Hans", "let us talk about the Baltic and the border"),
                                 ChatLine("Anna", "sure")]


def test_91_parse_a_colon_in_the_text_stays_in_the_text_not_the_sender():
    got = parse_lines(["Hans: the plan is: attack now", "go to the front line: now please"])
    assert got == [ChatLine("Hans", "the plan is: attack now go to the front line: now please")]


def test_91_parse_pattern_is_replaceable():
    got = parse_lines(["<Hans> hi"], pattern=r"^<(?P<sender>[^>]+)>\s*(?P<text>.+)$")
    assert got == [ChatLine("Hans", "hi")]


# --- de-duplication -------------------------------------------------------------------------


def test_91_dedup_an_unchanged_box_gives_nothing_and_a_new_line_gives_one():
    rows = ["Hans: one", "Hans: two"]
    r = reader_for(rows)
    assert [m.text for m in r.read()] == ["one", "two"]          # no baseline: all new, once
    assert r.read() == []
    rows.append("Hans: three")
    assert [m.text for m in r.read()] == ["three"]
    assert r.read() == []


def test_91_dedup_survives_the_box_scrolling_and_an_ocr_misread():
    rows = ["Hans: take a look at the border", "Hans: second", "Hans: third"]
    r = reader_for(rows)
    r.read()
    rows[:] = ["Hans: second", "Hans: th1rd", "Hans: fourth"]       # scrolled; one letter misread
    assert [m.text for m in r.read()] == ["fourth"]


def test_91_dedup_the_same_words_twice_in_a_row_are_two_lines():
    rows = ["Hans: ok"]
    r = reader_for(rows)
    r.read()
    rows.append("Hans: ok")
    assert [m.text for m in r.read()] == ["ok"]


def test_91_dedup_the_first_read_is_a_baseline_not_news():
    rows = ["Hans: old talk", "Hans: older talk"]
    r = reader_for(rows, baseline=True)
    assert r.read() == []
    rows.append("Hans: now")
    assert [m.text for m in r.read()] == ["now"]


def test_91_dedup_lines_that_fade_out_and_back_are_not_new_again():
    rows = ["Hans: one", "Hans: two"]
    r = reader_for(rows)
    r.read()
    rows[:] = []                                                    # the box faded
    assert r.read() == []
    rows[:] = ["Hans: one", "Hans: two"]
    assert r.read() == []
    assert new_lines([], [ChatLine("A", "x")], [ChatLine("A", "x")]) == []


# --- the harness's own lines ----------------------------------------------------------------


def test_91_own_lines_the_harness_typed_are_read_and_dropped():
    sent = ["FRA to GER, we hold the Rhine"]
    rows = ["Hans: hello"]
    r = reader_for(rows, sent=lambda: sent, own_tag="FRA")
    assert [m.text for m in r.read()] == ["hello"]
    rows.append("Bot: FRA to GER, we hold the Rhine")
    rows.append("Bot: FRA to everyone, This turn I did set_national_focus")
    rows.append("Hans: fine")
    assert [m.text for m in r.read()] == ["fine"]


def test_91_own_lines_a_misread_of_what_the_harness_sent_is_still_its_own():
    sent = ["FRA to GER, we hold the Rhine and the Ardennes"]
    rows = ["Bot: FRA to GER, we hold the Rh1ne and the Ardennes"]
    r = reader_for(rows, sent=lambda: sent)
    assert r.read() == []


def test_91_own_lines_a_configured_name_is_dropped_whatever_it_says():
    rows = ["Claude_bot: take army", "Hans: take navy"]
    r = reader_for(rows, own_names=["Claude_bot"])
    assert [(m.sender, m.text) for m in r.read()] == [("Hans", "take navy")]


# --- only the game window, only while it is in front ------------------------------------------


def test_91_capture_nothing_is_read_unless_the_game_window_is_in_front():
    backend = screen(["Hans: hi"])
    front = {"on": False}
    r = ChatReader(backend, lambda: (0, 0, 100, 100), focused=lambda: front["on"], baseline=False)
    assert r.read() == [] and backend.reads == [] and "not in front" in r.why
    front["on"] = True
    assert [m.text for m in r.read()] == ["hi"] and len(backend.reads) == 1


def test_91_capture_the_rectangle_is_a_fraction_of_the_window_and_is_clamped_to_it():
    g = Geometry(1920, 1080, origin=(100, 50))
    assert window_rect((0.0, 0.5, 0.5, 0.5), lambda: g)() == (100, 590, 960, 540)
    # a rectangle that spills over the window's edge is cut at the edge
    assert window_rect((0.9, 0.9, 0.9, 0.9), lambda: g)() == (1828, 1022, 192, 108)
    assert window_rect((-1, -1, 5, 5), lambda: g)() == (100, 50, 1920, 1080)


def test_91_capture_ocr_trouble_degrades_to_nothing_with_a_reason():
    r = ChatReader(FakeOcr(lambda rect: [], why="Windows has no OCR language"),
                   lambda: (0, 0, 10, 10))
    assert r.read() == [] and "no OCR language" in r.why
    r = ChatReader(screen([]), lambda: None)
    assert r.read() == [] and "size is unknown" in r.why


def test_91_raw_reads_are_logged_for_calibration(tmp_path):
    rows = ["Hans: hi"]
    r = reader_for(rows, log_path=tmp_path / "reads.jsonl")
    r.read()
    entry = json.loads((tmp_path / "reads.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert entry["rows"] == ["Hans: hi"] and entry["heard"] == [["Hans", "hi"]]


def test_91_build_reader_says_it_is_uncalibrated_and_refuses_a_dry_run():
    class Driver:
        def geometry(self):
            return Geometry(1920, 1080)

        def focus(self):
            return type("F", (), {"focused": True})()

    config = HarnessConfig(multiplayer=True, country="FRA", mp_human_tags=["GER"],
                           clock_owner="player", dry_run=False)
    reader, note = build_reader(config, Driver(), None, backend=screen([]))
    assert reader is not None and "UNCALIBRATED" in note
    config.mp_chat_screen = {"rect": [0, 0.5, 0.3, 0.3]}
    reader, note = build_reader(config, Driver(), None, backend=screen([]))
    assert reader is not None and "UNCALIBRATED" not in note
    config.dry_run = True
    assert build_reader(config, Driver(), None)[0] is None
    config.dry_run = False
    config.mp_chat_screen = {"enabled": False}
    assert build_reader(config, Driver(), None, backend=screen([]))[0] is None
    assert build_reader(config, None, None, backend=screen([]))[0] is None


# --- wired into co-op on the fake multiplayer game --------------------------------------------


class FrontDriver:
    """The input driver, with the game window reported in front (the suite blocks the real check)."""

    @staticmethod
    def geometry():
        return Geometry(1920, 1080)

    @staticmethod
    def focus():
        return type("Focus", (), {"focused": True})()


def wired(tmp_path):
    g = Game(tmp_path, script=host_plays())
    assert find_driver(g.env.adapter) is not None
    reader, note = build_reader(g.config, FrontDriver(), g.env.mp_chat,
                                backend=g.fake.chat_ocr, run_dir=g.run_dir)
    assert reader is not None, note
    g.loop.supervisor.incoming = reader
    return g, reader


def test_91_the_hosts_chat_command_is_read_off_the_box_and_moves_a_domain(tmp_path):
    g, reader = wired(tmp_path)
    reader.baseline = False
    assert "set_national_focus" in g.env.allowed_actions
    g.fake.host_says("take focus")
    g.loop.run(1)
    assert "set_national_focus" not in g.env.allowed_actions
    assert any("Focus is yours again" in text for _, text in g.fake.chat_sent)


def test_91_the_harness_does_not_obey_or_answer_its_own_lines_from_the_box(tmp_path):
    g, reader = wired(tmp_path)
    reader.baseline = False
    g.wake("", FOCUS)                      # the harness explains itself in the game's chat
    assert g.fake.chat_sent
    heard = []
    for _ in range(3):
        g.loop.run(1)
        heard += reader.heard
    # its own "SWE to everyone, ..." lines are on the box and were read, and none came back
    assert any(row.startswith("Claude_bot: SWE to") for row in g.fake.box_rows())
    assert reader.reads >= 3 and heard == []


def test_91_a_wrapped_long_line_from_the_host_arrives_whole_and_wakes_the_model(tmp_path):
    g, reader = wired(tmp_path)
    reader.baseline = False
    words = "please hold the Baltic coast and keep the fleet in port until I say otherwise"
    g.fake.host_says(words)
    assert len(words) > g.fake.chat_width
    g.wake("ok", FOCUS)
    brief = [b for b in g.briefs() if "Hans" in b][0]
    assert f"NEW Hans: {words}" in brief
    reasons = [json.loads(x) for x in
               (g.run_dir / "t.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any("message from Hans" in json.dumps(r) for r in reasons)


def test_91_the_first_read_in_a_run_is_context_not_a_command(tmp_path):
    g, reader = wired(tmp_path)               # baseline on, as in a real run
    g.fake.host_says("take focus")            # said before the harness first looked
    g.loop.run(1)
    assert "set_national_focus" in g.env.allowed_actions
    g.fake.host_says("take focus")
    g.loop.run(1)
    assert "set_national_focus" not in g.env.allowed_actions


def test_91_mp_chat_remembers_what_it_typed():
    class Driver:
        def press(self, key):
            pass

        def type_text(self, text):
            pass

    sender = MultiplayerChat(Driver(), "FRA", ["GER"], sleep=lambda _s: None)
    sender.send("GER", "hold the line")
    assert sender.sent_lines == ["FRA to GER, hold the line"]
