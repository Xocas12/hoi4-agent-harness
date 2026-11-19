"""Fit the map camera from measured city markers and predict where others are.

    PYTHONPATH=src python scripts/predict_markers.py \
        --game-dir "<HOI4 install>" --ref 6050:1488,579 --ref 9149:1441.5,418.5 \
        --ref 11091:1047,171 --ref 156:483,108 --target 130 --target 383

``--ref PROVINCE:SX,SY`` is a victory-point province and where its marker is on
screen (at least four). ``--target PROVINCE`` is a province to predict; give
``--hold PROVINCE:SX,SY`` for a city you measured but want held out, and the
error is printed. Reads ``map/unitstacks.txt`` (the type-38 anchor) and the VP
names from localisation; nothing here touches the game or sends input.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from hoi4_harness.mapcamera import CameraModelError, Viewport, fit_camera
from hoi4_harness.mapdata import VP_MARKER, read_unitstacks


def _pair(text: str) -> tuple[int, tuple[float, float]]:
    prov, xy = text.split(":")
    x, y = xy.split(",")
    return int(prov), (float(x), float(y))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game-dir", required=True, type=Path)
    ap.add_argument("--ref", action="append", default=[], type=_pair)
    ap.add_argument("--hold", action="append", default=[], type=_pair)
    ap.add_argument("--target", action="append", default=[], type=int)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--bottom", type=int, default=120)
    args = ap.parse_args(argv)

    stacks = read_unitstacks((args.game_dir / "map" / "unitstacks.txt").read_text(
        encoding="utf-8-sig", errors="replace"))
    names: dict[int, str] = {}
    loc = args.game_dir / "localisation" / "english" / "victory_points_l_english.yml"
    if loc.is_file():
        for m in re.finditer(r'^\s*VICTORY_POINTS_(\d+):\d*\s*"([^"]*)"',
                             loc.read_text(encoding="utf-8-sig", errors="replace"), re.M):
            names[int(m[1])] = m[2]

    def anchor(prov: int) -> tuple[float, float]:
        p = stacks[prov][VP_MARKER]
        return p.x, p.y

    w, h = (int(v) for v in args.size.split("x"))
    view = Viewport(w, h, top=args.top, bottom=args.bottom)
    try:
        fit = fit_camera([(anchor(p), s) for p, s in args.ref], view)
    except CameraModelError as exc:
        print(f"refused: {exc}")
        return 1
    cam = fit.camera
    print(f"model: {fit.model} ({fit.points} points) -- {fit.note}")
    print("residuals (px):", ", ".join(f"{r:.2f}" for r in fit.residuals),
          "| flat-model residuals:", ", ".join(f"{r:.0f}" for r in fit.flat_residuals))
    if fit.model == "perspective":
        for row in cam.matrix:
            print("  " + "  ".join(f"{v: .6g}" for v in row))
        print(f"conditioning {cam.conditioning:.3g}")
    wanted = [(p, None) for p in args.target] + list(args.hold)
    for prov, seen in wanted:
        sx, sy = cam.to_screen(*anchor(prov))
        where = "on screen" if view.contains(sx, sy) else "OFF screen"
        line = f"{names.get(prov, '?'):<12} {prov:>6}  predicted ({sx:7.0f}, {sy:7.0f}) {where}"
        if seen is not None:
            err = ((sx - seen[0]) ** 2 + (sy - seen[1]) ** 2) ** 0.5
            line += f"  seen {seen}  error {err:.1f} px"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
