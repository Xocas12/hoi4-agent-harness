"""What an unattended night leaves behind when the game goes away, and the focus policy."""

from __future__ import annotations

import json

from campaign_helpers import LazyClient, build_env, campaign_config
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.campaign import runner
from hoi4_harness.campaign.report import write_report
from hoi4_harness.campaign.runner import run_campaign
from hoi4_harness.campaign.watch import PatientFocus
from hoi4_harness.config import HarnessConfig


def kill_the_game_at(env, day):
    fake = env.adapter.fake_game
    tick = fake.tick_day

    def tick_then_die():
        tick()
        if fake.date == day:
            fake.kill()

    fake.tick_day = tick_then_die


def run_that_loses_the_game(tmp_path, monkeypatch):
    events = []

    class Recorder:
        def __enter__(self):
            events.append("hold")

        def __exit__(self, *exc):
            events.append("release")

    monkeypatch.setattr(runner, "keep_awake_for", lambda config, log=None: Recorder())
    config = campaign_config(tmp_path, campaign_until="1940-01-01", campaign_save_months=1)
    env = build_env(config)
    kill_the_game_at(env, "1936-04-10")
    _, summary = run_campaign(config, planner=LazyClient(nap=20), env=env,
                              profile="profiles/campaign-ger-world.json")
    return env, summary, events


def test_a_lost_game_stops_with_a_reason_a_resume_save_and_the_awake_hold_released(tmp_path, monkeypatch):
    env, summary, events = run_that_loses_the_game(tmp_path, monkeypatch)
    assert "process is gone" in summary["stopped_reason"]
    assert events == ["hold", "release"]
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "STOP: the game process is gone" in log
    resume = next(line for line in log.splitlines() if "RESUME:" in line)
    newest = sorted(p.name for p in env.adapter.fake_game.save_dir.glob("*.hoi4"))[-1]
    assert newest in resume
    assert "play --campaign --live --resume --profile profiles/campaign-ger-world.json" in resume
    assert f"--run-dir {tmp_path / 'run'}" in resume
    transcript = [json.loads(x) for x in
                  (tmp_path / "run" / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(r["kind"] == "game_lost" and "process is gone" in r["reason"] for r in transcript)


def test_the_report_says_where_it_stopped_and_what_to_resume_from(tmp_path, monkeypatch):
    env, _, _ = run_that_loses_the_game(tmp_path, monkeypatch)
    text, _ = write_report(tmp_path / "run")
    section = text.split("## Where to resume")[1].split("\n## ")[0]
    assert "stopped at" in section and "process is gone" in section
    newest = sorted(p.name for p in env.adapter.fake_game.save_dir.glob("*.hoi4"))[-1]
    assert newest in section
    assert "play --campaign --live --resume --profile profiles/campaign-ger-world.json" in section


def test_a_run_that_reached_the_end_has_nothing_to_resume(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1936-03-01")
    env = build_env(config)
    _, summary = run_campaign(config, planner=LazyClient(nap=30), env=env)
    assert summary["reached_end_date"]
    text, _ = write_report(tmp_path / "run")
    assert "Where to resume" not in text
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "RESUME:" not in log


def test_a_silent_log_leaves_the_same_resume_instructions(tmp_path):
    config = campaign_config(tmp_path, campaign_until="1940-01-01", campaign_silence_minutes=10)
    env = build_env(config)
    fake = env.adapter.fake_game
    tick = fake.tick_day

    def tick_then_lose_the_flag():
        tick()
        if fake.date == "1936-03-05":
            fake.flags.discard("llmb_harness")

    fake.tick_day = tick_then_lose_the_flag
    run_campaign(config, planner=LazyClient(nap=30), env=env)
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "STOP: game.log has not grown" in log and "RESUME:" in log


# --- the focus policy ---------------------------------------------------------------------------


class Window:
    """A game window that is elsewhere until ``come_back_after`` checks have gone by."""

    def __init__(self, come_back_after):
        self.checks, self.come_back_after, self.taken = 0, come_back_after, 0

    def check(self, title):
        self.checks += 1
        if self.checks > self.come_back_after:
            return FocusCheck(True, title)
        return FocusCheck(False, "Browser", reason="focused window is 'Browser'")

    def take(self, title):
        self.taken += 1
        self.come_back_after = 0
        return True


def test_by_default_the_harness_waits_and_never_takes_the_focus():
    assert HarnessConfig().campaign_take_focus is False
    window, slept, said = Window(come_back_after=3), [], []
    patient = PatientFocus(window.check, sleep=slept.append, retry_seconds=60, log=said.append)
    assert patient("Hearts of Iron IV").focused
    assert window.taken == 0 and slept == [60, 60, 60]
    assert any("waiting for the game window" in line for line in said)


def test_with_the_opt_in_it_asks_for_the_window_and_logs_that_it_did():
    window, slept, said = Window(come_back_after=50), [], []
    patient = PatientFocus(window.check, sleep=slept.append, log=said.append, take=window.take)
    assert patient("Hearts of Iron IV").focused
    assert window.taken == 1 and slept == []
    assert any("campaign_take_focus is on" in line for line in said)


def test_the_wait_is_bounded_and_ends_in_a_stop(tmp_path):
    import pytest

    from hoi4_harness.adapters.base import GameLost

    window = Window(come_back_after=10 ** 9)
    patient = PatientFocus(window.check, sleep=lambda s: None, retry_seconds=60, max_wait_seconds=300)
    with pytest.raises(GameLost, match="has not come back to the front"):
        patient("Hearts of Iron IV")
    assert window.taken == 0
