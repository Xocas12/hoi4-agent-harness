"""#102: a campaign records the Historical AI Focuses rule and says so when it is off.

Acceptance: a save with ``historical=0`` makes the campaign log a warning naming the rule at
the start of the run, and ``historical=1`` logs it as on; the setting appears in the
campaign report's summary.
"""

from __future__ import annotations

from pathlib import Path

from campaign_helpers import LazyClient, build_env, campaign_config
from hoi4_harness.adapters.savegame import historical_ai, read_historical_ai, read_save
from hoi4_harness.campaign.report import RunData, render_report
from hoi4_harness.campaign.runner import load_state, run_campaign

FIXTURE = Path(__file__).parent / "fixtures" / "ger_1936_01_01.hoi4"


def play(tmp_path, historical, **overrides):
    config = campaign_config(tmp_path, campaign_until="1936-05-01", campaign_save_months=1,
                             **overrides)
    env = build_env(config)
    env.adapter.fake_game.historical = historical
    run_campaign(config, planner=LazyClient(nap=20), env=env)
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    return log, RunData.load(tmp_path / "run")


def test_102_the_reader_returns_historical_off_from_a_real_save_header():
    assert read_historical_ai(FIXTURE) is False
    assert read_save(FIXTURE)["historical"] is False


def test_102_the_reader_returns_on_and_ignores_the_character_manager_block():
    on = "gameplaysettings={\n\tironman=0\n\thistorical=1\n}\ncharacter_manager={\n\thistorical={\n}\n}\n"
    off = "gameplaysettings={\n\thistorical=0\n}\ncharacter_manager={\n\thistorical={\n}\n}\n"
    assert historical_ai(on) is True
    assert historical_ai(off) is False
    assert historical_ai("player=\"GER\"\n") is None


def test_102_a_save_with_historical_0_logs_the_rule_off_at_the_start_of_the_run(tmp_path):
    log, _ = play(tmp_path, 0)
    assert "historical AI focuses: off" in log
    assert log.index("historical AI focuses: off") < log.index("STOP:")
    assert load_state(tmp_path / "run")["historical_ai"] is False


def test_102_a_save_with_historical_1_logs_the_rule_as_on_without_a_warning(tmp_path):
    log, _ = play(tmp_path, 1, campaign_historical_ai="on")
    assert "historical AI focuses: on" in log
    assert "WARNING: historical AI focuses" not in log
    assert load_state(tmp_path / "run")["historical_ai"] is True


def test_102_off_with_a_profile_that_wants_it_on_warns_and_the_run_goes_on(tmp_path):
    log, _ = play(tmp_path, 0, campaign_historical_ai="on")
    warning = [ln for ln in log.splitlines() if "WARNING: historical AI focuses" in ln]
    assert len(warning) == 1 and "campaign_historical_ai=on" in warning[0]
    assert "STOP: reached the end date" in log


def test_102_the_default_any_logs_the_rule_but_does_not_warn(tmp_path):
    log, _ = play(tmp_path, 0)
    assert "historical AI focuses: off" in log
    assert "WARNING: historical AI focuses" not in log


def test_102_the_rule_is_logged_once_per_run_not_once_per_save(tmp_path):
    log, _ = play(tmp_path, 0, campaign_historical_ai="on")
    assert log.count("historical AI focuses: off") == 1


def test_102_the_report_summary_shows_the_setting(tmp_path):
    _, data = play(tmp_path, 0)
    assert "| Historical AI focuses | off |" in render_report(data)


def test_102_the_report_of_a_run_with_the_rule_on_says_on(tmp_path):
    _, data = play(tmp_path, 1)
    assert "| Historical AI focuses | on |" in render_report(data)


def test_102_a_run_that_took_no_save_says_the_rule_was_not_recorded(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1936-03-01", campaign_save_months=0)
    env = build_env(config)
    run_campaign(config, planner=LazyClient(nap=20), env=env)
    text = render_report(RunData.load(tmp_path / "run"))
    assert "| Historical AI focuses | not recorded" in text
