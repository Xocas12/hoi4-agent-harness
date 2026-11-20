"""Set the PC up for a live session, and put it back afterwards.

    python scripts/live_setup.py prepare [--expert-ai]            # dry run: prints the diff
    python scripts/live_setup.py prepare [--expert-ai] --apply
    python scripts/live_setup.py restore                          # dry run
    python scripts/live_setup.py restore --apply

``prepare`` changes three things in the Hearts of Iron IV user folder, after backing up
what it is about to change:

* the installed mod (``mod/llm_bridge`` and ``mod/llm_bridge.mod``) is made identical to
  the repo's ``mod/llm_bridge``: changed and new files are copied in, files the repo no
  longer has are removed from the installed copy;
* ``dlc_load.json`` enables ``llm_bridge`` as the LAST mod, and with ``--expert-ai`` also
  Expert AI (Workshop 741805475, docs/expert-ai.md) just before it;
* ``settings.txt`` gets ``save_as_binary=no`` (the harness reads text saves only).

``restore`` puts back exactly the ``dlc_load.json`` and ``settings.txt`` of a backup (the
newest one, or ``--backup NAME``). The mod files stay where ``prepare`` left them: the
mod is inert unless the playset enables it.

Safety rules, all tested against temp folders only (``tests/test_live_setup.py``):

* Dry run unless ``--apply``. A dry run writes nothing, anywhere.
* Refuses while HOI4 is running (a read-only look at the process list by image name), and
  when it cannot tell. The launcher and the game rewrite these files on exit.
* Every apply makes a new timestamped folder under ``<user dir>/live_setup_backups`` first.
  A backup is never deleted or overwritten; ``restore`` first saves the files it is about
  to replace into a backup of its own, so a restore can be undone too.
* The user folder is found the way the rest of the harness finds it
  (``hoi4_harness.paths``: Documents, OneDrive-redirected Documents, XDG), or named with
  ``--user-dir``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from hoi4_harness import paths  # noqa: E402

MOD_NAME = "llm_bridge"
MOD_ENTRY = "mod/llm_bridge.mod"
EXPERT_AI_ID = "741805475"
EXPERT_AI_ENTRY = f"mod/ugc_{EXPERT_AI_ID}.mod"
BACKUP_DIR = "live_setup_backups"
GAME_PROCESSES = {"hoi4.exe", "hoi4"}
MANIFEST = "manifest.json"
TRACKED = ("dlc_load.json", "settings.txt")


class SetupError(Exception):
    """Something the operator must fix; the message says what."""


# --- the running-game check ---------------------------------------------------------------

def list_processes() -> list[str]:
    """Image names of the running processes. Read-only; raises SetupError if unreadable."""
    if sys.platform == "win32":
        command = ["tasklist", "/FO", "CSV", "/NH"]
    else:
        command = ["ps", "-A", "-o", "comm="]
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=20, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError(f"cannot read the process list ({type(exc).__name__}), so I cannot "
                         "tell whether HOI4 is running; refusing") from exc
    names = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if sys.platform == "win32":
            line = line.split('","')[0].strip('"')
        names.append(Path(line).name)
    return names


def refuse_if_running(lister: Callable[[], list[str]]) -> None:
    running = sorted({n for n in lister() if n.lower() in GAME_PROCESSES})
    if running:
        raise SetupError(f"HOI4 is running ({', '.join(running)}). Close the game and the launcher "
                         "first: they rewrite dlc_load.json and settings.txt on exit")


# --- pure edits ---------------------------------------------------------------------------

def edit_dlc_load(text: str | None, expert_ai: bool) -> str:
    """The new dlc_load.json text. Other keys and the order of other mods are kept."""
    if text is None:
        data: dict = {"enabled_mods": [], "disabled_dlcs": []}
    else:
        try:
            data = json.loads(text.lstrip("﻿"))
        except ValueError as exc:
            raise SetupError(f"dlc_load.json is not valid JSON ({exc}); not touching it") from None
        if not isinstance(data, dict):
            raise SetupError("dlc_load.json is not a JSON object; not touching it")
    mods = data.get("enabled_mods", [])
    if not isinstance(mods, list) or not all(isinstance(m, str) for m in mods):
        raise SetupError("dlc_load.json 'enabled_mods' is not a list of strings; not touching it")
    mods = [m for m in mods if m != MOD_ENTRY]
    if expert_ai and EXPERT_AI_ENTRY not in mods:
        mods.append(EXPERT_AI_ENTRY)
    mods.append(MOD_ENTRY)                      # last, always
    data["enabled_mods"] = mods
    data.setdefault("disabled_dlcs", [])
    return json.dumps(data, indent=4) + "\n"


_SAVE_AS_BINARY = re.compile(r"^(?P<lead>[ \t]*)save_as_binary[ \t]*=[ \t]*\w+(?P<tail>[^\r\n]*)", re.M)


def edit_settings(text: str | None) -> str:
    """settings.txt with save_as_binary=no. A BOM, line endings and every other line are kept."""
    if text is None:
        raise SetupError("settings.txt does not exist; start the game once so it is created, then retry")
    if _SAVE_AS_BINARY.search(text):
        def keep_shape(m):
            return f"{m.group('lead')}save_as_binary=no{m.group('tail')}"

        return _SAVE_AS_BINARY.sub(keep_shape, text, count=1)
    eol = "\r\n" if "\r\n" in text else "\n"
    return text + ("" if text.endswith(("\n", "\r")) or not text else eol) + "save_as_binary=no" + eol


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8")      # not utf-8-sig: a BOM is kept
    except FileNotFoundError:
        return None
    except UnicodeDecodeError as exc:
        raise SetupError(f"{path} is not UTF-8 ({exc}); not touching it") from None


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def unified(name: str, before: str | None, after: str) -> str:
    diff = difflib.unified_diff((before or "").splitlines(keepends=True), after.splitlines(keepends=True),
                                f"a/{name}" if before is not None else "/dev/null", f"b/{name}")
    return "".join(line if line.endswith("\n") else line + "\n" for line in diff)


# --- the mod ----------------------------------------------------------------------------------

def _tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _same(a: bytes, b: bytes) -> bool:
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")      # line endings are not a change


class ModPlan:
    def __init__(self, repo_mod: Path, installed_dir: Path, installed_descriptor: Path):
        self.repo_dir, self.repo_descriptor = repo_mod / MOD_NAME, repo_mod / f"{MOD_NAME}.mod"
        if not self.repo_dir.is_dir() or not self.repo_descriptor.is_file():
            raise SetupError(f"the repo's {self.repo_dir} / {self.repo_descriptor.name} are missing")
        self.installed_dir, self.installed_descriptor = installed_dir, installed_descriptor
        want = _tree(self.repo_dir)
        have = _tree(installed_dir) if installed_dir.is_dir() else {}
        self.new = sorted(set(want) - set(have))
        self.changed = sorted(k for k in set(want) & set(have) if not _same(want[k], have[k]))
        self.stale = sorted(set(have) - set(want))
        descriptor = self.installed_descriptor.read_bytes() if installed_descriptor.is_file() else None
        if descriptor is None:
            self.descriptor_state = "new"
        elif _same(descriptor, self.repo_descriptor.read_bytes()):
            self.descriptor_state = "same"
        else:
            self.descriptor_state = "changed"

    @property
    def nothing_to_do(self) -> bool:
        return not (self.new or self.changed or self.stale) and self.descriptor_state == "same"

    def render(self) -> str:
        if self.nothing_to_do:
            return f"mod/{MOD_NAME}: already identical to the repo"
        lines = [f"mod/{MOD_NAME}: make the installed copy identical to the repo"]
        lines += [f"  + {k}" for k in self.new] + [f"  ~ {k}" for k in self.changed]
        lines += [f"  - {k}   (not in the repo; removed after it is backed up)" for k in self.stale]
        if self.descriptor_state != "same":
            lines.append(f"  {'+' if self.descriptor_state == 'new' else '~'} {MOD_NAME}.mod")
        return "\n".join(lines)

    def apply(self) -> None:
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        for key in self.new + self.changed:
            target = self.installed_dir / key
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.repo_dir / key, target)
        for key in self.stale:
            (self.installed_dir / key).unlink()
        if self.descriptor_state != "same":
            shutil.copyfile(self.repo_descriptor, self.installed_descriptor)


# --- backups -----------------------------------------------------------------------------------

def _sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def new_backup_dir(user_dir: Path, now: dt.datetime | None = None) -> Path:
    """A fresh timestamped folder. An existing one is never reused."""
    root = user_dir / BACKUP_DIR
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    for n in range(1, 1000):
        candidate = root / (stamp if n == 1 else f"{stamp}-{n}")
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            continue
    raise SetupError("could not make a unique backup folder")


def make_backup(user_dir: Path, kind: str, mod_paths: tuple[Path, Path] | None = None,
                now: dt.datetime | None = None) -> Path:
    folder = new_backup_dir(user_dir, now)
    files = {}
    for name in TRACKED:
        source = user_dir / name
        files[name] = _sha(source)
        if source.is_file():
            shutil.copyfile(source, folder / name)
    if mod_paths:
        installed_dir, installed_descriptor = mod_paths
        if installed_dir.is_dir():
            shutil.copytree(installed_dir, folder / "mod" / MOD_NAME)
        if installed_descriptor.is_file():
            (folder / "mod").mkdir(exist_ok=True)
            shutil.copyfile(installed_descriptor, folder / "mod" / installed_descriptor.name)
    (folder / MANIFEST).write_text(json.dumps(
        {"kind": kind, "created": (now or dt.datetime.now()).isoformat(timespec="seconds"),
         "files": files, "has_mod": bool(mod_paths)}, indent=2) + "\n", encoding="utf-8")
    return folder


def list_backups(user_dir: Path) -> list[Path]:
    root = user_dir / BACKUP_DIR
    return sorted(p for p in root.iterdir() if (p / MANIFEST).is_file()) if root.is_dir() else []


def pick_backup(user_dir: Path, name: str | None) -> Path:
    if name:
        chosen = user_dir / BACKUP_DIR / name
        if not (chosen / MANIFEST).is_file():
            raise SetupError(f"no backup named {name!r} in {user_dir / BACKUP_DIR}")
        return chosen
    # The newest backup that `prepare` made: a `pre-restore` one holds what a restore replaced.
    prepared = [b for b in list_backups(user_dir)
                if json.loads((b / MANIFEST).read_text(encoding="utf-8")).get("kind") == "prepare"]
    if not prepared:
        raise SetupError(f"no prepare backup in {user_dir / BACKUP_DIR}; nothing to restore")
    return prepared[-1]


# --- the two commands -----------------------------------------------------------------------------

def find_user_dir(explicit: str | None) -> Path:
    found = paths.find_game_dir(Path(explicit) if explicit else None)
    if found is None:
        tried = f"--user-dir {explicit}" if explicit else ", ".join(str(p) for p in paths.game_dirs())
        raise SetupError(f"no Hearts of Iron IV user folder found (looked at: {tried})")
    return found


def prepare(user_dir: Path, *, apply: bool, expert_ai: bool, repo_mod: Path,
            lister: Callable[[], list[str]], out=print, now: dt.datetime | None = None) -> int:
    refuse_if_running(lister)
    mod_dir = user_dir / "mod"
    mods = ModPlan(repo_mod, mod_dir / MOD_NAME, mod_dir / f"{MOD_NAME}.mod")
    if expert_ai and not (user_dir / EXPERT_AI_ENTRY).is_file():
        raise SetupError(f"{user_dir / EXPERT_AI_ENTRY} does not exist: subscribe to Expert AI on the "
                         f"Workshop (id {EXPERT_AI_ID}) and start the launcher once so it writes it")
    old_dlc, old_settings = _read(user_dir / "dlc_load.json"), _read(user_dir / "settings.txt")
    new_dlc, new_settings = edit_dlc_load(old_dlc, expert_ai), edit_settings(old_settings)

    out(f"HOI4 user folder: {user_dir}")
    out(mods.render())
    changes = {"dlc_load.json": (old_dlc, new_dlc), "settings.txt": (old_settings, new_settings)}
    for name, (before, after) in changes.items():
        diff = unified(name, before, after)
        out(diff.rstrip("\n") if diff else f"{name}: no change")
    if not apply:
        out("\ndry run: nothing was written. Re-run with --apply to make these changes.")
        return 0

    backup = make_backup(user_dir, "prepare", (mod_dir / MOD_NAME, mod_dir / f"{MOD_NAME}.mod"), now)
    out(f"\nbacked up to {backup}")
    mods.apply()
    for name, (before, after) in changes.items():
        if before != after:
            _write(user_dir / name, after)
    out("applied. Next: hoi4-harness campaign-check; afterwards: "
        f"python scripts/live_setup.py restore --apply   (backup: {backup.name})")
    return 0


def restore(user_dir: Path, *, apply: bool, backup_name: str | None,
            lister: Callable[[], list[str]], out=print, now: dt.datetime | None = None) -> int:
    refuse_if_running(lister)
    backup = pick_backup(user_dir, backup_name)
    manifest = json.loads((backup / MANIFEST).read_text(encoding="utf-8"))
    out(f"HOI4 user folder: {user_dir}\nrestoring from backup {backup.name}")
    plan = []
    for name in TRACKED:
        had = manifest["files"].get(name)
        current = user_dir / name
        saved = backup / name
        if had is not None and (not saved.is_file() or _sha(saved) != had):
            raise SetupError(f"backup {backup.name} is damaged: {name} no longer matches its recorded hash; "
                             "not restoring from it")
        before = _read(current)
        after = saved.read_bytes().decode("utf-8") if had is not None else None
        if after is None:
            plan.append((name, "remove", before, None))
            out(f"{name}: did not exist before prepare; the current one is set aside "
                f"(renamed, not deleted)" if before is not None else f"{name}: absent then, absent now")
        else:
            diff = unified(name, before, after)
            out(diff.rstrip("\n") if diff else f"{name}: already as backed up")
            plan.append((name, "put", before, after))
    if not apply:
        out("\ndry run: nothing was written. Re-run with --apply to restore.")
        return 0

    safety = make_backup(user_dir, "pre-restore", None, now)
    out(f"\nwhat is being replaced is saved to {safety}")
    for name, action, before, after in plan:
        target = user_dir / name
        if action == "put":
            if before != after:
                _write(target, after)
            if _sha(target) != manifest["files"][name]:
                raise SetupError(f"{name} does not match the backup after writing; the pre-restore copy "
                                 f"in {safety} has what was there")
        elif before is not None:
            target.rename(user_dir / f"{name}.removed-by-restore-{safety.name}")
    out("restored dlc_load.json and settings.txt exactly as backed up (the mod files are left in place).")
    return 0


# --- command line ---------------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, text in (("prepare", "back up, then install the mod, enable it and set save_as_binary=no"),
                       ("restore", "put back the backed-up dlc_load.json and settings.txt")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
        p.add_argument("--user-dir", metavar="PATH",
                       help="the Hearts of Iron IV folder (default: found like the harness finds it)")
        if name == "prepare":
            p.add_argument("--expert-ai", action="store_true",
                           help=f"also enable Expert AI (Workshop {EXPERT_AI_ID}), just before llm_bridge")
        else:
            p.add_argument("--backup", metavar="NAME", help="a folder name under live_setup_backups "
                                                            "(default: the newest prepare backup)")
    return parser


def main(argv: list[str] | None = None, *, lister: Callable[[], list[str]] = list_processes,
         repo_mod: Path = REPO / "mod", out=print) -> int:
    args = build_parser().parse_args(argv)
    try:
        user_dir = find_user_dir(args.user_dir)
        if args.command == "prepare":
            return prepare(user_dir, apply=args.apply, expert_ai=args.expert_ai, repo_mod=repo_mod,
                           lister=lister, out=out)
        return restore(user_dir, apply=args.apply, backup_name=args.backup, lister=lister, out=out)
    except SetupError as exc:
        print(f"live_setup: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
