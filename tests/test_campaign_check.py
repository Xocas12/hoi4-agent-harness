"""campaign-check: the unattended-night preflight, against temp directories and fakes."""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

from hoi4_harness.bridge_check import MOD_DIR
from hoi4_harness.campaign import preflight
from hoi4_harness.campaign.preflight import FAIL, INFO, PASS, WARN, parse_ac_seconds, run_checks
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig

SECRET = "sk-ant-this-must-never-be-printed"
POWERCFG = """  Power Setting GUID: 29f6c1db  (Suspender apos)
    Current AC Power Setting Index: 0x{ac:08x}
    Current DC Power Setting Index: 0x{dc:08x}
"""


@pytest.fixture
def home(tmp_path):
    """A user directory that passes everything."""
    user = tmp_path / "Hearts of Iron IV"
    (user / "mod").mkdir(parents=True)
    shutil.copytree(MOD_DIR, user / "mod" / "llm_bridge")
    (user / "mod" / "llm_bridge" / "descriptor.mod").unlink(missing_ok=True)
    (user / "mod" / "llm_bridge.mod").write_text('name="LLM Bridge"\npath="mod/llm_bridge"\n')
    (user / "dlc_load.json").write_text(json.dumps({"enabled_mods": ["mod/llm_bridge.mod"]}))
    (user / "settings.txt").write_text("a=1\nsave_as_binary=no\nb=2\n")
    (user / "save games").mkdir()
    (user / "logs").mkdir()
    (user / "logs" / "game.log").write_text("x\n")
    return user


def config_for(home, **kw):
    config = HarnessConfig(log_path=home / "logs" / "game.log", save_dir=home / "save games",
                           game_dir=home, campaign=True, campaign_until="1948-12-31",
                           start_date="1936-01-01", **kw)
    config.planner.provider, config.planner.model = "anthropic", "claude-opus-5-5"
    config.budget.usd_per_m_input, config.budget.usd_per_m_output = 4.0, 20.0
    config.budget.usd_per_m_cached_input, config.budget.usd_per_m_cache_write = 0.2, 5.0
    config.budget.reserve_usd = 0.75
    return config


def run(home, config=None, **kw):
    kw.setdefault("env", {"ANTHROPIC_API_KEY": SECRET})
    kw.setdefault("platform", "win32")
    kw.setdefault("query", lambda sub, setting: POWERCFG.format(ac=0, dc=0))
    kw.setdefault("disk_usage", lambda path: SimpleNamespace(free=50 * 1024 ** 3))
    kw.setdefault("max_usd", 40.0)
    return run_checks(config or config_for(home), user_dir=home, **kw)


def by_name(checks, name):
    return next(c for c in checks if c.name == name)


def test_a_good_setup_has_no_fail(home):
    checks = run(home)
    assert not [c for c in checks if c.status == FAIL], [c.line() for c in checks]
    assert by_name(checks, "mod matches repo").status == PASS
    assert by_name(checks, "mod schema").status == PASS


def test_a_changed_or_missing_mod_file_fails_and_names_the_file(home):
    target = home / "mod" / "llm_bridge" / "common" / "scripted_effects" / "llm_bridge_telemetry.txt"
    target.write_bytes(target.read_bytes() + b"\n# edited\n")
    other = next((home / "mod" / "llm_bridge" / "common" / "on_actions").glob("*.txt"))
    other.unlink()
    check = by_name(run(home), "mod matches repo")
    assert check.status == FAIL
    assert "llm_bridge_telemetry.txt" in check.detail and other.name in check.detail


def test_line_endings_alone_are_a_warning_not_a_failure(home):
    target = next((home / "mod" / "llm_bridge" / "common" / "on_actions").glob("*.txt"))
    original = (MOD_DIR / "common" / "on_actions" / target.name).read_bytes()
    crlf, lf = bytes([13, 10]), bytes([10])
    flipped = original.replace(crlf, lf)
    if flipped == original:
        flipped = original.replace(lf, crlf)
    target.write_bytes(flipped)
    check = by_name(run(home), "mod matches repo")
    assert check.status == WARN and target.name in check.detail


def test_an_old_schema_fails(home):
    target = home / "mod" / "llm_bridge" / "common" / "scripted_effects" / "llm_bridge_telemetry.txt"
    target.write_text(target.read_text(encoding="utf-8").replace(
        "LLMB_SCHEMA_VERSION = 5", "LLMB_SCHEMA_VERSION = 4"), encoding="utf-8")
    assert by_name(run(home), "mod schema").status == FAIL


