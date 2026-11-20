"""The mouse tools of an independent campaign: what is offered, and what was refused and why.

``campaign_control = "llm"`` with ``campaign_input = "mouse"`` (the default) adds the input
driver's mouse to the headless adapter, for the three jobs the console has no effect for:

* ``start_research``: press ``y`` then ``w`` (the panel hotkeys toggle, so the production panel
  first makes sure the research one opens rather than closes), click a free slot, the folder
  tab, the technology's computed grid cell, then Return twice (the second answers the
  military-industrial-organisation chooser some technologies raise). Verified live on
  2026-11-19 through the input driver; the points come from the interface files
  (``uilayout.py``, ``techtree.py``), the rotated tabs with their +40 GUI units.
* ``move_division`` / ``attack_province``: orders by click on the map (``unit_orders.py``,
  docs/unit-orders.md). Built against the fake game only.
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

#: The live-verified research click path (the same steps as profiles/ui_scripts.1.19.json).
RESEARCH_SCRIPT = [
    {"press": "production"}, {"wait": 0.5}, {"press": "research"}, {"wait": 0.8},
    {"click": "research.free_slot"}, {"wait": 0.8},
    {"click": "research.tab.{tech_folder}"}, {"wait": 0.5},
    {"click": "research.tech_point"}, {"wait": 0.8},
    {"press": "enter"}, {"wait": 0.8}, {"press": "enter"},
]
#: The DRAFT delegation path: select the army in the army list, then press the AI toggle.
#: ``army.<name>`` and ``army.ai_control_toggle`` are recorded points (nothing computes them).
DELEGATE_TOGGLE = "army.ai_control_toggle"
DELEGATE_SCRIPT = [{"click": "army.{army}"}, {"wait": 0.5}, {"click": DELEGATE_TOGGLE}]


def research_steps() -> list[Step]:
    return parse_steps(RESEARCH_SCRIPT)


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
        plans = sorted(set(PLAN_ACTIONS) & set(offered))
        if plans:
            out.append(f"battle plans live: {', '.join(plans)}: UNVERIFIED against the real game "
                       "(docs/unit-orders.md section 7)")
        elif PLAN_ACTIONS[0] in self.refused:
            out.append(f"battle plans not offered: {self.refused[PLAN_ACTIONS[0]]}")
        if {"move_division", "attack_province"} & set(live):
            out.append("division orders are UNVERIFIED against the real game (docs/unit-orders.md)")
        return out

    def summary(self, offered) -> dict:
        """What the prompt reads (``HarnessConfig.campaign_hands``)."""
        return {"mouse": self.mouse, "offered": self.live(offered),
                "refused": dict(self.refused), "armies": list(self.armies)}
