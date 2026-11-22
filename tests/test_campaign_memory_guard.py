"""The campaign's memory guard: HOI4 leaks over a long run (78 GB private after three hours,
overnight 2026-11-22), so past a limit the run saves and stops cleanly instead of crashing."""

from __future__ import annotations

from hoi4_harness.campaign.progress import ProgressTracker
from hoi4_harness.campaign.runner import CampaignLog, CampaignSupervisor
from hoi4_harness.config import HarnessConfig
from hoi4_harness.types import GameState


def _sup(tmp_path, **cfg):
    config = HarnessConfig(country="GER", dry_run=False, **cfg)
    sup = CampaignSupervisor(config, env=None, tracker=ProgressTracker(), log=CampaignLog(None),
                             run_dir=tmp_path, watchdog=None)
    sup.saves = None
    saved = []
    sup._recovery_save = lambda state: saved.append((state.date, sup.last_save))
    return sup, saved


def test_under_the_limit_nothing_happens(tmp_path):
    sup, saved = _sup(tmp_path)
    assert sup._memory_check(GameState(country="GER", date="1940-11-01"), read=lambda _n: 20.0) is None
    assert saved == []


def test_past_the_limit_a_save_now_and_a_resumable_stop(tmp_path):
    sup, saved = _sup(tmp_path, campaign_memory_limit_gb=48)
    sup.last_save = "1940-10-06"       # a save a month ago would normally wait five more
    stop = sup._memory_check(GameState(country="GER", date="1940-11-01"), read=lambda _n: 78.0)
    assert saved == [("1940-11-01", None)]
    assert "78 GB" in stop and "--resume" in stop and "load that save" in stop


def test_warn_mode_and_off(tmp_path):
    sup, saved = _sup(tmp_path, campaign_on_memory="warn")
    assert sup._memory_check(GameState(country="GER", date="1941-01-01"), read=lambda _n: 90.0) is None
    assert saved == []
    off, _ = _sup(tmp_path, campaign_memory_limit_gb=0)
    assert off._memory_check(GameState(country="GER", date="1941-01-01"),
                             read=lambda _n: 1 / 0) is None   # never read when off


def test_an_unreadable_size_is_not_a_stop(tmp_path):
    sup, saved = _sup(tmp_path)
    assert sup._memory_check(GameState(country="GER", date="1941-01-01"), read=lambda _n: None) is None
