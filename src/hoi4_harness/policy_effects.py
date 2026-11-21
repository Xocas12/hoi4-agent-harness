"""Politics from the console or the inbox, with the game's own conditions and costs.

``add_ideas`` does not charge, does not read a law's ``available`` block and does not ask the law
it replaces whether it may go; ``set_occupation_law`` and ``give_guarantee`` do not either. Each
plan here is therefore one ``if`` in the country's scope that does what the screen does:

* ``change_law``: the new law's ``allowed``, ``visible`` and ``available`` blocks, copied verbatim
  into the ``limit`` (the game evaluates them, not the harness); for every other law of the group
  the country may hold, "not held, or its ``allowed_to_remove`` holds"; the political power the new
  law costs, tested in the ``limit`` and taken after it. The old law is removed and the new one
  added, so its ``on_add`` runs as it does from the screen.
* ``set_occupation_law``: the law's ``visible`` and ``available`` blocks (it has no cost).
* ``diplomacy_act``: ``guarantee`` and ``leave_faction``, the two acts that need nobody's answer.
  Every other act is refused with the reason and the faithful route, never approximated.

Each effect logs a marker (``LLMB|v5|click|<nonce>``) inside the guard, the way a custom window's
click does (``modcontext/gui.py``), so the log line is the proof the game let it through.

What no trigger can test is listed under ``unchecked`` and reported on the result: cost modifiers
(the base cost is charged), and for diplomacy the engine's own limits on who may be guaranteed.
Unverified against a live game, like the decisions: that ``add_ideas`` of a law replaces the one in
its slot is not assumed (the old one is removed first), and that ``leave_faction`` takes ``= yes``.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field

from .modcontext import script as S
from .modcontext.gui import CONSOLE_LINE, INBOX_LINE, marker

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")
_TAG = re.compile(r"^[A-Z][A-Z0-9]{2}$")
_UNSCOPED = {"FROM", "PREV"}


@dataclass
class PolicyPlan:
    line: str | None = None
    reason: str = ""
    cost: float = 0.0
    unchecked: list[str] = field(default_factory=list)
    nonce: str = ""
    #: What the result says when the game logged the marker.
    done: str = ""

    @property
    def executable(self) -> bool:
        return self.line is not None


def _refuse(reason: str) -> PolicyPlan:
    return PolicyPlan(reason=reason)


def _finish(country: str, guard: str, body: str, cost: float, unchecked: list[str], done: str,
            effect_command: str, console: bool) -> PolicyPlan:
    from .adapters.console import UnsafeConsoleLine, check_console_line

    nonce = "p" + secrets.token_hex(5)
    inner = f'if = {{ limit = {{ {guard or "always = yes"} }} {body} log = "{marker(nonce)}" }}'
    line = f"{effect_command} {country} = {{ {inner} }}"
    limit = CONSOLE_LINE if console else INBOX_LINE
    if len(line) > limit:
        return _refuse(f"its conditions make a {len(line)}-character line (limit {limit})")
    if console:
        try:
            check_console_line(line)
        except UnsafeConsoleLine as exc:
            return _refuse(f"{exc} (the inbox has no such limit: use --transport inbox)")
    return PolicyPlan(line, "", cost, unchecked, nonce, done)


def _inline(*blocks) -> str:
    return " ".join(S.inline(b) for b in blocks if b)


# --- laws ---------------------------------------------------------------------------------


def plan_law(law: dict, siblings: list[dict], country: str, *, effect_command: str = "eval_effect",
             console: bool = True) -> PolicyPlan:
    """The line that changes ``country``'s ``law.category`` group to ``law``, or why it cannot.

    ``law`` and ``siblings`` are ``ModContext.ideas()["laws"]`` records of one group."""
    ident = law["id"]
    if not _SAFE_ID.match(ident):
        return _refuse("its id has characters the console would split on")
    if S.tag_truth(law["allowed"], country) is False:
        return _refuse(f"its allowed block rules {country} out: this country never has it")
    cost = law["cost"]
    if cost is None:
        return _refuse("it names no cost: it is not a law a player picks on the politics screen "
                       "(a focus, event or decision sets it)")
    if cost < 0:
        return _refuse("a negative cost")
    others = [s for s in siblings if s["id"] != ident and _SAFE_ID.match(s["id"])]
    conditions = [law["allowed"], law.get("visible") or [], law.get("available") or []]
    leaving = []
    for other in others:
        removal = other.get("allowed_to_remove") or []
        if removal:
            conditions.append(removal)
            leaving.append((other["id"], removal))
    if any(S.mentions(block, _UNSCOPED) for block in conditions):
        return _refuse("its conditions read FROM/PREV, which have no value in a console scope")
    parts = [_inline(law["allowed"], law.get("visible"), law.get("available"))]
    for other_id, removal in leaving:
        parts.append(f"OR = {{ NOT = {{ has_idea = {other_id} }} AND = {{ {S.inline(removal)} }} }}")
    if cost:
        parts.append(f"has_political_power > {cost - 0.01:g}")
    parts.append(f"NOT = {{ has_idea = {ident} }}")
    guard = " ".join(p for p in parts if p)
    body = " ".join(f"if = {{ limit = {{ has_idea = {o['id']} }} remove_ideas = {o['id']} }}"
                    for o in others)
    body += f" add_ideas = {ident}"
    if cost:
        body += f" add_political_power = -{cost:g}"
    unchecked = ["cost modifiers (the base cost is charged)",
                 "the law you leave being known: whichever of the group is held is replaced"]
    return _finish(country, guard, body.strip(), cost, unchecked,
                   f"law changed to {ident}" + (f": {cost:g} political power spent" if cost else ""),
                   effect_command, console)


# --- occupation laws ----------------------------------------------------------------------


def plan_occupation_law(law: dict, country: str, occupied: str | None = None, *,
                        effect_command: str = "eval_effect", console: bool = True) -> PolicyPlan:
    """The line that sets ``country``'s occupation law (default, or for ``occupied`` only)."""
    ident = law["id"]
    if not _SAFE_ID.match(ident):
        return _refuse("its id has characters the console would split on")
    visible, available = law.get("visible") or [], law.get("available") or []
    if S.tag_truth(visible, country) is False:
        return _refuse("its visible block rules it out (`always = no`, or another tag): the game "
                       "never shows it to this country")
    if S.mentions([*visible, *available], _UNSCOPED):
        return _refuse("its visible/available conditions read FROM (the occupied country), which "
                       "have no value in a console scope: pick it on the screen, per country")
    guard = _inline(visible, available)
    if occupied:
        occupied = occupied.upper()
        if not _TAG.match(occupied):
            return _refuse(f"{occupied!r} is not a country tag")
        guard = (guard + f" any_occupied_country = {{ tag = {occupied} }}").strip()
        body = f"{occupied} = {{ set_occupation_law = {ident} }}"
        done = f"occupation law {ident} set for {occupied}'s land"
    else:
        body = f"set_occupation_law = {ident}"
        done = f"default occupation law set to {ident}"
    unchecked = ["the law's ai_will_do: the game's AI may pick another law for a state later"]
    return _finish(country, guard, body, 0.0, unchecked, done, effect_command, console)


