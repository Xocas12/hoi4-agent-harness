"""The "This playset" prompt section and the read-only decision tools.

Deterministic text for the cached prefix, cut to a token budget, and answered
from the playset's files without touching the game.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoi4_harness.actions import registry
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.agent.prompts import build_system, system_for
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.modcontext import playset_section
from hoi4_harness.observation.tokens import count_tokens
from hoi4_harness.types import ActionCall

FIX = Path(__file__).parent / "fixtures" / "modctx"
GAME, ALPHA = FIX / "game", FIX / "mods" / "alpha"


@pytest.fixture(scope="module")
def vanilla():
    return IdentifierIndex.build(Playset.from_paths(GAME))


@pytest.fixture()
def modded(tmp_path):
    return IdentifierIndex.build(Playset.from_paths(GAME, [ALPHA])), tmp_path


def test_the_section_is_the_same_text_every_time(vanilla):
    first = playset_section(vanilla, "SWE")
    assert first == playset_section(vanilla, "swe")
    fresh = IdentifierIndex.build(Playset.from_paths(GAME))
    assert playset_section(fresh, "SWE") == first            # not an accident of one object


def test_the_section_names_the_country_its_tree_and_its_decisions(vanilla):
    text = playset_section(vanilla, "SWE")
    assert text.startswith("## This playset")
    assert "SWE's focus tree: swe_tree, 4 focuses. It opens with: SWE_root (Root Focus)." in text
    assert "Decisions for SWE: 7 in 2 categories: Swedish Affairs (" in text
    assert "swe_cheap 25pp (Cheap Reform)" in text
    assert "swe_mission" not in text and "swe_spacer" not in text      # not takeable: not offered
    assert "list_decisions" in text and "take_decision" in text
    assert "ger_thing" not in text


def test_a_vanilla_playset_with_no_country_has_nothing_to_say(vanilla):
    assert playset_section(vanilla, None) == ""


def test_a_mod_adds_its_world_its_techs_and_its_mechanics(modded):
    index, cache = modded
    text = playset_section(index, "SWE", cache_dir=cache, offline=True)
    assert "Mods, in load order: Alpha World." in text
    assert "World (Alpha World): Alpha World 2.0 (Gameplay)" in text     # offline: the descriptor
    assert "1 technologies the base game lacks in infantry_folder" in text
    assert "ideologies: neutrality" in text and "resources: uranium" in text


def test_the_section_fits_its_budget_and_drops_the_least_useful_lines_first(modded):
    index, cache = modded
    full = playset_section(index, "SWE", cache_dir=cache, max_tokens=900)
    tight = playset_section(index, "SWE", cache_dir=cache, max_tokens=120)
    assert count_tokens(full)[0] <= 900 and count_tokens(tight)[0] <= 120
    assert len(tight) < len(full)
    assert "Mods, in load order" in tight                        # the header survives
    assert "Decisions to start from" not in tight                # the shopping list does not
    assert count_tokens(playset_section(index, "SWE", cache_dir=cache, max_tokens=15))[0] <= 15


def test_it_sits_in_the_stable_prefix_before_the_guidance():
    system = build_system("none", playset_context="## This playset\nstuff")
    assert "## This playset\nstuff" in system
    assert system == build_system("none", playset_context="## This playset\nstuff")
    assert build_system("none") != system


def test_system_for_adds_it_only_when_asked_and_never_fetches_unasked(vanilla, tmp_path):
    config = HarnessConfig(country="SWE", mod_context_cache_dir=tmp_path)
    assert "This playset" not in system_for(config, vanilla)
    config.mod_context = True
    assert "## This playset" in system_for(config, vanilla)
    assert system_for(config, vanilla) == system_for(config, vanilla)


# --- the tools -----------------------------------------------------------------------


def env_for(index, **options):
    config = HarnessConfig(country="SWE", mod_context=True, **options)
    return HOI4Env(MockAdapter(country="SWE"), config, index=index)


def ask(env, name, **arguments):
    result = env.act(ActionCall(name, arguments))
    return result, (json.loads(result.message) if result.ok else None)


def test_list_decisions_answers_from_the_files_and_filters(vanilla):
    env = env_for(vanilla)
    assert {"list_decisions", "describe_action"} <= env.allowed_actions
    result, data = ask(env, "list_decisions")
    assert result.ok and data["matching"] == 7 and data["country"] == "SWE"
    assert {d["id"] for d in data["decisions"]} >= {"swe_cheap", "swe_aid"}
    _, data = ask(env, "list_decisions", category="swe_targets_cat", limit=1)
    assert (data["matching"], data["shown"]) == (3, 1)
    _, data = ask(env, "list_decisions", query="reform")
    assert [d["id"] for d in data["decisions"]] == ["swe_cheap"]


def test_describe_action_says_what_a_thing_needs_and_whether_it_can_be_done(vanilla):
    env = env_for(vanilla)
    _, cheap = ask(env, "describe_action", id="swe_cheap")
    assert cheap["type"] == "decision" and cheap["cost"] == 25
    assert cheap["headless"].startswith("take_decision") and cheap["unchecked"]
    _, mission = ask(env, "describe_action", id="swe_mission")
    assert mission["headless"].startswith("observe-only: a mission")
    _, focus = ask(env, "describe_action", id="SWE_left")
    assert focus["type"] == "focus" and focus["mutually_exclusive"] == ["SWE_right"]
    missing, _ = ask(env, "describe_action", id="nothing_here")
    assert not missing.ok and missing.error_kind == "invalid_args"


def test_the_tools_do_not_exist_without_mod_context_or_a_playset(vanilla):
    off = HOI4Env(MockAdapter(country="SWE"), HarnessConfig(country="SWE"), index=vanilla)
    assert not ({"list_decisions", "describe_action"} & off.allowed_actions)
    assert not off.act(ActionCall("list_decisions", {})).ok
    bare = HOI4Env(MockAdapter(country="SWE"), HarnessConfig(country="SWE", mod_context=True))
    assert "list_decisions" not in bare.allowed_actions
    assert "list_decisions" not in {t.name for t in registry.tool_specs(off.allowed_actions)}


def test_queries_do_not_count_against_the_action_cap(vanilla):
    env = env_for(vanilla, max_actions_per_turn=1)
    from hoi4_harness.env import UNCAPPED_ACTIONS
    assert {"list_decisions", "describe_action"} <= UNCAPPED_ACTIONS
    assert env.act(ActionCall("list_decisions", {})).ok


def test_the_loop_answers_a_query_in_the_same_turn_and_does_not_journal_it(vanilla, tmp_path):
    from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
    from hoi4_harness.agent.llm.scripted import ScriptedClient

    def reply(*calls):
        return LLMResponse(tool_calls=list(calls), stop_reason="tool_use", usage=Usage(10, 10))

    script = [
        reply(ToolCall("q1", "list_decisions", {"query": "reform"})),
        reply(ToolCall("a1", "advance_time", {"days": 7})),
    ]
    config = HarnessConfig(adapter="mock", country="SWE", turns=1, mod_context=True,
                           mod_context_cache_dir=tmp_path, max_actions_per_turn=1)
    env = HOI4Env(MockAdapter(country="SWE"), config, index=vanilla)
    env.reset()
    loop = AgentLoop(env=env, planner=ScriptedClient(script=script), config=config)
    seen = []
    real_write = loop.transcript.write
    loop.transcript.write = lambda kind, **f: (seen.append((kind, f)), real_write(kind, **f))[1]
    loop.run(1)
    actions = [r for kind, f in seen if kind == "actions" for r in f["results"]]
    answer = next(r for r in actions if r["name"] == "list_decisions")
    assert not answer["is_error"] and "swe_cheap" in answer["content"]
    assert loop.report.actions_failed == 0
    assert "list_decisions" not in " ".join(loop.memory.context_block().split())
    assert "## This playset" in loop.system
