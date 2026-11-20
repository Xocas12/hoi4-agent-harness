"""Record the camera fit that division orders aim with (camera.json).

    PYTHONPATH=src python scripts/record_camera.py --game-dir "<HOI4 install>" \
        --fit-province 6050 --ref 6050:1488,579 --ref 9149:1441.5,418.5 \
        --ref 11091:1047,171 --ref 156:483,108 --ref 9228:1700,300 \
        --hold 130:1126,60 --out runs/camera.json

Do it in the live game, right after the console's ``goto_province <fit-province>``
and without moving or zooming the camera afterwards (docs/live-session.md, step 12).

``--ref PROVINCE:SX,SY`` is a victory-point province and where its marker is on
screen (at least four; five or more to be checked). ``--hold`` is a marker you
measured but kept out of the fit: if every held-out marker lands within 8 px of its
prediction the fit is written ``validated``; a fit of exactly four points is not,
whatever its residual, because four points fix a homography exactly.

The script also asks what the game centres on: it maps the middle of the viewport
back to the map and prints how far each candidate rule's point for the fit province
is from it (``anchor0`` the unit-counter anchor, ``anchor38`` the victory-point
marker, ``centroid``, ``interior``). ``--centre-rule auto`` writes the nearest, and
says so; a rule more than 15 map pixels off means none of them describes the game.
Nothing here touches the game or sends input.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hoi4_harness.adapters.unit_orders import CENTRE_RULES, CameraRecord, centre_point, stamp_now
from hoi4_harness.identifiers import Playset
from hoi4_harness.mapcamera import CameraModelError, Viewport, fit_camera
from hoi4_harness.mapdata import VP_MARKER, MapLayer

HOLD_TOLERANCE_PX = 8.0
CENTRE_TOLERANCE_MAP_PX = 15.0


def _pair(text: str) -> tuple[int, tuple[float, float]]:
    prov, xy = text.split(":")
    x, y = xy.split(",")
    return int(prov), (float(x), float(y))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--game-dir", required=True, type=Path)
    ap.add_argument("--mod-dir", action="append", default=[], type=Path)
    ap.add_argument("--fit-province", required=True, type=int)
    ap.add_argument("--ref", action="append", default=[], type=_pair)
    ap.add_argument("--hold", action="append", default=[], type=_pair)
    ap.add_argument("--centre-rule", default="auto", choices=("auto", *CENTRE_RULES))
    ap.add_argument("--counter-offset", default="0,0", help="screen px from the type-0 anchor, dx,dy")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--bottom", type=int, default=120)
    ap.add_argument("--out", type=Path, default=Path("runs/camera.json"))
    args = ap.parse_args(argv)

    layer = MapLayer.load(Playset.from_paths(args.game_dir, args.mod_dir))
    width, height = (int(v) for v in args.size.split("x"))
    view = Viewport(width, height, top=args.top, bottom=args.bottom)
    pairs = [(layer.anchor(p, VP_MARKER), s) for p, s in args.ref]
    try:
        fit = fit_camera(pairs, view)
    except CameraModelError as exc:
        print(f"refused: {exc}")
        return 1
    print(f"model: {fit.model} ({fit.points} points) -- {fit.note}")
    print("residuals (px):", ", ".join(f"{r:.2f}" for r in fit.residuals))

    errors = []
    for prov, seen in args.hold:
        sx, sy = fit.camera.to_screen(*layer.anchor(prov, VP_MARKER))
        errors.append(((sx - seen[0]) ** 2 + (sy - seen[1]) ** 2) ** 0.5)
        print(f"held out {prov}: predicted ({sx:.0f}, {sy:.0f}), seen {seen}, error {errors[-1]:.1f} px")
    validated = bool(errors) and max(errors) <= HOLD_TOLERANCE_PX
    print("validated:", validated, "" if validated else
          "(hold at least one marker out and have it land within 8 px)")

    mx, my = fit.camera.to_map(*view.centre)
    print(f"the viewport centre {tuple(round(v) for v in view.centre)} is map pixel ({mx:.1f}, {my:.1f}); "
          f"distance to each rule's point for province {args.fit_province}:")
    distance = {}
    for rule in CENTRE_RULES:
        px, py = centre_point(layer, args.fit_province, rule)
        distance[rule] = ((px - mx) ** 2 + (py - my) ** 2) ** 0.5
        print(f"  {rule:<9} ({px:.1f}, {py:.1f})  {distance[rule]:.1f} map px")
    rule = args.centre_rule if args.centre_rule != "auto" else min(distance, key=distance.get)
    if distance[rule] > CENTRE_TOLERANCE_MAP_PX:
        print(f"warning: {rule} is {distance[rule]:.0f} map px from where the game centred; the "
              "shifted camera for other provinces will be off by about that much")
    offset = tuple(int(v) for v in args.counter_offset.split(","))
    # What an unattended campaign checks in place of the record's age (UI scale, window mode,
    # resolution): the display the landmarks were read under.
    from hoi4_harness.adapters.input_driver import default_display_settings

    settings = default_display_settings()
    display = {"gui_scale": round(float(settings.gui_scale if settings else 1.0), 4),
               "mode": settings.mode if settings else None, "resolution": [width, height]}
    record = CameraRecord(
        screen=(width, height), viewport=view, pairs=[(m[0], m[1], s[0], s[1]) for m, s in pairs],
        fit_province=args.fit_province, centre_rule=rule, validated=validated,
        recorded=stamp_now(), counter_offset=offset, display=display)
    print("wrote", record.save(args.out), f"(centre rule {rule}, counter offset {offset})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
