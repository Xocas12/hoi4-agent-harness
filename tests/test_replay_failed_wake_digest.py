"""replay.memory_before writes a digest line for every observe record, including
a wake whose model call failed before any reply. The loop writes none for such a
turn (and resume.py honours that via `replied`), so replay's rebuilt prompt
carries a phantom 'no action' turn."""
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.replay import load, memory_before


class FailFirst(ScriptedClient):
    def __init__(self):
        super().__init__()
        self.n = 0

    def complete(self, system, messages, tools=None, max_tokens=None):
        self.n += 1
        if self.n == 1:
            raise TimeoutError("boom")
        return super().complete(system, messages, tools, max_tokens)


def test_replay_digest_matches_loop(tmp_path):
    config = HarnessConfig(adapter="mock", run_dir=tmp_path, days_per_turn=7)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    path = tmp_path / "transcript.jsonl"
    loop = AgentLoop(env=env, planner=FailFirst(), config=config, transcript_path=path)
    seen = []
    orig = loop.planner.complete

    def spy(system, messages, tools=None, max_tokens=None):
        seen.append(messages[0].text)
        return orig(system, messages, tools, max_tokens)

    loop.planner.complete = spy
    loop.run(6)
    records = load(path)
    observes = [r for r in records if r["kind"] == "observe"]
    assert len(observes) >= 2
    target = observes[-1]["turn"]
    rebuilt = memory_before(records, target).context_block()
    # The live prompt for that turn is the last one the planner received with that turn's brief.
    live = [t for t in seen if t.startswith(f"Turn {target}.")][0]
    assert rebuilt in live, (rebuilt, live)
