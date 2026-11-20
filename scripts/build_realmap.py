"""Build the real-map scenario and its compact test fixture from the game files (read only).

    PYTHONPATH=src python scripts/build_realmap.py --game-dir "<the Hearts of Iron IV folder>"

Writes src/hoi4_harness/tactics/data/realmap/<spec name>.json (the scenario) and a compact test
fixture (a few dozen provinces: ids, adjacency, terrain): tests/fixtures/realmap_layer.json for the
Poland spec, realmap_layer_<name>.json for a spec that lists "fixture_states". Another front:
--spec src/hoi4_harness/tactics/data/realmap/germany_france_1940.spec.json"""

import argparse
import json
from pathlib import Path

from hoi4_harness.identifiers import Playset
from hoi4_harness.mapdata import MapLayer
from hoi4_harness.tactics.realmap import AbstractLayer, border_rivers, scenario_from_layer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "hoi4_harness" / "tactics" / "data" / "realmap"
#: States the compact fixture keeps: the Silesian corner of the front, one German, one Polish, one Slovak.
FIXTURE_STATES = [66, 67, 72, 88, 762]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", required=True)
    ap.add_argument("--spec", default=str(DATA / "germany_poland_1939.spec.json"))
    args = ap.parse_args()
    layer = MapLayer.load(Playset.from_paths(Path(args.game_dir)))
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    states = [int(s) for ids in spec["sides"].values() for s in ids]
    rivers = border_rivers(layer, Path(args.game_dir) / "map" / "rivers.bmp", states)
    raw = scenario_from_layer(layer, spec, rivers)
    out = DATA / f"{spec['name']}.json"
    out.write_text(json.dumps(raw, indent=1) + "\n", encoding="utf-8")
    print(f"{out}: {len(raw['provinces'])} nodes, {len(raw['edges'])} edges, "
          f"{sum(1 for e in raw['edges'] if e[2])} with a river")
    keep = spec.get("fixture_states")
    fixture = AbstractLayer.extract(layer, [int(s) for s in keep] if keep else FIXTURE_STATES)
    fname = f"realmap_layer_{spec['name']}.json" if keep else "realmap_layer.json"
    path = ROOT / "tests" / "fixtures" / fname
    path.write_text(json.dumps(fixture, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{path}: {len(fixture['provinces'])} provinces")


if __name__ == "__main__":
    main()
