"""Which AI the game runs underneath the harness: vanilla HOI4, or an AI mod (docs/expert-ai.md).

Everything the game's AI does for the harness (the whole country under ``human_ai``, a delegated
army, every opponent) is that AI. Expert AI (Steam Workshop 741805475) replaces much of the
vanilla AI's economy, templates and country plans by script. The harness cannot bundle it (its
repository has no licence), so it is installed from the Workshop and found here through the
launcher's ``dlc_load.json``.

Two things follow when Expert AI is the base AI:

* its own country plans weigh 500 to 5000 (``antagonize POL 5000`` for Germany), so the mod's
  directives at 150 would be drowned out. ``base_ai = expert_ai`` makes the campaign send
  ``effect <TAG> llm_bridge_strong_directives``, which turns on the ``*_strong`` blocks
  (``modgen.STRONG_BOOST``);
* its scripts run only for countries where ``is_ai = yes``: under ``human_ai`` that includes the
  player's own country, without it (the independent mode) only the opponents.

Its difficulty bonuses (free equipment, attrition and supply help, extra manpower) are off unless
someone turns them on in its in-game configuration menu (``EAI_difficulty_bonus`` is set only by
``EAI_country_config_scripted_gui.txt``). Keep them off: a better result bought with bonuses says
nothing about the AI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

EXPERT_AI = "741805475"
#: Workshop AI mods worth naming in a check: id -> what they are called.
AI_MODS: dict[str, str] = {
    EXPERT_AI: "Expert AI",
    "3369706283": "Better AI",
    "3402941961": "Smarter Ai",
}
BASE_AIS = ("auto", "vanilla", "expert_ai")


@dataclass(frozen=True)
class ModEntry:
    """One enabled mod from ``dlc_load.json`` with what its ``.mod`` descriptor says."""

    entry: str                      # "mod/ugc_741805475.mod"
    workshop_id: str | None
    name: str
    supported_version: str


def enabled_mods(user_dir: Path | None) -> list[str] | None:
    """``enabled_mods`` from the launcher's ``dlc_load.json``; None when it cannot be read."""
    if user_dir is None:
        return None
    try:
        data = json.loads((Path(user_dir) / "dlc_load.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mods = data.get("enabled_mods") if isinstance(data, dict) else None
    return [m for m in mods if isinstance(m, str)] if isinstance(mods, list) else None


def _field(text: str, key: str) -> str:
    found = re.search(rf'^\s*{key}\s*=\s*"([^"]*)"', text, re.M)
    return found.group(1) if found else ""


def describe(user_dir: Path, entry: str) -> ModEntry:
    """Read one enabled mod's descriptor (the ``.mod`` file the entry names)."""
    found = re.search(r"ugc_(\d+)\.mod$", entry)
    workshop_id = found.group(1) if found else None
    try:
        text = (Path(user_dir) / entry).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    remote = _field(text, "remote_file_id")
    return ModEntry(entry, remote or workshop_id, _field(text, "name") or entry,
                    _field(text, "supported_version"))


def ai_mods(user_dir: Path | None) -> list[ModEntry] | None:
    """The enabled mods that are known AI mods, in load order; None when unreadable."""
    mods = enabled_mods(user_dir)
    if mods is None:
        return None
    out = []
    for entry in mods:
        mod = describe(user_dir, entry)
        if mod.workshop_id in AI_MODS:
            out.append(mod)
    return out


def detect(user_dir: Path | None) -> str | None:
    """``expert_ai`` or ``vanilla`` from what is enabled; None when the playset is unknown."""
    found = ai_mods(user_dir)
    if found is None:
        return None
    return "expert_ai" if any(m.workshop_id == EXPERT_AI for m in found) else "vanilla"


def resolve(setting: str, user_dir: Path | None) -> tuple[str, str]:
    """``(base_ai, why)`` for a ``base_ai`` setting of ``auto``, ``vanilla`` or ``expert_ai``.

    ``auto`` reads the playset and falls back to ``vanilla`` when it cannot (the directives
    then keep their normal weights, which is what they have always had)."""
    if setting not in BASE_AIS:
        raise ValueError(f"base_ai must be one of {', '.join(BASE_AIS)}, not {setting!r}")
    if setting != "auto":
        return setting, f"base_ai = {setting} in the profile"
    found = detect(user_dir)
    if found is None:
        return "vanilla", "base_ai = auto and dlc_load.json could not be read: assuming vanilla"
    if found == "expert_ai":
        return found, "base_ai = auto: Expert AI (Workshop 741805475) is enabled in dlc_load.json"
    return found, "base_ai = auto: no Expert AI in dlc_load.json"


def game_version(game_dir: Path | None) -> str | None:
    """The install's version (``rawVersion`` in ``launcher-settings.json``), e.g. ``1.20.0.0``."""
    if game_dir is None:
        return None
    try:
        data = json.loads((Path(game_dir) / "launcher-settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = data.get("rawVersion") if isinstance(data, dict) else None
    return raw if isinstance(raw, str) else None


def same_minor(a: str, b: str) -> bool:
    """Whether two versions agree on major.minor (``1.19.2.0`` vs ``1.20.0.0`` do not).
    A ``*`` in the mod's version matches anything from there on."""
    pa, pb = a.split("."), b.split(".")
    for x, y in zip(pa[:2], pb[:2], strict=False):
        if "*" in (x, y):
            return True
        if x != y:
            return False
    return len(pa) >= 2 and len(pb) >= 2
