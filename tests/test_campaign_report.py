"""The campaign report, from the files a run leaves."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from campaign_helpers import LazyClient, build_env, campaign_config, invade
from hoi4_harness.campaign.report import RunData, render_report, sparkline
from hoi4_harness.campaign.runner import run_campaign


def play(tmp_path, script=(), *, nap=60, **overrides):
    config = campaign_config(tmp_path, **overrides)
    env = build_env(config)
    report, summary = run_campaign(config, planner=LazyClient(list(script), nap=nap), env=env)
    return config, env, env.adapter.fake_game, report, summary, None


# --- the report -------------------------------------------------------------------------------


def test_the_report_of_a_finished_campaign_tells_the_story(tmp_path):
    config, env, fake, report, summary, _ = play(
        tmp_path, [invade("POL", text="Poland first: weakest, and on our border.")],
        campaign_until="1937-06-01")
    text = render_report(RunData.load(tmp_path / "run"))
    for needle in ("## Summary", "Reached the end date", "war declared", "capitulation: POL",
                   "set_ai_directive(directive=invade, target=POL)", "Poland first",
                   "invade raised 1936-01-01", "Spend and tokens", "Independent countries"):
        assert needle in text, needle


def test_the_report_of_a_run_that_never_ended_says_it_was_interrupted(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1937-06-01")
    env = build_env(config)
    from hoi4_harness.agent import loop as loop_module

    real = loop_module.AgentLoop._planner_turn

    def crash(self, reason):
        if self.report.turns >= 2:
            raise RuntimeError("killed")
        return real(self, reason)

    loop_module.AgentLoop._planner_turn = crash
    try:
        with pytest.raises(RuntimeError):
            run_campaign(config, planner=LazyClient(nap=30), env=env)
    finally:
        loop_module.AgentLoop._planner_turn = real
    text = render_report(RunData.load(tmp_path / "run"))
    assert "No end record" in text and "interrupted" in text and "crashed: RuntimeError" in text


def test_the_cli_runs_a_campaign_and_writes_its_report(tmp_path, monkeypatch, capsys):
    from hoi4_harness.cli import main

    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(root)
    run = tmp_path / "cli"
    code = main(["play", "--campaign", "--profile", "profiles/campaign-fake.json",
                 "--run-dir", str(run), "--until", "1937-03-01", "--quiet"])
    assert code == 0
    assert "1937-03-01" <= json.loads(capsys.readouterr().out)["end_date"] <= "1937-03-03"
    assert main(["campaign-report", "--run-dir", str(run)]) == 0
    report = (run / "campaign-report.md").read_text(encoding="utf-8")
    assert "# Campaign report" in report and "invade" in report




def test_a_sparkline_runs_from_low_to_high():
    line = sparkline([1, 2, 3, 4, 5, 6, 7, 8])
    assert line[0] == "_" and line[-1] == "#" and len(line) == 8
    assert sparkline([5, 5, 5]) == "___" and sparkline([None, None]) == ""
    assert len(sparkline(list(range(500)), width=40)) == 40
