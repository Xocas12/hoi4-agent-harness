"""Issue #157: ``hoi4-harness memory-probe`` measures GB per hour with an injected reader."""

from __future__ import annotations

import csv
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

from hoi4_harness import cli
from hoi4_harness import memory_probe as mp

GB = 1024 ** 3


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, seconds):
        self.t += seconds

    def __call__(self):
        return self.t


def _probe(tmp_path, reader, **kw):
    clock = Clock()
    return mp.run_probe(save="GER_1940.hoi4", minutes=kw.pop("minutes", 30), variant=kw.pop("variant", "mod"),
                        out_dir=tmp_path, read_bytes=reader, sleep=clock.sleep, clock=clock,
                        now=lambda: datetime(2026, 11, 26, 12, 0, 0), say=lambda _t: None, **kw)


def test_157_slope_of_a_steady_30_gb_an_hour_leak(tmp_path):
    clock = {"n": 0}

    def reader():
        used = int((10 + 30 * clock["n"] / 60) * GB)      # +0.5 GB a minute
        clock["n"] += 1
        return used

    result = _probe(tmp_path, reader)
    summary = result.summary()
    assert summary["samples"] == 31 and summary["slope_gb_per_hour"] == pytest.approx(30.0, abs=0.05)
    assert summary["start_gb"] == 10.0 and summary["end_gb"] == pytest.approx(25.0, abs=0.01)
    assert summary["r_squared"] == pytest.approx(1.0)
    assert "+30.0 GB/hour" in result.render()


def test_157_a_flat_game_has_zero_slope(tmp_path):
    result = _probe(tmp_path, lambda: 9 * GB, minutes=5)
    assert result.summary()["slope_gb_per_hour"] == 0.0 and result.summary()["r_squared"] == 1.0


def test_157_csv_is_written_a_row_a_minute_and_the_summary_beside_it(tmp_path):
    calls = {"n": 0}

    def reader():
        calls["n"] += 1
        return (5 + calls["n"]) * GB

    result = _probe(tmp_path, reader, minutes=3, label="nomod-nodebug", variant="nomod",
                    read_date=lambda: "1940.11.2.5")
    rows = list(csv.DictReader(result.csv_path.open(encoding="utf-8")))
    assert [r["elapsed_s"] for r in rows] == ["0.0", "60.0", "120.0", "180.0"]
    assert rows[0]["private_gb"] == "6.000" and rows[0]["game_date"] == "1940.11.2.5"
    assert result.csv_path.name == "probe-nomod-nomod-nodebug-20261126-120000.csv"
    saved = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert saved["variant"] == "nomod" and saved["label"] == "nomod-nodebug" and saved["samples"] == 4


def test_157_a_game_that_vanishes_stops_the_probe_with_what_it_has(tmp_path):
    seq = iter([8 * GB, 9 * GB, None, None, None, 99 * GB])
    result = _probe(tmp_path, lambda: next(seq))
    assert result.stopped.startswith("stopped: hoi4.exe unreadable")
    assert result.summary()["samples"] == 2 and result.summary()["missed"] == 3


def test_157_one_missed_read_is_not_a_stop(tmp_path):
    seq = iter([8 * GB, None, 9 * GB, 10 * GB])
    result = _probe(tmp_path, lambda: next(seq), minutes=3)
    assert result.stopped == "" and result.summary()["samples"] == 3


def test_157_a_date_reader_that_fails_does_not_end_the_probe(tmp_path):
    def bad():
        raise OSError("log locked")

    result = _probe(tmp_path, lambda: 8 * GB, minutes=2, read_date=bad)
    assert result.summary()["samples"] == 3


def test_157_bad_arguments():
    with pytest.raises(ValueError, match="unknown variant"):
        mp.run_probe(save="x", minutes=1, variant="nope", out_dir=Path("."), read_bytes=lambda: 1)
    with pytest.raises(ValueError):
        mp.run_probe(save="x", minutes=0, variant="mod", out_dir=Path("."), read_bytes=lambda: 1)


