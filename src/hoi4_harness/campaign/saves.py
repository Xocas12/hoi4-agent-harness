"""The recovery saves a campaign takes, and keeping them from piling up.

The console's ``savegame`` writes a ~66 MB text save into a folder that OneDrive syncs, a
few seconds after it is asked for, under the game's own name (``GER_1938_05_01_14.hoi4``).
Over 13 game years that is a lot of disk, so the harness keeps the first save of the run
and the newest ``campaign_keep_saves``, and deletes the rest.

What it may delete is deliberately narrow. The harness does not choose the file name, so a
save is *the harness's* only if it appeared after the harness asked for one: a name that
was not in the folder when ``savegame`` was sent, found within ``CLAIM_SECONDS``. It is
written down (``harness_saves.json`` in the run directory) with its size and modification
time, and deleted only while the file still has exactly those: a save the person
overwrote, or anything never recorded, is never touched. Everything else in the folder,
the person's saves above all, is not looked at beyond its name.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

SAVES_FILE = "harness_saves.json"
#: How long after asking for a save a new file in the folder is taken to be it.
CLAIM_SECONDS = 15 * 60.0


def _stat_record(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"name": path.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def load_harness_saves(run_dir: Path) -> dict:
    try:
        data = json.loads((Path(run_dir) / SAVES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"saves": [], "pending": None}
    if not isinstance(data, dict):
        return {"saves": [], "pending": None}
    data.setdefault("saves", [])
    data.setdefault("pending", None)
    return data


class SaveTracker:
    """Records the saves the harness asked for, and prunes the old ones."""

    def __init__(self, run_dir: Path, save_dir: Path | None, keep: int,
                 log: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.time):
        self.run_dir, self.save_dir = Path(run_dir), Path(save_dir) if save_dir else None
        self.keep, self.log, self.clock = max(int(keep), 1), log, clock
        self.data = load_harness_saves(self.run_dir)

    # --- bookkeeping ---------------------------------------------------------

    def _write(self) -> None:
        try:
            (self.run_dir / SAVES_FILE).write_text(json.dumps(self.data), encoding="utf-8")
        except OSError:
            pass

    def _say(self, text: str) -> None:
        if self.log:
            self.log(text)

    def _listing(self) -> set[str]:
        if self.save_dir is None or not self.save_dir.is_dir():
            return set()
        return {p.name for p in self.save_dir.glob("*.hoi4")}

    # --- the two moments -----------------------------------------------------

    def before_send(self, date: str) -> None:
        """Call just before ``savegame`` is sent: remember what was there already."""
        self.data["pending"] = {"before": sorted(self._listing()), "at": self.clock(), "date": date}
        self._write()

    def collect(self) -> None:
        """Claim the new file the pending ``savegame`` produced, once it is on disk."""
        pending = self.data.get("pending")
        if not pending or self.save_dir is None:
            return
        known = {s["name"] for s in self.data["saves"]}
        before = set(pending.get("before", []))
        for name in sorted(self._listing() - before - known):
            record = _stat_record(self.save_dir / name)
            if record:
                self.data["saves"].append({**record, "date": pending.get("date")})
                self._say(f"recovery save on disk: {name}")
        # A save may still be growing: refresh the claimed records inside the window only.
        inside = self.clock() - float(pending.get("at", 0)) <= CLAIM_SECONDS
        if inside:
            for saved in self.data["saves"]:
                fresh = _stat_record(self.save_dir / saved["name"])
                if fresh and saved["name"] not in before:
                    saved.update(size=fresh["size"], mtime_ns=fresh["mtime_ns"])
        else:
            self.data["pending"] = None
        self._write()

    # --- pruning -------------------------------------------------------------

    def prune(self) -> list[str]:
        """Delete harness saves beyond the first and the newest ``keep``; return the names."""
        self.collect()
        saves = self.data["saves"]
        if self.save_dir is None or len(saves) <= self.keep + 1:
            return []
        keep = {saves[0]["name"], *(s["name"] for s in saves[-self.keep:])}
        removed: list[str] = []
        for saved in list(saves):
            if saved["name"] in keep:
                continue
            path = self.save_dir / saved["name"]
            now = _stat_record(path)
            if now is None:
                saves.remove(saved)          # already gone: nothing to delete, stop tracking it
                continue
            if now["size"] != saved["size"] or now["mtime_ns"] != saved["mtime_ns"]:
                saves.remove(saved)          # changed since the harness wrote it: not ours now
                self._say(f"recovery save {saved['name']} was changed by someone else: left alone")
                continue
            try:
                path.unlink()
            except OSError as exc:
                self._say(f"could not delete old recovery save {saved['name']}: {exc}")
                continue
            saves.remove(saved)
            removed.append(saved["name"])
        if removed:
            self._say(f"pruned {len(removed)} old recovery save(s), kept the first and the "
                      f"newest {self.keep}: {', '.join(removed)}")
        self._write()
        return removed

    def latest(self) -> dict | None:
        """The newest harness save that is still on disk."""
        for saved in reversed(self.data["saves"]):
            if self.save_dir is not None and (self.save_dir / saved["name"]).exists():
                return saved
        return None
