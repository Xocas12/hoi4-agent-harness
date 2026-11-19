"""A 4x3 grid map and a whole stack around it, for the division-order tests (#85).

Provinces 1..12 run row by row, each a 20x20 pixel block; states are the columns::

        col0  col1  col2  col3
 row0     1     2     3     4
 row1     5     6     7     8
 row2     9    10    11    12
 state   10    11    12    13      (10, 11 are Swedish; 12, 13 German)

``stack(...)`` wires the same objects a live run has -- log reader, console
channel, input driver with a mouse, the executor -- to a ``FakeGame`` whose army
stands on this map: SWE has three divisions in province 6, GER two in province 7.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hoi4_harness.adapters.channels import ChannelRouter
from hoi4_harness.adapters.composite import CompositeAdapter
from hoi4_harness.adapters.console import ConsoleChannel, ConsoleConfig
from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter
from hoi4_harness.adapters.keys import ScanCodeKeys
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.adapters.unit_orders import (
    CameraRecord,
    OrderLedger,
    UnitOrderExecutor,
    console_centre,
    stamp_now,
)
from hoi4_harness.fakearmy import FakeArmy, true_camera_factory
from hoi4_harness.fakegame import FakeGame
from hoi4_harness.identifiers import Playset
from hoi4_harness.mapcamera import PerspectiveCamera, Viewport
from hoi4_harness.mapdata import MapLayer

COLS, ROWS, BLOCK = 4, 3, 20
SCREEN = (1920, 1080)
VIEWPORT = Viewport(1920, 1080, 0, 60, 0, 120)
STATES = {10: [1, 5, 9], 11: [2, 6, 10], 12: [3, 7, 11], 13: [4, 8, 12]}
OWNERS = {10: "SWE", 11: "SWE", 12: "GER", 13: "GER"}
FIT_PROVINCE = 6
#: Map pixels the unit counter sits from the block's middle: so "anchor0" and the
#: centroid are different points, as in the game.
COUNTER_SHIFT = (3, 2)


def _colour(pid: int) -> tuple[int, int, int]:
    return (20 * pid, 255 - 15 * pid, 40 + 10 * pid)


def _bmp() -> bytes:
    width, height = COLS * BLOCK, ROWS * BLOCK
    stride = (width * 3 + 3) & ~3
    rows = []
    for y in range(height):
        row = b""
        for x in range(width):
            pid = (y // BLOCK) * COLS + (x // BLOCK) + 1
            row += bytes(_colour(pid)[::-1])
        rows.append(row + b"\x00" * (stride - len(row)))
    pixels = b"".join(reversed(rows))
    header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


def write_grid_game(root: Path) -> Path:
    for sub in ("common", "map", "history/states"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root / "map" / "provinces.bmp").write_bytes(_bmp())
    defs = ["0;0;0;0;land;false;unknown;0"]
    for pid in range(1, COLS * ROWS + 1):
        r, g, b = _colour(pid)
        terrain = "hills" if pid == 3 else "plains"
        defs.append(f"{pid};{r};{g};{b};land;false;{terrain};1")
    (root / "map" / "definition.csv").write_text("\n".join(defs) + "\n", encoding="utf-8")
    (root / "map" / "adjacencies.csv").write_text(
        "From;To;Type;Through;start_x;start_y;stop_x;stop_y;adjacency_rule_name;Comment\n"
        "-1;-1;;-1;-1;-1;-1;-1;-1\n", encoding="utf-8")
    stacks = []
    height = ROWS * BLOCK
    for pid in range(1, COLS * ROWS + 1):
        cx = ((pid - 1) % COLS) * BLOCK + BLOCK // 2
        cy = ((pid - 1) // COLS) * BLOCK + BLOCK // 2
        stacks.append(f"{pid};0;{cx + COUNTER_SHIFT[0]};0.0;{height - (cy + COUNTER_SHIFT[1])};0;1.0")
        stacks.append(f"{pid};38;{cx};0.0;{height - cy};0;1.0")
    (root / "map" / "unitstacks.txt").write_text("\n".join(stacks) + "\n", encoding="utf-8")
    for sid, provinces in STATES.items():
        (root / "history" / "states" / f"{sid}-S.txt").write_text(
            f'state = {{ id = {sid} name = "STATE_{sid}" history = {{ owner = {OWNERS[sid]} }} '
            f'provinces = {{ {" ".join(map(str, provinces))} }} }}\n', encoding="utf-8")
    return root


def load_layer(root: Path) -> MapLayer:
    return MapLayer.load(Playset.from_paths(root), cache_dir=False)


def true_view() -> PerspectiveCamera:
    """The tilted camera when the view is centred on ``FIT_PROVINCE``: a is the
    px per map pixel, k how fast the map shrinks towards the top of the screen."""
    a, k = 10.0, 0.004
    cx = ((FIT_PROVINCE - 1) % COLS) * BLOCK + BLOCK // 2 + COUNTER_SHIFT[0]
    cy = ((FIT_PROVINCE - 1) // COLS) * BLOCK + BLOCK // 2 + COUNTER_SHIFT[1]
    mx, my = VIEWPORT.centre
    matrix = ((a, mx * k, mx - a * cx - mx * k * cy),
              (0.0, a + my * k, my - (a + my * k) * cy),
              (0.0, k, 1 - k * cy))
    return PerspectiveCamera(matrix, VIEWPORT)


def landmarks(layer: MapLayer, base, provinces=(1, 4, 9, 12, 7)) -> list[tuple[float, float, float, float]]:
    """(map x, map y, screen x, screen y) for victory-point markers, as read off the screen."""
    out = []
    for pid in provinces:
        mx, my = layer.anchor(pid, 38)
        sx, sy = base.to_screen(mx, my)
        out.append((mx, my, round(sx, 1), round(sy, 1)))
    return out


def camera_record(layer: MapLayer, **overrides) -> CameraRecord:
    base = true_view()
    fields = dict(screen=SCREEN, viewport=VIEWPORT, pairs=landmarks(layer, base),
                  fit_province=FIT_PROVINCE, centre_rule="anchor0", validated=True,
                  recorded=stamp_now(), counter_offset=(0, 0))
    fields.update(overrides)
    return CameraRecord(**fields)


@dataclass
class Stack:
    fake: FakeGame
    army: FakeArmy
    layer: MapLayer
    driver: InputDriverAdapter
    console: ConsoleChannel
    executor: UnitOrderExecutor
    adapter: CompositeAdapter
    gui: object

    def state(self):
        return self.adapter.read_state()


def stack(tmp_path: Path, *, record: CameraRecord | None = None, dry_run: bool = False,
          fake_rule: str = "anchor0", tolerance: float = 16.0, now=None) -> Stack:
    game = write_grid_game(tmp_path / "game")
    layer = load_layer(game)
    army = FakeArmy(
        layer, "SWE", [("SWE", 6, 3), ("GER", 7, 2)], {"GER"},
        true_camera_factory(layer, true_view(), FIT_PROVINCE, fake_rule),
        counter_tolerance=tolerance)
    fake = FakeGame(tmp_path / "user", country="SWE", army=army)
    gui = fake.gui
    driver = InputDriverAdapter(
        InputConfig(enforce_focus=False, mouse=True, screen=SCREEN, settle_seconds=0.0),
        dry_run=dry_run, gui=gui, sleep=fake.sleep, keys=ScanCodeKeys(send=fake.send, sleep=fake.sleep),
        verify_timeout=0.0)
    console = ConsoleChannel(
        driver, ConsoleConfig(directory=fake.user_dir, country="SWE", archive_dir=tmp_path / "runs"),
        sleep=fake.sleep)
    executor = UnitOrderExecutor(
        driver, layer, record or camera_record(layer), console_centre(console), sleep=fake.sleep,
        now=now or datetime.now, ledger=OrderLedger())
    driver.enable_unit_orders(executor)
    reader = LogTailAdapter(fake.log_path, country="SWE")
    adapter = CompositeAdapter(reader, ChannelRouter(driver, console), sleep=fake.sleep)
    fake.run_days(1)                      # the first daily tick prints the front picture
    return Stack(fake, army, layer, driver, console, executor, adapter, gui)
