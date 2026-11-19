"""Shared builders for the tactical simulator tests: tiny hand-made maps."""

from hoi4_harness.tactics import Scenario
from hoi4_harness.tactics.sim import World


class StubRng:
    """Always rolls the extreme: every attempt hits, every die lands on its top face."""

    def random(self):
        return 0.0

    def randrange(self, n):
        return 0

    def randint(self, lo, hi):
        return hi


DEFAULT_GOALS = {"A": {"kind": "hold", "provinces": ["p0"]}, "B": {"kind": "capture", "provinces": ["p0"]}}


def _hub(hubs, i):
    return {"hub": hubs[f"p{i}"]} if f"p{i}" in hubs else {}


def line_scenario(terrains, controllers, divisions, hubs=None, rivers=None, goals=None, max_hours=100):
    """A chain p0-p1-p2-... with the given terrain and controller per province."""
    hubs = hubs or {"p0": "A", f"p{len(terrains) - 1}": "B"}
    rivers = rivers or {}
    provinces = [
        {"id": f"p{i}", "terrain": t, "controller": controllers[i], **_hub(hubs, i)}
        for i, t in enumerate(terrains)
    ]
    edges = [[f"p{i}", f"p{i + 1}", rivers.get(i, 0)] for i in range(len(terrains) - 1)]
    return Scenario.from_dict({
        "name": "tiny", "max_hours": max_hours, "provinces": provinces, "edges": edges,
        "divisions": divisions,
        "goals": goals or DEFAULT_GOALS,
    })


def tiny_world(terrains, controllers, divisions, seed=0, **kw):
    return World(line_scenario(terrains, controllers, divisions, **kw), seed)
