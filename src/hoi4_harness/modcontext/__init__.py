"""What a playset lets a country do, and what its mods are about.

``ModContext`` reads the action surface from the merged game files (decisions,
focus trees, the technologies and mechanics a mod adds); ``workshop`` writes a
short world brief per mod; ``execute`` turns a decision into a guarded console
effect; ``prompt`` fits the lot into the system prompt. See ``docs/mods.md``.
"""

from .execute import DecisionPlan, plan_decision
from .prompt import playset_section
from .surface import ActionSurface, Decision, ModContext
from .workshop import ModBrief, mod_brief, playset_briefs

__all__ = ["ActionSurface", "Decision", "DecisionPlan", "ModBrief", "ModContext", "mod_brief",
           "plan_decision", "playset_briefs", "playset_section"]
