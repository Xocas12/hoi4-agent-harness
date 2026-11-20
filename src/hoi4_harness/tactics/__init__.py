"""A small land-combat simulator for training and scoring micro controllers (#85, stage 2).

    from hoi4_harness.tactics import TacticalEnv, load_scenario
    env = TacticalEnv()
    env.reset("hold_line", seed=3)
    env.step([Order("A1", "move", "c2r1")])

Pure Python, no dependencies. See docs/tactics.md for what it models and what it does not.
"""

from .controllers import CONTROLLERS, ScriptedController, VanillaLikeController
from .env import StepResult, TacticalEnv
from .learned import LearnedController
from .model import Order
from .planner import planner_controller, vanilla_plan_controller
from .plans import BattlePlan, EngineExecutor, PlanController
from .scenario import Scenario, list_scenarios, load_scenario

CONTROLLERS["learned"] = LearnedController
CONTROLLERS["vanilla_plans"] = vanilla_plan_controller
CONTROLLERS["planner"] = planner_controller
CONTROLLERS["planner_full"] = lambda: planner_controller("full")

__all__ = [
    "BattlePlan",
    "CONTROLLERS",
    "EngineExecutor",
    "PlanController",
    "LearnedController",
    "Order",
    "Scenario",
    "ScriptedController",
    "StepResult",
    "TacticalEnv",
    "VanillaLikeController",
    "list_scenarios",
    "load_scenario",
]
