"""Provider conformance (#22): what to check, when it may run, and what it may cost.

The checks are plain functions over an ``LLMClient`` so the same code runs two ways:
against a live key (``tests/live``, opt-in, capped) and against a recorded or faked
SDK (``tests/test_live_conformance.py``, free, always on). The second proves the
request shapes and the skip logic; only the first proves the provider still
accepts them.

Nothing in this module calls a provider. ``CappedClient`` is a wrapper the live
tests put around a real client; the cap is a hard stop, not a warning.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ..actions import registry
from ..agent.llm.base import LLMClient, LLMResponse, Msg, ToolResult
from ..agent.prompts import build_system
from ..config import BudgetConfig
from ..types import ActionCall

OPT_IN = "HOI4_LIVE_PROVIDER_TESTS"
#: The name the first version of the suite shipped with; still honoured so a
#: shell that already exports it keeps working.
LEGACY_OPT_IN = "HOI4_LIVE_TESTS"
CAP_ENV = "HOI4_LIVE_MAX_CALLS"
DEFAULT_CAP = 12
#: Output ceiling for every conformance call (the live fixture builds clients with it).
MAX_TOKENS = 2048

ASK = (
    "Turn 1. You were woken because: 3 research slot(s) idle.\n\n"
    "## Sweden (SWE) -- 1936-01-01\nResearch: idle | 3 SLOT(S) FREE\n\n"
    "Call start_research exactly once, for technology 'construction1'. Nothing else."
)
TOOLS = sorted({"start_research", "note", "advance_time"})


@dataclass(frozen=True)
class ProviderSpec:
    key_env: str | None             # None: no key needed (scripted)
    sdk: str | None
    #: Calls one conformance run makes: the first call (shared by the parse and
    #: usage checks), the tool-result round trip, and, where caching is promised,
    #: two identical calls.
    calls: int
    caching: bool = False
    #: OpenAI-compatible only: a local server (Ollama, vLLM) stands in for a key.
    base_url_env: str | None = None


SPECS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec("ANTHROPIC_API_KEY", "anthropic", calls=4, caching=True),
    "openai": ProviderSpec("OPENAI_API_KEY", "openai", calls=2,
                           base_url_env="HOI4_LIVE_OPENAI_BASE_URL"),
    "google": ProviderSpec("GOOGLE_API_KEY", "google.genai", calls=2),
    "scripted": ProviderSpec(None, None, calls=2),
}


def opted_in(env: Mapping[str, str]) -> bool:
    return env.get(OPT_IN) == "1" or env.get(LEGACY_OPT_IN) == "1"


def _importable(name: str) -> bool:
    try:
        importlib.import_module(name)
    except ImportError:
        return False
    return True


def skip_reason(provider: str, env: Mapping[str, str],
                importable: Callable[[str], bool] = _importable) -> str | None:
    """Why this provider must not run now, or None. The opt-in is checked first so
    a shell full of keys is never billed by a bare ``pytest``."""
    spec = SPECS[provider]
    if spec.key_env is None:
        return None                         # scripted: free, always runs
    if not opted_in(env):
        return f"live provider tests are opt-in: set {OPT_IN}=1 (they cost money)"
    has_key = bool(env.get(spec.key_env))
    local = bool(spec.base_url_env and env.get(spec.base_url_env))
    if not has_key and not local:
        return f"{spec.key_env} is not set"
    if spec.sdk and not importable(spec.sdk):
        return f"the {provider} SDK is not installed (pip install 'hoi4-agent-harness[{provider}]')"
    return None


def runnable(env: Mapping[str, str], importable: Callable[[str], bool] = _importable) -> list[str]:
    """Providers that would spend money (scripted excluded), in a fixed order."""
    return [p for p, s in SPECS.items() if s.key_env and skip_reason(p, env, importable) is None]


def call_cap(env: Mapping[str, str]) -> int:
    raw = env.get(CAP_ENV)
    if raw is None or raw == "":
        return DEFAULT_CAP
    try:
        cap = int(raw)
    except ValueError:
        raise ValueError(f"{CAP_ENV} must be a whole number, got {raw!r}") from None
    if cap < 0:
        raise ValueError(f"{CAP_ENV} must not be negative")
    return cap


class CallCapExceeded(AssertionError):
    pass


class CallBudget:
    """One counter for the whole run, shared by every provider's client."""

    def __init__(self, cap: int):
        self.cap = cap
        self.used = 0

    def take(self) -> None:
        if self.used >= self.cap:
            raise CallCapExceeded(f"hard call cap reached ({self.cap}); raise {CAP_ENV} to allow more")
        self.used += 1


class CappedClient(LLMClient):
    """Counts every call before it is sent, and refuses the one past the cap."""

    def __init__(self, inner: LLMClient, budget: CallBudget):
        self.inner, self.budget = inner, budget
        self.name, self.model = inner.name, inner.model

    def complete(self, system, messages, tools=None, max_tokens=None) -> LLMResponse:
        self.budget.take()
        return self.inner.complete(system, messages, tools, max_tokens)

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if close:
            close()


