"""What ``rival = TAG`` implies for the rest of the configuration.

Rival mode is headless and steers an AI country exactly as campaign mode steers the player's
own (the game's AI executes, the model sets directives and postures), with three differences
that are all set here: the person owns the clock (``clock_owner = "player"``, so the model
cannot pause, resume or change the speed, structurally), the reader keeps to the rival's tag
on a log that also holds the person's country, and nothing is typed except in a window the
person's behaviour makes safe (``rival/safety.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import HarnessConfig
from .safety import POLICIES

#: ``--rival majors``: the seven great powers of 1936, minus whoever the person plays.
MAJORS = ("GER", "ENG", "FRA", "ITA", "SOV", "USA", "JAP")
_STATE_LINE = re.compile(r"LLMB\|v\d+\|state\|.*?\btag=(?P<tag>[A-Za-z0-9]{3})\b.*?\bai=0\b")
#: How much of the end of game.log is read to find the person's country.
_LOG_TAIL = 1_000_000

OBJECTIVE = (
    "Make {tag} as strong and as successful as you can, against the person playing and "
    "against everyone else, without losing the country on the way."
)
_DEFAULT_OBJECTIVE = HarnessConfig.objective
#: Turns are a backstop; a run is bounded by ``--until`` or by the person ending the game.
BACKSTOP_TURNS = 5000


class RivalConfigError(ValueError):
    """The configuration cannot be a rival run."""


def human_tags_in_log(log_path: Path | None) -> list[str]:
    """The countries game.log shows as human (``ai=0`` state lines: the mod always logs the
    person's country), in order of first appearance. Empty when the log says nothing yet."""
    try:
        path = Path(log_path) if log_path else None
        if path is None or not path.is_file():
            return []
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - _LOG_TAIL))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    seen: list[str] = []
    for line in text.splitlines():
        match = _STATE_LINE.search(line)
        if match and match.group("tag").upper() not in seen:
            seen.append(match.group("tag").upper())
    return seen


def parse_rivals(spec: str, human: list[str] | tuple[str, ...] = ()) -> list[str]:
    """``FRA,ENG,SOV`` (or one tag, or ``majors``) -> the tags, checked.

    Refuses a malformed tag, a duplicate and the person's own country (``human``: what the log
    says they play). ``majors`` drops the person's country instead of refusing it, and needs
    to know it: with nobody known it refuses, because the person's country would be steered."""
    parts = [p.strip().upper() for p in re.split(r"[,\s]+", spec or "") if p.strip()]
    if not parts:
        raise RivalConfigError("--rival wants a country tag, a comma list (FRA,ENG,SOV) or 'majors'")
    mine = {h.upper() for h in human}
    tags: list[str] = []
    for part in parts:
        if part == "MAJORS":
            if not mine:
                raise RivalConfigError(
                    "--rival majors needs to know which country you play, and game.log does not "
                    "say yet (load the game with the mod on, then start), or list the tags")
            tags += [t for t in MAJORS if t not in mine]
        else:
            tags.append(part)
    for tag in tags:
        if len(tag) != 3 or not tag.isalnum():
            raise RivalConfigError(f"--rival wants country tags (three letters or digits), "
                                   f"not {tag!r}")
    dupes = sorted({t for t in tags if tags.count(t) > 1})
    if dupes:
        raise RivalConfigError(f"--rival lists {', '.join(dupes)} more than once")
    ours = [t for t in tags if t in mine]
    if ours:
        raise RivalConfigError(f"{', '.join(ours)} is the country you play (game.log shows it "
                               "as human): it cannot be a rival")
    return tags


def known_human(config: HarnessConfig) -> list[str]:
    """The person's country as far as it can be told before the game is read: the fake
    game's, or the ``ai=0`` countries in game.log."""
    if config.fake_game:
        return [config.fake_rival_human.upper()]
    from ..adapters.logtail import find_log

    return human_tags_in_log(find_log(config.log_path))


def _several(config: HarnessConfig) -> list[str] | None:
    """The tags of a several-country run (idempotent), None for the one-country form."""
    if len(config.rivals) > 1:
        return config.rivals
    spec = (config.rival or "").strip()
    if "," not in spec and " " not in spec and spec.upper() != "MAJORS":
        return None
    return parse_rivals(spec, known_human(config))


def share_weights(config: HarnessConfig, tags: list[str]) -> dict[str, float]:
    """Each rival's weight for splitting ``--max-usd`` (default 1 each: an equal split)."""
    unknown = sorted(set(config.rival_shares) - set(tags))
    if unknown:
        raise RivalConfigError(f"--rival-share names {', '.join(unknown)}, which is not a rival "
                               f"of this run ({', '.join(tags)})")
    weights = {t: float(config.rival_shares.get(t, 1.0)) for t in tags}
    if any(w <= 0 for w in weights.values()):
        raise RivalConfigError("--rival-share weights must be above zero")
    return weights


def apply_defaults(config: HarnessConfig) -> HarnessConfig:
    """Make ``config`` a rival run's. Idempotent."""
    tags = _several(config)
    if tags is not None and len(tags) > 1:
        return _apply_several(config, tags)
    if tags:
        config.rival = tags[0]          # `majors` that came to one country, or `FRA,`
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
        config.guidance = "rival+strategy"
    if config.objective == _DEFAULT_OBJECTIVE:
        config.objective = OBJECTIVE.format(tag=tag)
    config.turns = max(config.turns, BACKSTOP_TURNS)
    return config


def _apply_several(config: HarnessConfig, tags: list[str]) -> HarnessConfig:
    """The several-country form: the shared settings, checked once. Each country's own
    configuration is made from a copy of this one (rival/multi.py) through the one-country
    ``apply_defaults``, so a rival is configured the same way alone or with others."""
    import dataclasses

    config.rivals = list(tags)
    config.rival = ",".join(tags)
    share_weights(config, tags)
    if config.rival_window_cap < len(tags):
        raise RivalConfigError(f"--rival-window-cap {config.rival_window_cap} is below the "
                               f"{len(tags)} countries: a window must fit one action each")
    # What the one-country form refuses (a bad act-when, --campaign, --advisor) is refused here.
    apply_defaults(dataclasses.replace(config, rival=tags[0], rivals=[]))
    config.country = tags[0]
    config.adapter = "headless"
    config.clock_owner = "player"
    config.headless_reader = "logtail"
    config.headless_save_days = 0
    config.require_confirmation = False
    config.turns = max(config.turns, BACKSTOP_TURNS)
    return config
