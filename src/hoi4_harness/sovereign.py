"""Sovereign mode: the model owns the country, the game's AI runs only the armies (#161 step 2).

``control = "sovereign"`` (control.py ``ALIASES``) is ``economy`` plus the choices a player makes
between the desk work (focus, politics, diplomacy), with ``army: game`` and NO ``human_ai``. The
game's AI is never handed the country, so nothing it does can undo or double what the model set.
It is handed armies, one at a time, by the only way the game has: a front line against a named
enemy drawn for the army (``assign_army_front``, docs/armies.md), which the AI then carries out.

This module is what is particular to the mode, beyond the control map and the economy channels:

* ``preflight``: what a run must have before it starts, and the exact calibration points that are
  missing, because the army click paths cannot run on points nobody recorded;
* ``gaps``: the armies with no front and the divisions in no army, from the text save, which are
  what the wake rules and the brief both report;
* ``describe`` / ``lines`` / ``brief_lines``: who runs what, said in the prompt, the start log and
  every brief.

Built against the fake game only; nothing here has been run against the real game
(docs/modular-control.md, "Sovereign mode").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .adapters.battle_plans import ASSIGN_POINT
from .armies import held_plans
from .control import SOVEREIGN, SOVEREIGN_ARMY_ACTIONS, ControlMap

#: The recorded points every army action needs, whatever the armies are called.
GENERIC_POINTS = ("army.create", "plan.execute_careful", "plan.execute_balanced",
                  "plan.execute_rush")
#: Why a point is needed, for the message that names it.
POINT_USE = {
    "army.create": "the create-army card (create_army)",
    "plan.execute_careful": "the careful pace button (assign_army_front, set_army_execution)",
    "plan.execute_balanced": "the balanced pace button (assign_army_front, set_army_execution)",
    "plan.execute_rush": "the rush pace button (assign_army_front, set_army_execution)",
}


def is_sovereign(cmap: ControlMap) -> bool:
    return cmap.alias == SOVEREIGN


# --- what the save says ------------------------------------------------------------------------


def holds_front(group) -> bool:
    """An army holds a front when a line drawn on the map (not a garrison or a fallback) has
    divisions assigned: the save's own account, nothing the click path claims."""
    return any(p.kind not in ("garrison", "fallback") for p in held_plans(group))


@dataclass
class Gaps:
    """What the armies still need from the model."""

    #: Names of armies that have divisions and no front.
    no_front: list[str] = field(default_factory=list)
    #: Divisions the country has that belong to no army.
    unassigned: int = 0

    def __bool__(self) -> bool:
        return bool(self.no_front or self.unassigned)

    def signature(self) -> tuple:
        return (tuple(sorted(self.no_front)), self.unassigned)

    def words(self) -> str:
        parts = []
        if self.no_front:
            parts.append("army without a front: " + ", ".join(sorted(self.no_front)))
        if self.unassigned:
            parts.append(f"{self.unassigned} division(s) in no army")
        return "; ".join(parts)


def gaps(state) -> Gaps | None:
    """The armies with no front and the divisions in no army, or None when the save has not
    been read (no ``theatres`` block): nothing is claimed about armies nobody has seen."""
    groups = getattr(state, "army_groups", None)
    if groups is None:
        return None
    members = sum(g.members for g in groups)
    total = sum(d.count for d in (state.divisions or []))
    return Gaps(no_front=[g.name for g in groups if g.members > 0 and not holds_front(g)],
                unassigned=max(total - members, 0) if total else 0)


# --- the start ---------------------------------------------------------------------------------


def missing_points(coordinates: dict, army_names=()) -> list[str]:
    """The recorded points the army click paths need that ``coordinates`` lacks."""
    need = [*GENERIC_POINTS, *(f"army.{n}" for n in army_names)]
    return [p for p in need if p not in coordinates]


def calibrate_line(points) -> str:
    return "hoi4-harness calibrate " + " ".join(points) + (
        f"  (and optionally {ASSIGN_POINT}, the assign-units button)")


