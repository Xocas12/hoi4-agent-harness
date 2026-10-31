"""A wake whose first call answered (and acted) but whose second round raised:
the loop returns via _reflex_fallback and never calls memory.record_turn, so the
actions the model took vanish from the digest; resume.py marks that turn
replied and *does* write a digest line. Live memory and the rebuilt memory differ,
and the live one forgets orders that really executed."""
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.agent.resume import rebuild
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env


class ActThenFail(ScriptedClient):
    def __init__(self):
        super().__init__()
        self.n = 0

    def complete(self, system, messages, tools=None, max_tokens=None):
        self.n += 1
        if self.n == 1:
            return LLMResponse(tool_calls=[ToolCall("c1", "set_national_focus",
                                                    {"focus_id": "industrial_effort"})],
                               stop_reason="tool_use", usage=Usage(10, 5))
        if self.n == 2:
            raise TimeoutError("second round timed out")
        return super().complete(system, messages, tools, max_tokens)


def test_actions_of_a_wake_that_failed_mid_way_reach_the_digest(tmp_path):
    config = HarnessConfig(adapter="mock", run_dir=tmp_path)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    path = tmp_path / "transcript.jsonl"
    loop = AgentLoop(env=env, planner=ActThenFail(), config=config, transcript_path=path)
    loop.run(1)
    assert loop.report.actions_ok >= 1       # the focus really was set
    assert any("set_national_focus" in d for d in loop.memory.digest), loop.memory.digest


def test_resume_rebuilds_the_same_digest(tmp_path):
    config = HarnessConfig(adapter="mock", run_dir=tmp_path)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    path = tmp_path / "transcript.jsonl"
    loop = AgentLoop(env=env, planner=ActThenFail(), config=config, transcript_path=path)
    loop.run(1)
    assert rebuild(path).memory.digest == loop.memory.digest