# --- diplomacy ----------------------------------------------------------------------------

#: act -> why it has no faithful effect, and where to go instead.
DIPLOMACY_REFUSALS: dict[str, str] = {
    "military_access": (
        "it needs the other country to accept, and the game's AI decides that; the effect that "
        "grants access would skip the answer. Use the diplomacy action where the mouse plays, or "
        "steer the AI: set_ai_directive befriend or ally"),
    "join_faction": (
        "it needs the faction leader to accept; add_to_faction would skip the answer. Use the "
        "diplomacy action where the mouse plays, or steer the AI: set_ai_directive ally"),
    "invite_to_faction": (
        "it needs the invited country to accept; add_to_faction would skip the answer. Use the "
        "diplomacy action where the mouse plays, or steer the AI: set_ai_directive ally"),
    "non_aggression_pact": (
        "it needs the other country to accept, and no effect asks it. Use the diplomacy action "
        "where the mouse plays, or steer the AI: set_ai_directive befriend"),
    "lend_lease": (
        "lend-lease is a standing contract the other side accepts, and send_equipment is a gift "
        "with none of its terms. Steer the AI: set_ai_directive lend_lease (it sends it itself), "
        "or use the diplomacy action where the mouse plays"),
    "send_volunteers": (
        "volunteers are divisions picked from an army, which no script scope reaches. Steer the "
        "AI: set_ai_directive volunteers, or use the diplomacy action where the mouse plays"),
    "send_attache": (
        "no effect sends an attache (only recall_attache exists). Use the diplomacy action where "
        "the mouse plays"),
    "improve_relations": (
        "it costs political power and time on a cooldown, and add_opinion_modifier would give the "
        "opinion without either. Use the diplomacy action where the mouse plays, or steer the AI: "
        "set_ai_directive befriend"),
    "justify_war_goal": (
        "justifying takes weeks (and political power) before the war goal exists, and "
        "create_wargoal gives it at once: that is a different game. Use the diplomacy action where "
        "the mouse plays, or steer the AI: set_ai_directive prepare_war (it justifies and masses "
        "troops itself) then declare_war"),
    "declare_war": (
        "declare_war_on skips the war goal, the justification time and the tension a declaration "
        "costs. Use the diplomacy action where the mouse plays, or steer the AI: set_ai_directive "
        "declare_war (it declares when its own rules allow)"),
}


def plan_diplomacy(action: str, target: str | None, country: str, *,
                   effect_command: str = "eval_effect", console: bool = True) -> PolicyPlan:
    """The line for ``guarantee`` or ``leave_faction``, or why the act is refused."""
    if action in DIPLOMACY_REFUSALS:
        return _refuse(f"{action} has no faithful effect: {DIPLOMACY_REFUSALS[action]}")
    if action == "leave_faction":
        guard = "is_in_faction = yes NOT = { is_faction_leader = yes }"
        unchecked = ["the faction's own rules on leaving, and any penalty for leaving a war "
                     "that is under way (no trigger exposes them)"]
        return _finish(country, guard, "leave_faction = yes", 0.0, unchecked,
                       f"{country} left its faction", effect_command, console)
    if action != "guarantee":
        return _refuse(f"{action!r} is not a diplomatic act the harness knows")
    tag = (target or "").upper()
    if not _TAG.match(tag):
        return _refuse("guarantee needs a target: a country tag")
    if tag == country:
        return _refuse("a country cannot guarantee itself")
    guard = (f"country_exists = {tag} NOT = {{ has_war_with = {tag} }} "
             f"NOT = {{ has_guaranteed = {tag} }}")
    unchecked = ["the engine's own limits on who may be guaranteed (no trigger exposes them), and "
                 "the world tension a guarantee adds"]
    return _finish(country, guard, f"give_guarantee = {tag}", 0.0, unchecked,
                   f"{country} now guarantees {tag}", effect_command, console)
