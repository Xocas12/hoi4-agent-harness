"""Native play: you play HOI4, an LLM plays another country, and nothing touches your input.

``hoi4-harness native --rival POL`` is rival mode with every piece chosen so that it can run
in the background for as long as you play, with nothing for you to do:

* **orders** go through the inbox (inbox.py): a mod file the game reloads by itself when it
  is started with ``-debug_mode``. No console, no keyboard, no focus, no pause;
* **the model** is the Claude Code CLI on your subscription (``--provider claude-cli``,
  agent/llm/claude_cli.py) unless another provider is named. No API key;
* **the game's lifetime** is followed, not assumed: it waits for ``hoi4.exe``, plays while
  it runs, and with ``--keep-running`` waits for the next start when you quit. Each game
  session gets its own folder under the run dir.

What it checks before it starts, so a session that cannot work says why instead of sitting
silent: the installed mod has the inbox (its on_daily hook and the two inbox files), and the
running game was started with ``-debug_mode`` (read from the process's command line where
Windows tells it; a warning, not a refusal, when it cannot be read).
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .inbox import EFFECTS_FILE, EVENTS_FILE

CREATE_NO_WINDOW = 0x08000000
HOOK = "llmb_inbox.1"
DEBUG_FLAG = "-debug_mode"


def _quiet_run(cmd: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    kwargs: dict = dict(capture_output=True, text=True, timeout=timeout)
    if sys.platform == "win32":
        kwargs["creationflags"] = CREATE_NO_WINDOW      # no window flashing over the game
    try:
        return subprocess.run(cmd, **kwargs)
    except (OSError, subprocess.SubprocessError):
        return None


def game_command_line(process: str = "hoi4.exe") -> str | None:
    """The running game's command line, or None (not running, or Windows would not say)."""
    if sys.platform != "win32":
        return None
    out = _quiet_run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                      f"(Get-CimInstance Win32_Process -Filter \"name='{process}'\" | "
                      "Select-Object -First 1).CommandLine"])
    if out is None or out.returncode != 0:
        return None
    line = out.stdout.strip()
    return line or None


def game_running(process: str = "hoi4.exe") -> bool | None:
    if sys.platform != "win32":
        return None
    out = _quiet_run(["tasklist", "/FI", f"IMAGENAME eq {process}", "/NH"])
    if out is None or out.returncode != 0:
        return None
    return process.lower() in out.stdout.lower()


def installed_mod_problems(mod_dir: Path | None) -> list[str]:
    """What the installed mod lacks for the inbox. Empty: ready."""
    if mod_dir is None:
        return ["the game's user directory was not found, so neither was the installed mod"]
    mod_dir = Path(mod_dir)
    problems = []
    for rel in (EVENTS_FILE, EFFECTS_FILE):
        if not (mod_dir / rel).exists():
            problems.append(f"{mod_dir / rel} is missing")
    on_actions = mod_dir / "common" / "on_actions" / "llm_bridge_on_actions.txt"
    try:
        if HOOK not in on_actions.read_text(encoding="utf-8", errors="replace"):
            problems.append(f"{on_actions} does not fire {HOOK} (an older mod)")
    except OSError:
        problems.append(f"{on_actions} is missing")
    return problems


@dataclass
class DebugCheck:
    ok: bool | None          # None: could not tell
    message: str


def check_debug_mode(command_line: str | None) -> DebugCheck:
    if command_line is None:
        return DebugCheck(None, "could not read the game's command line; make sure it was started "
                                "with -debug_mode (launcher: Play > Open game in Debug Mode)")
    if DEBUG_FLAG in command_line.split() or DEBUG_FLAG in command_line:
        return DebugCheck(True, "the game runs with -debug_mode: it reloads the inbox")
    return DebugCheck(False, "the game was started WITHOUT -debug_mode, so it never reloads the "
                             "inbox and no order will land. Quit and start it again with Play > "
                             "Open game in Debug Mode (or add -debug_mode to the Steam launch "
                             "options once)")


def launch_game(game_dir: Path) -> subprocess.Popen:
    """Start hoi4.exe with -debug_mode, detached from this process."""
    exe = Path(game_dir) / "hoi4.exe"
    if not exe.exists():
        raise FileNotFoundError(f"no hoi4.exe in {game_dir}")
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen([str(exe), "-gdpr-compliant", DEBUG_FLAG], cwd=str(game_dir),
                            creationflags=flags, close_fds=True)


def wait_for_game(running: Callable[[], bool | None], sleep: Callable[[float], None],
                  echo: Callable[[str], None], poll: float = 5.0,
                  give_up: float | None = None, clock: Callable[[], float] = time.monotonic) -> bool:
    """Block until the game process is up. False if ``give_up`` seconds pass first."""
    started, said = clock(), False
    while True:
        state = running()
        if state or state is None:
            return True
        if not said:
            echo("native: waiting for Hearts of Iron IV to start (Play > Open game in Debug Mode)")
            said = True
        if give_up is not None and clock() - started >= give_up:
            return False
        sleep(poll)


def session_dir(run_dir: Path, now: Callable[[], float] = time.time) -> Path:
    return Path(run_dir) / time.strftime("session-%Y%m%d-%H%M%S", time.localtime(now()))
