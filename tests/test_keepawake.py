"""Keeping the PC awake for a live run: held on entry, always released, never real here."""

from __future__ import annotations

import sys
from contextlib import nullcontext

import pytest

from campaign_helpers import LazyClient, build_env, campaign_config
from hoi4_harness.campaign import runner
from hoi4_harness.config import HarnessConfig
from hoi4_harness.keepawake import (
    ES_CONTINUOUS,
    ES_DISPLAY_REQUIRED,
    ES_SYSTEM_REQUIRED,
    KeepAwake,
    keep_awake_for,
)


class Setter:
    def __init__(self, result=1, fail_on=None):
        self.calls, self.result, self.fail_on = [], result, fail_on

    def __call__(self, flags):
        self.calls.append(flags)
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise OSError("boom")
        return self.result


def test_it_holds_system_and_display_and_releases_on_exit():
    setter, said = Setter(), []
    with KeepAwake(setter, said.append) as awake:
        assert awake.held
        assert setter.calls == [ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED]
    assert setter.calls[-1] == ES_CONTINUOUS            # continuous with no required flags: let go
    assert any("held" in line for line in said) and any("released" in line for line in said)


def test_it_releases_when_the_run_raises():
    setter = Setter()
    with pytest.raises(RuntimeError), KeepAwake(setter):
        raise RuntimeError("the run crashed")
    assert setter.calls[-1] == ES_CONTINUOUS


def test_a_refused_or_failing_request_is_said_and_does_not_end_the_run():
    said = []
    with KeepAwake(Setter(result=0), said.append) as awake:
        assert not awake.held
    assert any("refused" in line for line in said)
    said.clear()
    with KeepAwake(Setter(fail_on=1), said.append) as awake:
        assert not awake.held
    assert any("could not be held" in line for line in said)


def test_a_failing_release_is_said_not_raised():
    said = []
    with KeepAwake(Setter(fail_on=2), said.append):
        pass
    assert any("release failed" in line for line in said)


def test_it_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with KeepAwake() as awake:
        assert awake.setter is None and not awake.held


def test_a_dry_run_or_the_fake_game_never_holds_the_pc():
    assert isinstance(keep_awake_for(HarnessConfig(dry_run=True)), type(nullcontext()))
    assert isinstance(keep_awake_for(HarnessConfig(dry_run=False, fake_game=True)), type(nullcontext()))
    live = keep_awake_for(HarnessConfig(dry_run=False), setter=Setter())
    assert isinstance(live, KeepAwake)


def test_a_campaign_holds_the_pc_for_its_whole_run_and_lets_go_after_a_crash(tmp_path, monkeypatch):
    events = []

    class Recorder:
        def __enter__(self):
            events.append("hold")

        def __exit__(self, *exc):
            events.append("release")

    monkeypatch.setattr(runner, "keep_awake_for", lambda config, log=None: Recorder())
    config = campaign_config(tmp_path, campaign_until="1936-02-01")
    env = build_env(config)
    runner.run_campaign(config, planner=LazyClient(nap=30), env=env)
    assert events == ["hold", "release"]

    events.clear()
    config = campaign_config(tmp_path / "again", campaign_until="1936-02-01")
    env = build_env(config)

    def crash(self, *args, **kwargs):
        raise ValueError("planner exploded")

    monkeypatch.setattr(runner.AgentLoop, "run", crash)
    with pytest.raises(ValueError):
        runner.run_campaign(config, planner=LazyClient(nap=30), env=env)
    assert events == ["hold", "release"]
