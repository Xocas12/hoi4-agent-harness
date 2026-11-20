"""A scenario from the real map: states or provinces, terrain, adjacency, rivers (#85, stage 4).

`Scenario.from_dict` takes the shape a real-map loader would emit; this is that loader. It reads
a *layer* (anything with the small surface below, which `hoi4_harness.mapdata.MapLayer` has,
and so does `AbstractLayer`, the compact form committed for tests) and a *spec* naming the
front, and returns the dict.

    layer.provinces[pid]       .kind ("land"...), .terrain
    layer.states[sid]          .provinces, .owner
    layer.state_of(pid)
    layer.neighbours(pid, straits=False)

Granularity:

* ``state``: one simulator province per state (id ``s<state>``), terrain the commonest of its
  land provinces, adjacency where any two provinces touch, controller from the spec's sides.
  This is the resolution of the mod's front picture. ``region`` is not needed: the node is
  the state.
* ``province``: one simulator province per land province (id ``p<province>``), with
  ``region`` = the state it sits in, so the aggregated planner view gets states back.

Rivers are read from ``map/rivers.bmp`` when a path is given (`border_rivers`); without it every
edge is river-free. How a river on a border becomes the sim's none/small/large flag is a
heuristic of ours (thresholds below), not a game rule.

Nothing here copies game files: the output is ids, adjacency and a terrain name per node.
"""

from __future__ import annotations

import json
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

#: Map terrain -> the simulator's terrain table (anything else plays as plains).
TERRAIN = {
    "plains": "plains", "forest": "forest", "hills": "hills", "mountain": "mountain",
    "urban": "urban", "marsh": "marsh", "jungle": "jungle", "desert": "desert",
}
#: rivers.bmp palette indices that are river pixels (0-2 are source/junction markers, 3-11 widths);
#: 254 and 255 are "no river" in the install read. Widths from 7 up are called large (our reading).
RIVER_MAX_INDEX = 11
LARGE_FROM_INDEX = 7
#: Share of a border's pixels that must be river for the edge to count as a river crossing.
SMALL_SHARE = 0.25
LARGE_SHARE = 0.25   # of those river pixels, the share that must be wide for a large river
UNITS_PER_STEP = 12


@dataclass
class _Prov:
    kind: str
    terrain: str


@dataclass
class _State:
    provinces: list[int]
    owner: str | None = None


@dataclass
class AbstractLayer:
    """The few facts a loader reads, as plain data (see tests/fixtures/realmap_layer.json)."""

    provinces: dict[int, _Prov] = field(default_factory=dict)
    states: dict[int, _State] = field(default_factory=dict)
    adjacent: dict[int, list[int]] = field(default_factory=dict)
    centroids: dict[int, tuple[float, float]] = field(default_factory=dict)  # per state, map pixels

    def state_of(self, pid: int) -> int | None:
        for sid, st in self.states.items():
            if pid in st.provinces:
                return sid
        return None

    def neighbours(self, pid: int, straits: bool = True) -> list[int]:
        return sorted(self.adjacent.get(pid, ()))

    def state_centroid(self, sid: int) -> tuple[float, float]:
        return self.centroids.get(sid, (0.0, 0.0))

    @classmethod
    def from_dict(cls, raw: dict) -> AbstractLayer:
        return cls(
            provinces={int(k): _Prov(v["kind"], v["terrain"]) for k, v in raw["provinces"].items()},
            states={int(k): _State([int(p) for p in v["provinces"]], v.get("owner"))
                    for k, v in raw["states"].items()},
            adjacent={int(k): [int(n) for n in v] for k, v in raw["adjacent"].items()},
            centroids={int(k): (float(v[0]), float(v[1])) for k, v in raw.get("centroids", {}).items()},
        )

    @classmethod
    def load(cls, path: str | Path) -> AbstractLayer:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def extract(cls, layer, states: list[int], depth_provinces: bool = True) -> dict:
        """The compact dict for `states` of a real layer: ids, kind, terrain, adjacency, centroids."""
        provs = sorted(p for s in states for p in layer.states[s].provinces)
        keep = set(provs)
        return {
            "provinces": {str(p): {"kind": layer.provinces[p].kind, "terrain": layer.provinces[p].terrain}
                          for p in provs},
            "states": {str(s): {"provinces": sorted(layer.states[s].provinces),
                                "owner": layer.states[s].owner} for s in sorted(states)},
            "adjacent": {str(p): [n for n in layer.neighbours(p, straits=False) if n in keep] for p in provs},
            "centroids": {str(s): [round(v, 1) for v in layer.state_centroid(s)] for s in sorted(states)},
        }


