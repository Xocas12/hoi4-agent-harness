"""The combined reader's rules on their own: what the log wins, what the save
fills, when a save counts as fresh or due, and what a failed save does."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hoi4_harness.adapters.combined import CombinedReader
from hoi4_harness.adapters.logtail import LogTailAdapter

FIXTURE = Path(__file__).parent / "fixtures" / "swe_1938_02_04.hoi4"
STATE = ("[10:00:00][1938.02.{d:02d}.21]: LLMB|v4|state|date=x|tag=SWE|pp=7|stab=50|ws=10"
         "|civ=1|mil=1|doc=1|mp=5\n")


def rig(tmp_path, days=(4,), every_days=7):
    saves = tmp_path / "saves"
    saves.mkdir()
    log = tmp_path / "game.log"
    log.write_text("".join(STATE.format(d=d) for d in days), encoding="utf-8")
    clock = {"t": 0.0}

    def sleep(seconds):
        clock["t"] += seconds

    reader = CombinedReader(LogTailAdapter(log, country="SWE"), saves, sleep=sleep,
                            every_days=every_days, timeout=3.0)

    def write_save():
        shutil.copy(FIXTURE, saves / "SWE_1938_02_04_22.hoi4")
        stat = (saves / "SWE_1938_02_04_22.hoi4").stat()
        import os
        os.utime(saves / "SWE_1938_02_04_22.hoi4", ns=(stat.st_atime_ns, reader._mark_ns + 10**9))

    return reader, log, saves, write_save


def test_before_any_save_the_save_fields_are_unknown(tmp_path):
    reader, *_ = rig(tmp_path, days=(3, 4))
    state = reader.read_state()
    assert state.political_power == 7 and not state.known("production")
    assert not state.known("construction") and state.raw.get("save_date") is None


def test_the_log_wins_and_the_save_fills(tmp_path):
    reader, log, saves, write_save = rig(tmp_path, days=(3, 4))
    reader.mark()
    write_save()
    assert reader.collect() is None
    state = reader.read_state()
    assert state.political_power == 7 and state.stability == pytest.approx(0.5)   # the log's
    assert state.known("production") and state.known("construction")
    assert state.fuel == pytest.approx(55912.75)
    assert state.raw["save_date"] == "1938-02-04"
    assert state.date == "1938-02-04"


def test_the_save_stands_in_for_what_the_log_has_not_said_yet(tmp_path):
    reader, log, saves, write_save = rig(tmp_path, days=(4,))      # one open day: no focus yet
    assert not reader.read_state().known("national_focus")
    reader.mark()
    write_save()
    reader.collect()
    state = reader.read_state()
    assert state.national_focus == "SWE_work_projects_for_the_unemployed"
    assert state.known("national_focus")


def test_merging_never_changes_what_the_log_reader_holds(tmp_path):
    reader, *_ , write_save = rig(tmp_path, days=(3, 4))
    reader.mark()
    write_save()
    reader.collect()
    merged = reader.read_state()
    merged.unknown_fields.append("sentinel")
    merged.production.clear()
    assert "sentinel" not in reader.log.read_state().unknown_fields
    assert reader.read_state().production                      # the cache is intact too


def test_fresh_and_due(tmp_path):
    reader, log, saves, write_save = rig(tmp_path, days=(4,), every_days=7)
    assert reader.due("1938-02-04") and not reader.fresh_for("1938-02-04")
    reader.mark()
    write_save()
    reader.collect()
    assert reader.fresh_for("1938-02-04")
    assert reader.fresh_for("1938-02-03")        # a paused game stands past the daily tick
    assert not reader.fresh_for("1938-02-05")    # the save is older than the log
    assert not reader.due("1938-02-10") and reader.due("1938-02-11")
    reader.every_days = 0
    assert not reader.due("1938-06-01")


def test_a_failed_save_invalidates_and_says_why(tmp_path):
    reader, log, saves, write_save = rig(tmp_path, days=(3, 4))
    reader.mark()
    write_save()
    reader.collect()
    assert reader.read_state().known("production")
    reader.mark()                                     # nothing newer arrives
    error = reader.collect()
    assert error and "no new save appeared" in error and reader.last_error == error
    assert not reader.read_state().known("production")


def test_an_unreadable_save_is_reported_not_raised(tmp_path):
    reader, log, saves, write_save = rig(tmp_path, days=(3, 4))
    reader.mark()
    (saves / "x.hoi4").write_bytes(b"HOI4bin\x00")
    assert "save_as_binary=no" in reader.collect()


def test_maybe_save_waits_for_telemetry_and_swallows_a_failure(tmp_path):
    reader, log, saves, write_save = rig(tmp_path, days=(4,))
    log.write_text("", encoding="utf-8")
    reader.maybe_save()                    # no telemetry yet: nothing to date a save against
    assert reader.saves_taken == 0
    log.write_text(STATE.format(d=4), encoding="utf-8")
    reader.take = lambda: (_ for _ in ()).throw(RuntimeError("no focus"))
    reader.maybe_save()
    assert "no focus" in reader.last_error and reader.saves_taken == 0