@dataclass(frozen=True)
class Estimate:
    provider: str
    calls: int
    input_tokens: int           # upper bound: no cache discount, ~4 chars a token
    output_tokens: int          # upper bound: every call runs to max_tokens
    usd: float | None           # None when no rate is configured


def _rates(env: Mapping[str, str]) -> BudgetConfig:
    def num(name: str) -> float:
        try:
            return float(env.get(name) or 0)
        except ValueError:
            return 0.0

    return BudgetConfig(usd_per_m_input=num("HOI4_USD_PER_M_INPUT"),
                        usd_per_m_output=num("HOI4_USD_PER_M_OUTPUT"))


def estimate(provider: str, env: Mapping[str, str]) -> Estimate:
    spec = SPECS[provider]
    small_tools = registry.tool_specs(set(TOOLS))
    small = len(build_system("minimal")) + len(json.dumps([t.parameters for t in small_tools])) + len(ASK)
    input_tokens = (small // 4) * min(spec.calls, 2)
    if spec.caching:
        full_tools = registry.tool_specs(None)
        full = (len(build_system("doctrine")) + len(json.dumps([t.parameters for t in full_tools]))
                + len(ASK))
        input_tokens += 2 * (full // 4)
    output_tokens = spec.calls * MAX_TOKENS
    rates = _rates(env)
    usd = None
    if rates.usd_per_m_input or rates.usd_per_m_output:
        usd = (input_tokens * rates.usd_per_m_input + output_tokens * rates.usd_per_m_output) / 1e6
    return Estimate(provider, spec.calls, input_tokens, output_tokens, usd)


def render_plan(env: Mapping[str, str], providers: list[str] | None = None) -> str:
    """What a live run is about to spend, printed before the first call."""
    providers = runnable(env) if providers is None else providers
    cap = call_cap(env)
    if not providers:
        return "live provider conformance: nothing will run (no opted-in provider with a key)"
    rows = [estimate(p, env) for p in providers]
    total_calls = sum(r.calls for r in rows)
    lines = [f"live provider conformance: {total_calls} call(s) planned, hard cap {cap} ({CAP_ENV})"]
    for r in rows:
        cost = (f"<= ${r.usd:.4f}" if r.usd is not None
                else "no dollar figure (set HOI4_USD_PER_M_INPUT and HOI4_USD_PER_M_OUTPUT)")
        lines.append(f"  {r.provider}: {r.calls} call(s), <= {r.input_tokens:,} input + "
                     f"<= {r.output_tokens:,} output tokens, {cost}")
    priced = [r.usd for r in rows if r.usd is not None]
    if len(priced) == len(rows):
        lines.append(f"  worst case total: ${sum(priced):.4f} (no cache discount, every call at max_tokens)")
    if total_calls > cap:
        lines.append(f"  WARNING: the cap is below the plan; the calls past {cap} will fail, not run")
    return "\n".join(lines)


# --- the checks: each is one claim, written to fail loudly ----------------------------

def first_call(client: LLMClient) -> LLMResponse:
    system = build_system("minimal")
    tools = registry.tool_specs(set(TOOLS))
    return client.complete(system, [Msg(role="user", text=ASK)], tools)


def check_tool_call_parses(response: LLMResponse) -> None:
    assert response.tool_calls, (
        f"no tool call came back: stop={response.stop_reason} text={response.text!r}")
    call = response.tool_calls[0]
    action = ActionCall(name=call.name, arguments=call.arguments, call_id=call.id)
    assert registry.check(action, set(TOOLS)) is None, f"invalid call: {call}"
    assert call.id, "a tool call needs an id to send its result back"


def check_usage(response: LLMResponse) -> None:
    usage = response.usage
    assert usage.input_tokens > 0 and usage.output_tokens > 0, f"usage not populated: {usage}"
    assert 0 <= usage.cached_input_tokens <= usage.input_tokens
    assert response.model


def check_round_trip(client: LLMClient, first: LLMResponse, *, strict_growth: bool = True) -> LLMResponse:
    """The second round must accept the first round's tool results."""
    check_tool_call_parses(first)
    messages = [
        Msg(role="user", text=ASK),
        Msg(role="assistant", text=first.text, tool_calls=first.tool_calls,
            provider_blocks=first.provider_blocks),
        Msg(role="user", tool_results=[
            ToolResult(call_id=c.id, name=c.name, content='{"ok": true, "message": "Researching."}')
            for c in first.tool_calls
        ]),
    ]
    second = client.complete(build_system("minimal"), messages, registry.tool_specs(set(TOOLS)))
    assert second.stop_reason != "error"
    if strict_growth:
        assert second.usage.input_tokens > first.usage.input_tokens, (
            f"the second round should carry the first ({first.tool_calls[0].name}) in its input")
    return second


def check_prefix_caching(client: LLMClient) -> None:
    system = build_system("doctrine")
    tools = registry.tool_specs(None)
    ask = [Msg(role="user", text=ASK)]
    client.complete(system, ask, tools)
    second = client.complete(system, ask, tools)
    assert second.usage.cached_input_tokens > 0, (
        "the system+tools prefix was marked cacheable and the second identical call read "
        "nothing from cache: either the prefix is below the model's minimum cacheable "
        "length or the cache breakpoints stopped reaching the wire")
