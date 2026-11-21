"""The playset-specific mod files follow the launcher's mods: a mod that redraws sea regions or
states gets blocks for its own ids, and `native` warns when the mods changed since."""

from __future__ import annotations

import json
from pathlib import Path

from hoi4_harness import playset_files
from hoi4_harness.regions import FILE as REGIONS_FILE
from orders_helpers import write_grid_game


def _user(tmp_path: Path, mods: list[str]) -> Path:
    user = tmp_path / "user"
    (user / "mod").mkdir(parents=True)
    (user / "dlc_load.json").write_text(json.dumps({"enabled_mods": mods, "disabled_dlcs": []}),
                                        encoding="utf-8")
    return user


def _mod(user: Path, tmp_path: Path) -> str:
    folder = tmp_path / "workshop" / "123"
    (folder / "map" / "strategicregions").mkdir(parents=True)
    (folder / "descriptor.mod").write_text('name="Sea Mod"\nsupported_version="1.20.*"\n',
                                           encoding="utf-8")
    (folder / "map" / "strategicregions" / "50-New Sea.txt").write_text(
        'strategic_region={\n\tid=50\n\tname="STRATEGICREGION_50"\n\tprovinces={ 1 2 }\n}\n',
        encoding="utf-8")
    (user / "mod" / "ugc_123.mod").write_text(f'name="Sea Mod"\npath="{folder.as_posix()}"\n',
                                              encoding="utf-8")
    return "mod/ugc_123.mod"


def test_the_playset_files_are_built_from_the_enabled_mods_and_stamped(tmp_path):
    game = write_grid_game(tmp_path / "game")
    user = _user(tmp_path, ["mod/llm_bridge.mod"])
    entry = _mod(user, tmp_path)
    (user / "dlc_load.json").write_text(json.dumps({"enabled_mods": [entry, "mod/llm_bridge.mod"]}),
                                        encoding="utf-8")
    installed = user / "mod" / "llm_bridge"
    line = playset_files.regenerate(installed, user, game)
    assert "vanilla + 1 mod(s) (Sea Mod)" in line
    regions = (installed / REGIONS_FILE).read_text(encoding="utf-8")
    assert "id = 50" in regions                       # the mod's own region
    assert json.loads((installed / playset_files.STAMP).read_text(encoding="utf-8")) == {
        "mods": [entry]}
    assert playset_files.stale(installed, user) is None


def test_native_is_warned_when_the_mods_changed_since(tmp_path):
    user = _user(tmp_path, [])
    installed = user / "mod" / "llm_bridge"
    installed.mkdir()
    assert playset_files.stale(installed, user) is None          # vanilla, vanilla files
    entry = _mod(user, tmp_path)
    (user / "dlc_load.json").write_text(json.dumps({"enabled_mods": [entry]}), encoding="utf-8")
    assert "built for vanilla" in playset_files.stale(installed, user)
    (installed / playset_files.STAMP).write_text(json.dumps({"mods": ["mod/other.mod"]}),
                                                 encoding="utf-8")
    assert "changed since" in playset_files.stale(installed, user)
