"""The scripted planner re-sends the same calls every tool round, so each wake burns
max_tool_rounds_per_turn calls on rejected repeats -- the stub behind the README's
calls-per-wake and ceiling figures."""
import json

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env


def test_scripted_planner_does_not_repeat_a_call_it_already_made_this_wake(tmp_path):
    config = HarnessConfig(adapter="mock", run_dir=tmp_path)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    loop = AgentLoop(env=env, planner=ScriptedClient(), config=config,
                     transcript_path=tmp_path / "t.jsonl")
    loop.run(1)
    rounds = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    calls = [c["name"] for r in rounds if r["kind"] == "llm" for c in r["tool_calls"]]
    assert len(calls) == len(set(calls)), calls
