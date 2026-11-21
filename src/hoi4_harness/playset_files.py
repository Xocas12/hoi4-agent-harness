"""The mod's playset-specific files, generated for the playset the launcher actually runs.

Three generated files depend on the playset, not on the harness: the focus/research/balance-of-power
probe (focus and tech ids), the reserve blocks (state ids) and the region blocks (strategic regions,
which sea regions exist). The repo ships them for vanilla. A mod that adds focuses, moves states or
redraws the sea regions needs them regenerated from its own files, or the orders point at the
wrong ids. ``live_setup prepare`` calls :func:`regenerate` for the mods ``dlc_load.json`` enables,
and leaves a stamp (``playset.json``) naming them, so ``native`` can tell when the launcher's mods
changed since.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

STAMP = "playset.json"
OWN = "llm_bridge"


def enabled_mod_dirs(user_dir: Path) -> tuple[list[str], list[Path]]:
    """The enabled mods' entries (without the harness's own) and the folders they load from."""
    from .baseai import enabled_mods

    entries = [e for e in (enabled_mods(user_dir) or []) if OWN not in e]
    folders = []
    for entry in entries:
        try:
            text = (Path(user_dir) / entry).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = re.search(r'^\s*path\s*=\s*"([^"]+)"', text, re.M)
        if not found:
            continue
        folder = Path(found.group(1))
        if not folder.is_absolute():
            folder = Path(user_dir) / folder
        folders.append(folder)
    return entries, folders


def regenerate(installed_mod: Path, user_dir: Path, game_dir: Path) -> str:
    """Write the playset files into the installed mod for the launcher's playset; one line back."""
    from . import modgen
    from .identifiers import IdentifierIndex, Mod, Playset

    entries, folders = enabled_mod_dirs(user_dir)
    mods = [Mod.load(f) for f in folders if (f / "descriptor.mod").exists() or f.is_dir()]
    index = IdentifierIndex.build(Playset(Path(game_dir), mods))
    modgen.write_probe(Path(installed_mod), index)
    (Path(installed_mod) / STAMP).write_text(json.dumps({"mods": entries}, indent=1), encoding="utf-8")
    names = ", ".join(m.name for m in mods) or "none"
    return (f"playset files regenerated for vanilla + {len(mods)} mod(s) ({names}): focus/research "
            f"probe, reserves, regions")


def stale(installed_mod: Path, user_dir: Path) -> str | None:
    """Why the installed playset files may not match the launcher's mods, or None."""
    entries, _ = enabled_mod_dirs(user_dir)
    stamp = Path(installed_mod) / STAMP
    try:
        built = json.loads(stamp.read_text(encoding="utf-8")).get("mods", [])
    except (OSError, ValueError):
        built = [] if not entries else None
    if built is None:
        return ("the installed mod's playset files were built for vanilla, and the launcher now "
                f"enables {len(entries)} mod(s): run `python scripts/live_setup.py prepare --apply` "
                "with the game closed, or front, reserve and region orders may point at wrong ids")
    if sorted(built) != sorted(entries):
        return ("the launcher's mods changed since the playset files were generated: run "
                "`python scripts/live_setup.py prepare --apply` with the game closed")
    return None
