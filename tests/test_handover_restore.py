"""play --resume --handover restores the queue the player was shown (the file)."""

import json

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall


def _env(tmp_path):
    config = HarnessConfig(adapter="mock", handover=True, run_dir=tmp_path)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    return env


def test_queued_actions_survive_a_restart(tmp_path):
    first = _env(tmp_path)
    first.act(ActionCall(name="start_research", arguments={"technology": "construction1"}))
    first.act(ActionCall(name="set_national_focus", arguments={"focus_id": "SWE_x"}))
    assert len(json.loads((tmp_path / "handover-queue.json").read_text())) == 2
    # The process dies; the player runs `play --resume --handover`.
    second = _env(tmp_path)
    assert second.handover.restore() == 2
    assert [p.call.name for p in second.handover.pending] == ["start_research", "set_national_focus"]
    # New entries carry on numbering after the restored ones.
    second.act(ActionCall(name="note", arguments={"text": "x"}))
    second.act(ActionCall(name="start_research", arguments={"technology": "electronics1"}))
    assert [p.position for p in second.handover.pending][-1] == 3


def test_a_fresh_run_starts_with_an_empty_queue(tmp_path):
    first = _env(tmp_path)
    first.act(ActionCall(name="start_research", arguments={"technology": "construction1"}))
    second = _env(tmp_path)
    second.handover.clear()
    assert json.loads((tmp_path / "handover-queue.json").read_text()) == []
