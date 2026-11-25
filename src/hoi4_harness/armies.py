"""The country's armies as the text save records them (#8, #137).

``theatres`` of the country block holds ``theatre`` blocks; each holds ``orders_group`` blocks, which
are the armies of the game's army panel ("Army 1"), with their divisions (``member={ unit={...} }``;
``member={ request={...} }`` is a reinforcement request, not a division) and their plans
(``order_instance``). Read off a real 1.20 save:

* ``type=5`` is a garrison (``area_defense_settings``, ``states``);
* every other type is a line drawn on the map: ``path`` lists its provinces, ``postfix`` names how it
  was made (``o_strat_offensive_default`` on an offensive), ``fallback`` marks a fallback line;
* ``scheduled_member`` lists the divisions the plan is assigned to: an order with none is drawn and
  carried out by nobody;
* ``virtual_order=yes`` instances are the game's own bookkeeping children and are skipped;
* ``execution_type`` on the group is how the plan is executed (0 careful, 1 balanced, 2 rush,
  from ``btn_execute_0..2`` of ``interface/battleplantools.gui``).

What the save does NOT say, and this reader therefore does not claim: which enemy a line faces, or
whether the game's AI rather than a person set the plan. A plan is read as "held" when it has divisions
assigned, nothing more. Nothing here has been checked against a game the harness itself clicked in.
"""

from __future__ import annotations

from .types import ArmyGroup, ArmyPlan

EXECUTION = {0: "careful", 1: "balanced", 2: "rush"}
EXECUTION_CODE = {name: code for code, name in EXECUTION.items()}
#: The ``order_instance`` type of a garrison (area defence) order.
GARRISON_TYPE = 5


def _list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _int(value) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _ints(value) -> list[int]:
    items = value.get("_items", []) if isinstance(value, dict) else _list(value)
    return [n for n in (_int(v) for v in items) if n is not None]


def _plan_kind(raw: dict) -> str:
    if _int(raw.get("type")) == GARRISON_TYPE or "area_defense_settings" in raw:
        return "garrison"
    if "fallback" in raw:
        return "fallback"
    postfix = str(raw.get("postfix", "")).lower()
    if "offens" in postfix:
        return "offensive"
    if "defen" in postfix:
        return "defensive"
    return "line"


def read_plan(raw: dict) -> ArmyPlan:
    return ArmyPlan(kind=_plan_kind(raw), path=_ints(raw.get("path")), states=_ints(raw.get("states")),
                    assigned=len([m for m in _list(raw.get("scheduled_member")) if isinstance(m, dict)]))


def read_group(raw: dict) -> ArmyGroup | None:
    ident = raw.get("id")
    ident = ident.get("id") if isinstance(ident, dict) else ident
    name = raw.get("name")
    if ident is None or not isinstance(name, str):
        return None
    members = sum(1 for m in _list(raw.get("member")) if isinstance(m, dict) and "unit" in m)
    plans = [read_plan(o) for o in _list(raw.get("order_instance"))
             if isinstance(o, dict) and str(o.get("virtual_order", "")).lower() != "yes"]
    return ArmyGroup(id=str(ident), name=name, members=members, plans=plans,
                     execution=_int(raw.get("execution_type")))


def read_army_groups(country: dict | None) -> list[ArmyGroup] | None:
    """The armies of a parsed country block. None when the block has no ``theatres`` key at all
    (a save this reader cannot place), [] when it has one and no army in it."""
    if not isinstance(country, dict) or "theatres" not in country:
        return None
    theatres = country.get("theatres")
    out: list[ArmyGroup] = []
    if isinstance(theatres, dict):
        for theatre in _list(theatres.get("theatre")):
            if not isinstance(theatre, dict):
                continue
            for raw in _list(theatre.get("orders_group")):
                group = read_group(raw) if isinstance(raw, dict) else None
                if group is not None:
                    out.append(group)
    return out


def group_named(groups: list[ArmyGroup] | None, name: str) -> ArmyGroup | None:
    for g in groups or []:
        if g.name.lower() == name.lower():
            return g
    return None


def held_plans(group: ArmyGroup | None) -> list[ArmyPlan]:
    """The plans of ``group`` that have divisions assigned."""
    return [p for p in (group.plans if group else []) if p.assigned > 0]


def describe(group: ArmyGroup) -> str:
    plans = held_plans(group)
    parts = [f"{group.members} division(s)"]
    if group.execution in EXECUTION:
        parts.append(f"executes {EXECUTION[group.execution]}")
    for p in plans:
        where = (f"states {p.states[:4]}" if p.kind == "garrison"
                 else f"{len(p.path)} province(s) {p.path[:3]}{'...' if len(p.path) > 3 else ''}")
        parts.append(f"{p.kind} over {where}")
    if not plans:
        parts.append("no plan assigned in its own block")
    return f"{group.name}: " + ", ".join(parts)


def brief_line(groups: list[ArmyGroup] | None, limit: int = 6) -> str | None:
    if not groups:
        return None
    shown = "; ".join(describe(g) for g in groups[:limit])
    more = f"; and {len(groups) - limit} more" if len(groups) > limit else ""
    return f"  Armies (from the save): {shown}{more}"
