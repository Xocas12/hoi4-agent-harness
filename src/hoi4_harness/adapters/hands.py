"""The mouse tools of an independent campaign: what is offered, and what was refused and why.

``campaign_control = "llm"`` with ``campaign_input = "mouse"`` (the default) adds the input
driver's mouse to the headless adapter, for the three jobs the console has no effect for:

* ``start_research``: press ``y`` then ``w`` (the panel hotkeys toggle, so the production panel
  first makes sure the research one opens rather than closes), click a free slot, the folder
  tab, the technology's computed grid cell, then the two answers: on 1.19 Return twice (the
  second answers the military-industrial-organisation chooser some technologies raise), on 1.20
  a click on the details popup's Research button and one on the chooser's "without an
  organisation" row (Return does neither there). Verified live: the 1.19 path on 2026-11-19, the
  1.20 path on 2026-11-21. The path follows the game's version (``research_path_for``); the
  points come from the interface files (``uilayout.py``, ``techtree.py``), the rotated tabs with
  their +40 GUI units, and the two 1.20 buttons from their measured 1080p positions scaled by
  ``ui_resolve.reference_point`` (a recorded ``research.confirm`` / ``research.mio_choice``
  wins).
* ``move_division`` / ``attack_province``: orders by click on the map (``unit_orders.py``,
  docs/unit-orders.md). Built against the fake game only.
* event and news windows (``event_windows.py``): nothing in this mode answers them and a click
  under one goes to the event, so every mouse tool first closes what is open, when a probe is
  configured (``event_windows`` in the profile), and refuses to click while one is open.
* ``delegate_army_to_ai``: hand an army to the game's AI, or take it back. The click path
  below is a DRAFT: the interface files do not say where the toggle is, so its points are
  recorded by a person (``hoi4-harness calibrate``), not computed. Built against the fake game
  only, and nothing reads the toggle back, so the result is always "unverified".

Every tool here goes through the input driver, so it keeps the driver's guards: the game window
must be in front (a campaign waits for it, it never types elsewhere), a point off the screen is
refused, and a dry run only logs. A tool whose prerequisites are not met is not offered, and
``Hands`` keeps the reason for the start log.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ui_scripts import Step, parse_steps

#: The actions that need the mouse in an independent campaign.
ARMY_ACTIONS = frozenset({"move_division", "attack_province", "delegate_army_to_ai"})
HAND_ACTIONS = frozenset({"start_research"}) | ARMY_ACTIONS
#: Battle plans by click (adapters/battle_plans.py): a fourth hand, off unless the profile asks
#: (``battle_plans = true``) and NOT live-verified, so not in HAND_ACTIONS.
PLAN_ACTIONS = ("draw_front_line", "draw_offensive", "assign_battle_plan")
#: Armies by click (adapters/armies.py): also off unless the profile asks (``army_ui = true``).
ARMY_UI_ACTIONS = ("create_army", "assign_divisions", "assign_army_front", "draw_fallback_line",
                   "set_army_execution", "release_army")

#: The live-verified 1.19 research click path (the same steps as profiles/ui_scripts.1.19.json).
RESEARCH_SCRIPT = [
    {"press": "production"}, {"wait": 0.5}, {"press": "research"}, {"wait": 0.8},
    {"click": "research.free_slot"}, {"wait": 0.8},
    {"click": "research.tab.{tech_folder}"}, {"wait": 0.5},
    {"click": "research.tech_point"}, {"wait": 0.8},
    {"press": "enter"}, {"wait": 0.8}, {"press": "enter"},
]
#: The 1.20 path (the same steps as profiles/ui_scripts.1.20.json): the technology opens a details
#: popup whose Research button Return does not press, then the MIO chooser, which ignores Return.
RESEARCH_SCRIPT_120 = RESEARCH_SCRIPT[:-3] + [
    {"click": "research.confirm"}, {"wait": 0.8},
    {"click": "research.mio_choice"}, {"wait": 0.5},
]
#: The version the 1.20 path is for.
RESEARCH_PATH_120 = "1.20.0"
#: The DRAFT delegation path: select the army in the army list, then press the AI toggle.
#: ``army.<name>`` and ``army.ai_control_toggle`` are recorded points (nothing computes them).
DELEGATE_TOGGLE = "army.ai_control_toggle"
DELEGATE_SCRIPT = [{"click": "army.{army}"}, {"wait": 0.5}, {"click": DELEGATE_TOGGLE}]


def research_path_for(version: str | None) -> str:
    """``"1.20"`` for a 1.20 game, else ``"1.19"`` (an unknown version keeps the old path)."""
    from ..baseai import same_minor

    return "1.20" if version and same_minor(version, RESEARCH_PATH_120) else "1.19"


def research_steps(version: str | None = None) -> list[Step]:
    """The research click path for the game's version (``baseai.game_version``)."""
    return parse_steps(RESEARCH_SCRIPT_120 if research_path_for(version) == "1.20"
                       else RESEARCH_SCRIPT)


