"""The research click path follows the game's version: 1.20 clicks the popup's Research button
and the MIO chooser's row (both computed from the measured 1080p points unless recorded), 1.19
keeps Return twice. Fake game only; nothing here touches the real game."""

from __future__ import annotations

import pytest

from campaign_helpers import say
from hoi4_harness.adapters.hands import research_path_for, research_steps
from hoi4_harness.adapters.ui_resolve import REFERENCE_POINTS, reference_point
from hoi4_harness.uilayout import Geometry
from test_independent_hands import SCREEN, Recorder, make_world, play, results

CONFIRM, MIO = (750, 252), (1207, 345)
RESEARCH = [say("research", ("start_research", {"technology": "infantry_at"}),
                ("advance_time", {"days": 30, "reason": "x"}))]


def with_120_screen(world):
    world.fake.research_ui.update(confirm=CONFIRM, mio=MIO)


def test_the_version_picks_the_path():
    assert research_path_for("1.20.0.0") == "1.20"
    assert research_path_for("1.20.1.0") == "1.20"
    assert research_path_for("1.19.5.0") == "1.19"
    assert research_path_for(None) == "1.19"
    kinds = [(s.kind, s.value) for s in research_steps("1.20.0.0")]
    assert ("click", "research.confirm") in kinds and ("click", "research.mio_choice") in kinds
    assert ("press", "enter") not in kinds
    assert [s.value for s in research_steps("1.19.5.0") if s.kind == "press"].count("enter") == 2


def test_the_measured_points_are_the_identity_at_1080p_and_scale_elsewhere():
    window = Geometry(1920, 1080)
    assert reference_point("research.confirm", window) == CONFIRM
    assert reference_point("research.mio_choice", window) == MIO
    # 1440p at the same UI scale: offsets from the centre grow by 4/3.
    big = reference_point("research.confirm", Geometry(2560, 1440))
    assert big == (round(1280 + (750 - 960) * 4 / 3), round(720 + (252 - 540) * 4 / 3))
    # UI scale 1.25 at 1080p: the popup is centred, so the offsets grow by the scale.
    scaled = reference_point("research.mio_choice", Geometry(1920, 1080, gui_scale=1.25))
    assert scaled == (round(960 + (1207 - 960) * 1.25), round(540 + (345 - 540) * 1.25))
    assert set(REFERENCE_POINTS) == {"research.confirm", "research.mio_choice"}


def test_on_120_research_runs_through_the_popup_with_no_recorded_points(tmp_path):
    world = make_world(tmp_path, version="1.20.0.0")
    with_120_screen(world)
    play(world, Recorder(RESEARCH))
    result = results(tmp_path)["start_research"]
    assert not result["is_error"], result
    assert "verified" in result["content"]
    assert "infantry_at" in {s.technology for s in world.fake.world.state.research}
    assert world.fake.keylog[:3] == ["y", "w", "grave"]      # no Return before the console
    log = " ".join(world.fake.ui_log)
    assert "popup Research" in log and "without an organisation" in log
    assert "research path: 1.20 click path" in world.log


def test_a_recorded_point_wins_over_the_computed_one(tmp_path):
    from hoi4_harness.adapters.ui_resolve import resolve
    from hoi4_harness.config import HarnessConfig
    from test_independent_hands import write_playset

    index = HarnessConfig(game_dir=write_playset(tmp_path / "game")).build_index()
    wanted = {"research.confirm", "research.mio_choice", "research.tech_point"}
    window = Geometry(1920, 1080)
    got = resolve("start_research", {"technology": "infantry_at"}, index,
                  {"research.confirm": (800, 300)}, SCREEN, wanted=wanted, geometry=lambda: window)
    assert "research.confirm" not in got.coordinates
    assert got.coordinates["research.mio_choice"] == MIO


@pytest.mark.parametrize("version", ["1.19.5.0", None])
def test_1_19_keeps_return_twice(tmp_path, version):
    world = make_world(tmp_path, version=version)
    play(world, Recorder(RESEARCH))
    assert not results(tmp_path)["start_research"]["is_error"]
    assert world.fake.keylog[:4] == ["y", "w", "enter", "enter"]
    assert "research path: 1.19 click path" in world.log
