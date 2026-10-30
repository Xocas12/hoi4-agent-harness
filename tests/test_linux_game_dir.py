"""Native Linux HOI4 keeps its user dir in ~/.local/share; discovery never looks there."""
from pathlib import Path

from hoi4_harness.adapters.logtail import find_log


def test_linux_user_dir_found(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.delenv("OneDrive", raising=False)
    log = tmp_path / ".local/share/Paradox Interactive/Hearts of Iron IV/logs/game.log"
    log.parent.mkdir(parents=True)
    log.write_text("")
    assert find_log() == log


def test_xdg_data_home_is_respected(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.delenv("USERPROFILE", raising=False)
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    log = tmp_path / "data/Paradox Interactive/Hearts of Iron IV/logs/game.log"
    log.parent.mkdir(parents=True)
    log.write_text("")
    assert find_log() == log
