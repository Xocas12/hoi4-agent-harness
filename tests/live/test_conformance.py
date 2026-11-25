"""What actually breaks against a live provider, one cheap call per check.

Wire formats drift and a scripted suite cannot see it. These check what has to
hold for the loop to work at all: the tool block is accepted, a tool call comes
back as a valid ActionCall, a tool result can be sent back for a second round,
and usage is reported. Prefix caching is checked where the provider promises it.
The checks themselves are in ``hoi4_harness.eval.live_conformance``.
"""

from __future__ import annotations

import os

import pytest

from hoi4_harness.agent.llm import build_llm
from hoi4_harness.config import DEFAULT_MODELS, LLMConfig
from hoi4_harness.eval import live_conformance as lc


def test_the_tool_block_is_accepted_and_a_call_parses_into_a_valid_action(first_response):
    lc.check_tool_call_parses(first_response)


def test_the_stop_reason_is_a_neutral_word(first_response):
    lc.check_stop_reason(first_response)


def test_a_rejected_key_is_classified_fatal(live, call_budget, monkeypatch):
    provider, _ = live
    if provider in ("scripted", "claude-cli"):
        pytest.skip(f"{provider} has no key to reject")
    if os.environ.get(lc.SPECS[provider].base_url_env or ""):
        pytest.skip("a local server may not check keys")
    monkeypatch.setenv("HOI4_LIVE_REJECTED_KEY", "invalid-key-for-conformance")
    model = os.environ.get(f"HOI4_LIVE_{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]
    client = build_llm(LLMConfig(provider=provider, model=model, max_tokens=16, effort="low",
                                 api_key_env="HOI4_LIVE_REJECTED_KEY"))
    lc.check_bad_key_is_fatal(lc.CappedClient(client, call_budget))   # counted against the cap


def test_usage_is_populated(first_response):
    lc.check_usage(first_response)


def test_a_tool_result_round_trips_into_a_second_round(live, first_response):
    provider, client = live
    # The offline policy's token counts are synthetic and do not grow with history.
    growing = provider not in ("scripted", "claude-cli")
    lc.check_round_trip(client, first_response, strict_growth=growing)


def test_prefix_caching_is_reported_where_the_provider_promises_it(live):
    provider, client = live
    if not lc.SPECS[provider].caching:
        # OpenAI and Gemini cache implicitly and without a guarantee on the
        # second call; a zero there is not a conformance failure.
        pytest.skip(f"{provider} caching is implicit and best-effort or absent")
    lc.check_prefix_caching(client)
