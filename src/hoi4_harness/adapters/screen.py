"""Vision adapter: read the game the way a person does.

Status: skeleton. Capture works once ``mss`` is installed; turning pixels into a
:class:`GameState` is left to a vision model call, wired through the same
provider-neutral LLM layer the planner uses.

This adapter exists because it is the only one that sees *everything the player
sees* -- alerts, the map, combat -- and the only one that works on ironman. It is
also the slowest and the most expensive per observation, so the intended pattern
is: savegame or mock for the numbers, screen for the handful of things numbers
cannot express, and never once per tick.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..types import ActionCall, ActionResult, GameState
from .base import AdapterInfo, GameAdapter


@dataclass
class Region:
    """A named rectangle of the HOI4 UI, in 1920x1080 reference coordinates."""

    name: str
    x: int
    y: int
    w: int
    h: int


# Scaled by the actual window size at capture time. Values are a starting point,
# not a promise: they need calibrating per resolution and UI scale.
REGIONS = [
    Region("top_bar", 0, 0, 1920, 40),        # date, PP, manpower, factories, speed
    Region("alerts", 1400, 40, 520, 120),     # the icons that mean "something needs you"
    Region("outliner", 1640, 160, 280, 720),  # focus, research, production, armies
]


class ScreenAdapter(GameAdapter):
    supported_actions = frozenset()

    def __init__(self, window_title: str = "Hearts of Iron IV", out_dir: Path | None = None):
        self.window_title = window_title
        self.out_dir = out_dir or Path("screenshots")

    def info(self) -> AdapterInfo:
        return AdapterInfo(
            name="screen",
            readable=True,
            writable=False,
            clock_control=False,
            notes="Requires the 'input' extra (mss). Vision-model call per observation.",
        )

    def capture(self, region: Region | None = None) -> bytes:
        """Grab a PNG of the game window, or one region of it."""
        try:
            import mss
            import mss.tools
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("pip install 'hoi4-agent-harness[input]' for screen capture") from exc

        with mss.mss() as sct:
            monitor = sct.monitors[1]
            box = (
                {
                    "left": monitor["left"] + region.x,
                    "top": monitor["top"] + region.y,
                    "width": region.w,
                    "height": region.h,
                }
                if region
                else monitor
            )
            shot = sct.grab(box)
            return mss.tools.to_png(shot.rgb, shot.size)

    def read_state(self) -> GameState:
        raise NotImplementedError(
            "ScreenAdapter.read_state needs a vision model. Capture with .capture(), send the "
            "PNG through agent.llm with a structured-output schema matching GameState, and fill "
            "unknown_fields for anything the model would not commit to."
        )

    def apply(self, call: ActionCall) -> ActionResult:
        return self.unsupported(call)

    def advance(self, days: int) -> GameState:
        raise NotImplementedError("Pair ScreenAdapter with a clock-capable sink.")
