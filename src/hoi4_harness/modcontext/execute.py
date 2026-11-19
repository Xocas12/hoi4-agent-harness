"""Taking a decision from the console, with the game's own conditions.

``activate_decision`` is documented as "Activates specified decision for scope
country" -- and says nothing about cost, ``available`` or cooldowns, so none of
those can be assumed to apply. A faithful take therefore does what the decisions
screen does, in script, inside one ``if``:

* the decision's own ``allowed``, ``visible`` and ``available`` blocks, copied
  verbatim into the ``limit`` (the game evaluates them, not the harness);
* its ``custom_cost_trigger`` where it has one;
* the political power it costs, tested in the ``limit`` and taken after it;
* ``NOT = { has_decision = ID }``, so one that is already running is not
  taken twice.

What no trigger can test is left on ``unchecked`` and reported back: a
``fire_only_once`` decision already taken, the cost *modifiers* the screen applies
(the base cost is charged), a cooldown. A take that cannot be guarded -- a
mission, a cost that is a variable, targets the harness cannot enumerate, a
condition that reads ``FROM`` -- is not executable; the reason is part of the
plan, and the action is observe-only for that decision.

Unverified against a live game, because the game is never launched for this: that
``activate_decision`` runs the ``complete_effect`` and that ``ROOT`` is the country
the ``eval_effect`` names. ``docs/mods.md`` lists both for the next live session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import script as S
from .surface import Decision

#: Longest console line the harness will write. A decision whose conditions
#: need more is observe-only rather than a line the console may truncate.
MAX_LINE = 6000
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")
_TAG = re.compile(r"^[A-Z][A-Z0-9]{2}$")


@dataclass
class DecisionPlan:
    decision: Decision
    #: The console line, or None when the decision is observe-only.
    line: str | None = None
    reason: str = ""
    cost: float = 0.0
    unchecked: list[str] = field(default_factory=list)

    @property
    def executable(self) -> bool:
        return self.line is not None


def _refuse(decision: Decision, reason: str) -> DecisionPlan:
    return DecisionPlan(decision, None, reason)


def plan_decision(decision: Decision, country: str, target: str | None = None,
                  effect_command: str = "eval_effect") -> DecisionPlan:
    """The console line that takes ``decision`` as ``country``, or why it cannot."""
    if not _SAFE_ID.match(decision.id):
        return _refuse(decision, "its id has characters the console would split on")
    if decision.kind == "mission":
        return _refuse(decision, "a mission: the game activates it, a player cannot take it")
    if decision.inert:
        return _refuse(decision, "it has no effect block of its own (an information line or a "
                                 "spacer in a custom window): taking it changes nothing")
    if decision.blocked:
        return _refuse(decision, "its available block is `always = no`: the game draws it but it can "
                                 "never be taken (a timer, a spacer or an information line)")
    if decision.cost_raw is not None:
        return _refuse(decision, f"its cost is the script value '{decision.cost_raw}', which "
                                 "the harness cannot evaluate, so it cannot be charged")
    cost = decision.cost or 0.0
    if cost < 0:
        return _refuse(decision, "a negative cost")
    unchecked = ["cost modifiers (the base cost is charged)"]
    if decision.fire_only_once:
        unchecked.append("fire_only_once (no trigger tells whether it was taken before; the "
                         "harness refuses a second take within one run)")
    if decision.days_re_enable:
        unchecked.append(f"the {decision.days_re_enable}-day cooldown after a take")

    conditions = []
    for label, nodes in (("allowed", decision.allowed), ("visible", decision.visible),
                         ("available", decision.available),
                         ("custom_cost_trigger", decision.custom_cost_trigger)):
        if nodes:
            conditions.append((label, nodes))

    if decision.kind == "targeted":
        return _plan_targeted(decision, country, target, conditions, cost, unchecked, effect_command)

    if S.mentions([n for _l, nodes in conditions for n in nodes], {"FROM", "PREV"}):
        return _refuse(decision, "its conditions read FROM/PREV, which have no value in a "
                                 "console scope")
    guard = " ".join(S.inline(nodes) for _l, nodes in conditions)
    if cost:
        guard += f" has_political_power > {cost - 0.01:g}"
    guard += f" NOT = {{ has_decision = {decision.id} }}"
    body = f"activate_decision = {decision.id}"
    if cost:
        body += f" add_political_power = -{cost:g}"
    return _finish(decision, country, f"if = {{ limit = {{ {guard.strip()} }} {body} }}",
                   cost, unchecked, effect_command)


def _plan_targeted(decision, country, target, conditions, cost, unchecked, effect_command):
    if decision.targets_dynamic or not decision.targets:
        return _refuse(decision, "its targets are computed by the game (target_array or "
                                 "targets_dynamic), so the harness cannot list or check them")
    if not target:
        return _refuse(decision, "a targeted decision: name a target, one of "
                                 + ", ".join(decision.targets))
    target = str(target).strip()
    if target not in decision.targets:
        return _refuse(decision, f"{target!r} is not one of its targets ({', '.join(decision.targets)})")
    if not (_TAG.match(target) or target.isdigit()):
        return _refuse(decision, f"target {target!r} is neither a country tag nor a state id")
    every = [n for _l, nodes in conditions for n in nodes] + list(decision.target_trigger)
    if S.mentions(every, {"FROM", "PREV"}):
        return _refuse(decision, "its conditions or target_trigger read FROM/PREV, which have no "
                                 "value in a console scope, so the guard cannot be written")
    guard = " ".join(S.inline(nodes) for _l, nodes in conditions)
    if cost:
        guard += f" has_political_power > {cost - 0.01:g}"
    inner = (f"activate_targeted_decision = {{ target = {target} decision = {decision.id} }}"
             + (f" add_political_power = -{cost:g}" if cost else ""))
    if decision.target_trigger:
        # The target_trigger runs in the target's scope; ROOT is still the country.
        check = S.inline(decision.target_trigger)
        inner = f"{target} = {{ if = {{ limit = {{ {check} }} ROOT = {{ {inner} }} }} }}"
        unchecked.append("ROOT inside the target scope is assumed to be the acting country")
    return _finish(decision, country, f"if = {{ limit = {{ {guard.strip() or 'always = yes'} }} {inner} }}",
                   cost, unchecked, effect_command)


def _finish(decision, country, body, cost, unchecked, effect_command):
    from ..adapters.console import UnsafeConsoleLine, check_console_line

    line = f"{effect_command} {country} = {{ {body} }}"
    if len(line) > MAX_LINE:
        return _refuse(decision, f"its conditions make a {len(line)}-character console line "
                                 f"(limit {MAX_LINE})")
    try:
        check_console_line(line)
    except UnsafeConsoleLine as exc:
        return _refuse(decision, str(exc))
    return DecisionPlan(decision, line, "", cost, unchecked)
