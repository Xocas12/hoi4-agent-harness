"""Building a rival run on the fake game with a person in it, for the rival tests.

The rival is SWE (an AI country the harness steers); the person plays GER (``fakerival.py``).
Everything is the real adapters, loop, queue and key guard against ``FakeRivalGame`` with a
scripted provider: no model is called and no key reaches this machine.
"""

from __future__ import annotations

import json

from campaign_helpers import LazyClient, say  # noqa: F401 - re-exported for the tests
from hoi4_harness.adapters import build_adapter
from hoi4_harness.adapters.headless import build_headless
from hoi4_harness.agent.llm.base import Usage  # noqa: F401
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.fakerival import Act
from hoi4_harness.rival.setup import apply_defaults


def rival_config(tmp_path, script=None, **overrides) -> HarnessConfig:
    """A rival config for the fake: SWE steered, GER played, ``script`` the person's acts."""
    path = None
    if script is not None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        path = tmp_path / "human.json"
        path.write_text(json.dumps([[a.at, a.kind, a.arg] if a.arg else [a.at, a.kind]
                                    for a in script]), encoding="utf-8")
    options = dict(
        adapter="headless", fake_game=True, fake_game_dir=tmp_path / "fake", rival="SWE",
        run_dir=tmp_path / "run", dry_run=False, fake_human_script=path, headless_poll_seconds=1.0,
    )
    options.update(overrides)
    config = HarnessConfig(**options)
    config.budget.usd_per_m_input, config.budget.usd_per_m_output = 4.0, 20.0
    config.budget.max_llm_calls = 3000
    config.budget.max_input_tokens, config.budget.max_output_tokens = 300_000_000, 20_000_000
    return apply_defaults(config)


def build_env(config: HarnessConfig, fake=None) -> HOI4Env:
    adapter = build_headless(config, fake=fake) if fake is not None else build_adapter(config)
    return HOI4Env(adapter, config, index=config.build_index())


def acts(*rows) -> list[Act]:
    """``acts((10, "pause"), (30, "resume"))``."""
    return [Act(float(r[0]), r[1], r[2] if len(r) > 2 else "") for r in rows]
