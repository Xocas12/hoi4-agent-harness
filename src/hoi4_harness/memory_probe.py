"""``hoi4-harness memory-probe``: how fast does hoi4.exe's memory grow (issue #157)?

HOI4 1.20 leaked about 30 GB an hour in the 2026-11-22 campaigns, and the cause is one of a few
candidates (the game itself, ``-debug_mode``, the mod's daily logging, the console path, text
saves). This measures one setup at a time: it records the game's private bytes every minute to a
CSV, then writes a summary with the growth in GB per hour (a least-squares slope), so two setups
can be compared on the same save.

The probe never starts, focuses or touches the game, and sends no key. It only reads the process's
private bytes (and the in-game date from game.log when it can) while the person has the game
running the way the variant says. ``VARIANTS`` holds that setup for each variant; the ones that
need a different mod list use ``scripts/live_setup.py``, the rest are launch or console steps that
are printed exactly.
"""

from __future__ import annotations

import csv
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

GB = 1024 ** 3
#: Stop when the game has been unreadable this many samples in a row (it exited or crashed).
MAX_MISSES = 3
CSV_COLUMNS = ("elapsed_s", "time", "private_bytes", "private_gb", "game_date")


@dataclass(frozen=True)
class Variant:
    name: str
    question: str
    steps: tuple[str, ...]


_LAUNCH_DEBUG = ("Launch the game with -debug_mode: Steam > HOI4 > Properties > Launch options "
                 "`-gdpr-compliant -debug_mode`, or run `hoi4.exe -gdpr-compliant -debug_mode`.")
_LAUNCH_PLAIN = ("Launch the game WITHOUT -debug_mode: clear the Steam launch options, or run "
                 "`hoi4.exe -gdpr-compliant`.")
_MOD_ON = ("Close the game, then `python scripts/live_setup.py prepare --apply` (installs and enables "
           "llm_bridge, last in the mod list).")
_MOD_OFF = ("Close the game, then `python scripts/live_setup.py prepare --no-mod --apply` (the same "
            "backup and settings, but llm_bridge is left OUT of dlc_load.json).")
_LOAD = ("Single Player > Load > the save named below > Load > START. Set the game speed to 5 and "
         "unpause. Do not touch the game, and run no harness, until the probe finishes.")
_RESTORE = "Afterwards: `python scripts/live_setup.py restore --apply` puts the mod list back."

VARIANTS: dict[str, Variant] = {v.name: v for v in (
    Variant("mod", "the baseline with the LLM Bridge mod: the daily telemetry, -debug_mode",
            (_MOD_ON, _LAUNCH_DEBUG, _LOAD)),
    Variant("nomod", "the same save with the mod OFF (does the game or the mod leak?)",
            (_MOD_OFF, _LAUNCH_DEBUG, _LOAD, _RESTORE)),
    Variant("debug", "the mod's setup, started WITH -debug_mode (pairs with nodebug)",
            (_MOD_ON, _LAUNCH_DEBUG, _LOAD)),
    Variant("nodebug", "the mod's setup, started WITHOUT -debug_mode (the mod still logs; the "
                       "console does not work, which the probe does not need)",
            (_MOD_ON, _LAUNCH_PLAIN, _LOAD)),
    Variant("log-daily", "the mod's telemetry once per in-game day (its default; pairs with "
                         "log-weekly)",
            (_MOD_ON, _LAUNCH_DEBUG,
             "Load and unpause as below, but FIRST mark a country and set the cadence in the "
             "console (the grave key): `effect <TAG> llm_bridge_mark_harness` then "
             "`effect <TAG> llm_bridge_telemetry_every_24h` (<TAG> is your country, e.g. GER).",
             _LOAD)),
    Variant("log-weekly", "the mod's telemetry once per in-game week (one tick in seven)",
            (_MOD_ON, _LAUNCH_DEBUG,
             "Load and unpause as below, but FIRST mark a country and set the cadence in the "
             "console (the grave key): `effect <TAG> llm_bridge_mark_harness` then "
             "`effect <TAG> llm_bridge_telemetry_every_168h` (<TAG> is your country, e.g. GER).",
             _LOAD)),
)}


def manual_steps(variant: str, save: str) -> list[str]:
    """The numbered steps a person follows before the probe starts sampling."""
    chosen = VARIANTS[variant]
    steps = [*chosen.steps, f"The save to load: {save}"]
    return [f"{n}. {text}" for n, text in enumerate(steps, 1)]


# --- the numbers ----------------------------------------------------------------------------------


