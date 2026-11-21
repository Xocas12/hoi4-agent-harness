"""The OCR wrapper: parsing what the PowerShell script prints, degrading when OCR is missing.

Every test but the last injects a fake runner or a fake backend: nothing here captures a screen.
The last one runs the real Windows OCR on an image the test draws itself (Windows only, skipped
where OCR or PIL is missing); it proves the script and the parser agree, not that the game's fonts
read well."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from hoi4_harness.adapters.ocr import FakeOcr, OcrUnavailable, WindowsOcr, parse_output

RECT = (100, 50, 600, 400)


def payload(**over):
    base = {"language": "en-US", "scale": 2,
            "lines": [{"text": "The Danzig Question", "x": 40, "y": 20, "w": 300, "h": 30},
                      {"text": "  ", "x": 0, "y": 0, "w": 1, "h": 1},
                      {"text": "Press on", "x": 100, "y": 200, "w": 90, "h": 24}]}
    base.update(over)
    return json.dumps(base)


def test_boxes_are_divided_by_the_upscale_and_moved_by_the_rectangle_origin():
    page = parse_output(payload(), RECT)
    assert [ln.text for ln in page.lines] == ["The Danzig Question", "Press on"]    # blank dropped
    first = page.lines[0]
    assert (first.x, first.y, first.w, first.h) == (120, 60, 150, 15)
    assert first.centre == (195, 67) and page.language == "en-US" and page.rect == RECT
    assert page.text == "The Danzig Question\nPress on"


def test_an_error_from_the_script_is_an_ocr_unavailable_with_its_words():
    with pytest.raises(OcrUnavailable, match="no OCR language"):
        parse_output(json.dumps({"error": "Windows has no OCR language"}), RECT)


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]"])
def test_output_that_is_not_the_expected_json_is_unavailable_not_a_crash(raw):
    with pytest.raises(OcrUnavailable):
        parse_output(raw, RECT)


def test_malformed_rows_are_skipped_not_fatal():
    page = parse_output(json.dumps({"scale": 1, "lines": [{"text": "ok", "x": 1, "y": 2, "w": 3,
                                                           "h": 4}, {"text": "bad"}, {"x": 1}]}),
                        (0, 0, 10, 10))
    assert [ln.text for ln in page.lines] == ["ok"]


class Runner:
    def __init__(self, *outputs):
        self.outputs, self.calls, self.outputs_last = list(outputs), [], ""

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        out = self.outputs.pop(0) if self.outputs else self.outputs_last
        if isinstance(out, Exception):
            raise out
        return SimpleNamespace(stdout=out, stderr="", returncode=0)


def test_availability_is_probed_once_and_a_read_passes_the_rectangle_through_the_environment():
    runner = Runner(payload(lines=[]), payload())
    ocr = WindowsOcr(runner=runner, platform="win32", upscale=2)
    assert ocr.available() is None and ocr.available() is None
    assert len(runner.calls) == 1
    assert runner.calls[0][1]["env"]["HOI4_OCR_PROBE"] == "1"
    page = ocr.read(RECT)
    argv, kw = runner.calls[1]
    assert argv[0] == "powershell.exe" and "-EncodedCommand" in argv and "-NoProfile" in argv
    assert kw["env"]["HOI4_OCR_RECT"] == "100,50,600,400" and kw["env"]["HOI4_OCR_SCALE"] == "2"
    assert len(page.lines) == 2


def test_not_windows_is_a_clear_degrade_and_nothing_is_run():
    runner = Runner()
    ocr = WindowsOcr(runner=runner, platform="linux")
    assert "needs Windows" in ocr.available() and runner.calls == []
    with pytest.raises(OcrUnavailable, match="needs Windows"):
        ocr.read(RECT)


@pytest.mark.parametrize("failure,words", [
    (FileNotFoundError(), "powershell.exe was not found"),
    (subprocess.TimeoutExpired("powershell", 30), "did not finish"),
    (PermissionError("denied"), "could not be started"),
])
def test_a_missing_or_stuck_powershell_is_reported_in_words(failure, words):
    ocr = WindowsOcr(runner=Runner(failure), platform="win32")
    assert words in ocr.available()


def test_a_missing_language_pack_names_the_fix():
    ocr = WindowsOcr(runner=Runner(json.dumps({"error": "Windows has no OCR language for the "
                                               "user profile languages: add the English (US) "
                                               "language pack"})), platform="win32")
    assert "language pack" in ocr.available()


def test_an_empty_rectangle_is_refused_before_anything_runs():
    runner = Runner(payload(lines=[]))
    ocr = WindowsOcr(runner=runner, platform="win32")
    with pytest.raises(OcrUnavailable, match="empty"):
        ocr.read((0, 0, 0, 10))


def test_the_fake_backend_reports_its_reason_and_records_what_was_asked():
    fake = FakeOcr(lambda rect: [], why="no OCR here")
    assert fake.available() == "no OCR here"
    with pytest.raises(OcrUnavailable):
        fake.read((0, 0, 1, 1))
    ok = FakeOcr(lambda rect: [])
    assert ok.read((1, 2, 3, 4)).lines == () and ok.reads == [(1, 2, 3, 4)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows OCR")
def test_the_real_engine_reads_an_image_drawn_by_the_test(tmp_path):
    pil = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/cour.ttf", 22)
    except OSError:
        pytest.skip("no Courier New to draw with")
    image = pil.new("RGB", (581, 220), (214, 198, 160))
    draw = ImageDraw.Draw(image)
    draw.text((60, 20), "The Rhineland Crisis", font=font, fill=(20, 20, 20))
    draw.text((230, 150), "Protest loudly", font=font, fill=(20, 20, 20))
    path = tmp_path / "synthetic.png"
    image.save(path)
    ocr = WindowsOcr()
    why = ocr.available()
    if why:
        pytest.skip(why)
    page = ocr.read_image(path)
    found = {ln.text: ln for ln in page.lines}
    assert "The Rhineland Crisis" in found and "Protest loudly" in found
    title = found["The Rhineland Crisis"]
    assert abs(title.x - 60) <= 6 and abs(title.y - 20) <= 8      # boxes are in the image's pixels
    option = found["Protest loudly"]
    assert abs(option.x - 230) <= 6 and abs(option.y - 150) <= 8
