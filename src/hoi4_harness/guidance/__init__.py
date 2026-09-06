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

Any path to a text file works too, so a project can ship its own doctrine:

    hoi4-harness play --guidance ./my-doctrine.md
"""

from __future__ import annotations

from pathlib import Path

BUILTIN_DIR = Path(__file__).parent
BUILTIN = ("none", "unconstrained", "minimal", "doctrine", "historical", "coach")


def available() -> list[str]:
    return sorted(p.stem for p in BUILTIN_DIR.glob("*.md"))


def load(name_or_path: str | Path | None) -> str:
    """Return guidance text. ``None`` or empty means no guidance at all."""
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


def _read(path: Path) -> str:
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("<!--")
    ]
    return "\n".join(lines).strip()
