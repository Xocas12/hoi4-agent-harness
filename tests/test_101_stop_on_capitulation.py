"""#101: a campaign stops when the PLAYER's own country capitulates (fake game only)."""

from __future__ import annotations

import json

from campaign_helpers import LazyClient, build_env, campaign_config
from hoi4_harness.campaign.report import write_report
from hoi4_harness.campaign.runner import run_campaign

DAY = "1936-04-10"


def capitulate_at(env, day, tag=None, to="SOV"):
    """Make the fake game's player (or ``tag``, an enemy) capitulate on ``day``."""
    fake = env.adapter.fake_game
    tick = fake.tick_day

    def tick_then_capitulate():
        tick()
        if fake.date == day:
            if tag is None:
                fake.player_capitulates(to=to)
            else:
                fake.capitulation(tag)

    fake.tick_day = tick_then_capitulate


def play(tmp_path, tag=None, **options):
    config = campaign_config(tmp_path, campaign_until="1936-09-01", **options)
    env = build_env(config)
    capitulate_at(env, DAY, tag)
    _, summary = run_campaign(config, planner=LazyClient(nap=20), env=env)
    return summary


def test_101_the_run_stops_on_the_next_turn_naming_the_capitulation(tmp_path):
    summary = play(tmp_path)
    assert summary["stopped_reason"].startswith("GER capitulated (")
    assert "1936-04" in summary["stopped_reason"]
    assert not summary["reached_end_date"]
    assert summary["end_date"] < "1936-09-01"
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "STOP: GER capitulated" in log


def test_101_the_end_record_is_written(tmp_path):
    play(tmp_path)
    records = [json.loads(x) for x in
               (tmp_path / "run" / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    end = [r for r in records if r["kind"] == "run_end"]
    assert end and end[-1]["stopped_reason"].startswith("GER capitulated")


def test_101_the_report_says_how_the_run_ended(tmp_path):
    play(tmp_path)
    text, _ = write_report(tmp_path / "run")
    section = text.split("## How the run ended")[1].split("\n## ")[0]
    assert "GER capitulated" in section


def test_101_with_the_switch_off_the_same_run_plays_on(tmp_path):
    summary = play(tmp_path, campaign_stop_on_capitulation=False)
    assert summary["stopped_reason"] is None
    assert summary["reached_end_date"]
    assert summary["end_date"] >= "1936-09-01"


def test_101_an_enemy_capitulating_does_not_stop_the_run(tmp_path):
    summary = play(tmp_path, tag="POL")
    assert summary["stopped_reason"] is None
    assert summary["reached_end_date"]
