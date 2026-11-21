"""Skill packs: guidance that players and modders write themselves.

A skill is a folder holding ``SKILL.md`` in the Claude Code format: a frontmatter
block with ``name`` and ``description``, then markdown. The same file works as a
Claude Code skill. The harness reads three extra, optional frontmatter keys:

    mods:    [Kaiserreich, 1521695605]   mod names (case-insensitive) or Steam workshop ids
    tags:    [GER, FRA]                  country tags
    always:  true                        include in every run

Where skills are looked for, first match wins for a repeated name:

1. ``--skills-dir`` / ``skills_dirs`` in the config
2. ``~/.hoi4-harness/skills/`` (the home is ``HOI4_HARNESS_HOME`` when that is set)
3. the repository's ``.claude/skills/`` (the shipped ``hoi4-strategy`` lives here)
4. ``harness_skills/`` inside each active mod, so a mod can ship its own

A skill goes into the strategist's system prompt when it is ``always``, when a mod it
lists is in the playset, when the country's tag is listed, or when it is named with
``--skill``. A skill with none of those keys is a Claude Code skill only, unless named.
The total text is capped by a token budget; the prompt says what was left out.
See ``docs/skills.md``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BUDGET_TOKENS = 3000
MOD_SKILLS_DIR = "harness_skills"
SKILL_FILE = "SKILL.md"
#: The guidance pack that is also the shipped Claude Code skill of this name.
STRATEGY_SKILL = "hoi4-strategy"
STRATEGY_PACK = "strategy"


class SkillError(ValueError):
    """A skill was asked for by name and none exists, or a SKILL.md cannot be used."""


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    source: str                       # where it was found: "config", "home", "repo" or "mod:<name>"
    mods: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    always: bool = False

    @property
    def has_harness_keys(self) -> bool:
        return bool(self.always or self.mods or self.tags)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.body)


def estimate_tokens(text: str) -> int:
    return (len(text) + 3) // 4


# --- parsing -----------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """``(keys, body)`` of a SKILL.md. Understands what skills use: ``key: value``,
    quoted values, ``[a, b]`` and ``a, b`` lists, ``- item`` block lists and indented
    continuation lines. Text without a frontmatter block is all body."""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text.strip()
    keys: dict[str, object] = {}
    current: str | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s*-\s+(.*)$", line)
        if item and current is not None:
            value = keys.get(current)
            keys[current] = (value if isinstance(value, list) else
                             ([value] if value else [])) + [_unquote(item.group(1))]
            continue
        match = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if match:
            current = match.group(1).lower()
            raw = match.group(2).strip()
            keys[current] = _list(raw) if raw.startswith("[") else _unquote(raw)
        elif current is not None and isinstance(keys.get(current), str):
            keys[current] = (str(keys[current]) + " " + line.strip()).strip()
    return keys, "\n".join(lines[end + 1:]).strip()


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _list(raw: str) -> list[str]:
    inner = raw.strip()[1:].rstrip("] ")
    parts = re.findall(r"""\s*(?:"([^"]*)"|'([^']*)'|([^,]*))\s*(?:,|$)""", inner)
    return [next(g for g in groups if g) for groups in parts if any(groups)]


def _as_list(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "yes", "on", "1"}


def parse_skill(path: Path, source: str = "config") -> Skill:
    path = Path(path)
    keys, body = split_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    name = str(keys.get("name") or path.parent.name).strip()
    return Skill(
        name=name,
        description=str(keys.get("description") or "").strip(),
        body=body,
        path=path,
        source=source,
        mods=_as_list(keys.get("mods")),
        tags=[t.upper() for t in _as_list(keys.get("tags"))],
        always=_as_bool(keys.get("always", False)),
    )


# --- discovery ---------------------------------------------------------------


def harness_home() -> Path:
    env = os.environ.get("HOI4_HARNESS_HOME", "").strip()
    return Path(env) if env else Path.home() / ".hoi4-harness"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def search_paths(skills_dirs=(), mods=(), repo: Path | None = None) -> list[tuple[str, Path]]:
    """``(source, folder)`` pairs in search order."""
    found: list[tuple[str, Path]] = [("config", Path(d)) for d in skills_dirs or ()]
    found.append(("home", harness_home() / "skills"))
    found.append(("repo", (repo if repo is not None else repo_root()) / ".claude" / "skills"))
    for mod in mods or ():
        found.append((f"mod:{mod.name}", Path(mod.path) / MOD_SKILLS_DIR))
    return found


def discover(skills_dirs=(), mods=(), repo: Path | None = None) -> list[Skill]:
    """Every skill on the search paths. A name found twice keeps its first
    (highest-priority) copy. Folders that do not exist are skipped; an unreadable
    SKILL.md is skipped rather than stopping a run."""
    skills: dict[str, Skill] = {}
    for source, folder in search_paths(skills_dirs, mods, repo):
        if not folder.is_dir():
            continue
        # The folder itself may be a skill (a --skills-dir pointing at one), or hold skills.
        candidates = [folder / SKILL_FILE] if (folder / SKILL_FILE).is_file() else []
        candidates += [child / SKILL_FILE for child in sorted(folder.iterdir())
                       if child.is_dir() and (child / SKILL_FILE).is_file()]
        for path in candidates:
            try:
                skill = parse_skill(path, source)
            except (OSError, UnicodeError):
                continue
            skills.setdefault(skill.name.lower(), skill)
    return sorted(skills.values(), key=lambda s: s.name.lower())


# --- selection ---------------------------------------------------------------


def _mod_matches(wanted: str, mod) -> bool:
    wanted = wanted.strip().lower()
    remote = (getattr(mod, "remote_file_id", None) or "").strip().lower()
    return (wanted == mod.name.strip().lower()
            or (bool(remote) and wanted == remote)
            or (wanted.isdigit() and Path(mod.path).name.lower() == wanted))