def test_157_every_variant_has_exact_steps_and_names_the_save():
    assert set(mp.VARIANTS) == {"mod", "nomod", "debug", "nodebug", "log-daily", "log-weekly"}
    for name in mp.VARIANTS:
        steps = mp.manual_steps(name, "GER_1940.hoi4")
        assert steps[-1].endswith("GER_1940.hoi4") and steps[0].startswith("1. ")
    text = " ".join(mp.manual_steps("nomod", "s"))
    assert "live_setup.py prepare --no-mod --apply" in text and "restore --apply" in text
    assert "-debug_mode" in " ".join(mp.manual_steps("debug", "s"))
    assert "WITHOUT -debug_mode" in " ".join(mp.manual_steps("nodebug", "s"))
    assert "telemetry_every_168h" in " ".join(mp.manual_steps("log-weekly", "s"))
    assert "telemetry_every_24h" in " ".join(mp.manual_steps("log-daily", "s"))


def test_157_the_cadence_effects_named_in_the_steps_exist():
    from hoi4_harness import cadence

    assert cadence.effect_name(24) == "llm_bridge_telemetry_every_24h"
    assert cadence.effect_name(168) == "llm_bridge_telemetry_every_168h"


def test_157_compare_table_orders_by_growth(tmp_path):
    for variant, rate in (("mod", 30.0), ("nomod", 2.0)):
        n = {"i": 0}

        def reader(rate=rate, n=n):
            n["i"] += 1
            return int((10 + rate * (n["i"] - 1) / 60) * GB)

        _probe(tmp_path, reader, variant=variant, minutes=10)
    table = mp.compare(tmp_path)
    lines = table.splitlines()
    assert lines[1].startswith("nomod") and "+2.0" in lines[1]
    assert lines[2].startswith("mod") and "+30.0" in lines[2]
    assert "no *.summary.json" in mp.compare(tmp_path / "empty")


def test_157_cli_steps_only_and_compare(tmp_path, capsys):
    assert cli.main(["memory-probe", "--save", "GER_1940.hoi4", "--variant", "nomod", "--steps-only"]) == 0
    out = capsys.readouterr().out
    assert "prepare --no-mod --apply" in out and "GER_1940.hoi4" in out
    assert cli.main(["memory-probe", "--compare", str(tmp_path)]) == 0
    assert cli.main(["memory-probe", "--variant", "mod"]) == 2


def test_157_cli_runs_the_probe_with_the_injected_reader(tmp_path, monkeypatch, capsys):
    import hoi4_harness.campaign.watch as watch

    monkeypatch.setattr(watch, "process_private_bytes", lambda _n: 12 * GB)
    monkeypatch.setattr(mp.time, "sleep", lambda _s: None)
    code = cli.main(["memory-probe", "--save", "s.hoi4", "--variant", "debug", "--minutes", "0.05",
                     "--interval", "1", "--out", str(tmp_path)])
    assert code == 0
    assert "GB/hour" in capsys.readouterr().out and list(tmp_path.glob("probe-debug-*.csv"))


# --- live_setup --no-mod -------------------------------------------------------------------------

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_setup.py"
_spec = importlib.util.spec_from_file_location("live_setup_157", SCRIPT)
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)


def test_157_live_setup_can_leave_the_mod_out():
    text = json.dumps({"enabled_mods": ["mod/other.mod", ls.MOD_ENTRY], "disabled_dlcs": []})
    without = json.loads(ls.edit_dlc_load(text, False, with_mod=False))
    assert without["enabled_mods"] == ["mod/other.mod"]
    assert json.loads(ls.edit_dlc_load(text, False))["enabled_mods"] == ["mod/other.mod", ls.MOD_ENTRY]
    assert ls.build_parser().parse_args(["prepare", "--no-mod"]).no_mod is True
