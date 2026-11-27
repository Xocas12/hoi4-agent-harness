"""#160: the lobby's Custom Game Rules window driven by reading it (campaign/lobby_scan.py),
against a fake window: collapsible sections, a scrolling list, dropdown rows, a footer."""

from __future__ import annotations

import pytest

from hoi4_harness.adapters.ocr import FakeOcr, OcrLine
from hoi4_harness.campaign import lobby, lobby_scan
from hoi4_harness.campaign.lobby_scan import RuleScanner, ScanError, ScanLayout

RECT = (0, 0, 1920, 1080)
LIST_TOP, LIST_BOTTOM, ROW_H, FOOTER_Y = 100, 960, 40, 1000
# Footer: Save Preset ends at 1810, so the panel right edge is 1850 (pad 40), the arrow is
# clicked at 1820 (dx 30) and the dropdown at 1720 (dx 130); the fake accepts +-12 and +-60.
FOOTER = [("Apply", 1300), ("Reset", 1420), ("Load Preset", 1520), ("Save Preset", 1680)]


class FakeLobby:
    """The window as a state machine: ``sections`` is [(name, [rows])] and ``values`` the
    dropdown values by row label; it is both the OCR source and the clicker."""

    def __init__(self, wheel=True, open_sections=None):
        self.sections = [
            ("Major Nation Buffs", [f"Buff {i}" for i in range(8)]),
            ("Foreign Policy", [f"Policy {i}" for i in range(10)]),
            ("AI Behavior", [f"Country {i}" for i in range(25)] + ["Germany"]),
            ("Game Difficulty", [f"Diff {i}" for i in range(6)]),
        ]
        self.open = set(open_sections if open_sections is not None else (n for n, _ in self.sections))
        self.values = {"Germany": "Default"}
        self.options = ["Default", "Fascist", "Kaiser"]
        self.wheel, self.offset, self.dropdown = wheel, 0, None
        self.applied, self.front, self.log = False, True, []
        self.arrow_x, self.dd_x = 1810, 1710

    # content ----------------------------------------------------------------------
    def items(self):
        out = []
        for name, rows in self.sections:
            out.append(("header", name))
            if name in self.open:
                out += [("row", r) for r in rows]
        return out

    def height(self):
        return len(self.items()) * ROW_H

    def y_of(self, index):
        return LIST_TOP + index * ROW_H - self.offset

    def lines(self, rect=None):
        out = []
        for index, (kind, text) in enumerate(self.items()):
            y = self.y_of(index)
            if not (LIST_TOP <= y <= LIST_BOTTOM - 24):
                continue
            out.append(OcrLine(text, 1000 if kind == "row" else 900, y, 10 * len(text), 24))
            if kind == "row" and text in self.values:
                out.append(OcrLine(self.values[text], 1650, y, 70, 24))
        if self.dropdown is not None:
            y = self.dropdown + ROW_H
            for option in self.options:
                out.append(OcrLine(option, 1650, y, 70, 24))
                y += 30
        for label, x in FOOTER:
            width = 10 * len(label) + (20 if label == "Save Preset" else 0)
            out.append(OcrLine(label, x, FOOTER_Y, width, 24))
        return out

    # clicker ----------------------------------------------------------------------
    def in_front(self):
        return self.front is True or (callable(self.front) and self.front(len(self.log)))

    def click(self, point, label):
        self.log.append(("click", point, label))
        x, y = point
        if self.dropdown is not None:
            row_y = self.dropdown
            for n, option in enumerate(self.options):
                oy = row_y + ROW_H + 30 * n
                if oy <= y <= oy + 24:
                    self.values["Germany"] = option
            self.dropdown = None
            return
        if abs(y - FOOTER_Y - 12) <= 14 and 1290 <= x <= 1400:
            self.applied = True
            return
        for index, (kind, text) in enumerate(self.items()):
            cy = self.y_of(index) + 12
            if abs(y - cy) > 14 or not LIST_TOP <= cy <= LIST_BOTTOM:
                continue
            if kind == "header" and abs(x - self.arrow_x) <= 12:
                self.open ^= {text}
                self.offset = min(self.offset, max(0, self.height() - (LIST_BOTTOM - LIST_TOP)))
            elif kind == "row" and text in self.values and abs(x - self.dd_x) <= 60:
                self.dropdown = self.y_of(index)

    def scroll(self, point, clicks):
        self.log.append(("scroll", point, clicks))
        if self.wheel:
            self._set_offset(self.offset - clicks * 40)

    def drag(self, start, end):
        self.log.append(("drag", start, end))
        span = max(1, self.height() - (LIST_BOTTOM - LIST_TOP))
        track = (FOOTER_Y - 10) - (LIST_TOP - 40 + 60)
        self._set_offset(int(span * (end[1] - (LIST_TOP + 20)) / max(1, track)))

    def _set_offset(self, value):
        self.offset = max(0, min(value, max(0, self.height() - (LIST_BOTTOM - LIST_TOP))))

    def clicks(self):
        return [entry for entry in self.log if entry[0] == "click"]


def scanner(fake, **kwargs):
    lines = []
    layout = kwargs.pop("layout", ScanLayout(settle=0, wheel_clicks=5, drag_step=160))
    sc = RuleScanner(FakeOcr(lambda rect: fake.lines()), fake, lambda: RECT, lines.append,
                     layout=layout, pause=lambda s: None, **kwargs)
    return sc, lines


