"""Tactical controllers by name, for ``army_controller`` (control.py, auto/army.py).

One line per controller: a new one (the battle-plan front planner, say) is added by
registering its class here, nothing else changes. A controller is any class whose instances
have ``name`` and ``act(view) -> list[Order]`` (see controllers.py).
"""

from __future__ import annotations

from .controllers import ScriptedController, VanillaLikeController
from .learned import LearnedController
from .planner import planner_controller

REGISTRY = {
    "scripted": ScriptedController,
    "learned": LearnedController,
    "vanilla_like": VanillaLikeController,
    "planner": planner_controller,
}


def names() -> list[str]:
    return sorted(REGISTRY)


def make(name: str):
    """A fresh controller of the registered ``name``."""
    try:
        return REGISTRY[name]()
    except KeyError:
        raise KeyError(f"no tactical controller {name!r}: one of {', '.join(names())}") from None