def why(skill: Skill, mods=(), tag: str | None = None, explicit=()) -> str | None:
    """The reason a skill applies to this run, or None."""
    if skill.name.lower() in {e.lower() for e in explicit}:
        return "named"
    if skill.always:
        return "always"
    for wanted in skill.mods:
        for mod in mods or ():
            if _mod_matches(wanted, mod):
                return f"mod {mod.name}"
    if tag and tag.upper() in skill.tags:
        return f"tag {tag.upper()}"
    return None


_ORDER = {"named": 0, "always": 1}


def select(skills, mods=(), tag: str | None = None, explicit=()) -> list[tuple[Skill, str]]:
    """The applicable skills with the reason, in a fixed order: named ones first, then
    always, mod and tag matches, each by name. Raises ``SkillError`` for a name that
    matches no skill."""
    known = {s.name.lower() for s in skills}
    missing = [e for e in explicit if e.lower() not in known]
    if missing:
        raise SkillError(
            f"No skill named {', '.join(map(repr, missing))}. Found: "
            f"{', '.join(sorted(known)) or 'none'} (hoi4-harness skills list)")
    chosen = []
    for skill in skills:
        reason = why(skill, mods, tag, explicit)
        if reason:
            chosen.append((skill, reason))
    chosen.sort(key=lambda pair: (_ORDER.get(pair[1], 2), pair[0].name.lower()))
    return chosen


@dataclass
class Selection:
    included: list[Skill] = field(default_factory=list)
    left_out: list[Skill] = field(default_factory=list)
    duplicates: list[Skill] = field(default_factory=list)   # skipped: guidance already has it

    def prompt(self) -> str:
        if not (self.included or self.left_out):
            return ""
        head = ("Skills for this run: notes written by the player or the mods in this playset, "
                "not by the harness. Use them as background and trust the situation reports "
                "over them where they disagree. ")
        head += "Included: " + (", ".join(s.name for s in self.included) or "none") + "."
        if self.left_out:
            head += " Left out for space: " + ", ".join(s.name for s in self.left_out) + "."
        parts = [head]
        for skill in self.included:
            parts.append(f"## Skill: {skill.name}\n\n{skill.body}")
        return "\n\n".join(parts)


def fit(chosen, budget_tokens: int | None = DEFAULT_BUDGET_TOKENS,
        guidance: str | None = None) -> Selection:
    """Apply the budget to ``select``'s result, in order. A skill that does not fit is
    left out and the next one still gets its chance. The strategy skill is skipped when
    the ``strategy`` guidance pack is already part of the prompt."""
    has_strategy = STRATEGY_PACK in [p.strip() for p in str(guidance or "").split("+")]
    out = Selection()
    used = 0
    for skill, _reason in chosen:
        if has_strategy and skill.name.lower() == STRATEGY_SKILL:
            out.duplicates.append(skill)
            continue
        cost = skill.tokens
        if budget_tokens is not None and used + cost > budget_tokens:
            out.left_out.append(skill)
            continue
        used += cost
        out.included.append(skill)
    return out


def skills_section(config, index=None, repo: Path | None = None) -> str:
    """The text ``system_for`` adds to the prompt for this config; empty when no skill applies."""
    mods = index.playset.mods if index is not None else []
    skills = discover(getattr(config, "skills_dirs", ()) or (), mods, repo)
    explicit = list(getattr(config, "skills", ()) or ())
    chosen = select(skills, mods, getattr(config, "country", None), explicit)
    budget = getattr(config, "skill_tokens", DEFAULT_BUDGET_TOKENS)
    return fit(chosen, budget, getattr(config, "guidance", None)).prompt()


# --- scaffold ----------------------------------------------------------------

TEMPLATE = """---
name: {name}
description: {description}
{keys}---

# {name}

<!-- Written for the strategist model and for Claude Code. Replace every <...> with
facts you have checked in the mod or the game. Delete what you cannot fill in: a
wrong fact does more harm than a missing one. -->

## What {subject} changes

<What this mod or play style changes compared to the base game, in a few lines.>

## Key mechanics and decisions

- <Mechanic or decision, what it does, when it is worth taking.>

## Custom windows worth pressing

- <Window name, and which button does what. The harness lists buttons with list_gui_buttons.>

## Balance of power bars

- <Bar name, what pushes it each way, which decision category moves it.>

## What plays well

- <Opening, priorities, a sequence that works.>

## What not to do

- <A trap, a decision that backfires, a mistake the AI makes.>
"""


def slug(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not cleaned:
        raise SkillError(f"{name!r} cannot be made into a skill name")
    return cleaned


def scaffold(name: str, directory: Path, mods=(), tags=()) -> Path:
    """Write ``<directory>/<name>/SKILL.md`` from the template and return its path."""
    folder = Path(directory) / slug(name)
    target = folder / SKILL_FILE
    if target.exists():
        raise SkillError(f"{target} already exists; edit it or pick another name")
    keys = ""
    if mods:
        keys += "mods: [" + ", ".join(_quote(m) for m in mods) + "]\n"
    if tags:
        keys += "tags: [" + ", ".join(t.upper() for t in tags) + "]\n"
    subject = " and ".join(mods) if mods else "this play style"
    description = (f"Notes on playing {subject} in Hearts of Iron IV. Use when steering or playing "
                   f"with {subject}.")
    folder.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE.format(name=slug(name), description=description, keys=keys,
                                      subject=subject), encoding="utf-8", newline="\n")
    return target


def _quote(value: str) -> str:
    return f'"{value}"' if re.search(r"[,\[\]:#]", value) else value
