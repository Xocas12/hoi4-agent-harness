"""``headless-check``: each claim headless play rests on, tried against the fake game,
and what it says when the game does not behave."""

from __future__ import annotations

from hoi4_harness.adapters import build_adapter
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.headless_check import run_checks


def rig(tmp_path):
    config = HarnessConfig(adapter="headless", fake_game=True, fake_game_dir=tmp_path / "f",
                           run_dir=tmp_path / "run", dry_run=False)
    adapter = build_adapter(config)
    return adapter, adapter.fake_game


def named(results):
    return {r.name: r for r in results}


def test_every_claim_holds_against_a_game_that_does_what_the_documentation_says(tmp_path):
    adapter, fake = rig(tmp_path)
    lines = []
    results = run_checks(adapter, days=7, echo=lines.append)
    assert [r.ok for r in results] == [True] * 9, lines
    assert fake.paused                                  # left paused
    assert all(line.startswith("PASS") for line in lines)


def test_a_game_that_ignores_gamespeed_fails_the_console_clock_claims(tmp_path):
    adapter, fake = rig(tmp_path)
    fake._gamespeed = lambda rest: None
    got = named(run_checks(adapter, echo=lambda line: None))
    assert not got["gamespeed 3 starts the clock"].ok
    assert "did not move" in got["gamespeed 3 starts the clock"].detail
    assert not got["gamespeed 0 stops it"].ok
    assert got["refresh: two fresh state lines while paused"].ok       # a different claim


def test_a_game_that_ignores_pause_in_hours_fails_that_claim_and_is_still_paused_after(tmp_path):
    adapter, fake = rig(tmp_path)
    fake._pause_in_hours = lambda rest: None
    got = named(run_checks(adapter, echo=lambda line: None))
    exact = got["pause_in_hours stops the game by itself"]
    assert not exact.ok and "was still running" in exact.detail
    assert fake.paused


def test_a_mod_without_the_refresh_effect_fails_the_refresh_claim(tmp_path):
    adapter, fake = rig(tmp_path)
    original = fake._scripted
    fake._scripted = lambda name: None if name == "llm_bridge_emit_state" else original(name)
    got = named(run_checks(adapter, echo=lambda line: None))
    refresh = got["refresh: two fresh state lines while paused"]
    assert not refresh.ok and "0 fresh state line(s) (need 2)" in refresh.detail


def test_without_telemetry_it_stops_after_the_first_claim(tmp_path):
    adapter, fake = rig(tmp_path)
    fake.log_path.write_text("", encoding="utf-8")
    lines = []
    results = run_checks(adapter, echo=lines.append)
    assert len(results) == 1 and not results[0].ok
    assert "stopping" in lines[-1]


def test_the_command_refuses_to_send_keys_on_a_dry_run(capsys):
    assert main(["headless-check", "--headless"]) == 2
    assert "needs --live" in capsys.readouterr().err


def test_the_command_runs_against_the_fake_and_reports_a_count(tmp_path, capsys):
    code = main(["headless-check", "--fake-game", "--fake-game-dir", str(tmp_path / "f"),
                 "--run-dir", str(tmp_path / "run")])
    out = capsys.readouterr().out
    assert code == 0 and "9 of 9 passed" in out


def test_the_save_claim_reports_what_it_read_and_why_it_failed(tmp_path):
    adapter, fake = rig(tmp_path)
    got = named(run_checks(adapter, echo=lambda line: None))
    save = got["savegame writes a text save the harness can read"]
    assert save.ok and "SWE_1936_01_01_22.hoi4" in save.detail and "1 production line(s)" in save.detail
    adapter, fake = rig(tmp_path / "b")
    fake.save_mode = "binary"
    got = named(run_checks(adapter, echo=lambda line: None))
    save = got["savegame writes a text save the harness can read"]
    assert not save.ok and "save_as_binary=no" in save.detail


def test_the_war_claim_reads_an_enemy_and_catches_a_bad_line(tmp_path):
    adapter, fake = rig(tmp_path)
    fake.start_war("POL", divisions=33)
    fake.run_days(2)
    got = named(run_checks(adapter, echo=lambda line: None))
    war = got["the war lines are readable"]
    assert war.ok and "POL: 33 divisions, borders us" in war.detail
    adapter, fake = rig(tmp_path / "b")
    fake.start_war("POL", divisions=33)
    fake.run_days(2)
    fake.log_path.write_text(fake.log_path.read_text(encoding="utf-8").replace(
        "divs=33", "divs=[?llmb_war_divs]"), encoding="utf-8")
    got = named(run_checks(adapter, echo=lambda line: None))
    assert not got["the war lines are readable"].ok


def test_at_peace_the_war_claim_says_it_cannot_be_proved(tmp_path):
    adapter, fake = rig(tmp_path)
    got = named(run_checks(adapter, echo=lambda line: None))
    assert got["the war lines are readable"].ok and "at peace" in got["the war lines are readable"].detail
