"""The harness restarts the game itself when the memory guard fires (issue #157).

HOI4 1.20 leaks about 30 GB an hour in a harness campaign. With ``campaign_on_memory =
"restart"`` the supervisor, past ``campaign_memory_limit_gb``, does what a person did by hand
on 2026-11-22 and what two scripts got wrong:

1. asks for a recovery save and waits until it is COMPLETE on disk (the size stopped changing
   and the file ends with ``checksum=``);
2. kills only the hoi4.exe this run knows (the pid it recorded), never "every hoi4.exe";
3. relaunches the game with ``-debug_mode`` OUTSIDE the harness's process tree (Win32_Process
   Create through WMI): a child of the harness died with it the first time;
4. waits for the game's window, then walks the menus: Single Player, Load, the top save of the
   list (the newest), the Load button, START;
5. hands back to the supervisor, which marks the country and turns ``human_ai`` on in the same
   process (``begin``) and carries on.

Before EVERY click the game window must be in front. If it is not, it is brought forward with
``take_focus`` called from a FRESH subprocess (from a long-running process Windows refused it
twice; a new process got it at once), and only when the keyboard and mouse have been idle (the
``idle_gated`` / ``InputGuard`` rules of #174). If it is still not in front, the restart stops
with ``RestartFailed``, whose message says what state the game is left in and how to finish by
hand; the run ends resumably.

Everything that touches the machine is a seam on ``Seams``, so the sequence is tested on a fake
game. ``live_seams`` builds the real ones. Off by default.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

#: The menu points at 1920x1080: where the 2026-11-22 restart clicked. Scaled to the window's
#: client size (x by width / 1920, y by height / 1080) and offset by its origin.
BASE_SIZE = (1920, 1080)
MENU_STEPS: tuple[tuple[str, str, tuple[int, int]], ...] = (
    ("single_player", "Single Player", (960, 226)),
    ("load", "Load", (960, 438)),
    ("top_save", "the newest save in the list", (200, 466)),
    ("load_button", "the Load button", (316, 1028)),
    ("start", "START", (1742, 1028)),
)
LAUNCH_ARGS = ("-gdpr-compliant", "-debug_mode")


class RestartFailed(RuntimeError):
    """The restart stopped. The message is the run's stop reason: what happened, what state
    the game is in, and how to carry on by hand (the run is resumable)."""


@dataclass
class Timings:
    """Seconds. Defaults match what the live game needed on 2026-11-22; profiles override them."""

    save_wait: float = 300.0        # for the recovery save to be complete on disk
    save_poll: float = 2.0
    save_stable_polls: int = 2      # the size unchanged this many polls in a row
    kill_wait: float = 60.0
    window_wait: float = 300.0      # for the relaunched game's window to exist
    menu_settle: float = 20.0       # the main menu needs a moment after its window appears
    click_pause: float = 2.5        # after a click, for the next screen to draw
    load_wait: float = 900.0        # for the save to finish loading (the game.log goes quiet)
    load_quiet: float = 10.0
    load_fallback: float = 90.0     # no game.log to watch: a fixed wait
    focus_wait: float = 360.0       # to get the game in front, in all, before a click
    focus_poll: float = 5.0


@dataclass
class Seams:
    """Everything the restart does to the machine, injectable."""

    find_pids: Callable[[str], list[int] | None]
    kill: Callable[[int], bool]
    launch: Callable[[Path, list[str]], int | None]
    window_exists: Callable[[], bool]
    in_front: Callable[[], bool]
    #: Bring the window forward from a fresh process, idle-gated; True when it was asked.
    take_focus: Callable[[], bool]
    click: Callable[[tuple[int, int], str], None]
    #: The window's client area ``(x, y, width, height)`` in screen pixels, or None.
    client_rect: Callable[[], tuple[int, int, int, int] | None]
    #: A comparable stamp of game.log (size, mtime) or None when there is none to watch.
    log_signature: Callable[[], object]
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


@dataclass
class RestartResult:
    save: Path
    pid: int
    steps: list[str] = field(default_factory=list)


def scale_point(point: tuple[int, int], rect: tuple[int, int, int, int]) -> tuple[int, int]:
    """A 1920x1080 menu point in this window's screen pixels."""
    x, y, width, height = rect
    return (x + round(point[0] * width / BASE_SIZE[0]), y + round(point[1] * height / BASE_SIZE[1]))


