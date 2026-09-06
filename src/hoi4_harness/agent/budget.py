"""Spend tracking and hard ceilings for a run.

A campaign is long and a loop is patient, which is exactly the combination that
produces a surprise bill. The budget is checked before every model call; when it
is exhausted the loop does not stop the game, it drops to the reflex policy and
keeps playing for free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import BudgetConfig
from .llm.base import Usage


@dataclass
class Spend:
    calls: int = 0
    usage: Usage = field(default_factory=Usage)

    @property
    def usd(self) -> float:
        return self._usd

    _usd: float = 0.0


class BudgetGuard:
    def __init__(self, config: BudgetConfig):
        self.config = config
        self.spend = Spend()
        self.stopped_reason: str | None = None

    def record(self, usage: Usage) -> None:
        self.spend.calls += 1
        self.spend.usage = self.spend.usage + usage
        self.spend._usd += (
            usage.input_tokens / 1_000_000 * self.config.usd_per_m_input
            + usage.output_tokens / 1_000_000 * self.config.usd_per_m_output
        )

    def allows_call(self) -> bool:
        return self.why_blocked() is None

    def why_blocked(self) -> str | None:
        c, s = self.config, self.spend
        if s.calls >= c.max_llm_calls:
            return f"call ceiling reached ({c.max_llm_calls})"
        if s.usage.input_tokens >= c.max_input_tokens:
            return f"input-token ceiling reached ({c.max_input_tokens:,})"
        if s.usage.output_tokens >= c.max_output_tokens:
            return f"output-token ceiling reached ({c.max_output_tokens:,})"
        if c.max_usd is not None and s.usd >= c.max_usd:
            return f"spend ceiling reached (${c.max_usd:.2f})"
        return None

    def summary(self) -> dict:
        s = self.spend
        return {
            "llm_calls": s.calls,
            "input_tokens": s.usage.input_tokens,
            "cached_input_tokens": s.usage.cached_input_tokens,
            "output_tokens": s.usage.output_tokens,
            "usd": round(s.usd, 4),
            "blocked": self.why_blocked(),
        }