def test_160_the_scan_collapses_every_section_finds_the_row_and_sets_the_option():
    fake = FakeLobby()
    sc, lines = scanner(fake)
    sc.run(["Germany", "GER_ai_behavior"], ["Fascist"])
    assert fake.values["Germany"] == "Fascist"
    assert fake.applied
    assert fake.open == {"AI Behavior"}            # the others were collapsed, this one expanded
    collapsed = [c[2] for c in fake.clicks() if c[2].startswith("collapse")]
    assert len(collapsed) == 4                      # one click per section, never a double toggle
    assert any("now shows 'Fascist'" in line for line in lines)


def test_160_the_scan_drags_the_scrollbar_when_the_wheel_does_nothing():
    fake = FakeLobby(wheel=False)
    sc, lines = scanner(fake)
    sc.run(["Germany"], ["Kaiser"])
    assert fake.values["Germany"] == "Kaiser"
    assert any(entry[0] == "drag" for entry in fake.log)
    assert any("mouse wheel did not scroll" in line for line in lines)


def test_160_an_option_that_is_already_set_is_left_alone():
    fake = FakeLobby()
    fake.values["Germany"] = "Fascist"
    sc, lines = scanner(fake)
    sc.run(["Germany"], ["Fascist"])
    assert not any(c[2].startswith("open the dropdown") for c in fake.clicks())
    assert any("already shows" in line for line in lines)


def test_160_every_click_is_behind_the_window_in_front_check():
    fake = FakeLobby()
    fake.front = lambda n: n < 3                    # the game loses focus after three actions
    sc, _ = scanner(fake)
    with pytest.raises(lobby.LobbyError, match="not in front"):
        sc.run(["Germany"], ["Fascist"])
    assert len(fake.log) == 3                       # nothing was done after the window left


def test_160_a_click_that_changes_nothing_stops_the_scan_instead_of_toggling_again():
    fake = FakeLobby()
    layout = ScanLayout(settle=0, arrow_dx=300)     # a wrong arrow offset
    sc, _ = scanner(fake, layout=layout)
    with pytest.raises(ScanError, match="changed nothing"):
        sc.run(["Germany"], ["Fascist"])
    assert len(fake.clicks()) == 1


def test_160_a_missing_row_is_an_error_naming_what_was_looked_for():
    fake = FakeLobby()
    sc, _ = scanner(fake)
    with pytest.raises(ScanError, match="Atlantis"):
        sc.run(["Atlantis"], ["Fascist"])
    assert not fake.applied


def test_160_an_option_missing_from_the_opened_list_is_an_error():
    fake = FakeLobby()
    sc, _ = scanner(fake)
    with pytest.raises(ScanError, match="Monarchist"):
        sc.run(["Germany"], ["Monarchist"])
    assert not fake.applied


def test_160_a_closed_window_is_reported_before_any_click():
    fake = FakeLobby()
    sc = RuleScanner(FakeOcr(lambda rect: []), fake, lambda: RECT, lambda t: None,
                     pause=lambda s: None)
    with pytest.raises(ScanError, match="footer"):
        sc.run(["Germany"], ["Fascist"])
    assert fake.log == []


def test_160_ocr_that_cannot_run_is_reported_with_its_reason():
    fake = FakeLobby()
    sc = RuleScanner(FakeOcr(lambda rect: [], why="no OCR language"), fake, lambda: RECT,
                     lambda t: None, pause=lambda s: None)
    with pytest.raises(ScanError, match="no OCR language"):
        sc.run(["Germany"], ["Fascist"])


def test_160_calibration_logs_what_it_read_and_clicks_nothing(tmp_path):
    fake = FakeLobby(open_sections=["AI Behavior"])
    dump = tmp_path / "lobby.json"
    sc, lines = scanner(fake, calibrate=True, dump=dump)
    assert sc.run(["Germany"], ["Fascist"]) == 0
    assert fake.log == []
    text = "\n".join(lines)
    assert "'Apply'" in text and "panel right edge 1850" in text
    assert "headers recognised: 'AI Behavior'" in text or "'AI Behavior'" in text
    assert dump.exists() and '"Apply"' in dump.read_text(encoding="utf-8")


def test_160_the_whole_lobby_path_opens_the_window_scans_then_ticks_the_box_and_starts():
    fake = FakeLobby()
    sc, _ = scanner(fake)
    steps = lobby.steps_for("GER_ai_behavior", "FASCIST", None, scan=True)
    assert [s.key for s in steps] == ["custom_rules", "historical_ai", "start"]
    lobby_scan.run_scan_path(sc, steps, ["Germany"], ["Fascist"], fake, lambda t: None,
                             pause=lambda s: None)
    labels = [c[2] for c in fake.clicks()]
    assert labels[0] == "Custom Game Rules button"
    assert labels[-2:] == ["Historical AI Focuses box (toggle, assumed on at entry)", "START"]
    assert "Apply" in labels and fake.values["Germany"] == "Fascist"


def test_160_layout_overrides_come_from_the_lobby_points():
    layout = lobby_scan.layout_from({"scan_arrow_dx": 44, "scan_ai_section": ["ai rules"],
                                     "rule_row": [1, 2]})
    assert layout.arrow_dx == 44 and layout.ai_section == ("ai rules",)


def test_160_the_lobby_scan_flags_set_the_config():
    from hoi4_harness.cli import build_parser

    args = build_parser().parse_args(["play", "--campaign", "--choose-path", "--lobby-calibrate"])
    assert args.lobby_calibrate and not getattr(args, "lobby_scan", False)
