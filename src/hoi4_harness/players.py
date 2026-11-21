"""The players registry: which countries an LLM plays, and how each run is doing.

``native`` and ``rival`` runs write ``players.json`` (in the harness home folder, home.py) when they
start and mark it when they end, so the chat window can list every LLM country and say whether it
is still there. A record: ``tag``, ``provider``, ``model``, ``status``, ``pid``, ``started`` and
``updated`` (epoch seconds), ``run_dir``. A status that says a run is alive (starting, running,
waiting) is read back as ``stopped`` when its process no longer exists, so a crash that never got to
mark itself does not leave a ghost.

Never holds a key: only names, a pid and a status.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .home import home_dir

LIVE = ("starting", "running", "waiting")
FILENAME = "players.json"


def default_path() -> Path:
    return home_dir() / FILENAME


def pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":                       # pragma: no cover - Windows only
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@dataclass
class Player:
    tag: str
    provider: str = ""
    model: str = ""
    status: str = "starting"
    pid: int | None = None
    started: float = 0.0
    updated: float = 0.0
    run_dir: str = ""

    @property
    def alive(self) -> bool:
        return self.status in LIVE


class PlayersRegistry:
    def __init__(self, path: Path | None = None, clock=time.time, alive=pid_alive):
        self.path = Path(path) if path else default_path()
        self.clock = clock
        self._alive = alive

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        rows = data.get("players") if isinstance(data, dict) else None
        return {str(r["tag"]).upper(): r for r in rows or [] if isinstance(r, dict) and r.get("tag")}

    def _write(self, rows: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"players": list(rows.values())}, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def register(self, tag: str, provider: str = "", model: str = "", run_dir: str = "",
                 pid: int | None = None, status: str = "running") -> Player:
        now = self.clock()
        player = Player(tag.upper(), provider, model, status, os.getpid() if pid is None else pid,
                        now, now, str(run_dir))
        rows = self._read()
        rows[player.tag] = asdict(player)
        self._write(rows)
        return player

    def mark(self, tag: str, status: str) -> None:
        rows = self._read()
        row = rows.get(tag.upper())
        if row is None:
            return
        row["status"], row["updated"] = status, self.clock()
        self._write(rows)

    def players(self) -> list[Player]:
        out = []
        for row in self._read().values():
            try:
                player = Player(**{k: row.get(k, d) for k, d in asdict(Player("")).items()})
            except TypeError:
                continue
            if player.status in LIVE and not self._alive(player.pid):
                player.status = "stopped"
            out.append(player)
        return sorted(out, key=lambda p: p.tag)


class RunMark:
    """``with RunMark(tags, planner_of, run_dir):`` registers on entry and marks on the way out:
    ``finished`` on a clean exit, ``failed`` on an exception, ``stopped`` on Ctrl+C."""

    def __init__(self, tags, describe, run_dir="", registry: PlayersRegistry | None = None,
                 status: str = "running"):
        self.tags = [t.upper() for t in tags]
        self.describe = describe          # tag -> (provider, model)
        self.run_dir = run_dir
        self.registry = registry or PlayersRegistry()
        self.status = "finished"
        self.initial = status

    def __enter__(self):
        for tag in self.tags:
            provider, model = self.describe(tag)
            try:
                self.registry.register(tag, provider, model, str(self.run_dir), status=self.initial)
            except OSError:
                pass                       # the registry is a convenience, never a reason to stop
        return self

    def set(self, status: str) -> None:
        for tag in self.tags:
            try:
                self.registry.mark(tag, status)
            except OSError:
                pass

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.set(self.status)
        elif issubclass(exc_type, KeyboardInterrupt):
            self.set("stopped")
        else:
            self.set("failed")
        return False
