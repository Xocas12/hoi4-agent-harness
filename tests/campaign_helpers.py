"""Building a campaign on the fake game, for the campaign tests.

A campaign is the real adapters, loop, supervisor and report against ``FakeGame``
(``fakegame.py``): GER, the game's AI under ``human_ai``, a scripted provider. Nothing
here calls a model or sends a key to this machine.
"""

from __future__ import annotations

from hoi4_harness.adapters import build_adapter
from hoi4_harness.adapters.headless import build_headless
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.campaign.setup import apply_defaults
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env


def campaign_config(tmp_path, max_usd: float | None = None, **overrides) -> HarnessConfig:
    """A campaign config for the fake: a scripted planner with a price, GER, 1936."""
    options = dict(
        adapter="headless", fake_game=True, fake_game_dir=tmp_path / "fake", country="GER",
        headless_reader="logtail", run_dir=tmp_path / "run", dry_run=False,
        campaign_until="1936-09-01", campaign_save_months=0,
    )
    options.update(overrides)
    config = HarnessConfig(**options)
    config.budget.usd_per_m_input, config.budget.usd_per_m_output = 4.0, 20.0
    config.budget.max_usd = max_usd
    config.budget.max_llm_calls = 3000
    config.budget.max_input_tokens, config.budget.max_output_tokens = 300_000_000, 20_000_000
    return apply_defaults(config)


def build_env(config: HarnessConfig, fake=None) -> HOI4Env:
    """An env over the fake game (a new one, or ``fake`` to play on after an interruption)."""
    adapter = build_headless(config, fake=fake) if fake is not None else build_adapter(config)
    return HOI4Env(adapter, config, index=config.build_index())


def say(text: str, *calls: tuple[str, dict], usage: Usage | None = None) -> LLMResponse:
    return LLMResponse(
        text=text, stop_reason="tool_use",
        tool_calls=[ToolCall(f"c{i}-{abs(hash((text, name)))}", name, args)
                    for i, (name, args) in enumerate(calls)],
        usage=usage or Usage(1000, 100), model="scripted")


class LazyClient(ScriptedClient):
    """The script, then a long nap each wake: what a model does when nothing needs it."""

    def __init__(self, script=None, nap: int = 60, usage: Usage | None = None):
        super().__init__(script=script)
        self.nap, self.usage = nap, usage

    def _policy(self, messages, tools):
        response = self._respond([self._call("advance_time", days=self.nap, reason="quiet")], "")
        if self.usage is not None:
            response.usage = self.usage
        return response


def invade(target: str, days: int = 30, text: str = "Pointing the AI at it.") -> LLMResponse:
    return say(text,
               ("set_ai_directive", {"directive": "invade", "target": target}),
               ("set_ai_posture", {"posture": "offensive", "theater": target}),
               ("advance_time", {"days": days, "reason": "let it run"}))
