"""Scoring a run.

Two numbers matter and they are reported separately on purpose: how well the
agent played, and what it cost to play that way. An agent that scores 0.9 for
four hundred model calls has not beaten one that scores 0.8 for forty.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..agent.loop import RunReport
from ..types import GameState
from .scenarios import Scenario


@dataclass
class ScoreCard:
    scenario: str
    score: float = 0.0
    objectives: dict[str, bool] = field(default_factory=dict)
    turns: int = 0
    planner_calls: int = 0
    reflex_turns: int = 0
    actions_ok: int = 0
    actions_failed: int = 0
    invalid_rate: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0

    def render(self) -> str:
        lines = [f"{self.scenario}: score {self.score:.2f}"]
        for name, passed in self.objectives.items():
            lines.append(f"  [{'x' if passed else ' '}] {name}")
        lines.append(
            f"  {self.turns} turns, {self.planner_calls} model calls "
            f"({self.reflex_turns} handled by reflex)"
        )
        lines.append(
            f"  actions: {self.actions_ok} ok / {self.actions_failed} rejected "
            f"({self.invalid_rate:.0%} invalid)"
        )
        lines.append(
            f"  tokens: {self.tokens_in:,} in / {self.tokens_out:,} out"
            + (f", ${self.usd:.2f}" if self.usd else "")
        )
        return "\n".join(lines)


def score(scenario: Scenario, final_state: GameState, report: RunReport) -> ScoreCard:
    results = {obj.name: bool(obj.check(final_state)) for obj in scenario.objectives}
    total_weight = sum(obj.weight for obj in scenario.objectives) or 1.0
    earned = sum(obj.weight for obj in scenario.objectives if results[obj.name])
    attempted = report.actions_ok + report.actions_failed
    spend = report.spend or {}
    return ScoreCard(
        scenario=scenario.key,
        score=earned / total_weight,
        objectives=results,
        turns=report.turns,
        planner_calls=report.planner_calls,
        reflex_turns=report.reflex_turns,
        actions_ok=report.actions_ok,
        actions_failed=report.actions_failed,
        invalid_rate=(report.actions_failed / attempted) if attempted else 0.0,
        tokens_in=spend.get("input_tokens", 0),
        tokens_out=spend.get("output_tokens", 0),
        usd=spend.get("usd", 0.0),
    )
