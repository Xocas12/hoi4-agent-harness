"""Render a GameState as a compact brief.

The brief is the single biggest recurring cost in a run, so it is written to a
budget: roughly 250-500 tokens for a full one, and a fraction of that for a
delta. Rules applied here:

* numbers, not prose -- no sentences the model can infer from a table;
* omit empty sections entirely rather than printing "none";
* collapse long lists (divisions, stockpiles) to the few that matter;
* say "unknown" for anything the adapter could not read, never 0.
"""

from __future__ import annotations

from ..types import GameState
from .forces import forces_lines, order_lines
from .fronts import summarise_fronts
from .mechanics import lines as mechanics_lines

_SEV = {"critical": "!!", "notable": "!", "info": "-"}


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _val(state: GameState, field: str, fmt=str) -> str:
    return "unknown" if not state.known(field) else fmt(getattr(state, field))


def _country_label(state: GameState) -> str:
    """``Sweden (SWE)``, or just ``SWE`` when no adapter knows the name.

    The log reader sees a tag and nothing else, and the identifier index holds
    no localised names, so "Unknown (SWE)" is what it used to print.
    """
    name = (state.country_name or "").strip()
    if not name or name == "Unknown" or name == state.country:
        return state.country
    return f"{name} ({state.country})"


def unnamed_enemies(state: GameState) -> int:
    """Enemies the weekly camp line counts when no daily war line names any.

    The two disagree when the installed mod predates the call that prints the war lines
    (or a war started this week): the count is then the only sign of the war, and a
    brief that said "no wars" would be wrong. 0 when the war lines name the enemies.
    """
    camp = state.campaign
    if state.wars or camp is None or not camp.enemies:
        return 0
    return camp.enemies


def _unnamed_war_line(state: GameState) -> str:
    camp = state.campaign
    return (f"AT WAR with {camp.enemies} countries (the weekly count of {camp.date}; no daily "
            f"war line names them, so who and how strong is unknown: read the front picture)")


def _war_line(war) -> str:
    """One war. What a reader could not see is left out, never filled in."""
    since = f" since {war.since}" if war.since else " (already on when telemetry began)"
    seen = [
        f"enemy {war.enemy_divisions} divisions" if war.enemy_divisions is not None else None,
        None if war.borders is None else ("borders us" if war.borders else "no shared border"),
        None if war.surrender_progress is None
        else f"surrender progress {war.surrender_progress * 100:.0f}%",
    ]
    seen = [bit for bit in seen if bit]
    if not seen:
        return f"AT WAR with {war.against}{since} (war score {war.war_score:+.0f})"
    score = [f"war score {war.war_score:+.0f}"] if war.war_score else []
    return f"AT WAR with {war.against}{since} ({', '.join(score + seen)})"


