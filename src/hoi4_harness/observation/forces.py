"""The front picture in the brief: where the player's divisions and the enemy's stand.

The mod can only count divisions per state (docs/unit-orders.md), so that is the
grain. A state holding both sides is contact; a state holding only enemy
divisions is where they are massing or where an advance will land. The list is
capped like the fronts are: a handful of lines, the rest folded into one.
"""

from __future__ import annotations

from ..types import GameState, StateForces

#: Days a picture may lag the state line before the brief says it is old.
STALE_DAYS = 2


def _who(f: StateForces) -> str:
    return f.name or f"state {f.state}"


def _count(n: int, weak: int) -> str:
    return f"{n}" + (f" ({weak} weak)" if weak else "")


def _line(f: StateForces) -> str:
    where = f"{_who(f)}" + (f" [{f.controller}]" if f.controller else "")
    parts = []
    if f.own:
        parts.append(f"own {_count(f.own, f.own_weak)}")
    if f.foe:
        parts.append(f"foe {_count(f.foe, f.foe_weak)}")
    tag = "CONTACT " if f.own and f.foe else ""
    return f"  {tag}{where}: " + " | ".join(parts)


def _days_between(a: str | None, b: str | None) -> int | None:
    from datetime import date

    try:
        return abs((date.fromisoformat(b or "") - date.fromisoformat(a or "")).days)
    except ValueError:
        return None


def forces_lines(state: GameState, limit: int = 5) -> list[str]:
    """The brief's front-picture lines; empty when there is nothing to say
    (not observed, or no division of either side in a contested state)."""
    picture = state.state_forces
    if not picture:
        return []
    own = sum(f.own for f in picture)
    foe = sum(f.foe for f in picture)
    age = _days_between(state.state_forces_date, state.date)
    stale = f", {age} days old" if age is not None and age > STALE_DAYS else ""
    head = f"Front picture ({state.state_forces_date or state.date}{stale}): "
    head += f"own {own} vs foe {foe} in {len(picture)} state(s)"
    # Contact first (both sides present), then by the size of what is there.
    ranked = sorted(picture, key=lambda f: (not (f.own and f.foe), -(f.own + f.foe), f.state))
    lines = [head, *(_line(f) for f in ranked[:limit])]
    rest = ranked[limit:]
    if rest:
        lines.append(f"  ... {len(rest)} more state(s): own {sum(f.own for f in rest)}, "
                     f"foe {sum(f.foe for f in rest)}")
    return lines


def forces_changes(previous: GameState, current: GameState, limit: int = 4) -> list[str]:
    """What moved on the front since the last brief, for the delta."""
    if current.state_forces is None:
        return []
    before = {f.state: f for f in previous.state_forces or []}
    after = {f.state: f for f in current.state_forces}
    out = []
    for sid in sorted(set(before) | set(after)):
        was, now = before.get(sid), after.get(sid)
        a = (was.own, was.foe) if was else (0, 0)
        b = (now.own, now.foe) if now else (0, 0)
        if a == b:
            continue
        ref = now or was
        out.append(f"Front {_who(ref)}: own {a[0]} -> {b[0]}, foe {a[1]} -> {b[1]}"
                   + (f" [{now.controller}]" if now and now.controller else ""))
    if len(out) > limit:
        out = out[:limit] + [f"Front: {len(out) - limit} more state(s) changed"]
    return out


def order_lines(state: GameState) -> list[str]:
    """Division orders given earlier and what the telemetry shows of them."""
    return [f"Order: {text}" for text in state.order_status[:4]]
