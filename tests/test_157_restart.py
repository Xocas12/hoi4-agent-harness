"""Issue #157: the harness restarts the game itself when the memory guard fires.

The whole sequence runs on a fake machine: a process list, a window that appears after a
relaunch, a click recorder, a clock that only moves when something sleeps. Nothing real is
killed, launched, focused or clicked."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hoi4_harness.campaign import restart as R
from hoi4_harness.campaign.progress import ProgressTracker
from hoi4_harness.campaign.restart import GameRestarter, RestartFailed, Seams, Timings
from hoi4_harness.campaign.runner import CampaignLog, CampaignSupervisor
from hoi4_harness.config import HarnessConfig
from hoi4_harness.types import GameState

GOOD = b"date=\"1942.6.27\"\n" + b"x" * 400 + b"\nchecksum=\"f7c1a25ee28f971da9eebe0b11d73cf4\""
NOW = 1_000_000.0


class FakeMachine:
    """The game as the restarter sees it."""

    def __init__(self, tmp_path, *, rect=(0, 0, 1920, 1080)):
        self.t = 0.0
        self.pids = [4242]
        self.events: list[str] = []
        self.clicks: list[tuple[tuple[int, int], str]] = []
        self.front = True
        self.window_after: float | None = None
        self.rect = rect
        self.load_at: float | None = None
        self.taken = 0
        self.can_take = True
        self.click_error: Exception | None = None
        self.front_at_click: list[bool] = []
        self.launch_args: list = []
        self.save_dir = tmp_path / "saves"
        self.save_dir.mkdir()
        self.state: dict = {}

    # seams
    def find_pids(self, _name):
        return list(self.pids)

    def kill(self, pid):
        self.events.append(f"kill {pid}")
        self.pids = [p for p in self.pids if p != pid]
        self.window_after = None
        return True

    def launch(self, game_dir, args):
        self.events.append("launch")
        self.launch_args = [game_dir, args]
        self.pids.append(7777)
        self.window_after = self.t + 40
        self.front = False          # the relaunched game stays behind whatever was there
        return 7777

    def window_exists(self):
        return self.window_after is not None and self.t >= self.window_after

    def in_front(self):
        return self.front

    def take_focus(self):
        self.taken += 1
        if self.can_take:
            self.front = True
        return self.can_take

    def click(self, point, label):
        self.front_at_click.append(self.front)
        if self.click_error:
            raise self.click_error
        self.clicks.append((point, label))
        self.events.append(f"click {label}")
        if label == "the Load button":
            self.load_at = self.t          # the game starts writing its log as the save loads

    def client_rect(self):
        return self.rect

    def log_signature(self):
        if self.load_at is None:
            return (1, 1)
        busy = min(self.t, self.load_at + 30)      # it grows for 30 s, then goes quiet
        return (2 + int(busy), 2)

    def sleep(self, seconds):
        self.t += seconds

    def clock(self):
        return self.t

    def seams(self):
        return Seams(find_pids=self.find_pids, kill=self.kill, launch=self.launch,
                     window_exists=self.window_exists, in_front=self.in_front,
                     take_focus=self.take_focus, click=self.click, client_rect=self.client_rect,
                     log_signature=self.log_signature, sleep=self.sleep, clock=self.clock)

    def write_save(self, name="GER_1942_6_27_1.hoi4", body=GOOD, mtime=NOW + 5):
        path = self.save_dir / name
        path.write_bytes(body)
        os.utime(path, (mtime, mtime))
        return path

    def restarter(self, **kw):
        timings = kw.pop("timings", Timings(load_quiet=6.0))
        return GameRestarter(seams=self.seams(), save_dir=self.save_dir, game_dir=Path("C:/hoi4"),
                             timings=timings, log=self.events_log, load_state=lambda: dict(self.state),
                             save_state=lambda **k: self.state.update(k), wall=lambda: NOW, **kw)

    def events_log(self, text):
        self.events.append("log " + text)


def _plain(events):
    return [e for e in events if not e.startswith("log ")]


def test_157_the_full_sequence_in_order(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    save = m.write_save()
    result = m.restarter().restart(NOW)
    assert result.save == save and result.pid == 7777
    assert _plain(m.events) == ["kill 4242", "launch", "click Single Player", "click Load",
                                "click the newest save in the list", "click the Load button",
                                "click START"]
    assert [p for p, _ in m.clicks] == [(960, 226), (960, 438), (200, 466), (316, 1028), (1742, 1028)]
    assert m.launch_args == [Path("C:/hoi4"), ["-gdpr-compliant", "-debug_mode"]]
    assert m.state["game_pid"] == 7777          # the new game is the one the run knows now
    assert m.pids == [7777]


def test_157_menu_points_scale_to_the_window(tmp_path):
    m = FakeMachine(tmp_path, rect=(100, 50, 2560, 1440))
    m.state["game_pid"] = 4242
    m.write_save()
    m.restarter().restart(NOW)
    assert m.clicks[0][0] == (100 + round(960 * 2560 / 1920), 50 + round(226 * 1440 / 1080))
    assert m.clicks[-1][0] == (100 + round(1742 * 2560 / 1920), 50 + round(1028 * 1440 / 1080))
    assert R.scale_point((960, 226), (0, 0, 1920, 1080)) == (960, 226)


def test_157_only_the_pid_the_run_knows_is_killed(tmp_path):
    m = FakeMachine(tmp_path)
    m.pids = [111, 4242]                     # someone else's game too
    m.state["game_pid"] = 4242
    m.write_save()
    m.restarter().restart(NOW)
    assert "kill 4242" in m.events and "kill 111" not in m.events
    assert 111 in m.pids


def test_157_two_games_and_no_recorded_pid_kills_nothing(tmp_path):
    m = FakeMachine(tmp_path)
    m.pids = [111, 4242]
    m.write_save()
    with pytest.raises(RestartFailed, match="none is the pid this run recorded"):
        m.restarter().restart(NOW)
    assert not any(e.startswith("kill") for e in m.events) and m.pids == [111, 4242]


def test_157_a_lone_game_with_no_record_is_the_run_s_game(tmp_path):
    m = FakeMachine(tmp_path)
    m.write_save()
    m.restarter().restart(NOW)
    assert "kill 4242" in m.events


def test_157_remember_pid_records_only_an_unambiguous_one(tmp_path):
    m = FakeMachine(tmp_path)
    r = m.restarter()
    assert r.remember_pid() == 4242 and m.state["game_pid"] == 4242
    m.pids = [4242, 5]
    assert r.remember_pid() == 4242            # still alive: kept
    m.state.clear()
    assert r.remember_pid() is None and "game_pid" not in m.state


def test_157_waits_for_the_save_to_be_complete_before_killing(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    path = m.write_save(body=b"still writing, no checksum")
    real_sleep = m.sleep

    def sleep(seconds):                        # the game finishes the file after a while
        real_sleep(seconds)
        if m.t > 20 and not R.ends_with_checksum(path):
            path.write_bytes(GOOD)
            os.utime(path, (NOW + 5, NOW + 5))

    m.sleep = sleep
    result = m.restarter().restart(NOW)
    assert result.save == path
    assert m.t > 20 and _plain(m.events)[0] == "kill 4242"


def test_157_an_incomplete_save_means_nothing_is_killed(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    m.write_save(body=b"truncated save with no end")
    with pytest.raises(RestartFailed, match="not complete on disk"):
        m.restarter(timings=Timings(save_wait=30)).restart(NOW)
    assert not any(e.startswith(("kill", "launch")) for e in m.events) and m.pids == [4242]


def test_157_an_old_save_is_not_the_recovery_save(tmp_path):
    m = FakeMachine(tmp_path)
    m.write_save(mtime=NOW - 3600)            # complete, but from before the request
    with pytest.raises(RestartFailed, match="not complete on disk"):
        m.restarter(timings=Timings(save_wait=20)).restart(NOW)
    assert m.pids == [4242]


def test_157_save_checksum_rule():
    assert not R.ends_with_checksum(Path("does-not-exist.hoi4"))


def test_157_every_click_has_the_game_in_front_and_focus_comes_from_the_fresh_taker(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    m.write_save()
    m.restarter().restart(NOW)
    assert m.front_at_click == [True] * 5
    assert m.taken >= 1                         # the relaunch left it behind: taken before click 1


def test_157_game_behind_a_person_s_window_stops_the_run_resumably(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    save = m.write_save()
    m.can_take = False                          # the person is at the keyboard: take is withheld
    with pytest.raises(RestartFailed) as caught:
        m.restarter(timings=Timings(focus_wait=60)).restart(NOW)
    text = str(caught.value)
    assert "not in front before the click on Single Player" in text
    assert "nothing was clicked" in text and save.name in text and "--resume" in text
    assert m.clicks == []                       # never clicked behind the person's back
    assert "launch" in m.events                 # the game was relaunched: the message says so


def test_157_focus_comes_back_when_the_person_goes_idle(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    m.write_save()
    calls = {"n": 0}

    def take():
        calls["n"] += 1
        m.front = calls["n"] >= 4                # withheld three times, then idle long enough
        return m.front

    m.take_focus = take
    m.restarter().restart(NOW)
    assert calls["n"] >= 4 and m.front_at_click == [True] * 5


def test_157_the_window_never_appears(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    m.write_save()
    m.launch = lambda *_a: m.pids.append(7777) or 7777
    with pytest.raises(RestartFailed, match="window did not appear"):
        m.restarter(timings=Timings(window_wait=30)).restart(NOW)


def test_157_a_click_a_person_interrupted_stops_with_the_reason(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    m.write_save()
    m.click_error = RuntimeError("the person started using the keyboard or mouse")
    with pytest.raises(RestartFailed, match="was not made: RuntimeError: the person started"):
        m.restarter().restart(NOW)


def test_157_the_load_is_waited_for_by_the_log_not_a_fixed_time(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    m.write_save()
    m.restarter().restart(NOW)
    ev = _plain(m.events)
    assert ev.index("click the Load button") < ev.index("click START")
    assert any("game.log has gone quiet" in e for e in m.events)


def test_157_a_crash_while_loading_is_caught(tmp_path):
    m = FakeMachine(tmp_path)
    m.state["game_pid"] = 4242
    m.write_save()
    real = m.click

    def click(point, label):
        real(point, label)
        if label == "START":
            m.pids = []

    m.click = click
    with pytest.raises(RestartFailed, match="gone after START"):
        m.restarter().restart(NOW)


# --- the supervisor around it ---------------------------------------------------------------


def _sup(tmp_path, **cfg):
    config = HarnessConfig(country="GER", dry_run=False, campaign_on_memory="restart", **cfg)
    sup = CampaignSupervisor(config, env=None, tracker=ProgressTracker(), log=CampaignLog(None),
                             run_dir=tmp_path, watchdog=None)
    sup.saves = None
    sup.wall = lambda: NOW
    sup.calls = []

    def recovery(state):
        sup.calls.append("save")
        sup.saves_taken += 1
    sup._recovery_save = recovery
    return sup


class FakeRestarter:
    def __init__(self, fail: str | None = None):
        self.fail, self.seen, self.pid_notes = fail, [], 0

    def remember_pid(self):
        self.pid_notes += 1

    def restart(self, asked_at):
        self.seen.append(asked_at)
        if self.fail:
            raise RestartFailed(self.fail)
        return SimpleNamespace(save=Path("GER_1942_6_27_1.hoi4"), pid=7777)


STATE = GameState(country="GER", date="1942-06-27")


def test_157_past_the_limit_the_supervisor_restarts_and_carries_on(tmp_path):
    sup = _sup(tmp_path)
    sup.restarter = FakeRestarter()
    sup.resume_game = lambda: sup.calls.append("resume")
    assert sup._memory_check(STATE, read=lambda _n: 50.0) is None
    assert sup.calls == ["save", "resume"] and sup.restarter.seen == [NOW] and sup.restarts == 1


def test_157_under_the_limit_it_only_notes_the_pid(tmp_path):
    sup = _sup(tmp_path)
    sup.restarter = FakeRestarter()
    assert sup._memory_check(STATE, read=lambda _n: 10.0) is None
    assert sup.calls == [] and sup.restarter.pid_notes == 1


def test_157_a_failed_restart_stops_with_its_reason(tmp_path):
    sup = _sup(tmp_path)
    sup.restarter = FakeRestarter(fail="the game window is not in front before the click on Load")
    sup.resume_game = lambda: pytest.fail("must not resume after a failed restart")
    stop = sup._memory_check(STATE, read=lambda _n: 50.0)
    assert "not in front" in stop


def test_157_no_live_game_falls_back_to_the_resumable_stop(tmp_path):
    sup = _sup(tmp_path)
    stop = sup._memory_check(STATE, read=lambda _n: 50.0)
    assert "--resume" in stop and sup.calls == ["save"]


def test_157_restart_cap_then_stop(tmp_path):
    sup = _sup(tmp_path, campaign_restart_max=1)
    sup.restarter = FakeRestarter()
    sup.resume_game = lambda: None
    assert sup._memory_check(STATE, read=lambda _n: 50.0) is None
    stop = sup._memory_check(STATE, read=lambda _n: 50.0)
    assert stop and "--resume" in stop and len(sup.restarter.seen) == 1


def test_157_no_save_asked_means_no_kill(tmp_path):
    sup = _sup(tmp_path)
    sup.restarter = FakeRestarter()
    sup._recovery_save = lambda state: None        # the console was unavailable
    stop = sup._memory_check(STATE, read=lambda _n: 50.0)
    assert "could not be asked for" in stop and sup.restarter.seen == []


def test_157_a_run_that_cannot_pick_the_game_up_stops(tmp_path):
    from hoi4_harness.campaign.runner import CampaignStartError

    sup = _sup(tmp_path)
    sup.restarter = FakeRestarter()

    def boom():
        raise CampaignStartError("no telemetry")
    sup.resume_game = boom
    stop = sup._memory_check(STATE, read=lambda _n: 50.0)
    assert "no telemetry" in stop and "--resume" in stop


def test_157_the_default_is_still_stop(tmp_path):
    assert HarnessConfig().campaign_on_memory == "stop"


# --- the real-machine helpers, with the OS call injected ---------------------------------------


def test_157_wmi_launch_goes_through_win32_process_create(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    seen = {}

    def run(cmd, **_kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="5150\n", stderr="")

    pid = R.wmi_launch(Path("C:/Games/it's HOI4"), ["-gdpr-compliant", "-debug_mode"], run=run)
    script = seen["cmd"][-1]
    assert pid == 5150
    assert "Win32_Process" in script and "-debug_mode" in script and "Create" in script
    assert "it''s HOI4" in script                      # quoted for PowerShell


def test_157_wmi_launch_failure_is_none(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    run = lambda cmd, **_k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")  # noqa: E731
    assert R.wmi_launch(Path("C:/x"), [], run=run) is None


def test_157_kill_is_one_pid_only(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    seen = []
    run = lambda cmd, **_k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0)  # noqa: E731
    assert R.kill_pid(4242, run=run) is True
    assert seen == [["taskkill", "/PID", "4242", "/F"]]


def test_157_take_focus_runs_in_a_fresh_process():
    from hoi4_harness.adapters import window

    seen = {}

    def run(cmd, **_kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    assert window.take_focus_fresh("Hearts of Iron IV", run=run) is True
    assert seen["cmd"][0] == sys.executable and seen["cmd"][1] == "-c"
    assert seen["cmd"][-1] == "Hearts of Iron IV" and "take_focus" in seen["cmd"][2]
    failing = lambda cmd, **_k: subprocess.CompletedProcess(cmd, 1)  # noqa: E731
    assert window.take_focus_fresh("Hearts of Iron IV", run=failing) is False
    assert window.take_focus_fresh("  ", run=run) is False


def test_157_process_pids_parse(monkeypatch):
    from hoi4_harness.campaign import watch

    monkeypatch.setattr(sys, "platform", "win32")
    out = '"hoi4.exe","4242","Console","1","60,000 K"\n"hoi4.exe","99","Console","1","1 K"\n'
    monkeypatch.setattr(watch.subprocess, "run",
                        lambda cmd, **_k: subprocess.CompletedProcess(cmd, 0, stdout=out, stderr=""))
    assert watch.process_pids("hoi4.exe") == [4242, 99]


def test_157_restart_is_a_choice_and_preflight_checks_game_dir(tmp_path):
    from hoi4_harness.campaign.preflight import FAIL, check_focus_policy
    from hoi4_harness.settings_model import _CHOICE_TABLE

    assert "restart" in _CHOICE_TABLE["campaign_on_memory"]
    config = HarnessConfig(campaign_on_memory="restart", game_dir=tmp_path)
    assert any(c.status == FAIL and c.name == "restart" for c in check_focus_policy(config))
    (tmp_path / "hoi4.exe").write_bytes(b"")
    assert not any(c.status == FAIL and c.name == "restart" for c in check_focus_policy(config))
