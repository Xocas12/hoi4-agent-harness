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


def grid_scenario(cols, rows, split, divisions, goals=None, max_hours=200, terrain="plains"):
    """A cols x rows grid of provinces c<x>r<y>: columns before `split` are A's, the rest B's."""
    provinces, edges = [], []
    for x in range(cols):
        for y in range(rows):
            p = {"id": f"c{x}r{y}", "terrain": terrain, "controller": "A" if x < split else "B",
                 "x": x, "y": y}
            if (x, y) == (0, 0):
                p["hub"] = "A"
            if (x, y) == (cols - 1, 0):
                p["hub"] = "B"
            provinces.append(p)
            if x + 1 < cols:
                edges.append([f"c{x}r{y}", f"c{x + 1}r{y}", 0])
            if y + 1 < rows:
                edges.append([f"c{x}r{y}", f"c{x}r{y + 1}", 0])
    return Scenario.from_dict({
        "name": "grid", "max_hours": max_hours, "provinces": provinces, "edges": edges,
        "divisions": divisions,
        "goals": goals or {"A": {"kind": "capture", "provinces": [f"c{cols - 1}r{rows - 1}"]},
                           "B": {"kind": "hold", "provinces": [f"c{cols - 1}r{rows - 1}"]}},
    })


def graph_scenario(nodes, edges, divisions, goals, max_hours=200):
    """An explicit graph. nodes: id -> (controller, hub or None); every province is its own region."""
    provinces = [{"id": n, "terrain": "plains", "controller": c, "region": n,
                  **({"hub": h} if h else {})} for n, (c, h) in nodes.items()]
    return Scenario.from_dict({
        "name": "graph", "max_hours": max_hours, "provinces": provinces,
        "edges": [[a, b, 0] for a, b in edges], "divisions": divisions, "goals": goals,
    })
