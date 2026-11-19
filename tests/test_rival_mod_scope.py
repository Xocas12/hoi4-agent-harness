"""Rival mode steers a country that is not the player's, so nothing the mod's directives rely on
may be bound to the player. Read from the files, since the real game is not here to ask.

* an ``ai_strategy`` block is evaluated for the AI country that owns it, and its ``enable`` /
  ``abort`` test a *country flag* in that scope, so a flag set on B steers B's AI;
* the effects the harness calls (``effect B llm_bridge_raise_invade``) only set and clear flags on
  the scope they run in;
* every on_action that reports limits on ``llm_bridge_is_harness`` (human OR flagged), not on
  ``is_ai = no``, so a flagged AI country is reported and a human one still is.
"""

from __future__ import annotations

import re
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge" / "common"
PLAYER_BOUND = re.compile(r"\b(is_ai|PLAYER|FROM|PREV|ROOT)\b|every_country|random_country")


def text(*parts: str) -> str:
    return (MOD.joinpath(*parts)).read_text(encoding="utf-8")


def blocks(source: str) -> list[str]:
    """Top-level ``name = { ... }`` blocks of a script file (comments dropped)."""
    source = re.sub(r"#[^\n]*", "", source)
    out, depth, start = [], 0, 0
    for i, char in enumerate(source):
        if char == "{":
            if depth == 0:
                start = source.rfind("\n", 0, i) + 1
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                out.append(source[start:i + 1])
    return out


def test_every_ai_strategy_block_is_gated_on_a_country_flag_and_nothing_player_bound():
    for name in ("llm_bridge_targets.txt", "llm_bridge_directives.txt"):
        found = blocks(text("ai_strategy", name))
        assert found
        for block in found:
            assert "has_country_flag = llmb_" in block, block
            assert not PLAYER_BOUND.search(block), block


def test_the_effects_the_harness_calls_only_touch_the_scope_they_run_in():
    for name in ("llm_bridge_targets.txt", "llm_bridge_directives.txt"):
        found = blocks(text("scripted_effects", name))
        assert found
        for block in found:
            body = re.sub(r'log = "[^"]*"', "", block)       # the log lines name ROOT; see below
            assert not re.search(r"\b(PLAYER|FROM|PREV|every_country|random_country)\b", body), block
            assert not re.search(r"\bROOT\s*=\s*\{", body), block      # no effect inside ROOT = {...}


def test_every_reporting_on_action_limits_on_the_harness_trigger_not_on_is_ai():
    source = re.sub(r"#[^\n]*", "", text("on_actions", "llm_bridge_on_actions.txt"))
    assert "llm_bridge_is_harness = yes" in source
    assert "is_ai" not in source
    trigger = text("scripted_triggers", "llm_bridge_triggers.txt")
    assert "is_ai = no" in trigger and "has_country_flag = llmb_harness" in trigger


def test_the_mark_and_unmark_effects_are_the_only_way_a_flag_makes_a_country_reported():
    telemetry = text("scripted_effects", "llm_bridge_telemetry.txt")
    assert "set_country_flag = llmb_harness" in telemetry
    assert telemetry.count("llmb_harness") >= 2
    assert "llm_bridge_unmark_harness" in telemetry
