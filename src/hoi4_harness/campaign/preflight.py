"""``campaign-check``: everything an unattended night depends on that can be checked
without touching the game.

Each check is one PASS / WARN / FAIL line; the command exits nonzero on any FAIL. Nothing
here sends a key, looks at the game process, writes a file, or prints a secret (an API key
is only ever "present" or "missing"). The outside world (the user directory, the
environment, ``powercfg``, the disk) comes in as arguments so tests can fake it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import paths
from ..adapters.logtail import SCHEMA_VERSION
from ..bridge_check import MOD_DIR
from ..config import HarnessConfig

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
#: One text save, as measured (late-game saves are bigger).
SAVE_MB = 66
SAVE_MB_LATE = 100
#: Wakes for the whole run, from docs/campaign.md "Cost estimate".
EXPECTED_WAKES, HEAVY_WAKES = 355, 680
#: Power settings: (label, subgroup alias, setting alias).
POWER_SETTINGS = (
    ("sleep after", "SUB_SLEEP", "STANDBYIDLE"),
    ("display off after", "SUB_VIDEO", "VIDEOIDLE"),
    ("lock after", "SUB_NONE", "CONSOLELOCK"),
)
PROVIDER_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                 "google": "GOOGLE_API_KEY"}


@dataclass
class Check:
    status: str
    name: str
    detail: str

    def line(self) -> str:
        return f"{self.status:<5} {self.name}: {self.detail}"


# --- the mod --------------------------------------------------------------------


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _norm(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _names(names: list[str], limit: int = 6) -> str:
    shown = ", ".join(names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def check_mod(user_dir: Path | None, repo_mod: Path = MOD_DIR) -> list[Check]:
    if user_dir is None:
        return [Check(FAIL, "mod installed", "the Hearts of Iron IV user directory was not found "
                      "(set HOI4_SAVE_DIR, or pass --user-dir)")]
    installed = user_dir / "mod" / "llm_bridge"
    descriptor = user_dir / "mod" / "llm_bridge.mod"
    out: list[Check] = []
    if not installed.is_dir():
        return [Check(FAIL, "mod installed", f"{installed} does not exist: copy mod/llm_bridge there")]
    if not descriptor.is_file():
        out.append(Check(FAIL, "mod descriptor", f"{descriptor} is missing"))
    elif 'path="mod/llm_bridge"' not in descriptor.read_text(encoding="utf-8", errors="replace"):
        out.append(Check(FAIL, "mod descriptor", f'{descriptor.name} has no path="mod/llm_bridge"'))
    else:
        out.append(Check(PASS, "mod descriptor", str(descriptor)))
    try:
        want, have = _files(repo_mod), _files(installed)
    except OSError as exc:
        return [*out, Check(FAIL, "mod files", f"cannot read the mod: {exc}")]
    # descriptor.mod in the repo folder is the launcher's file, not part of the mod's content.
    want.pop("descriptor.mod", None)
    have.pop("descriptor.mod", None)
    missing = sorted(set(want) - set(have))
    extra = sorted(set(have) - set(want))
    differ = sorted(n for n in set(want) & set(have) if want[n] != have[n])
    eol_only = [n for n in differ if _norm(want[n]) == _norm(have[n])]
    real = [n for n in differ if n not in eol_only]
    if missing or real:
        parts = []
        if missing:
            parts.append(f"missing {_names(missing)}")
        if real:
            parts.append(f"differ {_names(real)}")
        out.append(Check(FAIL, "mod matches repo", "; ".join(parts) + " (copy mod/llm_bridge over it)"))
    elif eol_only:
        out.append(Check(WARN, "mod matches repo",
                         f"identical except line endings: {_names(eol_only)}"))
    else:
        out.append(Check(PASS, "mod matches repo", f"{len(want)} files, byte for byte"))
    if extra:
        out.append(Check(WARN, "mod extra files", f"not in the repo: {_names(extra)}"))
    key = "common/scripted_effects/llm_bridge_telemetry.txt"
    found = re.search(r"LLMB_SCHEMA_VERSION\s*=\s*(\d+)", have.get(key, b"").decode("utf-8", "replace"))
    if not found:
        out.append(Check(FAIL, "mod schema", "no LLMB_SCHEMA_VERSION in the installed telemetry"))
    elif int(found.group(1)) != SCHEMA_VERSION:
        out.append(Check(FAIL, "mod schema",
                         f"installed v{found.group(1)}, the harness reads v{SCHEMA_VERSION}"))
    else:
        out.append(Check(PASS, "mod schema", f"v{SCHEMA_VERSION}"))
    return out


def check_playset(user_dir: Path | None) -> list[Check]:
    if user_dir is None:
        return []
    out: list[Check] = []
    dlc = user_dir / "dlc_load.json"
    try:
        enabled = json.loads(dlc.read_text(encoding="utf-8")).get("enabled_mods", [])
    except (OSError, ValueError, AttributeError) as exc:
        out.append(Check(FAIL, "dlc_load.json", f"cannot read {dlc}: {exc}"))
    else:
        if "mod/llm_bridge.mod" not in enabled:
            out.append(Check(FAIL, "dlc_load.json", "llm_bridge is not in enabled_mods"))
        else:
            order = enabled.index("mod/llm_bridge.mod") + 1
            if order == len(enabled):
                out.append(Check(PASS, "dlc_load.json", "llm_bridge enabled, last in the load order"))
            else:
                out.append(Check(WARN, "dlc_load.json", f"llm_bridge enabled at position {order} "
                                 f"of {len(enabled)} (last is safest)"))
    settings = user_dir / "settings.txt"
    try:
        text = settings.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        out.append(Check(FAIL, "settings.txt", f"cannot read {settings}: {exc}"))
    else:
        found = re.search(r"^\s*save_as_binary\s*=\s*(\w+)", text, re.M)
        if found and found.group(1) == "no":
            out.append(Check(PASS, "save_as_binary", "no (text saves)"))
        else:
            out.append(Check(FAIL, "save_as_binary", f"is {found.group(1) if found else 'not set'}; "
                             "set save_as_binary=no with the game closed"))
    out.append(renderer_check(user_dir))
    return out


def renderer_check(user_dir: Path) -> Check:
    """The renderer in ``pdx_settings.txt``. On OpenGL, HOI4 1.20 leaked 20-32 GB of private
    memory an hour on a wall-clock timer (paused, minimised, with or without the mod), and
    DirectX 11 on the same save leaked none (issue #157, measured 2026-11-27)."""
    path = Path(user_dir) / "pdx_settings.txt"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Check(INFO, "renderer", f"no {path.name}: the game's default (DirectX 11)")
    found = re.search(r'"renderer"\s*=\s*\{\s*value\s*=\s*"?(\w+)', text)
    value = found.group(1) if found else "dx11"
    if value == "opengl":
        return Check(WARN, "renderer", "OpenGL: HOI4 leaks about 20-30 GB an hour on it (issue #157); "
                     "set Settings > Video > Renderer to DirectX 11 with the game closed")
    return Check(PASS, "renderer", value)


# --- paths, disk, key, budget ---------------------------------------------------


def check_paths(config: HarnessConfig, save_dir: Path | None) -> list[Check]:
    out: list[Check] = []
    log = config.log_path
    if log is None:
        out.append(Check(FAIL, "HOI4_LOG_PATH", "not set"))
    elif not log.is_file():
        out.append(Check(FAIL, "HOI4_LOG_PATH", f"{log} does not exist (start the game once with the mod)"))
    else:
        out.append(Check(PASS, "HOI4_LOG_PATH", str(log)))
    if save_dir is None:
        out.append(Check(FAIL, "HOI4_SAVE_DIR", "not set and no save games folder was found"))
    elif not save_dir.is_dir():
        out.append(Check(FAIL, "HOI4_SAVE_DIR", f"{save_dir} does not exist"))
    elif config.save_dir:
        out.append(Check(PASS, "HOI4_SAVE_DIR", str(save_dir)))
    else:
        out.append(Check(WARN, "HOI4_SAVE_DIR", f"{save_dir} (found, not set: export HOI4_SAVE_DIR)"))
    if config.game_dir is None or not Path(config.game_dir).is_dir():
        level = FAIL if config.mod_context else WARN
        out.append(Check(level, "HOI4_GAME_DIR", f"{config.game_dir or 'not set'} is not a directory"))
    else:
        out.append(Check(PASS, "HOI4_GAME_DIR", str(config.game_dir)))
    return out


def _months(start: str, end: str) -> int:
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        return 0
    return max((b.year - a.year) * 12 + b.month - a.month, 0)


def check_disk(config: HarnessConfig, save_dir: Path | None,
               disk_usage: Callable = shutil.disk_usage) -> list[Check]:
    if save_dir is None or not save_dir.is_dir():
        return []
    try:
        free_gb = disk_usage(save_dir).free / 1024 ** 3
    except OSError as exc:
        return [Check(WARN, "disk space", f"cannot read free space of {save_dir}: {exc}")]
    every, keep = config.campaign_save_months, config.campaign_keep_saves
    if not every:
        return [Check(PASS, "disk space", f"{free_gb:.1f} GB free; recovery saves are off "
                      "(campaign_save_months = 0)")]
    months = _months(config.start_date or "1936-01-01", config.campaign_until)
    total = months // every + 1
    peak = min(total, keep + 2)             # the first, the newest N, and one not yet pruned
    need_gb = peak * SAVE_MB_LATE / 1024 + 1.0
    detail = (f"{free_gb:.1f} GB free; {total} saves (one every {every} months), pruned to the first "
              f"and the newest {keep}: {peak * SAVE_MB / 1024:.1f}-{peak * SAVE_MB_LATE / 1024:.1f} GB "
              f"at most on disk ({total * SAVE_MB / 1024:.1f} GB if nothing were pruned)")
    if free_gb < need_gb:
        return [Check(FAIL, "disk space", f"{detail}; need about {need_gb:.1f} GB")]
    if "onedrive" in str(save_dir).lower():
        detail += "; the folder is OneDrive-synced, so each save also uploads"
    return [Check(PASS, "disk space", detail)]


def check_key(config: HarnessConfig, env: Mapping[str, str]) -> list[Check]:
    if not config.planner_enabled or config.planner.provider == "scripted":
        return [Check(INFO, "API key", "no real provider: nothing to check")]
    name = config.planner.api_key_env or PROVIDER_KEYS.get(config.planner.provider)
    if not name:
        return [Check(WARN, "API key", f"provider {config.planner.provider} has no key variable on record")]
    if str(env.get(name, "")).strip():
        return [Check(PASS, "API key", f"{name} is present")]
    local = config.planner.provider == "openai" and bool(config.planner.base_url)
    return [Check(WARN if local else FAIL, "API key", f"{name} is missing")]


def check_budget(config: HarnessConfig, max_usd: float | None) -> list[Check]:
    if not config.planner_enabled or config.planner.provider == "scripted":
        return []
    b = config.budget
    cap = max_usd if max_usd is not None else b.max_usd
    if not (b.usd_per_m_input or b.usd_per_m_output):
        return [Check(FAIL, "budget", "the profile has no prices, so a cap would count every call as free")]
    price_in = b.usd_per_m_input or 0.0
    cached = b.usd_per_m_cached_input if b.usd_per_m_cached_input is not None else price_in
    write = b.usd_per_m_cache_write if b.usd_per_m_cache_write is not None else price_in
    # docs/campaign.md's per-wake token model: a 3.5k prefix written and then read, 2.8k
    # uncached input, 1k output.
    per_wake = (3.5e3 * write + 2.8e3 * price_in + 3.5e3 * cached + 1.0e3 * (b.usd_per_m_output or 0.0)) / 1e6
    expected, heavy = EXPECTED_WAKES * per_wake, HEAVY_WAKES * per_wake
    est = (f"at these prices about ${per_wake:.3f} a wake: ~${expected:.0f} expected, "
           f"${heavy:.0f} at one wake a week")
    if cap is None:
        return [Check(WARN, "budget", f"no cap given (the run needs --max-usd); {est}")]
    if cap <= b.reserve_usd:
        return [Check(FAIL, "budget", f"cap ${cap:.2f} is not above the ${b.reserve_usd:.2f} reserve: "
                      "the run would stop before its first call")]
    if cap < expected:
        return [Check(WARN, "budget", f"cap ${cap:.2f} is below the expected spend; {est}")]
    return [Check(PASS, "budget", f"cap ${cap:.2f}, reserve ${b.reserve_usd:.2f}; {est}")]


# --- the power plan --------------------------------------------------------------


def powercfg_query(sub: str, setting: str) -> str | None:
    """``powercfg /q`` for one setting, by alias (so the output's language does not matter)."""
    try:
        out = subprocess.run(["powercfg", "/q", "SCHEME_CURRENT", sub, setting],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def parse_ac_seconds(text: str) -> int | None:
    """The AC value of a ``powercfg /q`` answer: its last two 0x... numbers are AC then DC."""
    values = re.findall(r"0x[0-9a-fA-F]{8}", text)
    return int(values[-2], 16) if len(values) >= 2 else None


def check_power(platform: str = sys.platform,
                query: Callable[[str, str], str | None] = powercfg_query) -> list[Check]:
    if platform != "win32":
        return [Check(INFO, "power plan", "not Windows: keep-awake is a no-op here")]
    out: list[Check] = []
    for label, sub, setting in POWER_SETTINGS:
        text = query(sub, setting)
        seconds = parse_ac_seconds(text) if text else None
        if seconds is None and text is not None and setting == "CONSOLELOCK":
            # Hidden unless the plan exposes it: no value means no lock-on-idle timer here.
            out.append(Check(INFO, f"power {label}", "no timeout in this power plan"))
        elif seconds is None:
            out.append(Check(WARN, f"power {label}", "could not be read (powercfg)"))
        elif seconds == 0:
            out.append(Check(PASS, f"power {label}", "never (on AC)"))
        else:
            out.append(Check(PASS, f"power {label}",
                             f"{seconds // 60} min on AC; the run holds the PC awake "
                             "(SetThreadExecutionState), so it will not fire. Do not lock the "
                             "screen by hand (Win+L) or sign out"))
    return out


def check_focus_policy(config: HarnessConfig) -> list[Check]:
    out: list[Check] = []
    if not config.enforce_window_focus:
        out.append(Check(FAIL, "window guard", "enforce_window_focus is off: a campaign refuses to run so"))
    if config.campaign_take_focus:
        out.append(Check(WARN, "focus", "campaign_take_focus is on: the harness will pull the game "
                         "window to the front, over whatever you are doing"))
    else:
        out.append(Check(PASS, "focus", "the harness waits for the game window and never takes the focus"))
    if config.campaign_on_memory == "restart":
        if config.game_dir is None or not (Path(config.game_dir) / "hoi4.exe").is_file():
            out.append(Check(FAIL, "restart", "campaign_on_memory is restart but game_dir does not "
                             "hold hoi4.exe, so the game cannot be relaunched"))
        else:
            out.append(Check(WARN, "restart", "campaign_on_memory is restart: past the memory limit the "
                             "harness kills this run's hoi4.exe, relaunches it and clicks through the "
                             "menus, pulling the game to the front when the keyboard is idle "
                             f"({config.campaign_take_focus_idle_seconds:g} s). Menu points are for "
                             "1920x1080 scaled to the window; run a live check first (docs/campaign.md)"))
    return out


def check_hands(config: HarnessConfig, now: Callable[[], datetime] = datetime.now) -> list[Check]:
    """The mouse tools of an independent campaign (adapters/hands.py): what a person has to
    provide for each, said before the night rather than found in the start log. WARN, never
    FAIL: a tool whose prerequisite is missing is simply not offered, and the run goes on."""
    if not config.hands_mouse:
        return []
    from ..adapters.hands import DELEGATE_TOGGLE, delegable_armies
    from ..adapters.unit_orders import CameraRecord, CameraRefused

    out: list[Check] = []
    index = None
    if config.game_dir is None or not Path(config.game_dir).is_dir():
        out.append(Check(WARN, "research and orders", "no game_dir (HOI4_GAME_DIR): start_research "
                         "and the division orders need the game's files and will not be offered"))
    else:
        index = config.build_index()
        out.append(Check(PASS, "research", f"{len(index.tech_places)} technologies placed on the "
                         "tree; its click points come from the interface files (clicks the "
                         "harness makes while the game window is in front)")
                   if index.tech_places else
                   Check(WARN, "research", "the playset has no technologies: start_research "
                         "will not be offered"))
    path = Path(config.camera_path or Path(config.run_dir) / "camera.json")
    if config.unit_orders is False:
        out.append(Check(INFO, "division orders", "unit_orders is false: move_division and "
                         "attack_province are off"))
    else:
        try:
            record = CameraRecord.load(path)
        except CameraRefused as exc:
            out.append(Check(WARN, "camera", f"{exc}. move_division and attack_province will not "
                             "be offered until a person records it in the live game "
                             "(scripts/record_camera.py); delegate_army_to_ai does not need it"))
        else:
            try:
                age = now() - datetime.fromisoformat(record.recorded)
            except ValueError:
                age = None
            if record.display is not None:
                out.append(Check(PASS, "camera", f"{path} carries a display fingerprint "
                                 f"({record.display}): an unattended run accepts it at any age "
                                 "while the UI scale, window mode and resolution still match. "
                                 "It cannot see the map's zoom: do not zoom or scroll the map"))
            elif age is None or age > timedelta(hours=record.max_age_hours):
                out.append(Check(WARN, "camera", f"{path} has no display fingerprint and is older "
                                 f"than {record.max_age_hours:g} h: the orders will be refused at "
                                 "the start. Record it again with the current "
                                 "scripts/record_camera.py"))
            else:
                out.append(Check(WARN, "camera", f"{path} has no display fingerprint, so it is "
                                 f"refused once it is {record.max_age_hours:g} h old: an overnight "
                                 "run will outlive it. Record it again with the current "
                                 "scripts/record_camera.py"))
    calibration = Path(config.calibration_path or Path(config.run_dir) / "calibration.json")
    try:
        from ..calibration import load_calibration

        recorded = load_calibration(calibration).coordinates
    except (OSError, ValueError, RuntimeError):
        recorded = {}
    if DELEGATE_TOGGLE in recorded and delegable_armies(recorded):
        out.append(Check(INFO, "delegation", "delegate_army_to_ai can hand over: "
                         + ", ".join(delegable_armies(recorded)) + " (a DRAFT click path, "
                         "unverified in the real game)"))
    else:
        out.append(Check(WARN, "delegation", f"no recorded points for {DELEGATE_TOGGLE} and "
                         "army.<name> in calibration.json: delegate_army_to_ai will not be offered "
                         "(hoi4-harness calibrate ...)"))
    from ..adapters.event_windows import ANSWER_POINTS

    if not config.event_windows:
        out.append(Check(WARN, "event windows", "no event_windows probes in the profile: nothing "
                         "answers an event or closes a news window in this mode, and the mouse "
                         "tools click under one (docs/live-session.md section 14c)"))
    else:
        gone = [p for p in ANSWER_POINTS.values() if p not in recorded]
        out.append(Check(WARN, "event windows", "probes set, but a window cannot be closed until "
                         f"{', '.join(gone)} are recorded (hoi4-harness calibrate ...)")
                   if gone else Check(INFO, "event windows", "the mouse tools close an open event "
                                      "or news window first (the first option of a country event); "
                                      "UNVERIFIED in the real game"))
    from ..adapters import production_ui as pu

    edit, change = pu.missing_for_edit(recorded), pu.missing_for_equipment(recorded)
    if config.headless_reader not in ("log+save", "savegame"):
        out.append(Check(INFO, "production", f"the {config.headless_reader} reader does not see "
                         "production lines: no production by mouse (needs log+save)"))
    elif not edit:
        out.append(Check(INFO, "production", "set_production edits an existing line by click (a "
                         "DRAFT click path, unverified in the real game)"
                         + ("" if not change else "; change_production_equipment will not be "
                            f"offered until {', '.join(change)} are recorded")))
    else:
        out.append(Check(WARN, "production", "set_production can only open a new line (an "
                         "existing line is refused) and change_production_equipment is not "
                         f"offered: {pu.calibrate_line(edit)} (and "
                         "production.equipment.change production.equipment.<id> for the change)"))
    return out


# --- the whole thing -------------------------------------------------------------


def check_base_ai(config: HarnessConfig, user_dir: Path | None) -> list[Check]:
    """Which AI the game runs underneath (docs/expert-ai.md), and whether it fits this install."""
    from .. import baseai

    found = baseai.ai_mods(user_dir)
    if found is None:
        return [Check(INFO, "base AI", "dlc_load.json not read; base_ai = "
                      f"{config.base_ai} (auto falls back to vanilla)")]
    out: list[Check] = []
    if len(found) > 1:
        out.append(Check(WARN, "base AI", "more than one AI mod is enabled ("
                         + ", ".join(m.name for m in found) + "): they overwrite each other's AI "
                         "files, so which one plays is whichever loads last"))
    eai = next((m for m in found if m.workshop_id == baseai.EXPERT_AI), None)
    version = baseai.game_version(config.game_dir)
    if eai is None:
        if config.base_ai == "expert_ai":
            out.append(Check(FAIL, "base AI", "base_ai = expert_ai but Expert AI (Workshop "
                             "741805475) is not enabled in dlc_load.json"))
        else:
            others = ", ".join(m.name for m in found)
            out.append(Check(PASS, "base AI", "vanilla HOI4 AI" + (f" plus {others}" if others
                                                                   else "")))
        return out
    if config.base_ai == "vanilla":
        out.append(Check(WARN, "base AI", "Expert AI is enabled but base_ai = vanilla: the "
                         "directives keep their normal weights (150), which its own plans "
                         "(500-5000) drown out; use auto or expert_ai"))
    else:
        out.append(Check(PASS, "base AI", f"{eai.name} (Workshop 741805475): the campaign turns on "
                         "the strong directive weights"))
    if version and eai.supported_version and not baseai.same_minor(eai.supported_version,
                                                                   version):
        out.append(Check(WARN, "base AI version", f"{eai.name} is made for "
                         f"{eai.supported_version}, the game is {version}: check error.log for "
                         "its files after a first start"))
    out.append(Check(INFO, "base AI bonuses", "Expert AI's difficulty bonuses are off unless set "
                     "in its in-game configuration menu; leave them off (a result bought with "
                     "bonuses says nothing about the AI)"))
    return out


def check_cadence(config: HarnessConfig) -> list[Check]:
    """The telemetry cadence the run will set (cadence.py); nothing when it is left alone."""
    from .. import cadence

    if config.telemetry_hours is None:
        return []
    try:
        hours = cadence.validate(config.telemetry_hours)
    except ValueError as exc:
        return [Check(FAIL, "telemetry cadence", str(exc))]
    if hours >= cadence.DAILY_HOURS:
        return [Check(INFO, "telemetry cadence", cadence.describe(hours))]
    ticks = 24 // hours
    return [Check(INFO, "telemetry cadence", f"{cadence.describe(hours)}: {ticks} state ticks a "
                  "game day in game.log (each with its army, war, probe and front lines), so the "
                  "log grows that much faster; the first live use needs the check in "
                  "docs/live-session.md (the chain's events and the game speed)")]


def resolve_user_dir(config: HarnessConfig, explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_dir() else None
    if config.save_dir and (Path(config.save_dir).parent / "settings.txt").is_file():
        return Path(config.save_dir).parent
    return paths.find_game_dir()


def run_checks(config: HarnessConfig, *, user_dir: Path | None = None, max_usd: float | None = None,
               repo_mod: Path = MOD_DIR, env: Mapping[str, str] | None = None,
               platform: str = sys.platform,
               query: Callable[[str, str], str | None] = powercfg_query,
               disk_usage: Callable = shutil.disk_usage) -> list[Check]:
    env = os.environ if env is None else env
    root = resolve_user_dir(config, user_dir)
    save_dir = Path(config.save_dir) if config.save_dir else (root / "save games" if root else None)
    return [
        *check_mod(root, repo_mod), *check_playset(root), *check_paths(config, save_dir),
        *check_disk(config, save_dir, disk_usage), *check_key(config, env),
        *check_budget(config, max_usd), *check_power(platform, query), *check_focus_policy(config),
        *check_hands(config), *check_base_ai(config, root), *check_cadence(config),
    ]
