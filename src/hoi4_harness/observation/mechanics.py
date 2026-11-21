"""The brief lines and ``inspect_world`` extras for what the mod reads from script.

Source: ``GameState.observed`` (adapters/observe_lines.py), the weekly ``res``, ``tension``,
``equip``, ``garrison``, ``peace``, ``fac``, ``vol`` and ``collab`` lines. All of it is the country's
own (or public: world tension, the faction window), so nothing here is intel-gated. A section is
left out when its line has not arrived or its token did not expand: never a zero.
"""

from __future__ import annotations

from ..types import GameState, ModObserved

#: Archetypes the brief names a gap for, most-short first, at most this many.
EQUIPMENT_LINES = 4


def _n(value: float) -> str:
    return f"{value:,.0f}"


def _signed(value: float) -> str:
    return f"{value:+,.0f}"


def deficits(observed: ModObserved) -> list[tuple[str, float, dict[str, float]]]:
    """(archetype, how many divisions lack, the line) for each archetype whose armies hold less than
    they need, worst first. Needs both numbers; a line with one of them missing is not a gap."""
    out = []
    for name, line in observed.equipment.items():
        if "army" in line and "need" in line and line["need"] > line["army"]:
            out.append((name, line["need"] - line["army"], line))
    return sorted(out, key=lambda t: (-t[1], t[0]))


def resource_balance(observed: ModObserved) -> list[tuple[str, float]]:
    """(resource, net) for each resource with a net reading, lowest net first."""
    return sorted(((name, line["net"]) for name, line in observed.resources.items() if "net" in line),
                  key=lambda t: (t[1], t[0]))


def lines(state: GameState) -> list[str]:
    observed = state.observed
    if observed is None:
        return []
    out = []
    balance = resource_balance(observed)
    if balance:
        short = [f"{name} {_signed(net)}" for name, net in balance if net < 0]
        rest = [f"{name} {_signed(net)}" for name, net in reversed(balance) if net >= 0]
        out.append("Resources (net a day, mod): " + ", ".join(short + rest)
                   + (" | SHORT: " + ", ".join(n.split()[0] for n in short) if short else ""))
    gaps = deficits(observed)
    if gaps:
        top = "; ".join(f"{name} short {_n(gap)} (armies hold {_n(line['army'])} of {_n(line['need'])}"
                        + (f", stock {_n(line['stock'])}" if "stock" in line else "") + ")"
                        for name, gap, line in gaps[:EQUIPMENT_LINES])
        out.append("Equipment gap: " + top + (f" (+{len(gaps) - EQUIPMENT_LINES} more)"
                                              if len(gaps) > EQUIPMENT_LINES else ""))
    bits = []
    if observed.tension is not None:
        bits.append(f"world tension {observed.tension:.0%}")
    garrison = observed.garrison
    if "need" in garrison:
        bits.append(f"garrison need {_n(garrison['need'])} manpower"
                    + (f" in {_n(garrison['occupied'])} occupied states" if "occupied" in garrison else ""))
    if observed.peace.get("in"):
        peace = observed.peace
        bits.append("IN A PEACE CONFERENCE" + (f" (score {_n(peace['score'])} of {_n(peace['total'])})"
                                               if "score" in peace and "total" in peace else ""))
    faction = observed.faction
    if "rank" in faction:
        bits.append(f"faction influence rank {faction['rank']:.0f}"
                    + (f" ({faction['ratio']:.0%} of it)" if "ratio" in faction else ""))
    if observed.collaboration:
        shown = sorted(observed.collaboration.items(), key=lambda kv: -kv[1])[:3]
        bits.append("collaboration " + ", ".join(f"{t} {v:.0%}" for t, v in shown))
    volunteers = {t: v for t, v in observed.volunteers.items() if v.get("in") or v.get("out")}
    if volunteers:
        parts = []
        for tag, v in sorted(volunteers.items()):
            if v.get("in"):
                parts.append(f"{tag} sends you >={v['in']}")
            if v.get("out"):
                parts.append(f"you send {tag} >={v['out']}")
        bits.append("volunteers " + ", ".join(parts[:4]))
    if bits:
        out.append("World and war: " + "; ".join(bits))
    return out


def view_extras(state: GameState, view: str) -> dict:
    """What to add to an ``inspect_world`` view of the country's own: only where the mod read it."""
    observed = state.observed
    if observed is None:
        return {}
    extra: dict = {}
    if view == "economy":
        if observed.resources:
            extra["resources"] = {name: {k: round(v, 1) for k, v in line.items()}
                                  for name, line in sorted(observed.resources.items())}
        if observed.equipment:
            extra["equipment_vs_need"] = {
                name: {**{k: round(v) for k, v in line.items()},
                       **({"short": round(line["need"] - line["army"])}
                          if "army" in line and "need" in line and line["need"] > line["army"] else {})}
                for name, line in sorted(observed.equipment.items())}
    elif view == "occupation":
        if observed.garrison:
            extra["garrison"] = {k: round(v) for k, v in observed.garrison.items()}
        if observed.collaboration:
            extra["collaboration"] = {t: round(v, 2) for t, v in sorted(observed.collaboration.items())}
    elif view == "diplomacy":
        if observed.tension is not None:
            extra["world_tension"] = round(observed.tension, 3)
        if observed.peace:
            extra["peace_conference"] = dict(observed.peace)
        if observed.volunteers:
            extra["volunteers_lower_bounds"] = dict(sorted(observed.volunteers.items()))
    elif view == "faction":
        if observed.faction:
            extra["your_influence"] = {k: round(v, 3) for k, v in observed.faction.items()}
        if observed.faction_members:
            extra["member_influence"] = {t: {k: round(v, 3) for k, v in line.items()}
                                         for t, line in sorted(observed.faction_members.items())}
    if extra:
        extra["mod_read_on"] = observed.date
    return extra
