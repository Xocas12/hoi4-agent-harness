"""A failed model call plays the turn on reflexes: counted as one, and acting on nothing in advisor mode."""
import json

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env


class Flaky(ScriptedClient):
    def complete(self, *a, **k):
        raise TimeoutError("read timed out")


def test_advisor_fallback_attempts_no_actions(tmp_path):
    config = HarnessConfig(adapter="mock", advisor=True, run_dir=tmp_path)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    loop = AgentLoop(env=env, planner=Flaky(), config=config,
                     transcript_path=tmp_path / "t.jsonl")
    report = loop.run(3)
    kinds = [json.loads(line)["kind"] for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    # A reflex turn in advisor mode attempts nothing (_reflex_turn); the fallback should match.
    assert "reflex" not in kinds
    assert report.actions_failed == 0


def _loop(tmp_path, resume=False):
    config = HarnessConfig(adapter="mock", run_dir=tmp_path, max_consecutive_llm_errors=50)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    return AgentLoop(env=env, planner=Flaky(), config=config,
                     transcript_path=tmp_path / "transcript.jsonl", resume=resume)


def test_resume_restores_the_same_reflex_turn_count(tmp_path):
    first = _loop(tmp_path)
    report = first.run(4)
    assert report.llm_errors >= 1
    second = _loop(tmp_path, resume=True)
    assert second.report.turns == report.turns
    assert second.report.reflex_turns == report.reflex_turns