def ends_with_checksum(path: Path) -> bool:
    """A text save is complete when its last line is ``checksum="..."``."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 512))
            tail = handle.read()
    except OSError:
        return False
    lines = tail.strip().splitlines()
    return bool(lines) and lines[-1].strip().startswith(b"checksum=")


class GameRestarter:
    def __init__(self, *, seams: Seams, save_dir: Path | None, game_dir: Path | None,
                 process_name: str = "hoi4.exe", timings: Timings | None = None,
                 log: Callable[[str], None] = lambda _t: None,
                 load_state: Callable[[], dict] = dict,
                 save_state: Callable[..., None] = lambda **_kw: None,
                 wall: Callable[[], float] = time.time):
        self.s = seams
        self.save_dir = Path(save_dir) if save_dir else None
        self.game_dir = Path(game_dir) if game_dir else None
        self.process_name = process_name or "hoi4.exe"
        self.t = timings or Timings()
        self.log, self._load, self._save, self.wall = log, load_state, save_state, wall
        self.stage = "before anything was touched"
        self.save_used: Path | None = None
        self.restarts = 0

    # --- the pid this run knows -------------------------------------------------------

    def remember_pid(self) -> int | None:
        """Record the game's pid when it is unambiguous (the one recorded is gone, and exactly
        one hoi4.exe runs). Called each memory check; cheap and never raises."""
        try:
            pids = self.s.find_pids(self.process_name)
        except Exception:  # noqa: BLE001
            return None
        known = self._load().get("game_pid")
        if pids is None:
            return known
        if known in pids:
            return known
        if len(pids) == 1:
            self._save(game_pid=pids[0])
            return pids[0]
        return None

    def _known_pid(self) -> int:
        pids = self.s.find_pids(self.process_name)
        if pids is None:
            raise RestartFailed("the process list could not be read, so the harness cannot tell "
                                f"which {self.process_name} is its game; nothing was killed")
        known = self._load().get("game_pid")
        if known in pids:
            return known
        if len(pids) == 1:
            self.log(f"restart: the run had no pid on record; {self.process_name} pid "
                     f"{pids[0]} is the only one running, taking it as the game")
            return pids[0]
        raise RestartFailed(
            f"{len(pids)} {self.process_name} processes are running and none is the pid this run "
            f"recorded ({known}): nothing was killed. Close the extra game(s), or restart by hand")

    # --- the save ---------------------------------------------------------------------

    def _newest_save(self) -> Path | None:
        if self.save_dir is None or not self.save_dir.is_dir():
            return None
        best, best_time = None, -1.0
        for path in self.save_dir.glob("*.hoi4"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > best_time:
                best, best_time = path, mtime
        return best

    def wait_for_save(self, requested_at: float) -> Path:
        """The newest save, once it was written after ``requested_at``, has stopped growing and
        ends with ``checksum=``. This is also the one at the top of the game's Load list."""
        self.stage = "waiting for the recovery save"
        if self.save_dir is None:
            raise RestartFailed("no save folder is known (save_dir), so the recovery save cannot "
                                "be checked; nothing was killed")
        deadline = self.s.clock() + self.t.save_wait
        last: tuple[str, int] | None = None
        stable = 0
        while True:
            path = self._newest_save()
            if path is not None:
                try:
                    stat = path.stat()
                except OSError:
                    stat = None
                if stat is not None and stat.st_mtime >= requested_at - 2:
                    mark = (path.name, stat.st_size)
                    stable = stable + 1 if mark == last else 0
                    last = mark
                    if stable >= self.t.save_stable_polls and ends_with_checksum(path):
                        self.log(f"restart: the recovery save {path.name} is complete "
                                 f"({stat.st_size / 1024 ** 2:.0f} MB, ends with checksum=)")
                        return path
                else:
                    last, stable = None, 0
            if self.s.clock() >= deadline:
                raise RestartFailed(
                    f"the recovery save was not complete on disk in {self.t.save_wait / 60:.0f} "
                    "minutes (no new file, still growing, or no checksum= at its end); the game "
                    "was not killed")
            self.s.sleep(self.t.save_poll)

    # --- the game ---------------------------------------------------------------------

    def _kill(self, pid: int) -> None:
        self.stage = f"killing {self.process_name} pid {pid}"
        self.log(f"restart: killing {self.process_name} pid {pid} (the one this run knows)")
        self.s.kill(pid)
        deadline = self.s.clock() + self.t.kill_wait
        while True:
            pids = self.s.find_pids(self.process_name)
            if pids is not None and pid not in pids:
                return
            if self.s.clock() >= deadline:
                raise RestartFailed(f"pid {pid} was still running {self.t.kill_wait:g} s after the "
                                    "kill; restart the game by hand")
            self.s.sleep(1.0)

    def _launch(self) -> int:
        self.stage = "relaunching the game"
        if self.game_dir is None:
            raise RestartFailed("game_dir is not set, so the game cannot be relaunched")
        pid = self.s.launch(self.game_dir, list(LAUNCH_ARGS))
        if not pid:
            raise RestartFailed("the relaunch did not return a process id")
        self._save(game_pid=pid)
        self.log(f"restart: relaunched {self.process_name} with {' '.join(LAUNCH_ARGS)} outside "
                 f"this process tree (pid {pid})")
        return pid

    def _wait_for_window(self) -> None:
        self.stage = "waiting for the game window"
        deadline = self.s.clock() + self.t.window_wait
        while not self.s.window_exists():
            if self.s.clock() >= deadline:
                raise RestartFailed(f"the game window did not appear in {self.t.window_wait / 60:.0f} "
                                    "minutes after the relaunch")
            self.s.sleep(2.0)
        self.log("restart: the game window is up; letting the main menu draw")
        self.s.sleep(self.t.menu_settle)

    def _ensure_front(self, what: str) -> None:
        """The game window in front, or a RestartFailed. Brings it forward from a fresh process
        when the keyboard and mouse are idle; never otherwise."""
        deadline = self.s.clock() + self.t.focus_wait
        said = False
        while not self.s.in_front():
            if self.s.clock() >= deadline:
                raise RestartFailed(
                    f"the game window is not in front before {what} and could not be brought "
                    f"there in {self.t.focus_wait / 60:.0f} minutes (a person is at the "
                    "keyboard, or Windows refused); nothing was clicked")
            if not said:
                self.log(f"restart: the game is not in front before {what}; asking Windows from a "
                         "fresh process when the keyboard has been idle")
                said = True
            if self.s.take_focus():
                self.s.sleep(1.0)
                continue
            self.s.sleep(self.t.focus_poll)

    def _click(self, key: str, label: str, point: tuple[int, int]) -> None:
        what = f"the click on {label}"
        self.stage = f"menu: {label}"
        self._ensure_front(what)
        rect = self.s.client_rect()
        if rect is None:
            raise RestartFailed(f"the game window's size could not be read before {what}")
        target = scale_point(point, rect)
        self.log(f"restart: click {label} at {target} (window {rect[2]}x{rect[3]})")
        try:
            self.s.click(target, label)
        except Exception as exc:  # noqa: BLE001 - a person took over, no mouse, a refused point
            raise RestartFailed(f"{what} was not made: {type(exc).__name__}: {exc}") from exc
        self.s.sleep(self.t.click_pause)

    def _wait_for_load(self, before: object) -> None:
        self.stage = "waiting for the save to load"
        if before is None:
            self.log(f"restart: no game.log to watch; waiting {self.t.load_fallback:g} s for the load")
            self.s.sleep(self.t.load_fallback)
            return
        deadline = self.s.clock() + self.t.load_wait
        changed_at: float | None = None
        last = before
        while True:
            now = self.s.log_signature()
            if now != last:
                last, changed_at = now, self.s.clock()
            if changed_at is not None and now != before and self.s.clock() - changed_at >= self.t.load_quiet:
                self.log("restart: game.log has gone quiet after the load")
                return
            if self.s.clock() >= deadline:
                raise RestartFailed(f"the save did not finish loading in {self.t.load_wait / 60:.0f} "
                                    "minutes (game.log never changed, or kept changing)")
            self.s.sleep(2.0)

    # --- the whole sequence -------------------------------------------------------------

    def restart(self, requested_at: float) -> RestartResult:
        """Save is asked for already (at wall time ``requested_at``). Returns once START was
        clicked and the game process is up; the caller marks the country and resumes."""
        try:
            return self._restart(requested_at)
        except RestartFailed as exc:
            raise RestartFailed(self._explain(str(exc))) from exc

    def _restart(self, requested_at: float) -> RestartResult:
        save = self.wait_for_save(requested_at)
        self.save_used = save
        pid = self._known_pid()
        self._kill(pid)
        new_pid = self._launch()
        self._wait_for_window()
        result = RestartResult(save=save, pid=new_pid)
        for key, label, point in MENU_STEPS:
            before = self.s.log_signature() if key == "load_button" else None
            self._click(key, label, point)
            result.steps.append(key)
            if key == "load_button":
                self._wait_for_load(before)
        pids = self.s.find_pids(self.process_name)
        if pids is not None and new_pid not in pids:
            raise RestartFailed("the game process is gone after START (it crashed while loading)")
        self.restarts += 1
        self.stage = "game started"
        self.log(f"restart: START clicked; {save.name} is loading as pid {new_pid}")
        return result

    def _explain(self, reason: str) -> str:
        save = f" The save to load is {self.save_used.name}." if self.save_used else ""
        return (f"automatic restart stopped while {self.stage}: {reason}.{save} The run is "
                "resumable: finish the restart by hand (relaunch with -debug_mode, Single Player > "
                "Load > the newest save > Load > START), then run the same command with --resume")


