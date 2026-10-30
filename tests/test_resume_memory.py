"""Resume replaces the exact memory checkpoint with a digest rebuilt wrongly."""
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.agent.memory import Memory
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env


def _loop(tmp_path, resume=False, memory=None):
    config = HarnessConfig(adapter="mock", run_dir=tmp_path)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    return AgentLoop(env=env, planner=ScriptedClient(), config=config, memory=memory,
                     transcript_path=tmp_path / "transcript.jsonl", resume=resume)


def test_resume_keeps_the_digest_the_model_was_seeing(tmp_path):
    first = _loop(tmp_path)
    first.run(12)
    live = list(first.memory.digest)
    checkpoint = Memory.load(tmp_path / "memory.json")
    assert checkpoint.digest == live
    # What cmd_play does: load the checkpoint, then resume.
    second = _loop(tmp_path, resume=True, memory=checkpoint)
    assert second.memory.digest == live


def test_without_a_checkpoint_the_transcript_rebuilds_the_same_digest(tmp_path):
    first = _loop(tmp_path)
    first.run(12)
    live = list(first.memory.digest)
    (tmp_path / "memory.json").unlink()
    second = _loop(tmp_path, resume=True)
    assert second.memory.digest == live
