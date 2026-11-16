"""A tiny synthetic map, written in pure Python, for the map layer's tests.

``ART`` is the picture as a person would see it (top row first). The letters
are provinces; ``A`` is C-shaped and wraps ``S`` on three sides, so the centroid
of ``A`` falls in the sea -- the case an interior point exists for. ``D`` is a
one-pixel island that touches only ``S`` (it is joined to ``A`` by a strait in
adjacencies.csv instead). 11 pixels wide, so every row carries 3 padding bytes.
"""

from __future__ import annotations

import struct
from pathlib import Path

ART = [
    "AAAAAAAAAAB",
    "AAAAAAAAAAB",
    "AAAASSSSSBB",
    "AAAASSDSSBB",
    "AAAASSSSSBB",
    "AAAASSSSSBB",
    "AAAAAAAAAAB",
    "AAAAAAAAAAB",
]

COLOURS = {"A": (200, 30, 40), "S": (0, 0, 250), "B": (30, 200, 60), "D": (250, 250, 0)}
IDS = {"A": 1, "S": 2, "B": 3, "D": 4}


def bmp_bytes(art: list[str] = ART, bottom_up: bool = True) -> bytes:
    height, width = len(art), len(art[0])
    stride = (width * 3 + 3) & ~3
    rows = []
    for line in art:
        raw = b"".join(bytes(COLOURS[c][::-1]) for c in line)  # BMP is BGR
        rows.append(raw + b"\x00" * (stride - len(raw)))
    if bottom_up:
        rows.reverse()
    pixels = b"".join(rows)
    header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, height if bottom_up else -height,
                       1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


def write_game(root: Path, bottom_up: bool = True) -> Path:
    """A game folder holding just the map files and two states."""
    (root / "common").mkdir(parents=True, exist_ok=True)
    (root / "map").mkdir(exist_ok=True)
    (root / "history" / "states").mkdir(parents=True, exist_ok=True)
    (root / "map" / "provinces.bmp").write_bytes(bmp_bytes(bottom_up=bottom_up))
    (root / "map" / "definition.csv").write_text(
        "0;0;0;0;land;false;unknown;0\n"
        "1;200;30;40;land;true;plains;1\n"
        "2;0;0;250;sea;true;ocean;0\n"
        "3;30;200;60;land;false;hills;1\n"
        "4;250;250;0;land;true;forest;1\n", encoding="utf-8")
    (root / "map" / "adjacencies.csv").write_text(
        "From;To;Type;Through;start_x;start_y;stop_x;stop_y;adjacency_rule_name;Comment\n"
        "1;4;sea;2;-1;-1;-1;-1;;the strait\n"
        "1;3;sea;2;-1;-1;-1;-1;;already touching\n"
        "-1;-1;;-1;-1;-1;-1;-1;-1\n", encoding="utf-8")
    (root / "history" / "states" / "1-Crescent.txt").write_text(
        'state = { id = 1 name = "STATE_1" history = { owner = SWE } provinces = { 1 } }\n',
        encoding="utf-8")
    (root / "history" / "states" / "2-Islands.txt").write_text(
        'state = { id = 2 name = "STATE_2" history = { owner = FIN } provinces = { 3 4 } }\n',
        encoding="utf-8")
    return root