def _land(layer, pid: int) -> bool:
    info = layer.provinces.get(pid)
    return info is not None and info.kind == "land"


def _terrain(layer, provinces: list[int]) -> str:
    names = [TERRAIN.get(layer.provinces[p].terrain, "plains") for p in provinces if _land(layer, p)]
    if not names:
        return "plains"
    counts = Counter(names)
    top = max(counts.values())
    return sorted(n for n, c in counts.items() if c == top)[0]


def border_rivers(layer, rivers_bmp: str | Path, states: list[int]) -> dict[tuple[int, int], int]:
    """River flag (0/1/2) for every pair of the given states that touch, from rivers.bmp.

    Walks the pixels of the states' bounding box once (needs a real `MapLayer`: it uses the
    province picture). On each border pixel pair it asks whether either pixel is a river pixel."""
    data = Path(rivers_bmp).read_bytes()
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height, _planes, bpp = struct.unpack_from("<iiHH", data, 18)
    if bpp != 8:
        raise ValueError("rivers.bmp is expected to be an 8-bit indexed picture")
    stride = (abs(width) + 3) & ~3
    bottom_up = height > 0
    height = abs(height)

    def river_index(x: int, y: int) -> int:
        row = height - 1 - y if bottom_up else y
        return data[offset + row * stride + x]

    wanted = set(states)
    of_prov = {p: s for s in states for p in layer.states[s].provinces}
    by_colour = {info.colour: pid for pid, info in layer.provinces.items()}
    boxes = [layer.state_bbox(s) for s in states]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    bmp = layer.picture()
    border: Counter = Counter()
    river: Counter = Counter()
    wide: Counter = Counter()

    def note(a: int | None, b: int | None, xa: int, ya: int, xb: int, yb: int) -> None:
        if a is None or b is None or a == b or a not in wanted or b not in wanted:
            return
        key = (min(a, b), max(a, b))
        border[key] += 1
        best = min(river_index(xa, ya), river_index(xb, yb))
        if best <= RIVER_MAX_INDEX:
            river[key] += 1
            wide[key] += best >= LARGE_FROM_INDEX

    below = bmp.row(y0)
    for y in range(y0, min(y1 + 1, bmp.height)):
        here = below
        below = bmp.row(y + 1) if y + 1 < bmp.height else None
        for x in range(x0, min(x1 + 1, bmp.width)):
            a = of_prov.get(by_colour.get(here[x]))
            if x + 1 < bmp.width:
                note(a, of_prov.get(by_colour.get(here[x + 1])), x, y, x + 1, y)
            if below is not None:
                note(a, of_prov.get(by_colour.get(below[x])), x, y, x, y + 1)
    out: dict[tuple[int, int], int] = {}
    for key, length in border.items():
        share = river[key] / length
        if share < SMALL_SHARE:
            out[key] = 0
        else:
            out[key] = 2 if wide[key] / max(river[key], 1) >= LARGE_SHARE else 1
    return out