def test_the_mod_must_be_enabled_and_saves_must_be_text(home):
    (home / "dlc_load.json").write_text(json.dumps({"enabled_mods": ["mod/other.mod"]}))
    (home / "settings.txt").write_text("save_as_binary=yes\n")
    checks = run(home)
    assert by_name(checks, "dlc_load.json").status == FAIL
    assert by_name(checks, "save_as_binary").status == FAIL


def test_a_missing_log_or_save_dir_fails(home):
    (home / "logs" / "game.log").unlink()
    shutil.rmtree(home / "save games")
    checks = run(home)
    assert by_name(checks, "HOI4_LOG_PATH").status == FAIL
    assert by_name(checks, "HOI4_SAVE_DIR").status == FAIL


def test_not_enough_disk_fails_and_pruning_is_what_makes_it_small(home):
    tight = run(home, disk_usage=lambda path: SimpleNamespace(free=int(0.5 * 1024 ** 3)))
    assert by_name(tight, "disk space").status == FAIL
    ok = by_name(run(home), "disk space")
    assert ok.status == PASS and "pruned" in ok.detail


def test_the_key_is_reported_present_or_missing_and_never_printed(home):
    present = by_name(run(home), "API key")
    assert present.status == PASS and SECRET not in present.line()
    assert "ANTHROPIC_API_KEY is present" in present.line()
    missing = by_name(run(home, env={}), "API key")
    assert missing.status == FAIL and "missing" in missing.detail


def test_the_budget_cap_is_checked_against_the_profiles_prices(home):
    assert by_name(run(home, max_usd=0.5), "budget").status == FAIL      # under the reserve
    assert by_name(run(home, max_usd=3.0), "budget").status == WARN      # under the expected spend
    assert by_name(run(home, max_usd=40.0), "budget").status == PASS
    config = config_for(home)
    config.budget.usd_per_m_input = config.budget.usd_per_m_output = None
    assert by_name(run(home, config), "budget").status == FAIL


def test_power_timeouts_are_parsed_by_ac_value_and_covered_by_keep_awake(home):
    assert parse_ac_seconds(POWERCFG.format(ac=1800, dc=300)) == 1800
    assert parse_ac_seconds("nothing here") is None
    checks = run(home, query=lambda sub, setting: POWERCFG.format(ac=900, dc=60))
    sleep = by_name(checks, "power sleep after")
    assert sleep.status == PASS and "15 min on AC" in sleep.detail and "awake" in sleep.detail
    assert by_name(run(home), "power sleep after").detail == "never (on AC)"
    unreadable = run(home, query=lambda sub, setting: None)
    assert by_name(unreadable, "power sleep after").status == WARN


def test_a_lock_timeout_the_plan_does_not_expose_is_not_a_warning(home):
    def query(sub, setting):
        return "" if setting == "CONSOLELOCK" else POWERCFG.format(ac=0, dc=0)

    assert by_name(run(home, query=query), "power lock after").status == INFO


def test_off_windows_the_power_plan_is_informational(home):
    assert by_name(run(home, platform="linux"), "power plan").status == INFO


def test_taking_the_focus_is_a_warning_because_it_steals_the_keyboard(home):
    assert by_name(run(home), "focus").status == PASS
    config = config_for(home, campaign_take_focus=True)
    assert by_name(run(home, config), "focus").status == WARN


def test_the_command_exits_nonzero_on_fail_and_prints_no_key(home, tmp_path, capsys, monkeypatch):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"campaign": True, "campaign_until": "1948-12-31",
                                   "planner": {"provider": "anthropic", "model": "m"},
                                   "budget": {"usd_per_m_input": 4.0, "usd_per_m_output": 20.0}}))
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("HOI4_LOG_PATH", str(home / "logs" / "game.log"))
    monkeypatch.setenv("HOI4_SAVE_DIR", str(home / "save games"))
    (home / "settings.txt").write_text("save_as_binary=yes\n")
    code = main(["campaign-check", "--profile", str(profile), "--user-dir", str(home),
                 "--max-usd", "40"])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL  save_as_binary" in out and "PASS  API key" in out
    assert SECRET not in out
    (home / "settings.txt").write_text("save_as_binary=no\n")
    assert preflight.FAIL not in {c.status for c in run(home)}
