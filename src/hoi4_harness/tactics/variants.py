"""Procedurally varied copies of a scenario, so a learned controller is not trained on four maps (#85).

`vary(raw, vseed)` returns a `Scenario` made from the scenario dict `raw` with, by seed: some
open terrain swapped for another open terrain, some rivers added or removed, the stack sizes
of both sides nudged by one division, and the time limit stretched or squeezed a little. The
map's shape, hubs, objectives and goals are kept, so a variant is the same problem played
on slightly different ground. The variants are for training; they are not a benchmark.
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

from .scenario import SCENARIO_DIR, Scenario

OPEN = ("plains", "forest", "hills")


def vary(raw: dict[str, Any], vseed: int) -> Scenario:
    rng = random.Random(f"variant-{raw['name']}-{vseed}")
    out = copy.deepcopy(raw)
    out["name"] = f"{raw['name']}~v{vseed}"
    protected = {p for g in out["goals"].values() for p in g.get("provinces", ())}
    for p in out["provinces"]:
        if p["terrain"] in OPEN and not p.get("hub") and p["id"] not in protected and rng.random() < 0.15:
            p["terrain"] = rng.choice(OPEN)
    for e in out["edges"]:
        river = int(e[2]) if len(e) > 2 else 0
        roll = rng.random()
        if river == 0 and roll < 0.06:
            river = 1
        elif river > 0 and roll < 0.12:
            river = 0 if river == 1 else 1
        e[:] = [e[0], e[1], river]
    for d in out["divisions"]:
        if rng.random() < 0.3:
            d["count"] = max(1, int(d.get("count", 1)) + rng.choice((-1, 1)))
    out["max_hours"] = int(out["max_hours"] * rng.uniform(0.9, 1.15))
    return Scenario.from_dict(out)


def load_raw(name_or_path: str | Path) -> dict[str, Any]:
    path = Path(name_or_path)
    if not path.suffix:
        path = SCENARIO_DIR / f"{name_or_path}.json"
    return json.loads(path.read_text(encoding="utf-8"))
