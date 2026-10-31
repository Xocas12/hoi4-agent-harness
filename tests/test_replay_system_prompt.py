"""replay rebuilds the system prompt without the playset note (and advisor block) the run had."""
from pathlib import Path

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.replay import rebuild_prompt

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "playset"


def _system_used_by_loop(config):
    env = HOI4Env(MockAdapter(seed=3), config, index=config.build_index())
    return AgentLoop(env=env, planner=ScriptedClient(), config=config).system


RECORD = {"kind": "observe", "turn": 1, "date": "1936-01-08", "brief": "b", "wake_reason": "x"}


def test_replay_system_prompt_matches_a_total_conversion_run():
    config = HarnessConfig(adapter="mock", game_dir=FIXTURE / "game",
                           mod_dirs=[FIXTURE / "mods" / "new_order"])
    rebuilt = rebuild_prompt(config, [RECORD], RECORD)
    assert rebuilt.system == _system_used_by_loop(config)


def test_replay_system_prompt_matches_an_advisor_run():
    config = HarnessConfig(adapter="mock", advisor=True)
    rebuilt = rebuild_prompt(config, [RECORD], RECORD)
    assert rebuilt.system == _system_used_by_loop(config)


def test_the_prompt_command_prints_the_system_prompt_the_run_would_use(capsys, tmp_path):
    from hoi4_harness.cli import main

    profile = tmp_path / "p.json"
    profile.write_text('{"advisor": true, "operational_control": "ai"}')
    main(["prompt", "--profile", str(profile)])
    expected = _system_used_by_loop(HarnessConfig(adapter="mock", advisor=True, operational_control="ai"))
    assert capsys.readouterr().out.strip() == expected.strip()
