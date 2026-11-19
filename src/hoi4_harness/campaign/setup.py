"""What ``campaign = true`` implies for the rest of the configuration.

A campaign is headless, runs the game's AI under ``operational_control = "game_ai"``
and wakes the model on its own rules, so the wake rules the other modes use for things
the AI now does (a focus to pick, a research slot to fill, a front that went quiet) are
turned off here, in one place, and the reflex layer that would fight the AI is off.
"""

from __future__ import annotations

from ..config import HarnessConfig

OBJECTIVE = (
    "Conquer the world: from a fresh 1936 start, bring as much of it under your flag as you "
    "can by the end of 1948, without losing the country on the way."
)
#: The ``HarnessConfig`` default objective, which a campaign replaces unless the operator
#: wrote their own.
_DEFAULT_OBJECTIVE = HarnessConfig.objective
#: Turns are a backstop for a campaign (the end date is the bound): a month a turn for
#: 13 years is under 200, a week a turn under 700.
BACKSTOP_TURNS = 5000


def apply_defaults(config: HarnessConfig) -> HarnessConfig:
    """Make ``config`` a campaign's. Idempotent; operator settings that are not part of
    what a campaign requires (the model, the budget, the speed, the review intervals) are
    left as they are."""
    config.campaign = True
    config.adapter = "headless"
    config.operational_control = "game_ai"
    config.reflex_enabled = False
    # The AI picks the focus and the research and runs the fronts: none of those is news
    # for the model, and each would wake it for nothing.
    config.wake_on_no_focus = False
    config.wake_on_free_research_slot = False
    config.wake_on_front_quiet = False
    config.wake_on_losing_ground = False
    config.require_confirmation = False
    config.headless_save_days = 0
    if config.guidance == "doctrine":
        config.guidance = "world-conquest"
    if config.objective == _DEFAULT_OBJECTIVE:
        config.objective = OBJECTIVE
    config.turns = max(config.turns, BACKSTOP_TURNS)
    return config
