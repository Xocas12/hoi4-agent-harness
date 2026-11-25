"""Provider conformance, offline (#22): recorded wire responses through the real clients.

Every provider client (anthropic, openai-compatible, gemini, claude-cli) is fed the response
shapes in ``tests/fixtures/providers/responses.json`` by a fake SDK, and must turn each into the
same neutral ``LLMResponse``: tool calls, usage in the one meaning ``Usage`` documents, stop
reasons from one vocabulary, and errors the loop can classify. The same claims are made against
the real services by ``tests/live`` (opt-in, ``hoi4-harness provider-check``); this file is what
runs everywhere, for free, and what pins the mapping when no key is at hand.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hoi4_harness.actions import registry
from hoi4_harness.agent.errors import classify
from hoi4_harness.agent.llm.anthropic_provider import AnthropicClient
from hoi4_harness.agent.llm.base import STOP_REASONS, Msg, ToolResult, normalise_stop
from hoi4_harness.agent.llm.claude_cli import ClaudeCliClient, ClaudeCliError
from hoi4_harness.agent.llm.google_provider import GoogleClient
from hoi4_harness.agent.llm.openai_provider import OpenAIClient
from hoi4_harness.eval import live_conformance as lc

RECORDED = json.loads(
    (Path(__file__).parent / "fixtures" / "providers" / "responses.json").read_text("utf-8"))
TOOLS = registry.tool_specs(set(lc.TOOLS))


def ns(value):
    """A JSON value as the attribute-style object an SDK returns."""
    if isinstance(value, dict):
        # A tool's input/args is a plain dict on the wire and in the SDKs.
        return SimpleNamespace(**{k: v if k in ("input", "args") else ns(v)
                                  for k, v in value.items()})
    if isinstance(value, list):
        return [ns(v) for v in value]
    return value


class Boom(Exception):
    def __init__(self, name: str, status: int | None):
        super().__init__(f"{name}: simulated")
        self.status_code = status


def _anthropic(wire=None, error=None, sent=None):
    class Stream:
        def __enter__(self):
            if error:
                raise error
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return ns(wire)

    def stream(**kwargs):
        if sent is not None:
            sent.append(kwargs)
        return Stream()

    client = AnthropicClient.__new__(AnthropicClient)
    client.client = SimpleNamespace(messages=SimpleNamespace(stream=stream))
    client.model, client.max_tokens, client.effort, client.cache_prefix = "m", 2048, "low", True
    return client


def _openai(wire=None, error=None, sent=None):
    def create(**kwargs):
        if sent is not None:
            sent.append(kwargs)
        if error:
            raise error
        return ns(wire)

    client = OpenAIClient.__new__(OpenAIClient)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client.model, client.max_tokens, client.effort, client.temperature = "gpt-x", 2048, "low", None
    client._send_effort, client.supports_strict_tools = False, False
    return client


def _google(wire=None, error=None, sent=None):
    def generate_content(**kwargs):
        if sent is not None:
            sent.append(kwargs)
        if error:
            raise error
        return ns(wire)

    client = GoogleClient.__new__(GoogleClient)
    client.client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    client.model, client.max_tokens, client.temperature = "gemini-x", 2048, None
    return client


def _cli(wire=None, error=None, sent=None, tmp_path=None):
    def run(cmd, **kwargs):
        if sent is not None:
            sent.append({"cmd": cmd, **kwargs})
        if error:
            raise error
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(wire), stderr="")

    return ClaudeCliClient(model="haiku", cli="claude", run=run, workdir=tmp_path, retries=0)


BUILDERS = {"anthropic": _anthropic, "openai": _openai, "google": _google}
ALL = ("anthropic", "openai", "google", "claude-cli")


def make(provider, tmp_path, wire=None, error=None, sent=None):
    if provider == "claude-cli":
        return _cli(wire, error, sent, tmp_path)
    return BUILDERS[provider](wire, error, sent)


def ask(client):
    return client.complete("system", [Msg(role="user", text=lc.ASK)], TOOLS)


# --- tool calls ------------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ALL)
def test_a_recorded_tool_call_parses_into_a_valid_action(provider, tmp_path):
    response = ask(make(provider, tmp_path, RECORDED[provider]["tool_call"]))
    lc.check_tool_call_parses(response)
    call = response.tool_calls[0]
    assert (call.name, call.arguments) == ("start_research", {"technology": "construction1"})
    assert response.stop_reason == "tool_use"
    lc.check_stop_reason(response)


def test_the_cli_reads_a_fenced_answer(tmp_path):
    response = ask(make("claude-cli", tmp_path, RECORDED["claude-cli"]["fenced"]))
    assert [c.name for c in response.tool_calls] == ["note"] and response.stop_reason == "tool_use"


def test_malformed_arguments_are_kept_visible_not_dropped(tmp_path):
    response = ask(make("openai", tmp_path, RECORDED["openai"]["malformed_arguments"]))
    assert response.tool_calls[0].arguments == {"_unparsed": '{"technology": '}


def test_a_cli_reply_that_is_not_json_means_no_calls_not_a_crash(tmp_path):
    response = ask(make("claude-cli", tmp_path, RECORDED["claude-cli"]["prose_only"]))
    assert response.tool_calls == [] and response.stop_reason == "end_turn"
    assert response.text == "I think we should wait."


# --- tool results go back in the provider's own shape ----------------------------------------


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
def test_a_tool_result_is_sent_back_in_the_providers_shape(provider, tmp_path):
    sent: list[dict] = []
    client = make(provider, tmp_path, RECORDED[provider]["tool_call"], sent=sent)
    first = ask(client)
    lc.check_round_trip(client, first, strict_growth=False)
    messages = sent[1].get("messages") or sent[1].get("contents")
    flat = json.dumps(messages, default=str)
    assert first.tool_calls[0].id in flat or provider == "google"
    assert {"anthropic": "tool_result", "openai": '"role": "tool"',
            "google": "function_response"}[provider] in flat
    assert "Researching." in flat           # the tool's result text itself arrived


def test_the_cli_round_trip_puts_the_tool_result_in_the_prompt(tmp_path):
    sent: list[dict] = []
    client = make("claude-cli", tmp_path, RECORDED["claude-cli"]["tool_call"], sent=sent)
    first = ask(client)
    client.complete("system", [
        Msg(role="user", text=lc.ASK),
        Msg(role="assistant", text=first.text, tool_calls=first.tool_calls),
        Msg(role="user", tool_results=[ToolResult(call_id=first.tool_calls[0].id,
                                                  name="start_research", content='{"ok": true}')]),
    ], TOOLS)
    assert '{"ok": true}' in sent[1]["input"] and "ANTHROPIC_API_KEY" not in sent[1]["env"]


# --- usage: one meaning for every provider ---------------------------------------------------


def _usage(response):
    u = response.usage
    return u.input_tokens, u.output_tokens, u.cached_input_tokens, u.cache_write_input_tokens


@pytest.mark.parametrize(("provider", "expected"), [
    ("anthropic", (120 + 900 + 300, 35, 900, 300)),     # uncached + read + written
    ("openai", (1200, 40, 900, 0)),                     # prompt_tokens already holds the cache
    ("google", (1200, 12 + 30, 900, 0)),                # thinking bills as output
    ("claude-cli", (120 + 900 + 300, 35, 900, 300)),
])
def test_usage_means_the_same_thing_for_every_provider(provider, expected, tmp_path):
    response = ask(make(provider, tmp_path, RECORDED[provider]["tool_call"]))
    assert _usage(response) == expected
    lc.check_usage(response) if response.model else None
    assert response.usage.uncached_input_tokens == (
        expected[0] - expected[2] - expected[3])


def test_a_response_with_no_usage_block_reads_as_zero_not_an_error(tmp_path):
    wire = {**RECORDED["openai"]["end_turn"], "usage": None}
    assert _usage(ask(make("openai", tmp_path, wire))) == (0, 0, 0, 0)


# --- stop reasons -----------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
@pytest.mark.parametrize(("scenario", "stop"), [
    ("end_turn", "end_turn"), ("max_tokens", "max_tokens"), ("refusal", "refusal")])
def test_every_ending_maps_into_the_neutral_vocabulary(provider, scenario, stop, tmp_path):
    response = ask(make(provider, tmp_path, RECORDED[provider][scenario]))
    assert response.stop_reason == stop and stop in STOP_REASONS
    assert response.tool_calls == []


def test_an_unknown_finish_reason_is_kept_not_hidden():
    assert normalise_stop("pause_turn") == "pause_turn"
    assert normalise_stop(None) == "end_turn"
    assert normalise_stop("STOP") == "end_turn" and normalise_stop("MAX_TOKENS") == "max_tokens"
    assert normalise_stop("length", calls=True) == "tool_use"


def test_a_gemini_enum_finish_reason_is_read_by_name():
    enum_like = SimpleNamespace(name="MAX_TOKENS")
    assert normalise_stop(enum_like) == "max_tokens"


# --- errors: the loop must be able to tell a bad key from a bad minute ----------------------


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
@pytest.mark.parametrize(("status", "kind"), [
    (401, "fatal"), (403, "fatal"), (404, "fatal"), (400, "fatal"),
    (429, "transient"), (500, "transient"), (529, "transient")])
def test_a_provider_error_reaches_the_loop_with_its_class(provider, status, kind, tmp_path):
    client = make(provider, tmp_path, error=Boom("ApiError", status))
    with pytest.raises(Boom) as raised:
        ask(client)
    assert classify(raised.value) == kind


@pytest.mark.parametrize("provider", ["anthropic", "openai", "google"])
def test_a_dropped_connection_is_transient(provider, tmp_path):
    client = make(provider, tmp_path, error=ConnectionError("reset"))
    with pytest.raises(ConnectionError) as raised:
        ask(client)
    assert classify(raised.value) == "transient"


def test_the_cli_reports_an_error_envelope_a_bad_exit_a_timeout_and_a_missing_binary(tmp_path):
    with pytest.raises(ClaudeCliError, match="Credit balance"):
        ask(make("claude-cli", tmp_path, RECORDED["claude-cli"]["error"]))

    def exits(code):
        def run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, code, stdout="", stderr="not logged in")
        return ClaudeCliClient(cli="claude", run=run, workdir=tmp_path, retries=0)

    with pytest.raises(ClaudeCliError, match="exited 1: not logged in"):
        ask(exits(1))
    with pytest.raises(ClaudeCliError, match="longer than"):
        ask(make("claude-cli", tmp_path, error=subprocess.TimeoutExpired("claude", 1)))
    with pytest.raises(ClaudeCliError, match="could not run"):
        ask(make("claude-cli", tmp_path, error=FileNotFoundError("claude")))


# --- keys are never in what the harness prints -------------------------------------------------


def test_a_rejected_key_error_text_is_not_echoed_by_the_plan_or_the_check():
    secret = "sk-ant-secret-value-123"
    env = {lc.ALT_OPT_IN: "1", "ANTHROPIC_API_KEY": secret}
    assert secret not in lc.render_plan(env, ["anthropic"])
    assert lc.skip_reason("anthropic", env, lambda name: True) is None   # HOI4_LIVE_PROVIDERS opts in


# --- the live switches ------------------------------------------------------------------------


def test_claude_cli_needs_the_opt_in_and_the_binary_but_no_key():
    on = {lc.ALT_OPT_IN: "1"}
    assert "opt-in" in lc.skip_reason("claude-cli", {}, lambda n: True, lambda n: "/bin/claude")
    assert lc.skip_reason("claude-cli", on, lambda n: True, lambda n: "/bin/claude") is None
    assert "not on PATH" in lc.skip_reason("claude-cli", on, lambda n: True, lambda n: None)


def test_the_only_switch_narrows_a_run_to_the_named_providers():
    env = {lc.ALT_OPT_IN: "1", "ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k",
           lc.ONLY_ENV: "openai"}
    have = lambda n: True  # noqa: E731
    assert lc.runnable(env, have, lambda n: None) == ["openai"]
    assert "not selected" in lc.skip_reason("anthropic", env, have)


def test_provider_check_sets_the_opt_in_and_never_prints_a_key(tmp_path, capsys, monkeypatch):
    from hoi4_harness import cli

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-do-not-print")
    monkeypatch.delenv("HOI4_LIVE_PROVIDERS", raising=False)
    monkeypatch.setattr("hoi4_harness.eval.live_conformance._importable", lambda n: True)
    parser_args = ["provider-check", "--plan", "--provider", "anthropic", "--max-calls", "7"]
    assert cli.main(parser_args) == 0
    out = capsys.readouterr()
    assert "sk-do-not-print" not in out.out + out.err
    assert "anthropic: 5 call(s)" in out.out and "hard cap 7" in out.out


def test_provider_check_with_no_key_says_nothing_will_run(monkeypatch, capsys):
    from hoi4_harness import cli

    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "HOI4_LIVE_OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert cli.main(["provider-check", "--plan", "--provider", "anthropic"]) == 2
    assert "nothing will run" in capsys.readouterr().out
