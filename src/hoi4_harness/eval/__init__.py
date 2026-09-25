"""Scenarios, scoring, baselines, and a runner that joins them."""

from .baselines import BASELINE_PATH, attach_baseline, for_scenario, load, write_baselines
from .campaigns import CAMPAIGNS
from .comparison import Comparison, ModelResult, compare
from .measure import Measurement, measure
from .metrics import ScoreCard, score
from .runner import run_scenario
from .scenarios import SCENARIOS, Objective, Scenario

__all__ = [
    "BASELINE_PATH",
    "CAMPAIGNS",
    "SCENARIOS",
    "Comparison",
    "Measurement",
    "ModelResult",
    "Objective",
    "Scenario",
    "ScoreCard",
    "attach_baseline",
    "compare",
    "for_scenario",
    "load",
    "measure",
    "run_scenario",
    "score",
    "write_baselines",
]