def scenario_from_layer(layer, spec: dict, rivers: dict[tuple[int, int], int] | None = None) -> dict:
    """The scenario dict (the shape `Scenario.from_dict` reads) for `spec` on `layer`.

    spec: name, description, granularity ("state" | "province"), sides {"A": [states], "B": [states]},
    hubs {"A": [states], "B": [states]}, forces [{side, template, state, count}], goals,
    max_hours, step_hours. States not named in `sides` are left out (neutral ground)."""
    gran = spec.get("granularity", "state")
    if gran not in ("state", "province"):
        raise ValueError("granularity is 'state' or 'province'")
    side_of = {int(s): side for side, ids in spec["sides"].items() for s in ids}
    hub_states = {int(s): side for side, ids in spec.get("hubs", {}).items() for s in ids}
    for sid in side_of:
        if sid not in layer.states:
            raise ValueError(f"state {sid} is not on this map")
    rivers = rivers or {}

    def node(sid: int, pid: int | None = None) -> str:
        return f"s{sid}" if gran == "state" else f"p{pid}"

    provinces: list[dict] = []
    edges: set[tuple[str, str, int]] = set()
    members: dict[int, list[int]] = {sid: sorted(p for p in layer.states[sid].provinces if _land(layer, p))
                                     for sid in side_of}
    for sid in sorted(side_of):
        if not members[sid]:
            raise ValueError(f"state {sid} has no land province")
        x, y = layer.state_centroid(sid)
        if gran == "state":
            entry = {"id": node(sid), "terrain": _terrain(layer, members[sid]), "controller": side_of[sid],
                     "region": node(sid), "x": round(x / 10, 1), "y": round(y / 10, 1), "state": sid}
            if sid in hub_states:
                entry["hub"] = hub_states[sid]
            provinces.append(entry)
        else:
            hub_pid = members[sid][0]
            for pid in members[sid]:
                entry = {"id": node(sid, pid), "terrain": TERRAIN.get(layer.provinces[pid].terrain, "plains"),
                         "controller": side_of[sid], "region": f"s{sid}", "state": sid, "x": round(x / 10, 1),
                         "y": round(y / 10, 1)}
                if sid in hub_states and pid == hub_pid:
                    entry["hub"] = hub_states[sid]
                provinces.append(entry)
    seen: set[tuple[str, str]] = set()
    for sid in sorted(side_of):
        for pid in members[sid]:
            for nb in layer.neighbours(pid, straits=False):
                other = layer.state_of(nb)
                if other not in side_of or not _land(layer, nb):
                    continue
                if gran == "state":
                    if other == sid:
                        continue
                    a, b = node(sid), node(other)
                    river = rivers.get((min(sid, other), max(sid, other)), 0)
                else:
                    a, b = node(sid, pid), node(other, nb)
                    river = 0
                key = (min(a, b), max(a, b))
                if key not in seen:
                    seen.add(key)
                    edges.add((key[0], key[1], river))
    divisions = []
    for f in spec["forces"]:
        sid = int(f["state"])
        if sid not in side_of:
            raise ValueError(f"forces in state {sid}, which the scenario does not include")
        if side_of[sid] != f["side"]:
            raise ValueError(f"state {sid} is {side_of[sid]}'s, not {f['side']}'s")
        where = node(sid) if gran == "state" else node(sid, members[sid][0])
        divisions.append({"side": f["side"], "template": f["template"], "province": where,
                          "count": int(f.get("count", 1))})
    ids = {p["id"] for p in provinces}

    def goal_nodes(goal: dict) -> dict:
        if "states" not in goal:
            return goal
        out = {k: v for k, v in goal.items() if k != "states"}
        nodes = [node(int(s)) if gran == "state" else node(int(s), pid)
                 for s in goal["states"] for pid in ([None] if gran == "state" else members[int(s)])]
        out["provinces"] = nodes
        assert set(nodes) <= ids
        return out

    return {
        "name": spec["name"], "description": spec.get("description", ""), "scored_side": "A",
        "max_hours": int(spec.get("max_hours", 720)),
        "step_hours": int(spec.get("step_hours", UNITS_PER_STEP)),
        "provinces": provinces, "edges": [list(e) for e in sorted(edges)], "divisions": divisions,
        "goals": {side: goal_nodes(g) for side, g in spec["goals"].items()},
        "assumptions": spec.get("assumptions", []),
    }