# --- the real machine -------------------------------------------------------------------------


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def wmi_launch(game_dir: Path, args: list[str], *, run=subprocess.run) -> int | None:
    """Start ``hoi4.exe`` through ``Win32_Process.Create``: the new process belongs to WMI, not to
    this one, so stopping or crashing the harness does not take the game with it. Returns the new
    process id, or None."""
    if sys.platform != "win32":
        return None
    exe = Path(game_dir) / "hoi4.exe"
    command = " ".join([f'"{exe}"', *args])
    script = (f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
              f"@{{CommandLine={_ps_quote(command)}; CurrentDirectory={_ps_quote(str(game_dir))}}}; "
              "if ($r.ReturnValue -eq 0) { $r.ProcessId } else { exit 1 }")
    try:
        out = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                  capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return int(out.stdout.strip()) if out.returncode == 0 else None
    except ValueError:
        return None


def kill_pid(pid: int, *, run=subprocess.run) -> bool:
    """``taskkill`` of exactly one process id."""
    if sys.platform != "win32":
        return False
    try:
        return run(["taskkill", "/PID", str(int(pid)), "/F"], capture_output=True,
                   timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_driver(adapter):
    """The run's input driver: on the adapter itself, or on the channel router of a headless
    stack (a console-only campaign keeps it there: ``adapter.writer.router.driver``; the live
    run of 2026-11-27 found the restart refused as "no live game" because it only looked on the
    adapter)."""
    found = getattr(adapter, "driver", None)
    if found is not None:
        return found
    router = getattr(getattr(adapter, "writer", None), "router", None)
    return getattr(router, "driver", None)


def live_seams(config, adapter, log_path: Path | None) -> Seams:  # pragma: no cover - live only
    """The seams on the real machine, from the run's own driver (its click path, its input
    guard, its focus check)."""
    from ..adapters.headless import idle_gated
    from ..adapters.window import check_focus, client_rect, take_focus_fresh, window_visible
    from ..rival.safety import system_input_watch
    from .watch import process_pids

    driver = run_driver(adapter)
    title = config.window_title
    take = idle_gated(take_focus_fresh, system_input_watch().idle_seconds,
                      config.campaign_take_focus_idle_seconds)
    guard = getattr(driver, "input_guard", None)
    if guard is not None:
        take = guard.gate_take(take)

    def click(point, label):
        gui = driver._gui()
        if guard is None:
            gui.moveTo(point[0], point[1], duration=driver.config.move_duration)
            gui.click()
            return
        with guard.sequence("click", f"restart: {label}"):
            guard.checkpoint()
            gui.moveTo(point[0], point[1], duration=driver.config.move_duration)
            guard.checkpoint()
            gui.click()
            guard.sent()

    def signature():
        try:
            stat = Path(log_path).stat() if log_path else None
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns) if stat else None

    return Seams(
        find_pids=process_pids, kill=kill_pid, launch=wmi_launch,
        window_exists=lambda: window_visible(title),
        in_front=lambda: check_focus(title).focused,
        take_focus=lambda: take(title),
        click=click, client_rect=client_rect, log_signature=signature)


def build_for_run(config, adapter, log, load_state, save_state, save_dir) -> GameRestarter | None:
    """The restarter a live campaign uses, or None where it cannot be built (a fake game, no
    driver). Timings come from the ``campaign_restart_*`` config fields."""
    if getattr(adapter, "fake_game", None) is not None or run_driver(adapter) is None:
        return None
    timings = Timings(
        save_wait=config.campaign_restart_save_wait_seconds,
        window_wait=config.campaign_restart_window_wait_seconds,
        menu_settle=config.campaign_restart_menu_settle_seconds,
        load_wait=config.campaign_restart_load_wait_seconds,
        focus_wait=config.campaign_restart_focus_wait_seconds)
    return GameRestarter(
        seams=live_seams(config, adapter, config.log_path), save_dir=save_dir,
        game_dir=config.game_dir, process_name=config.campaign_game_process or "hoi4.exe",
        timings=timings, log=log, load_state=load_state, save_state=save_state)
