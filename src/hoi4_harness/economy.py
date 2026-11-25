"""Direct control of the economy, at a human's level (issue #137, economy part).

A player runs the country's desk work by hand: picks the research, edits the production lines,
queues the buildings. ``control = "economy"`` (control.py ``ALIASES``) gives the model exactly that
and nothing else: research, production and construction (with the focus, politics and diplomacy a
player does between them) are the model's, the game's AI owns nothing, and the armies are left to
whoever the operator names (``economy,army=script``). The army half of #137 is not here.

What this module adds is the honest account of HOW each part is reached in a given run, because a
tool list alone hides it: a research pick is a click on the tech tree (needs the mouse and the
game window in front), a production edit is a click on the production screen (needs recorded
points), and construction goes through the console. Where the way in is missing the model is told
so, and the start log says it, instead of the prompt promising a human-level economy the run
cannot deliver.

Built against the simulators only; nothing here has been run against the real game
(docs/modular-control.md, "Direct economy").
"""

from __future__ import annotations

from dataclasses import dataclass

from .control import DOMAIN_ACTIONS, ECONOMY, ECONOMY_DOMAINS, ControlMap

MOUSE, CONSOLE, MISSING = "mouse", "console", "missing"


@dataclass(frozen=True)
class Channel:
    """How one economy action reaches the game in this run."""

    domain: str
    action: str
    route: str          # mouse | console | missing
    note: str = ""

    def line(self) -> str:
        return f"{self.domain}: {self.action} by {self.route}" + (f" ({self.note})" if self.note else "")


def is_economy(cmap: ControlMap) -> bool:
    return cmap.alias == ECONOMY


def channels(cmap: ControlMap, offered, hands: dict | None = None) -> list[Channel]:
    """Every economy action the map gives the model, with the way it reaches the game.

    ``offered`` is the run's tool list; ``hands`` is ``HarnessConfig.campaign_hands`` (what the
    mouse tools and production click path are live). An action the map gives the model but the
    run does not offer is a ``missing`` channel, with the reason where one is known."""
    offered = set(offered)
    hands = hands or {}
    production = hands.get("production") or {}
    refused = hands.get("refused") or {}
    out: list[Channel] = []
    for domain in ECONOMY_DOMAINS:
        if cmap.controller(domain) != "llm":
            continue
        for action in sorted(DOMAIN_ACTIONS[domain]):
            if action == "add_production_line":
                continue            # the contested experiment's verb (#161), not an economy one
            if action not in offered:
                out.append(Channel(domain, action, MISSING,
                                   refused.get(action) or "not in this run's tool list"))
            elif action == "start_research":
                out.append(Channel(domain, action, MOUSE, "clicks the tech tree"))
            elif action == "set_production":
                if production.get("edit"):
                    out.append(Channel(domain, action, MOUSE,
                                       "edits an existing line by click, opens one by console"))
                else:
                    out.append(Channel(
                        domain, action, CONSOLE,
                        "opens a line for equipment that has none; an existing line cannot be "
                        "edited without the production points"))
            elif action == "change_production_equipment":
                out.append(Channel(domain, action, MOUSE, "changes a line's equipment by click"))
            else:
                out.append(Channel(domain, action, CONSOLE))
    return out


def lines(cmap: ControlMap, offered, hands: dict | None = None) -> list[str]:
    """The start-log lines: one per domain, then what is missing for a human-level economy."""
    if not is_economy(cmap):
        return []
    chans = channels(cmap, offered, hands)
    out = ["economy mode: the model runs research, production and construction by hand; the "
           "game's AI owns nothing and nothing here has been run against the real game"]
    for domain in ECONOMY_DOMAINS:
        mine = [c for c in chans if c.domain == domain]
        if not mine:
            out.append(f"economy {domain}: not the model's in this map")
            continue
        got = ", ".join(f"{c.action} ({c.route})" for c in mine if c.route != MISSING)
        out.append(f"economy {domain}: {got or 'nothing offered'}")
    for c in chans:
        if c.route == MISSING and c.action in ("start_research", "set_production"):
            out.append(f"economy: {c.line()}")
    for domain in ECONOMY_DOMAINS:
        if cmap.controller(domain) == "llm" and not any(
                c.domain == domain and c.route != MISSING for c in chans):
            out.append(f"economy WARNING: {domain} is the model's but no tool for it is offered, so "
                       "it stays empty")
    return out


def describe(cmap: ControlMap, offered, hands: dict | None = None) -> str:
    """The prompt paragraph: what the model runs by hand and how each part reaches the game."""
    if not is_economy(cmap):
        return ""
    chans = channels(cmap, offered, hands)
    by_action = {c.action: c for c in chans}
    parts = ["You run the economy by hand, as a player does, and the game's own AI runs none of it: "
             "what you leave empty stays empty."]
    research = by_action.get("start_research")
    if research is not None and research.route == MOUSE:
        parts.append("Research: start_research picks a technology into a free slot by clicking "
                     "the tech tree, so it runs only while the game window is in front. Pick for "
                     "the slots yourself; you are woken when one goes idle.")
    elif "research" in cmap.owned_by("llm"):
        parts.append("Research: you have no way to pick a technology in this run, so the slots "
                     "fill only from what the game does on its own. Do not plan around research.")
    production = by_action.get("set_production")
    if production is not None:
        if production.route == MOUSE:
            parts.append(
                "Production: set_production edits the line that already makes that equipment "
                "(factories up or down, 0 removes it) on the production screen and opens a line "
                "only when none exists, never a second one for the same equipment"
                + ("; change_production_equipment switches a line to newer equipment and keeps "
                   "its factories" if "change_production_equipment" in by_action
                   and by_action["change_production_equipment"].route == MOUSE else "")
                + ". Read the lines in the brief first, then edit.")
        else:
            parts.append("Production: set_production can only open a line for equipment that has "
                         "none; a line that exists cannot be changed in this run, so choose the "
                         "split between equipment when you open them.")
    if "construction" in cmap.owned_by("llm"):
        parts.append("Construction: queue_construction and queue_province_building fill the "
                     "build queue; the queue is yours, and it is empty until you fill it.")
    return " ".join(parts)
