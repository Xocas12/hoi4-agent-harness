"""The live conformance suite's own logic, tested with no key and no network (#22).

Skip rules, the call cap and the cost plan are checked with plain dicts standing in
for the environment. The request shapes are checked by running the very same check
functions the live suite runs against each provider client with its SDK replaced by
a fake that records the request and answers with a recorded-style response.
"""

from types import SimpleNamespace as NS

import pytest

from hoi4_harness.agent.llm.anthropic_provider import AnthropicClient
from hoi4_harness.agent.llm.google_provider import GoogleClient
from hoi4_harness.agent.llm.openai_provider import OpenAIClient
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.eval import live_conformance as lc

HAVE_SDK = lambda name: True  # noqa: E731
NO_SDK = lambda name: False  # noqa: E731
NO_CLI = lambda name: None  # noqa: E731
HAVE_CLI = lambda name: "/bin/claude"  # noqa: E731


# --- skip logic -----------------------------------------------------------------------

def test_a_key_without_the_opt_in_never_runs():
    env = {"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k", "GOOGLE_API_KEY": "k"}
    for provider in ("anthropic", "openai", "google"):
        assert "opt-in" in lc.skip_reason(provider, env, HAVE_SDK)
    assert lc.runnable(env, HAVE_SDK) == []


def test_the_opt_in_without_a_key_skips_naming_the_variable():
    env = {lc.OPT_IN: "1"}
    assert lc.skip_reason("anthropic", env, HAVE_SDK) == "ANTHROPIC_API_KEY is not set"
    assert lc.skip_reason("google", env, HAVE_SDK) == "GOOGLE_API_KEY is not set"


def test_the_opt_in_must_be_exactly_one():
    env = {"ANTHROPIC_API_KEY": "k", lc.OPT_IN: "true"}
    assert "opt-in" in lc.skip_reason("anthropic", env, HAVE_SDK)


def test_the_legacy_opt_in_still_counts():
    env = {"ANTHROPIC_API_KEY": "k", lc.LEGACY_OPT_IN: "1"}
    assert lc.skip_reason("anthropic", env, HAVE_SDK) is None


def test_a_missing_sdk_skips_even_with_key_and_opt_in():
    env = {"GOOGLE_API_KEY": "k", lc.OPT_IN: "1"}
    assert "SDK is not installed" in lc.skip_reason("google", env, NO_SDK)


def test_a_local_openai_compatible_server_stands_in_for_a_key():
    env = {lc.OPT_IN: "1", "HOI4_LIVE_OPENAI_BASE_URL": "http://localhost:11434/v1"}
    assert lc.skip_reason("openai", env, HAVE_SDK) is None
    # but only the opt-in unlocks it
    assert "opt-in" in lc.skip_reason("openai", {"HOI4_LIVE_OPENAI_BASE_URL": "x"}, HAVE_SDK)


def test_scripted_is_free_and_always_runs():
    assert lc.skip_reason("scripted", {}, NO_SDK) is None
    assert "scripted" not in lc.runnable({lc.OPT_IN: "1"}, HAVE_SDK)


# --- the cap and the plan ---------------------------------------------------------------

def test_the_cap_defaults_and_rejects_nonsense():
    assert lc.call_cap({}) == lc.DEFAULT_CAP
    assert lc.call_cap({lc.CAP_ENV: "3"}) == 3
    with pytest.raises(ValueError):
        lc.call_cap({lc.CAP_ENV: "many"})
    with pytest.raises(ValueError):
        lc.call_cap({lc.CAP_ENV: "-1"})


def test_the_call_past_the_cap_is_refused_before_it_reaches_the_client():
    inner = ScriptedClient()
    client = lc.CappedClient(inner, lc.CallBudget(2))
    lc.first_call(client)
    lc.first_call(client)
    with pytest.raises(lc.CallCapExceeded):
        lc.first_call(client)
    assert len(inner.calls) == 2


def test_the_plan_states_calls_cap_and_dollars_when_rates_are_set():
    env = {lc.OPT_IN: "1", "ANTHROPIC_API_KEY": "k", "GOOGLE_API_KEY": "k",
           "HOI4_USD_PER_M_INPUT": "5", "HOI4_USD_PER_M_OUTPUT": "25", lc.CAP_ENV: "5"}
    text = lc.render_plan(env, lc.runnable(env, HAVE_SDK, NO_CLI))
    assert "8 call(s) planned, hard cap 5" in text
    assert "anthropic: 5 call(s)" in text and "google: 3 call(s)" in text
    assert "worst case total: $" in text
    assert "WARNING" in text                 # 8 planned against a cap of 5


def test_the_plan_never_echoes_a_key_and_says_so_when_unpriced():
    env = {lc.OPT_IN: "1", "ANTHROPIC_API_KEY": "sk-secret-value"}
    text = lc.render_plan(env, ["anthropic"])
    assert "sk-secret-value" not in text
    assert "no dollar figure" in text


def test_the_plan_with_nothing_runnable_says_so():
    assert "nothing will run" in lc.render_plan({}, [])


# --- request shapes, through the real clients with a fake SDK ------------------------------

