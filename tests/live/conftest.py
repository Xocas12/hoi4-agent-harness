"""Provider conformance against live keys (#22). Opt-in, capped, and cheap.

Two things must both be true before a paid provider runs: ``HOI4_LIVE_PROVIDER_TESTS=1``
(the older ``HOI4_LIVE_TESTS=1`` also counts) and that provider's key (or, for the
OpenAI-compatible one, ``HOI4_LIVE_OPENAI_BASE_URL`` for a local server). A shell with keys
in it and a bare ``pytest`` is not billed; a fork with no secrets sees skips.

The expected cost is printed before the first call, and ``HOI4_LIVE_MAX_CALLS`` (default 12)
is a hard cap across every provider in the run. The ``scripted`` provider runs the same
checks for free, so the suite always exercises the check code itself.

Models: ``HOI4_LIVE_<PROVIDER>_MODEL``, or the harness default for the provider. The logic
lives in ``hoi4_harness.eval.live_conformance`` where the mocked tests also use it.
"""

from __future__ import annotations

import os

import pytest

from hoi4_harness.agent.llm import build_llm
from hoi4_harness.config import DEFAULT_MODELS, LLMConfig
from hoi4_harness.eval import live_conformance as lc

_state: dict = {}


def _budget() -> lc.CallBudget:
    if "budget" not in _state:
        _state["budget"] = lc.CallBudget(lc.call_cap(os.environ))
    return _state["budget"]


def _announce(config) -> None:
    if _state.get("announced"):
        return
    _state["announced"] = True
    text = lc.render_plan(os.environ)
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(text)
    else:  # pragma: no cover
        print(text)


@pytest.fixture(params=sorted(lc.SPECS))
def live(request):
    """(provider, client) for every provider allowed to run; the rest skip."""
    provider = request.param
    reason = lc.skip_reason(provider, os.environ)
    if reason:
        pytest.skip(reason)
    if lc.SPECS[provider].key_env:
        _announce(request.config)
    if provider == "scripted":
        client = build_llm(LLMConfig(provider="scripted"))
    else:
        model = os.environ.get(f"HOI4_LIVE_{provider.upper()}_MODEL") or DEFAULT_MODELS[provider]
        base_url = os.environ.get(lc.SPECS[provider].base_url_env or "") or None
        # A small output budget and low effort: every check here is one short turn.
        inner = build_llm(LLMConfig(provider=provider, model=model, max_tokens=lc.MAX_TOKENS,
                                    effort="low", base_url=base_url))
        client = lc.CappedClient(inner, _budget())
    yield provider, client
    close = getattr(client, "close", None)
    if close:
        close()


@pytest.fixture
def first_response(live):
    """The first call, made once per provider and shared by the checks that read it."""
    provider, client = live
    key = ("first", provider)
    if key not in _state:
        _state[key] = lc.first_call(client)
    return _state[key]
