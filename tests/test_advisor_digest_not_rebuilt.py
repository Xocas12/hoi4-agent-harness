"""Advisor mode: the loop's digest says 'X (recommended)' per call, but no
`actions` record is written, so resume.py (and replay.memory_before) rebuild
every advisor turn as 'no action'. A resumed advisor session without a
memory.json, or any replay of one, shows the model a history of doing nothing."""
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.agent.resume import rebuild
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env


def test_advisor_digest_survives_rebuild(tmp_path, capsys):
    config = HarnessConfig(adapter="mock", run_dir=tmp_path, advisor=True)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    path = tmp_path / "transcript.jsonl"
    loop = AgentLoop(env=env, planner=ScriptedClient(), config=config, transcript_path=path)
    loop.run(3)
    assert any("(recommended)" in d for d in loop.memory.digest)
    assert rebuild(path).memory.digest == loop.memory.digest
