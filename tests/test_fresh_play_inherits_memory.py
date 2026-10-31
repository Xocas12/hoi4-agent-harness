"""cmd_play loads run_dir/memory.json even without --resume. The transcript is
truncated (a fresh run), but the previous run's journal and digest are carried
into the new run's first prompt -- unlike the handover queue, which a fresh run
clears ('A fresh run starts with it empty')."""
import json

from hoi4_harness import cli
from hoi4_harness.agent.llm.scripted import ScriptedClient


def test_fresh_run_starts_with_empty_memory(tmp_path, monkeypatch):
    (tmp_path / "memory.json").write_text(json.dumps({
        "journal": [{"date": "1939-09-01", "turn": 99, "text": "OLD RUN: invade POL"}],
        "digest": ["1939-09-01: diplomacy"],
    }))
    seen = []
    orig = ScriptedClient.complete

    def spy(self, system, messages, tools=None, max_tokens=None):
        seen.append(messages[0].text)
        return orig(self, system, messages, tools, max_tokens)

    monkeypatch.setattr(ScriptedClient, "complete", spy)
    for var in ("HOI4_LLM_PROVIDER", "HOI4_ADAPTER"):
        monkeypatch.delenv(var, raising=False)
    rc = cli.main(["play", "--adapter", "mock", "--turns", "1", "--run-dir", str(tmp_path), "--quiet"])
    assert rc == 0
    assert seen, "planner never called"
    assert "OLD RUN" not in seen[0], seen[0]
