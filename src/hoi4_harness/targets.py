"""Which country an action names, said once for every channel (#7).

A directive, a theater posture or a steer that names a country must act on exactly that
tag: not on the previous target, not on a default, not on a spelling that only some
channel understands. ``check`` normalises the tag in place (so the console, the inbox,
the ui and the mock all see the same upper-case three-character tag) or refuses a call
that names none. ``acted_on`` and ``annotate`` put the tag that was acted on into the
result the model reads, so a wrong target is visible in the very next turn.
"""

from __future__ import annotations

import dataclasses
import re

from .types import ActionCall, ActionResult

TAG = re.compile(r"^[A-Z0-9]{3}$")

#: Action -> the argument that names the country it acts on.
NAMING = {"set_ai_directive": "target", "set_ai_posture": "theater"}


def _bad(call: ActionCall, why: str) -> ActionResult:
    return ActionResult(ok=False, action=call.name, call_id=call.call_id, message=why,
                        error_kind="invalid_args")


def check(call: ActionCall) -> ActionResult | None:
    """Refuse a call whose country is missing or malformed; upper-case a good one in place."""
    argument = NAMING.get(call.name)
    if argument is None:
        return None
    args = call.arguments
    value = args.get(argument)
    if call.name == "set_ai_posture" and value is None:
        return None           # no theater: every front, said by leaving it out
    if not isinstance(value, str) or not value.strip():
        if call.name == "set_ai_posture":
            return _bad(call, "theater is empty: name the country tag a front faces, or leave "
                              "theater out for every front.")
        return _bad(call, f"{call.name} names no country: pass {argument} as a country tag "
                          "(e.g. POL). Nothing was done.")
    tag = value.strip().upper()
    if not TAG.match(tag):
        return _bad(call, f"{value!r} is not a country tag (three letters or digits, e.g. POL). "
                          "Nothing was done.")
    args[argument] = tag
    return None


def acted_on(call: ActionCall) -> str | None:
    """The tag the call names, upper-cased, or None for a call that names none."""
    argument = NAMING.get(call.name)
    value = call.arguments.get(argument) if argument else None
    return value.strip().upper() if isinstance(value, str) and value.strip() else None


def annotate(call: ActionCall, result: ActionResult) -> ActionResult:
    """A successful result says which tag it acted on, and carries it in ``changed``."""
    tag = acted_on(call)
    if tag is None or not result.ok:
        return result
    note = f"[acted on {tag}]"
    if note in result.message:
        return result
    return dataclasses.replace(result, message=f"{result.message} {note}".strip(),
                               changed={**result.changed, "target": tag})
