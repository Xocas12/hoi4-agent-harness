"""What ``rival = TAG`` implies for the rest of the configuration.

Rival mode is headless and steers an AI country exactly as campaign mode steers the player's
own (the game's AI executes, the model sets directives and postures), with three differences
that are all set here: the person owns the clock (``clock_owner = "player"``, so the model
cannot pause, resume or change the speed, structurally), the reader keeps to the rival's tag
on a log that also holds the person's country, and nothing is typed except in a window the
person's behaviour makes safe (``rival/safety.py``).
"""

from __future__ import annotations

from ..config import HarnessConfig
from .safety import POLICIES

OBJECTIVE = (
    "Make {tag} as strong and as successful as you can, against the person playing and "
    "against everyone else, without losing the country on the way."
)
_DEFAULT_OBJECTIVE = HarnessConfig.objective
#: Turns are a backstop; a run is bounded by ``--until`` or by the person ending the game.
BACKSTOP_TURNS = 5000


class RivalConfigError(ValueError):
    """The configuration cannot be a rival run."""


def apply_defaults(config: HarnessConfig) -> HarnessConfig:
    """Make ``config`` a rival run's. Idempotent."""
    tag = (config.rival or "").strip().upper()
    if len(tag) != 3 or not tag.isalnum():
        raise RivalConfigError(f"--rival wants a country tag (three letters or digits), not {config.rival!r}")
    if config.rival_act_when not in POLICIES:
        raise RivalConfigError(
            f"--rival-act-when {config.rival_act_when!r}; known: {', '.join(POLICIES)}")
    if config.campaign:
        raise RivalConfigError("--rival and --campaign are different modes: the campaign hands "
                               "the player's own country to the AI, rival steers another one")
    if config.advisor or config.handover:
        raise RivalConfigError("--rival has its own queue and policy (--rival-act-when); "
                               "--advisor and --handover do not combine with it")
    config.rival, config.country = tag, tag
    config.adapter = "headless"
    config.operational_control = "game_ai"
    config.clock_owner = "player"          # the person's clock: never paused, resumed or set
    config.reflex_enabled = False
    config.wake_on_no_focus = False
    config.wake_on_free_research_slot = False
    config.wake_on_front_quiet = False
    config.wake_on_losing_ground = False
    config.require_confirmation = False
    # The rival's actions are checked against its telemetry; a text save is a console
    # command typed at a moment nobody chose, and nothing here needs one.
    config.headless_reader = "logtail"
    config.headless_save_days = 0
    if config.guidance == "doctrine":
        config.guidance = "rival"
    if config.objective == _DEFAULT_OBJECTIVE:
        config.objective = OBJECTIVE.format(tag=tag)
    config.turns = max(config.turns, BACKSTOP_TURNS)
    return config
