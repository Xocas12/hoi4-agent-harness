"""scripts/record_camera.py writes the fit that division orders aim with (#85)."""

import importlib.util
from pathlib import Path

import pytest

from hoi4_harness.adapters.unit_orders import CameraRecord
from orders_helpers import FIT_PROVINCE, true_view, write_grid_game

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "record_camera.py"


@pytest.fixture
def record_camera(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    spec = importlib.util.spec_from_file_location("record_camera", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seen(layer_game, province):
    from orders_helpers import load_layer

    sx, sy = true_view().to_screen(*load_layer(layer_game).anchor(province, 38))
    return f"{province}:{sx:.1f},{sy:.1f}"


def run(module, game, out, *extra, refs=(1, 4, 9, 12, 7)):
    args = ["--game-dir", str(game), "--fit-province", str(FIT_PROVINCE), "--out", str(out),
            "--top", "60", "--bottom", "120", *extra]
    for p in refs:
        args += ["--ref", seen(game, p)]
    return module.main(args)


def test_a_fit_with_a_held_out_marker_is_written_validated_with_the_nearest_centre_rule(
        record_camera, tmp_path, capsys):
    game = write_grid_game(tmp_path / "game")
    out = tmp_path / "camera.json"
    assert run(record_camera, game, out, "--hold", seen(game, 8)) == 0
    record = CameraRecord.load(out)
    assert record.validated and record.fit_province == FIT_PROVINCE
    assert record.centre_rule == "anchor0"          # the grid's game centres on the counter anchor
    shown = capsys.readouterr().out
    assert "held out 8" in shown and "validated: True" in shown


def test_a_fit_nobody_checked_is_written_unvalidated(record_camera, tmp_path, capsys):
    game = write_grid_game(tmp_path / "game")
    out = tmp_path / "camera.json"
    run(record_camera, game, out, refs=(1, 4, 9, 12))
    assert not CameraRecord.load(out).validated
    assert "validated: False" in capsys.readouterr().out


def test_a_wrongly_read_marker_is_caught_by_the_held_out_one(record_camera, tmp_path, capsys):
    game = write_grid_game(tmp_path / "game")
    out = tmp_path / "camera.json"
    run(record_camera, game, out, "--hold", "8:100,100")
    assert not CameraRecord.load(out).validated
