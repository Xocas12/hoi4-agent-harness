from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import BudgetConfig, HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import GameState


def make_loop(script=None, **overrides):
    config = HarnessConfig(adapter="mock", turns=6, **overrides)
    env = HOI4Env(MockAdapter(seed=3), config)
    env.reset()
    return AgentLoop(env=env, planner=ScriptedClient(script=script), config=config), env


def test_a_run_advances_the_calendar_and_takes_actions():
    loop, env = make_loop()
    report = loop.run(6)
    assert report.turns == 6
    assert report.start_date < report.end_date
    assert report.actions_ok > 0


def test_irreversible_actions_are_blocked_in_dry_run():
    script = [
        LLMResponse(
            tool_calls=[ToolCall("1", "diplomacy", {"action": "declare_war", "target": "GER"})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    loop, env = make_loop(script=script)
    loop.run(1)
    assert loop.report.actions_failed >= 1


def test_the_budget_guard_downgrades_to_reflex_instead_of_stopping():
    loop, env = make_loop()
    loop.budget.config = BudgetConfig(max_llm_calls=1)
    report = loop.run(5)
    assert report.planner_calls <= 1
    assert report.reflex_turns >= 3      # the rest ran for free
    assert report.turns == 5             # and the game kept moving


def test_notes_written_by_the_agent_come_back_in_the_next_prompt():
    script = [
        LLMResponse(
            tool_calls=[ToolCall("1", "note", {"text": "Build civs until 1938."})],
            stop_reason="tool_use",
            usage=Usage(10, 10),
        )
    ]
    loop, env = make_loop(script=script)
    loop.run(2)
    assert any("Build civs until 1938." in e.text for e in loop.memory.journal)
    assert "Build civs until 1938." in loop.memory.context_block()


def test_the_planner_is_not_woken_when_nothing_is_happening():
    from hoi4_harness.agent.policy import Policy

    quiet = GameState(national_focus="industrial_effort")
    quiet.research = []
    assert not Policy(days_per_turn=7).should_wake(quiet, days_since_planner=1).wake
    assert Policy(days_per_turn=7).should_wake(quiet, days_since_planner=7).wake


def test_a_wake_turn_leaves_a_progress_line():
    lines: list[str] = []
    loop, _env = make_loop()
    loop.progress = lines.append
    loop.run(2)
    assert len(lines) == 2
    assert lines[0].startswith("1936-01-01")
    assert "wake: no national focus is running" in lines[0]
    assert "advance_time" in lines[0]
    assert " tok" in lines[0]
    assert lines[0].endswith("$0.00")


def test_a_reflex_turn_leaves_a_progress_line_without_a_token_column():
    lines: list[str] = []
    loop, _env = make_loop(wake_on_no_focus=False, wake_on_free_research_slot=False)
    loop.progress = lines.append
    loop.run(2)
    assert len(lines) == 2
    assert "wake:" not in lines[1]
    assert "queue_construction" in lines[1]
    assert lines[1].endswith("$0.00")


def test_a_budget_downgrade_says_why_in_its_progress_line():
    lines: list[str] = []
    loop, _env = make_loop()
    loop.progress = lines.append
    loop.budget.config = BudgetConfig(max_llm_calls=1)
    loop.run(2)
    assert "reflex (budget: call ceiling reached (1))" in lines[1]
    assert "wake:" not in lines[1]