def render_full(state: GameState, max_events: int = 6, max_fronts: int = 3) -> str:
    """A complete situation brief.

    Fronts are capped at ``max_fronts`` full lines plus one folded line, however
    many the war has -- see observation/fronts.py.
    """
    lines: list[str] = []
    lines.append(f"## {_country_label(state)} -- {state.date}")

    lines.append(
        "PP {pp} | stability {stab} | war support {ws} | manpower {mp}".format(
            pp=_val(state, "political_power", lambda v: f"{v:.0f}"),
            stab=_val(state, "stability", _pct),
            ws=_val(state, "war_support", _pct),
            mp=_val(state, "manpower", lambda v: f"{v:,}"),
        )
    )
    lines.append(
        "Factories: {civ} / {mil} mil / {dock} dockyards | fuel {fuel} | convoys {conv}".format(
            civ=_val(state, "civilian_factories") + " civ" + (
                f" (+{state.trade_civilian_factories} from trade)"
                if state.trade_civilian_factories else ""),
            mil=_val(state, "military_factories"),
            dock=_val(state, "dockyards"),
            fuel=_val(state, "fuel", lambda v: f"{v:.0f}"),
            conv=_val(state, "convoys"),
        )
    )

    if state.resources:
        detail = ", ".join(f"{k} {v}" for k, v in sorted(state.resources.items()))
        lines.append(f"Resources: {detail}")

    if state.national_focus:
        left = state.focus_days_remaining
        # The log reader sees which focus runs, not how long is left.
        lines.append(f"Focus: {state.national_focus}" + (f" ({left}d left)" if left is not None else ""))
    elif state.known("national_focus"):
        lines.append("Focus: NONE SELECTED")
    else:
        # Shouting NONE SELECTED for a field the adapter cannot see sends the
        # model to start a focus that may already be running.
        lines.append("Focus: unknown")

    busy = [s for s in state.research if s.technology]
    free = len(state.research) - len(busy)
    if state.known("research"):
        detail = "; ".join(
            s.technology + (f" ({s.days_remaining}d)" if s.days_remaining is not None else "")
            for s in busy
        ) or "idle"
        lines.append(f"Research: {detail}" + (f" | {free} SLOT(S) FREE" if free else ""))

    if state.production:
        assigned = sum(line.factories for line in state.production)
        detail = "; ".join(
            f"{line.equipment} x{line.factories} ({_pct(line.efficiency)} eff)"
            for line in state.production[:6]
        )
        if len(state.production) > 6:
            rest = state.production[6:]
            detail += f"; +{len(rest)} more line(s), {sum(r.factories for r in rest)} factories"
        idle = (state.military_factories - assigned) if state.known("military_factories") else 0
        lines.append(f"Production: {detail}" + (f" | {idle} MIL FACTORIES IDLE" if idle > 0 else ""))

    if state.construction:
        head = state.construction[0]
        lines.append(
            f"Construction: {len(state.construction)} queued, "
            f"next {head.building} in {head.state} ({_pct(head.progress)})"
        )
    elif state.known("construction"):
        lines.append("Construction: QUEUE EMPTY")

    if state.divisions:
        total = sum(g.count for g in state.divisions)
        by_template = "; ".join(f"{g.count}x {g.template} @ {g.location}" for g in state.divisions[:5])
        if len(state.divisions) > 5:
            by_template += f"; +{sum(g.count for g in state.divisions[5:])} more"
        lines.append(f"Army: {total} divisions -- {by_template}")

    saved = state.raw.get("save_date") if isinstance(state.raw, dict) else None
    if saved and saved != state.date:
        # Production, the queue and the army come from a save, which is as old as
        # the day it was taken; the numbers above are today's from the log.
        lines.append(f"(production, construction queue and army as of the save of {saved})")

    if state.stockpiles:
        top = sorted(state.stockpiles.items(), key=lambda kv: -kv[1])[:4]
        lines.append("Stockpile: " + ", ".join(f"{k} {v:,}" for k, v in top))

    if state.wars:
        for war in state.wars:
            lines.append(_war_line(war))
    elif unnamed_enemies(state):
        lines.append(_unnamed_war_line(state))
    lines.extend(mechanics_lines(state))
    lines.extend(summarise_fronts(state.fronts, limit=max_fronts))
    lines.extend(forces_lines(state))
    lines.extend(order_lines(state))

    if state.delegated_armies or state.ai_directives or state.posture or state.theater_postures:
        bits = []
        if state.delegated_armies:
            bits.append(f"{len(state.delegated_armies)} army(s) delegated to the game AI")
        if state.posture:
            bits.append(f"posture {state.posture}" + (" (global)" if state.theater_postures else ""))
        # One entry per theater, each a country a front faces: "GER: offensive".
        bits.extend(f"{tag}: {p}" for tag, p in sorted(state.theater_postures.items()))
        if state.ai_directives:
            bits.append("directives: " + "; ".join(state.ai_directives[:4]))
        lines.append("AI control: " + " | ".join(bits))

    if state.events:
        lines.append("Since last turn:")
        for event in state.events[:max_events]:
            lines.append(f"  {_SEV.get(event.severity, '-')} {event.text}")
        if len(state.events) > max_events:
            lines.append(f"  ... and {len(state.events) - max_events} more")

    if state.unknown_fields:
        lines.append(f"(not observable with this adapter: {', '.join(state.unknown_fields[:8])})")

    return "\n".join(lines)
