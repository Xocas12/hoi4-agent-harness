"""What ``campaign = true`` implies for the rest of the configuration.

A campaign is headless (an independent one may add the mouse, adapters/hands.py), runs the
game's AI under ``operational_control = "game_ai"`` and wakes the model on its own rules, so
the wake rules the other modes use for things the AI now does (a focus to pick, a research
slot to fill, a front that went quiet) are turned off here, in one place, and the reflex layer
that would fight the AI is off.
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
#: Who plays the country (``HarnessConfig.campaign_control``).
CONTROLS = ("game_ai", "llm")
#: Whether the independent campaign has the mouse (``HarnessConfig.campaign_input``).
INPUTS = ("mouse", "none")
#: The guidance packs an independent campaign picks for itself (a pack the operator named
#: is left alone): the keyboard-only one, and the one that knows about the mouse tools.
GUIDANCE_LLM = "world-conquest-llm"
GUIDANCE_LLM_HANDS = "world-conquest-llm-hands"


def apply_defaults(config: HarnessConfig) -> HarnessConfig:
    """Make ``config`` a campaign's. Idempotent; operator settings that are not part of
    what a campaign requires (the model, the budget, the speed, the review intervals) are
    left as they are."""
    config.campaign = True
    cmap = config.control_map()
    if cmap.explicit:
        # A control map the operator wrote decides who plays: the game's AI owns the country
        # (``human_ai`` is sent) iff some domain but the army is ``game``; otherwise it is the
        # independent kind, where nothing is handed to the AI but possibly armies one by one.
        config.campaign_control = "game_ai" if cmap.human_ai else "llm"
        config.campaign_human_ai = cmap.human_ai
    if config.campaign_control not in CONTROLS:
        raise ValueError(f"campaign_control must be one of {', '.join(CONTROLS)}, "
                         f"not {config.campaign_control!r}")
    if config.campaign_input not in INPUTS:
        raise ValueError(f"campaign_input must be one of {', '.join(INPUTS)}, "
                         f"not {config.campaign_input!r}")
    config.campaign = True
    config.adapter = "headless"
    if config.campaign_control == "llm":
        # Independent mode: nothing runs the country but the model and the reflex layer. The
        # game's AI is not handed the country (no human_ai), so the things it would have done
        # (a focus to pick, a research slot to fill, a production line to keep full) come
        # back as reflexes and as wakes; Policy gates the wakes on the action being offered.
        if not cmap.explicit:
            config.operational_control = "llm"
        config.campaign_human_ai = False
        config.reflex_enabled = True
        config.wake_on_no_focus = True
        config.wake_on_free_research_slot = True
        # The wartime rules (a front losing ground, a front gone quiet) are for a model that
        # can act on a front. With the mouse they are on, and the policy keeps them off again
        # unless an army tool is actually offered (agent/policy.py); without it they stay off.
        config.wake_on_front_quiet = config.hands_mouse
        config.wake_on_losing_ground = config.hands_mouse
        # The clock is the harness's own in a campaign, as in the other mode.
        if "set_game_speed" not in config.disabled_actions:
            config.disabled_actions = [*config.disabled_actions, "set_game_speed"]
    else:
        if not cmap.explicit:
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
    if config.guidance == "doctrine" and not cmap.explicit:
        config.guidance = (
            (GUIDANCE_LLM_HANDS if config.hands_mouse else GUIDANCE_LLM)
            if config.campaign_control == "llm" else "world-conquest")
    if config.objective == _DEFAULT_OBJECTIVE:
        config.objective = OBJECTIVE
    config.turns = max(config.turns, BACKSTOP_TURNS)
    return config