def slope_gb_per_hour(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Least-squares slope of ``(elapsed seconds, GB)`` in GB per hour, and R squared. None for
    fewer than two points or no spread in time."""
    n = len(points)
    if n < 2:
        return None
    mean_t = sum(t for t, _ in points) / n
    mean_g = sum(g for _, g in points) / n
    var_t = sum((t - mean_t) ** 2 for t, _ in points)
    if var_t == 0:
        return None
    cov = sum((t - mean_t) * (g - mean_g) for t, g in points)
    slope = cov / var_t
    var_g = sum((g - mean_g) ** 2 for _, g in points)
    r2 = 1.0 if var_g == 0 else (cov * cov) / (var_t * var_g)
    return slope * 3600.0, r2


@dataclass
class ProbeResult:
    variant: str
    save: str
    label: str
    minutes_asked: float
    rows: list[dict] = field(default_factory=list)
    stopped: str = ""
    csv_path: Path | None = None
    summary_path: Path | None = None

    def summary(self) -> dict:
        good = [(r["elapsed_s"], r["private_gb"]) for r in self.rows if r["private_gb"] is not None]
        fit = slope_gb_per_hour(good)
        dates = [r["game_date"] for r in self.rows if r.get("game_date")]
        return {
            "variant": self.variant, "label": self.label, "save": self.save,
            "minutes_asked": self.minutes_asked,
            "minutes_measured": round(good[-1][0] / 60, 1) if good else 0.0,
            "samples": len(good), "missed": len(self.rows) - len(good),
            "start_gb": round(good[0][1], 3) if good else None,
            "end_gb": round(good[-1][1], 3) if good else None,
            "slope_gb_per_hour": round(fit[0], 2) if fit else None,
            "r_squared": round(fit[1], 4) if fit else None,
            "game_date_first": dates[0] if dates else None,
            "game_date_last": dates[-1] if dates else None,
            "stopped": self.stopped,
        }

    def render(self) -> str:
        s = self.summary()
        if s["slope_gb_per_hour"] is None:
            return (f"memory-probe {self.variant}: no growth figure ({s['samples']} readable "
                    f"samples). {self.stopped}".strip())
        text = (f"memory-probe {self.variant}{' / ' + self.label if self.label else ''} on "
                f"{self.save}: {s['start_gb']:.2f} -> {s['end_gb']:.2f} GB over "
                f"{s['minutes_measured']:g} min, {s['slope_gb_per_hour']:+.1f} GB/hour "
                f"(R2 {s['r_squared']:.3f}, {s['samples']} samples)")
        if s["game_date_first"]:
            text += f"; game {s['game_date_first']} -> {s['game_date_last']}"
        return text + (f". {self.stopped}" if self.stopped else "")


def run_probe(*, save: str, minutes: float, variant: str, out_dir: Path,
              read_bytes: Callable[[], int | None],
              read_date: Callable[[], str | None] = lambda: None,
              interval: float = 60.0, label: str = "",
              sleep: Callable[[float], None] = time.sleep,
              clock: Callable[[], float] = time.monotonic,
              now: Callable[[], datetime] = datetime.now,
              say: Callable[[str], None] = print) -> ProbeResult:
    """Sample every ``interval`` seconds for ``minutes`` (a sample at 0 and one at the end), write
    each row to the CSV as it is taken, then the summary. ``read_bytes`` is the game's private
    bytes (None when it cannot be read); stops early after ``MAX_MISSES`` unreadable samples."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; use one of {', '.join(VARIANTS)}")
    if minutes <= 0 or interval <= 0:
        raise ValueError("minutes and interval must be positive")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"probe-{variant}{'-' + label if label else ''}-{now().strftime('%Y%m%d-%H%M%S')}"
    result = ProbeResult(variant, save, label, minutes, csv_path=out_dir / f"{stem}.csv",
                         summary_path=out_dir / f"{stem}.summary.json")
    total = int(round(minutes * 60 / interval))
    start, misses = clock(), 0
    with result.csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for i in range(total + 1):
            wait = start + i * interval - clock()
            if wait > 0:
                sleep(wait)
            used = read_bytes()
            elapsed = round(clock() - start, 1)
            try:
                game_date = read_date()
            except Exception:  # noqa: BLE001 - the date is a nicety, never a reason to stop
                game_date = None
            row = {"elapsed_s": elapsed, "time": now().isoformat(timespec="seconds"),
                   "private_bytes": used, "private_gb": None if used is None else used / GB,
                   "game_date": game_date}
            result.rows.append(row)
            writer.writerow([row["elapsed_s"], row["time"], "" if used is None else used,
                             "" if used is None else f"{used / GB:.3f}", game_date or ""])
            handle.flush()
            if used is None:
                misses += 1
                if misses >= MAX_MISSES:
                    result.stopped = (f"stopped: hoi4.exe unreadable {MAX_MISSES} samples in a row "
                                      "(the game exited, crashed, or this is not Windows)")
                    break
            else:
                misses = 0
                tail = f"  {game_date}" if game_date else ""
                say(f"  t+{elapsed / 60:5.1f} min  {used / GB:6.2f} GB{tail}")
    result.summary_path.write_text(json.dumps(result.summary(), indent=2) + "\n", encoding="utf-8")
    return result


def compare(directory: Path) -> str:
    """A table of every ``*.summary.json`` in ``directory``, slowest growth first, so the pairs of
    the four comparisons can be read side by side."""
    rows = []
    for path in sorted(Path(directory).glob("*.summary.json")):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    if not rows:
        return f"no *.summary.json in {directory}"
    rows.sort(key=lambda r: (r.get("slope_gb_per_hour") is None, r.get("slope_gb_per_hour") or 0))
    lines = [f"{'variant':<12}{'label':<14}{'save':<26}{'min':>6}{'start GB':>10}{'end GB':>9}"
             f"{'GB/hour':>9}{'R2':>7}"]
    for r in rows:
        slope = r.get("slope_gb_per_hour")
        per_hour = "n/a" if slope is None else format(slope, "+.1f")
        lines.append(f"{r['variant']:<12}{(r.get('label') or '-'):<14}{r['save'][:25]:<26}"
                     f"{r['minutes_measured']:>6g}{_num(r.get('start_gb')):>10}{_num(r.get('end_gb')):>9}"
                     f"{per_hour:>9}{_num(r.get('r_squared'), 3):>7}")
    return "\n".join(lines)


def _num(value, places: int = 2) -> str:
    return "-" if value is None else f"{value:.{places}f}"