def delegate_steps() -> list[Step]:
    return parse_steps(DELEGATE_SCRIPT)


def delegable_armies(coordinates: dict) -> list[str]:
    """The armies a person recorded a point for (``army.<name>``), toggle excluded."""
    return sorted(name[len("army."):] for name in coordinates
                  if name.startswith("army.") and name != DELEGATE_TOGGLE)


@dataclass
class Hands:
    """What an independent campaign's mouse can do, and why each other tool is off."""

    #: Whether the campaign asked for the mouse and the adapter has one.
    mouse: bool = False
    #: action -> why it is not offered.
    refused: dict[str, str] = field(default_factory=dict)
    #: The armies ``delegate_army_to_ai`` can name (recorded points), plus ``all`` if recorded.
    armies: list[str] = field(default_factory=list)
    #: The production click path (adapters/production_ui.py): recorded points and what is missing;
    #: empty when it was not wired.
    production: dict = field(default_factory=dict)
    #: Which research click path was chosen ("1.19" or "1.20") and why, for the start log.
    research_path: str = ""
    #: What the event-window guard does (adapters/event_windows.py): ``{"guarded": bool,
    #: "missing": [recorded points the answers click that are not recorded], "why": str}``.
    events: dict = field(default_factory=dict)
    #: Reading the open event and choosing its option (adapters/event_choice.py): ``{"on": bool,
    #: "why": str, "ocr": str}`` and the reader itself (None: off).
    event_reading: dict = field(default_factory=dict)
    event_reader: object = None

    def refuse(self, actions, reason: str) -> None:
        for action in ([actions] if isinstance(actions, str) else actions):
            self.refused.setdefault(action, reason)

    def live(self, offered) -> list[str]:
        return sorted(HAND_ACTIONS & set(offered))

    def lines(self, offered) -> list[str]:
        """The ``campaign.log`` lines for the start: which hands-on tools are live and which
        were refused, with the reason."""
        if not self.mouse:
            return ["mouse tools: off (campaign_input = none or no mouse in this adapter): "
                    "no research, no army orders; the tool list is the keyboard-and-console one"
                    + "".join(f"; {name}: {why}" for name, why in sorted(self.refused.items()))]
        live = self.live(offered)
        out = [("mouse tools live: " + ", ".join(live)) if live else "mouse tools live: none"]
        for name in sorted(HAND_ACTIONS - set(live)):
            out.append(f"mouse tool refused: {name}: {self.refused.get(name, 'not offered')}")
        if "delegate_army_to_ai" in live:
            out.append("delegate_army_to_ai is UNVERIFIED against the real game (draft click path, "
                       f"recorded points for: {', '.join(self.armies)}); the toggle cannot be read "
                       "back, so every result is 'unverified'")
        if self.research_path and "start_research" in live:
            out.append(f"research path: {self.research_path}")
        out.extend(self.event_lines())
        out.extend(self.event_reading_lines())
        out.extend(self.production_lines(offered))
        plans = sorted(set(PLAN_ACTIONS) & set(offered))
        if plans:
            out.append(f"battle plans live: {', '.join(plans)}: UNVERIFIED against the real game "
                       "(docs/unit-orders.md section 7)")
        elif PLAN_ACTIONS[0] in self.refused:
            out.append(f"battle plans not offered: {self.refused[PLAN_ACTIONS[0]]}")
        armies = sorted(set(ARMY_UI_ACTIONS) & set(offered))
        if armies:
            out.append(f"army actions live: {', '.join(armies)}: UNVERIFIED against the real game "
                       "(docs/armies.md); the next text save confirms or denies each")
        elif ARMY_UI_ACTIONS[0] in self.refused:
            out.append(f"army actions not offered: {self.refused[ARMY_UI_ACTIONS[0]]}")
        if {"move_division", "attack_province"} & set(live):
            out.append("division orders are UNVERIFIED against the real game (docs/unit-orders.md)")
        return out

    def event_lines(self) -> list[str]:
        """The start-log lines for event and news windows (issue #125)."""
        if not self.events:
            return []
        if not self.events.get("guarded"):
            return ["event windows: NOT guarded: " + self.events.get("why", "")
                    + "; nothing answers an event or closes a news window in this mode, and the "
                    "mouse tools click under one (docs/live-session.md section 14c)"]
        out = ["event windows: guarded (UNVERIFIED against the real game): every mouse tool "
               "first closes an open window (country: the first option; news: closed) and "
               "refuses to click while one is open; answers are in events.jsonl"]
        if self.events.get("missing"):
            out.append("event windows: a window that opens cannot be closed until these points "
                       "are recorded, and the mouse tools refuse meanwhile: "
                       + ", ".join(self.events["missing"]))
        return out

    def event_reading_lines(self) -> list[str]:
        """The start-log lines for reading events and choosing an option."""
        if not self.event_reading:
            return []
        if not self.event_reading.get("on"):
            return ["event reading: OFF: " + self.event_reading.get("why", "")
                    + "; a country event is answered blind (option 1) before a mouse tool "
                    "(docs/events.md)"]
        return ["event reading: ON (UNVERIFIED against the real game): the brief carries an "
                "open event with its options, read_event reads it, choose_event_option clicks an "
                f"option (ocr {self.event_reading.get('ocr', '?')}; layout "
                f"{self.event_reading.get('layout', '?')}); docs/events.md has the live steps"]

    def production_lines(self, offered) -> list[str]:
        """The start-log lines for production by click: what edits a line, what is missing."""
        if not self.production or "set_production" not in set(offered):
            why = self.refused.get("set_production") or self.refused.get(
                "change_production_equipment")
            return [f"production by mouse not offered: {why}"] if why else []
        edit, change = self.production["edit_missing"], self.production["equipment_missing"]
        out = []
        if edit:
            out.append("production by mouse: set_production can only OPEN a line (console) until "
                       "the counter points are recorded; an existing line is refused, naming: "
                       + ", ".join(edit))
        else:
            out.append("production by mouse live: set_production edits an existing line (+/- by "
                       "row), opens one only when none exists; UNVERIFIED against the real game "
                       "(docs/live-session.md section 14b)")
        if "change_production_equipment" in set(offered):
            out.append("change_production_equipment live (UNVERIFIED against the real game)")
        elif change:
            out.append("production by mouse: change_production_equipment not offered: record "
                       + ", ".join(change))
        return out

    def summary(self, offered) -> dict:
        """What the prompt reads (``HarnessConfig.campaign_hands``)."""
        offered = set(offered)
        production = {"edit": bool(self.production) and not self.production.get("edit_missing")
                      and "set_production" in offered,
                      "equipment": "change_production_equipment" in offered}
        return {"mouse": self.mouse, "offered": self.live(offered),
                "refused": dict(self.refused), "armies": list(self.armies),
                "production": production, "events": "choose_event_option" in offered}
