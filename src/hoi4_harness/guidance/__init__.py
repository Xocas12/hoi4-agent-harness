"""Guidance packs: how much the harness tells the model about playing HOI4.

The mechanics half of the system prompt (what the tools are, how turns work) is
fixed -- without it nothing functions. The *strategy* half is swappable, and that
is the whole point: how much coaching a model needs, or should be allowed, is an
experimental variable, not a constant.

Built-in packs:

    none           say nothing about how to play
    unconstrained  play to win by any in-game means, ahistorical welcome
    minimal        three mechanics facts, no strategy
    doctrine       opinionated general advice (default)
    historical     stay close to what the country actually did
    coach          a prescriptive checklist, for weaker or smaller models
    world-conquest the unattended campaign: the game's AI executes, the model steers
    rival          rival mode: the game's AI runs a country a person is not playing
    strategy       how 1.20 works and what plays well now (a knowledge sheet, not a
                   role): combine it with another, ``rival+strategy``

Packs combine with ``+``, in order. Any path to a text file works too, so a project
can ship its own doctrine:

    hoi4-harness play --guidance ./my-doctrine.md

``strategy`` is also the Claude Code skill ``.claude/skills/hoi4-strategy/SKILL.md``,
generated from the same file (``skill_text``) so the two cannot drift.
"""

from __future__ import annotations

from pathlib import Path

BUILTIN_DIR = Path(__file__).parent
BUILTIN = ("none", "unconstrained", "minimal", "doctrine", "historical", "coach", "world-conquest", "rival",
           "strategy")
SKILL_PATH = Path(".claude") / "skills" / "hoi4-strategy" / "SKILL.md"
_SKILL_DESCRIPTION = (
    "How Hearts of Iron IV works and how to play a country well, in vanilla or any mod: the engine "
    "dynamics (organization and strength, combat width, supply, production efficiency, research, "
    "political power), judging a country and choosing a style, reading a mod's decisions, custom "
    "windows and balance of power bars; then vanilla 1.20's exact numbers, the 1.17-1.20 changes "
    "and a do-not list. Use before steering or playing a HOI4 country with the harness (rival, "
    "campaign, mailbox player), or when judging a HOI4 strategy."
)
SKILL_FRONTMATTER = (
    f"---\nname: hoi4-strategy\ndescription: {_SKILL_DESCRIPTION}\n---\n\n"
    "<!-- GENERATED from src/hoi4_harness/guidance/strategy.md by hoi4_harness.guidance.skill_text. "
    "Edit that file, then run: python -m hoi4_harness.guidance --write-skill -->\n"
)


def available() -> list[str]:
    return sorted(p.stem for p in BUILTIN_DIR.glob("*.md"))


def load(name_or_path: str | Path | None) -> str:
    """Return guidance text. ``None`` or empty means no guidance at all; ``a+b`` is both."""
    if isinstance(name_or_path, str) and "+" in name_or_path and not Path(name_or_path).exists():
        return "\n\n".join(text for part in name_or_path.split("+") if (text := load(part.strip())))
    if name_or_path in (None, "", "none"):
        if name_or_path == "none":
            return _read(BUILTIN_DIR / "none.md")
        return ""

    candidate = Path(name_or_path)
    if candidate.exists():
        return _read(candidate)

    builtin = BUILTIN_DIR / f"{name_or_path}.md"
    if builtin.exists():
        return _read(builtin)

    raise FileNotFoundError(
        f"No guidance pack {name_or_path!r}. Built-ins: {', '.join(available())}; "
        "or pass a path to a text file."
    )


def skill_text() -> str:
    """The Claude Code skill: the strategy sheet behind the skill's frontmatter."""
    return SKILL_FRONTMATTER + "\n" + _read(BUILTIN_DIR / "strategy.md") + "\n"


def write_skill(root: Path) -> Path:
    target = Path(root) / SKILL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(skill_text(), encoding="utf-8", newline="\n")
    return target


def _read(path: Path) -> str:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("<!--")
    ]
    return "\n".join(lines).strip()
