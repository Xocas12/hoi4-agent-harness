"""Countries that do not exist when the game starts: civil-war breakaways and released nations.

Two kinds, handled differently:

* **A fixed tag that appears later** (a released Slovakia, a puppet set up by a focus): orders for a
  tag that does not exist wait in the inbox (the dispatcher only addresses countries that exist),
  so the rival only has to wait without a deadline: ``--wait-for-country``.
* **A civil-war breakaway** (the Spanish Civil War, any ``start_civil_war``): the game gives the new
  side a dynamic tag, D01 to D50, that nobody can name in advance. ``is_dynamic_country`` and
  ``original_tag`` (triggers_documentation.md) tell which country it broke away from, so the mod's
  daily probe logs every dynamic country with its origin and ruling ideology:

      LLMB|v5|dyn|of=SPR|tag=D01|ideo=Fascism

  ``--breakaway`` makes ``--rival SPR`` mean "the side that breaks away from SPR" (optionally with
  ``--breakaway-ideology``), and :func:`wait_for_breakaway` finds its tag in the log.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .adapters.logtail import parse_line


@dataclass(frozen=True)
class Breakaway:
    tag: str
    origin: str
    ideology: str


def breakaways(text: str) -> list[Breakaway]:
    """Every ``dyn`` line in ``text``, in order (a country can appear several times)."""
    out = []
    for line in text.splitlines():
        if "|dyn|" not in line:
            continue
        try:
            parsed = parse_line(line)
        except ValueError:
            continue
        if not parsed or parsed[0] != "dyn":
            continue
        fields = parsed[1]
        tag, origin = fields.get("tag", "").strip().upper(), fields.get("of", "").strip().upper()
        if len(tag) == 3 and origin:
            out.append(Breakaway(tag, origin, fields.get("ideo", "").strip()))
    return out


def match(found: list[Breakaway], origin: str, ideology: str | None) -> Breakaway | None:
    origin = origin.upper()
    for b in found:
        if b.origin != origin:
            continue
        if ideology and ideology.lower()[:5] not in b.ideology.lower():
            continue
        return b
    return None


def wait_for_breakaway(log_path: Path, origin: str, ideology: str | None, echo,
                       sleep: Callable[[float], None] = time.sleep, poll: float = 10.0,
                       give_up: float | None = None, clock: Callable[[], float] = time.monotonic,
                       alive: Callable[[], bool] = lambda: True) -> Breakaway | None:
    """Watch game.log until a country breaks away from ``origin`` (with ``ideology`` if given).
    None when the game quit or ``give_up`` seconds passed first."""
    want = f" with {ideology} in power" if ideology else ""
    echo(f"waiting for a country to break away from {origin}{want} (a civil war: the mod's "
         "daily probe names it); the game keeps running meanwhile")
    started = clock()
    while True:
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        found = match(breakaways(text), origin, ideology)
        if found is not None:
            echo(f"{found.tag} broke away from {origin} ({found.ideology or 'ideology unknown'}): "
                 f"playing {found.tag}")
            return found
        if not alive() or (give_up is not None and clock() - started >= give_up):
            return None
        sleep(poll)


def probe_lines(tags, schema: int = 5) -> list[str]:
    """The probe's part: one scan of the dynamic countries, one ``if`` per possible origin."""
    out = ["\tevery_country = {", "\t\tlimit = { is_dynamic_country = yes }"]
    for tag in sorted(t for t in tags if len(t) == 3 and t.isalnum() and not t.startswith("D0")):
        out.append(f'\t\tif = {{ limit = {{ original_tag = {tag} }} log = "LLMB|v{schema}|dyn|of={tag}'
                   '|tag=[THIS.GetTag]|ideo=[THIS.GetRulingIdeology]" }')
    out.append("\t}")
    return out