def preflight(cmap: ControlMap, *, offered, refused: dict | None, coordinates: dict,
              state) -> list[str]:
    """Everything that stops a sovereign run from starting, each a sentence saying what and how
    to fix it. Empty when the run may start. Not a sovereign map: empty."""
    if not is_sovereign(cmap):
        return []
    problems: list[str] = []
    offered = set(offered)
    refused = refused or {}
    if state.ai_controlled is True:
        problems.append(
            f"the state line reads ai=1: the game's AI is running {state.country or 'the country'}. "
            "Sovereign mode needs the model to own it. human_ai is a toggle and the harness will "
            "not send it; toggle it off in the game's console yourself, or load a save from "
            "before, then run again")
    elif state.ai_controlled is None:
        problems.append(
            "the state line has no ai= field, so the harness cannot verify that the game's AI is "
            "off (is the mod schema v5? hoi4-harness verify-bridge)")
    absent = sorted(SOVEREIGN_ARMY_ACTIONS - offered)
    for name in absent:
        problems.append(f"{name} is not offered: "
                        + (refused.get(name) or "the adapter has no click path for it"))
    names = [g.name for g in (state.army_groups or [])]
    gone = missing_points(coordinates, names)
    if gone:
        problems.append("recorded points missing for the army click paths: " + ", ".join(
            f"{p} ({POINT_USE.get(p, 'the army card of ' + p[len('army.'):])})" for p in gone)
            + ". Record them with: " + calibrate_line(gone))
    return problems


def notes(state) -> list[str]:
    """Things preflight cannot settle yet, for the start log."""
    if getattr(state, "army_groups", None) is None:
        return ["sovereign: no text save read yet, so the armies' own recorded points (army.<name>) "
                "cannot be checked now; an army without one is refused when it is ordered"]
    return []


def lines(cmap: ControlMap, offered) -> list[str]:
    """The start-log lines of the mode."""
    if not is_sovereign(cmap):
        return []
    offered = set(offered)
    got = ", ".join(sorted(SOVEREIGN_ARMY_ACTIONS & offered)) or "nothing"
    return ["sovereign mode: the model owns the country; the game's AI runs only the armies "
            "handed to it by a front (human_ai is never sent)",
            f"sovereign armies: {got} (clicks, UNVERIFIED against the real game: the next text "
            "save is the only proof)",
            "sovereign navy and air: nobody orders fleets and wings; they keep the game's default "
            "missions"]


# --- the words ---------------------------------------------------------------------------------


def describe(cmap: ControlMap, offered) -> str:
    """The prompt paragraph: who runs what and how an army is handed over."""
    if not is_sovereign(cmap):
        return ""
    offered = set(offered)
    can = SOVEREIGN_ARMY_ACTIONS & offered
    text = ("You own the country: the focus, politics and diplomacy, research, production and "
            "construction are yours, and the game's AI is NOT running the country (human_ai is "
            "off), so it never undoes or doubles an order of yours. The game's AI runs only the "
            "armies you hand it. HOI4 has no per-army AI switch: an army is handed over by "
            "giving it a front.")
    if "assign_army_front" in can:
        text += (" assign_army_front(army, enemy, province?, mode?) draws the army's line on "
                 "the border against that enemy and sets its pace, and the game then carries "
                 "the plan out. While at war every army that has divisions should hold a front; "
                 "an army without one stands where it is.")
    if {"create_army", "assign_divisions"} <= can:
        text += (" create_army and assign_divisions put divisions into armies; divisions in no "
                 "army fight for nobody, so give each new one an army and the army a front.")
    if "set_army_execution" in can:
        text += " set_army_execution changes an army's pace; release_army takes it off its plan."
    text += (" Each of these is a click that the next text save confirms: until then the result "
             "says unverified, and the brief lists armies with no front and divisions in no "
             "army. Nobody orders the navy and the air force: fleets and wings keep the game's "
             "default missions, so there is nothing to do there and no tool for it.")
    return text


def brief_lines(state) -> list[str]:
    """Who runs what, and where the armies stand, in every brief."""
    groups = getattr(state, "army_groups", None)
    out = ["  Sovereign mode: you run the country (focus, politics, diplomacy, research, production, "
           "construction); the game's AI runs only the armies that hold a front; nobody runs "
           "navy and air."]
    if groups is None:
        out.append("  Armies: not read yet (no text save): assign fronts once the save shows them.")
        return out
    held = [g.name for g in groups if holds_front(g)]
    gap = gaps(state)
    out.append(f"  Armies: {len(groups)}; {len(held)} hold a front"
               + (f" ({', '.join(held[:6])})" if held else "")
               + (f"; {gap.words()}" if gap else "") + ".")
    return out
