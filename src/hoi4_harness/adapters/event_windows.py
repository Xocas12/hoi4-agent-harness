"""Event and news windows in an independent campaign (issue #125).

``--control llm`` does not send ``human_ai``, so nothing in the game answers a country event or
closes a news window: they stay on screen, and every mouse tool clicks fixed points that one can
cover (seen live on 2026-11-21: the Rhineland news lay over the technology popup's Research
button and the research was rejected).

What the harness can and cannot know, checked against the 1.20 install's documentation:

* The mod cannot log an event as the player receives it: the game has no on_action for that
  (``common/on_actions`` has hooks for wars, capitulation, elections, not for events), and a mod
  cannot add to a vanilla event's own ``immediate`` block.
* The save has no pending-event list the reader sees (``adapters/savegame.py`` reads none).
* The console cannot answer one: ``event <id> <TAG>`` *fires* an event, and no console command or
  effect picks an option of a window already open. Only the game's AI answers events for a
  country, which is the ``human_ai`` mode this one exists to avoid.

So the window itself is the only witness, and the only way to close it is the way a person does:
a click. This module is the guard around that:

* ``EventGuard.probe`` says whether a window is open and which kind (``country`` or ``news``).
  The live probe (``PixelProbe``) reads one screen pixel per kind against a recorded colour. The
  points and colours must be measured in the live game (docs/live-session.md section 14c); with
  none recorded there is no guard, and the start log says so.
* Before any mouse tool runs, the driver asks the guard to clear what is open: each window kind
  has a recorded answer (a click on ``event.option1``, the first option of a country event, or on
  ``event.news_close``). Every answer is recorded (``events.jsonl`` in the run dir, the tool's
  result under ``changed.events_answered``, the driver log). A window that will not close, or
  whose answer has no recorded point, refuses the tool: nothing is clicked while one is open.
* Each click of a tool checks the probe again, so a window that opens in the middle of a click
  path stops the rest of it.

The first option is not always the right answer, and it is the one the harness takes when nothing
better is known. Since ``event_choice.py`` the window CAN be read (OCR matched to the playset's
events) and the model can choose with ``choose_event_option``; this blind answer is what remains
for a window the model did not answer or the reader could not identify, and the brief says so
(docs/events.md). The record names the event when the reader knew it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .ui_scripts import parse_steps

#: The window kinds the guard knows, and the default answer to each (steps of ui_scripts.py).
KINDS = ("country", "news")
DEFAULT_ANSWERS: dict[str, list[dict]] = {
    # The first option of a country event: its position depends on the text, so it is a recorded
    # point (a person puts it where the first option usually is), not a computed one.
    "country": [{"click": "event.option1"}, {"wait": 0.8}],
    "news": [{"click": "event.news_close"}, {"wait": 0.8}],
}
#: What a person records for each kind: ``hoi4-harness calibrate event.option1 event.news_close``.
ANSWER_POINTS = {"country": "event.option1", "news": "event.news_close"}


class EventOpen(RuntimeError):
    """An event window is open and could not be closed: no mouse tool may click."""


@dataclass(frozen=True)
class EventSighting:
    kind: str                # country | news
    title: str = ""


class PixelProbe:
    """Reads the window kind from one screen pixel each: ``{"country": {"x", "y", "rgb",
    "tolerance"}, "news": {...}}``. A pixel within ``tolerance`` (per channel) of ``rgb`` means the
    window is open. Needs ``mss`` (the ``input`` extra). The points are screen pixels at the
    resolution the run uses; the colours are what the live game draws (to be measured)."""

    def __init__(self, probes: dict,
                 grab: Callable[[int, int], tuple[int, int, int]] | None = None):
        bad = sorted(set(probes) - set(KINDS))
        if bad:
            raise ValueError(f"event_windows.probes: unknown kind(s) {', '.join(bad)}; known: "
                             f"{', '.join(KINDS)}")
        self.probes = {}
        for kind, spec in probes.items():
            try:
                rgb = tuple(int(v) for v in spec["rgb"])
                self.probes[kind] = (int(spec["x"]), int(spec["y"]), rgb,
                                     int(spec.get("tolerance", 24)))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"event_windows.probes.{kind} needs x, y and rgb [r, g, b] "
                                 f"({exc})") from None
            if len(rgb) != 3:
                raise ValueError(f"event_windows.probes.{kind}.rgb must be three numbers")
        self._grab = grab or _mss_pixel

    def __call__(self) -> EventSighting | None:
        for kind in KINDS:
            if kind not in self.probes:
                continue
            x, y, rgb, tolerance = self.probes[kind]
            seen = self._grab(x, y)
            if all(abs(a - b) <= tolerance for a, b in zip(seen, rgb, strict=True)):
                return EventSighting(kind)
        return None


def _mss_pixel(x: int, y: int) -> tuple[int, int, int]:  # pragma: no cover - needs a screen
    try:
        import mss
    except ImportError as exc:
        raise RuntimeError("pip install 'hoi4-agent-harness[input]' for the event probe") from exc
    with mss.mss() as sct:
        shot = sct.grab({"left": x, "top": y, "width": 1, "height": 1})
        return tuple(shot.pixel(0, 0))[:3]


class EventGuard:
    """Clears the event windows that are open before a mouse tool runs, and records how."""

    def __init__(self, probe: Callable[[], EventSighting | None],
                 answers: dict[str, list[dict]] | None = None, *,
                 sleep: Callable[[float], None] = time.sleep, attempts: int = 3,
                 settle: float = 0.5, log_path: Path | None = None):
        raw = {**DEFAULT_ANSWERS, **(answers or {})}
        self.answers = {kind: parse_steps(steps) for kind, steps in raw.items()}
        self.probe = probe
        self.attempts = attempts
        self.settle = settle
        self._sleep = sleep
        self.log_path = log_path
        #: Names the open event when the event reader knows it (adapters/event_choice.py), so a
        #: blind answer says WHICH event it answered. None: the probe's title, if any.
        self.namer: Callable[[], str] | None = None
        #: Every answer given, in order (also appended to ``log_path`` as JSON lines).
        self.record: list[dict] = []

    def needed_points(self) -> list[str]:
        """The recorded points the answers click."""
        return sorted({s.value for steps in self.answers.values() for s in steps
                       if s.kind == "click"})

    def missing(self, coordinates: dict) -> list[str]:
        return [p for p in self.needed_points() if p not in coordinates]

    def clear(self, driver, tool: str) -> list[dict]:
        """Close every open window before ``tool`` clicks. Returns what was answered (possibly
        nothing); raises ``EventOpen`` when a window is still open afterwards or cannot be
        answered. Nothing is clicked for a tool when this raises."""
        answered: list[dict] = []
        for attempt in range(1, self.attempts + 1):
            sighting = self.probe()
            if sighting is None:
                return answered
            steps = self.answers.get(sighting.kind)
            have = set(driver.config.coordinates)
            missing = [s.value for s in (steps or []) if s.kind == "click" and s.value not in have]
            if steps is None or missing:
                raise EventOpen(
                    f"a {sighting.kind} event window is open and {tool} was not run: there is no "
                    f"recorded way to close it (record {', '.join(missing) or sighting.kind}: "
                    f"hoi4-harness calibrate {' '.join(missing)}). Nothing was clicked")
            entry = {"t": round(time.time(), 3), "tool": tool, "kind": sighting.kind,
                     "title": sighting.title or (self.namer() if self.namer else ""),
                     "attempt": attempt,
                     "answer": ("first option (event.option1), blind" if sighting.kind == "country"
                                else "closed (event.news_close)")}
            driver.run_event_steps(steps)
            self._sleep(self.settle)
            entry["closed"] = self.probe() is None
            self.record.append(entry)
            answered.append(entry)
            self._write(entry)
            driver.log.append(f"event window: {entry['kind']}, {entry['answer']}, "
                              f"{'closed' if entry['closed'] else 'still open'} (before {tool})")
        if self.probe() is not None:
            raise EventOpen(f"an event window is still open after {self.attempts} answers, so "
                            f"{tool} was not run. Nothing further was clicked")
        return answered

    def _write(self, entry: dict) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError:
            pass


def guard_from_config(spec: dict, run_dir: Path, *, sleep: Callable[[float], None],
                      probe: Callable[[], EventSighting | None] | None = None) -> EventGuard:
    """``HarnessConfig.event_windows`` -> a guard. ``probe`` stands in for the pixel probe (the
    fake game's window)."""
    if probe is None:
        probes = spec.get("probes")
        if not probes:
            raise ValueError("event_windows needs probes (a screen pixel and colour per window "
                             "kind: docs/live-session.md section 14c)")
        probe = PixelProbe(probes)
    return EventGuard(probe, spec.get("answers"), sleep=sleep,
                      attempts=int(spec.get("attempts", 3)), settle=float(spec.get("settle", 0.5)),
                      log_path=Path(run_dir) / "events.jsonl")