def _anthropic(requests):
    def message(i):
        read = 1200 if i >= 3 else 0          # the second identical call reads the cache
        return NS(
            content=[NS(type="tool_use", id=f"toolu_{i}", name="start_research",
                        input={"technology": "construction1"})],
            stop_reason="tool_use",
            usage=NS(input_tokens=100 + 40 * i, output_tokens=30, cache_read_input_tokens=read,
                     cache_creation_input_tokens=0 if i else 20),
        )

    class Stream:
        def __init__(self, i):
            self.i = i

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return message(self.i)

    def stream(**kwargs):
        requests.append(kwargs)
        return Stream(len(requests) - 1)

    client = AnthropicClient.__new__(AnthropicClient)
    client.client = NS(messages=NS(stream=stream))
    client.model, client.max_tokens, client.effort, client.cache_prefix = "m", 2048, "low", True
    return client


def test_anthropic_request_shapes_and_usage():
    requests: list[dict] = []
    client = _anthropic(requests)
    first = lc.first_call(client)
    lc.check_tool_call_parses(first)
    lc.check_usage(first)
    lc.check_round_trip(client, first)
    lc.check_prefix_caching(client)

    req = requests[0]
    assert req["tools"][-1]["cache_control"] == {"type": "ephemeral"}      # the breakpoint reaches the wire
    assert {t["name"] for t in req["tools"]} == set(lc.TOOLS)
    assert all("input_schema" in t for t in req["tools"])
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["messages"][0]["role"] == "user"
    second = requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[1]["content"][-1]["type"] == "tool_use"
    assert second[2]["content"][0]["type"] == "tool_result"
    assert second[2]["content"][0]["tool_use_id"] == first.tool_calls[0].id
    assert len(requests) == 4
    # Anthropic bills uncached + read + written as input; the neutral usage is the sum.
    assert first.usage.input_tokens == 100 + 20 and first.usage.cache_write_input_tokens == 20


def _openai(requests):
    def create(**kwargs):
        requests.append(kwargs)
        n = len(requests)
        call = NS(id="call_1", function=NS(name="start_research",
                                           arguments='{"technology": "construction1"}'))
        return NS(choices=[NS(message=NS(content=None, tool_calls=[call]), finish_reason="tool_calls")],
                  usage=NS(prompt_tokens=100 + 40 * n, completion_tokens=20,
                           prompt_tokens_details=NS(cached_tokens=0)))

    client = OpenAIClient.__new__(OpenAIClient)
    client.client = NS(chat=NS(completions=NS(create=create)))
    client.model, client.max_tokens, client.effort, client.temperature = "gpt-x", 2048, "low", None
    client._send_effort, client.supports_strict_tools = False, False
    return client


def test_openai_request_shapes_and_usage():
    requests: list[dict] = []
    client = _openai(requests)
    first = lc.first_call(client)
    lc.check_tool_call_parses(first)
    lc.check_usage(first)
    lc.check_round_trip(client, first)

    req = requests[0]
    assert req["tools"][0]["type"] == "function" and req["tool_choice"] == "auto"
    assert req["messages"][0]["role"] == "system"
    second = requests[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[2]["tool_calls"][0]["function"]["arguments"] == '{"technology": "construction1"}'
    assert second[3]["tool_call_id"] == "call_1"
    assert len(requests) == 2


def _google(requests):
    def generate_content(**kwargs):
        requests.append(kwargs)
        n = len(requests)
        fc = NS(name="start_research", args={"technology": "construction1"})
        return NS(function_calls=[fc], text="",
                  usage_metadata=NS(prompt_token_count=100 + 40 * n, candidates_token_count=10,
                                    thoughts_token_count=7, cached_content_token_count=0))

    client = GoogleClient.__new__(GoogleClient)
    client.client = NS(models=NS(generate_content=generate_content))
    client.model, client.max_tokens, client.temperature = "gemini-x", 2048, None
    return client


def test_google_request_shapes_and_usage():
    requests: list[dict] = []
    client = _google(requests)
    first = lc.first_call(client)
    lc.check_tool_call_parses(first)
    lc.check_usage(first)
    assert first.usage.output_tokens == 17                      # answer + thinking
    lc.check_round_trip(client, first)

    cfg = requests[0]["config"]
    declared = cfg["tools"][0]["function_declarations"]
    assert {d["name"] for d in declared} == set(lc.TOOLS)
    assert "system_instruction" in cfg
    second = requests[1]["contents"]
    assert [m["role"] for m in second] == ["user", "model", "user"]
    assert second[1]["parts"][0]["function_call"]["name"] == "start_research"
    assert second[2]["parts"][0]["function_response"]["name"] == "start_research"
    assert len(requests) == 2


def test_scripted_passes_the_same_checks_for_free():
    client = ScriptedClient()
    first = lc.first_call(client)
    lc.check_tool_call_parses(first)
    lc.check_usage(first)
    lc.check_round_trip(client, first, strict_growth=False)


def test_a_response_with_no_tool_call_fails_the_check_loudly():
    from hoi4_harness.agent.llm.base import LLMResponse

    with pytest.raises(AssertionError, match="no tool call"):
        lc.check_tool_call_parses(LLMResponse(text="I would rather chat", stop_reason="end_turn"))
