"""Balance of power bars (``common/bop``): what each bar is, so the brief can say where it stands.

A bar runs from -1 (all the way to its left side) to 1 (its right side). Ranges cut it into
bands; each band carries a modifier and may run effects when it becomes active. A player has
no button on the bar: it moves through the decisions in the category the bar names, through
focuses and through events. So the harness reads the bar here, the mod logs where it stands
(``bop`` lines, generated in the probe), and ``take_decision`` is what moves it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import script as S


@dataclass
class BopRange:
    id: str
    min: float
    max: float
    #: The side the range belongs to; None for a range at the top level (the centre).
    side: str | None = None
    modifiers: dict[str, str] = field(default_factory=dict)
    #: The range runs effects of its own when it becomes active or stops being active.
    has_effects: bool = False

    def holds(self, value: float) -> bool:
        return self.min <= value <= self.max


@dataclass
class Balance:
    id: str
    source: str
    initial: float | None = None
    left_side: str | None = None
    right_side: str | None = None
    decision_category: str | None = None
    ranges: list[BopRange] = field(default_factory=list)

    def range_at(self, value: float) -> BopRange | None:
        # On a shared edge (-0.1 is both the centre's min and the next band's max) the
        # narrower, inner band is what the game shows; take the one nearer zero.
        hits = [r for r in self.ranges if r.holds(value)]
        if not hits:
            return None
        return min(hits, key=lambda r: abs((r.min + r.max) / 2))

    def next_ranges(self, value: float) -> tuple[BopRange | None, BopRange | None]:
        """The nearest band wholly below and wholly above ``value``'s band."""
        here = self.range_at(value)
        lo = here.min if here else value
        hi = here.max if here else value
        below = [r for r in self.ranges if r.max <= lo and r is not here]
        above = [r for r in self.ranges if r.min >= hi and r is not here]
        return (max(below, key=lambda r: r.max) if below else None,
                min(above, key=lambda r: r.min) if above else None)

    def side_of(self, value: float) -> str | None:
        if value < 0:
            return self.left_side
        if value > 0:
            return self.right_side
        return None

    def keys(self) -> set[str]:
        """Localisation keys worth looking up."""
        out = {self.id, f"{self.id}_desc"}
        out |= {k for k in (self.left_side, self.right_side, self.decision_category) if k}
        out |= {r.id for r in self.ranges}
        return out


def _range(body: list, side: str | None) -> BopRange | None:
    ident = S.text(body, "id")
    low, high = S.number(S.text(body, "min")), S.number(S.text(body, "max"))
    if not ident or low is None or high is None:
        return None
    modifiers = {k: str(v) for k, _o, v in (S.block(body, "modifier") or [])
                 if k and isinstance(v, str)}
    effects = any(S.block(body, key) for key in ("on_activate", "on_deactivate"))
    return BopRange(ident, low, high, side, modifiers, effects)


def parse_balance(key: str, body: list, source: str) -> Balance:
    bar = Balance(id=key, source=source, initial=S.number(S.text(body, "initial_value")),
                  left_side=S.text(body, "left_side"), right_side=S.text(body, "right_side"),
                  decision_category=S.text(body, "decision_category"))
    for k, _o, value in body:
        if not isinstance(value, list):
            continue
        if k == "range":
            found = _range(value, None)
            if found:
                bar.ranges.append(found)
        elif k == "side":
            side = S.text(value, "id")
            for inner in S.blocks(value, "range"):
                found = _range(inner, side)
                if found:
                    bar.ranges.append(found)
    bar.ranges.sort(key=lambda r: (r.min, r.max))
    return bar


def _signed(value: float) -> str:
    return f"{value:+.2f}"


def _modifier_text(modifiers: dict[str, str], limit: int = 4) -> str:
    parts = []
    for name, raw in list(modifiers.items())[:limit]:
        number = S.number(raw)
        parts.append(f"{name} {number:+g}" if number is not None else f"{name} {raw}")
    more = len(modifiers) - limit
    return ", ".join(parts) + (f", +{more} more" if more > 0 else "")


def brief_line(bar: Balance | None, bar_id: str, value: float, weekly: float | None,
               names: dict[str, str]) -> str:
    """One line for the brief: where the bar stands, what that does, what moves it."""
    def name(key: str | None) -> str:
        return (names.get(key or "") or key or "?")

    if bar is None:
        trend = f", weekly {_signed(weekly)}" if weekly is not None else ""
        return f"Balance of power {bar_id}: {_signed(value)}{trend} (not in this playset's files)"
    title = names.get(bar.id) or bar.id
    side = bar.side_of(value)
    where = f"toward {name(side)}" if side else "centred"
    text = f"Balance of power '{title}' ({bar.id}): {_signed(value)} {where}"
    if weekly is not None:
        text += f", {_signed(weekly)}/week"
    here = bar.range_at(value)
    if here is not None:
        text += f" | band {name(here.id)} [{here.min:g}..{here.max:g}]"
        if here.modifiers:
            text += f": {_modifier_text(here.modifiers)}"
    below, above = bar.next_ranges(value)
    steps = []
    if below is not None:
        steps.append(f"{name(below.id)} at <= {below.max:g}"
                     + (" (runs effects)" if below.has_effects else ""))
    if above is not None:
        steps.append(f"{name(above.id)} at >= {above.min:g}"
                     + (" (runs effects)" if above.has_effects else ""))
    if steps:
        text += " | next: " + "; ".join(steps)
    sides = f"-1 = {name(bar.left_side)}, +1 = {name(bar.right_side)}"
    text += f" | {sides}"
    if bar.decision_category:
        text += (f" | moved by the decisions in {bar.decision_category} "
                 f"(list_decisions category={bar.decision_category}), focuses and events")
    return text


def describe(bar: Balance, names: dict[str, str]) -> dict:
    return {
        "type": "balance_of_power", "id": bar.id, "name": names.get(bar.id, ""),
        "description": names.get(f"{bar.id}_desc", ""), "source": bar.source,
        "initial_value": bar.initial,
        "left_side": {"id": bar.left_side, "name": names.get(bar.left_side or "", "")},
        "right_side": {"id": bar.right_side, "name": names.get(bar.right_side or "", "")},
        "decision_category": bar.decision_category,
        "ranges": [{"id": r.id, "name": names.get(r.id, ""), "min": r.min, "max": r.max,
                    "side": r.side, "modifiers": r.modifiers, "runs_effects": r.has_effects}
                   for r in bar.ranges],
        "headless": ("no buttons of its own: move it with take_decision on the decisions in "
                     f"{bar.decision_category}" if bar.decision_category else
                     "no buttons of its own: focuses and events move it"),
    }
