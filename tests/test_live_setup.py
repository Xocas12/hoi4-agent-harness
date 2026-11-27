"""scripts/live_setup.py, against temp folders only.

The real Hearts of Iron IV folder is never read or written here: discovery and the
real process list are replaced by functions that fail the test if they are called.
"""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_setup.py"
spec = importlib.util.spec_from_file_location("live_setup", SCRIPT)
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

NOT_RUNNING = lambda: ["explorer.exe", "hoi4-harness", "python.exe"]  # noqa: E731
RUNNING = lambda: ["explorer.exe", "hoi4.exe"]  # noqa: E731


@pytest.fixture(autouse=True)
def never_the_real_machine(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("a test reached the real machine (discovery or process list)")

    monkeypatch.setattr(ls, "list_processes", boom)
    # The real install is the real machine too: no test regenerates from it.
    monkeypatch.setattr(ls, "default_game_dir", lambda explicit=None: Path(explicit) if explicit else None)
    monkeypatch.setattr(ls.paths, "game_dirs", boom)
    monkeypatch.setattr(ls.paths, "documents_roots", boom)
    original = ls.paths.find_game_dir
    monkeypatch.setattr(ls.paths, "find_game_dir",
                        lambda explicit=None: original(explicit) if explicit else boom())


DLC = {"enabled_mods": ["mod/ugc_111.mod", "mod/llm_bridge.mod", "mod/ugc_222.mod"], "disabled_dlcs": [],
       "extra": 1}
SETTINGS = "\ufeffgraphics={\r\n\tsize={ x=1280 y=720 }\r\n}\r\nsave_as_binary=yes\r\nautosave=1\r\n"


def make_user(tmp_path, settings=SETTINGS, dlc=DLC, installed_mod=True, expert=True) -> Path:
    user = tmp_path / "Hearts of Iron IV"
    (user / "mod").mkdir(parents=True)
    if dlc is not None:
        (user / "dlc_load.json").write_text(json.dumps(dlc), encoding="utf-8")
    if settings is not None:
        (user / "settings.txt").write_bytes(settings.encode("utf-8"))
    if installed_mod:
        old = user / "mod" / "llm_bridge"
        (old / "common").mkdir(parents=True)
        (old / "common" / "old.txt").write_text("stale", encoding="utf-8")
        (old / "descriptor.mod").write_text("old descriptor", encoding="utf-8")
        (user / "mod" / "llm_bridge.mod").write_text("old", encoding="utf-8")
    if expert:
        (user / "mod" / "ugc_741805475.mod").write_text('name="Expert AI"\n', encoding="utf-8")
    return user


@pytest.fixture
def repo_mod(tmp_path):
    root = tmp_path / "repo" / "mod"
    (root / "llm_bridge" / "common").mkdir(parents=True)
    (root / "llm_bridge" / "common" / "a.txt").write_text("new a\r\n", encoding="utf-8")
    (root / "llm_bridge" / "descriptor.mod").write_text("desc", encoding="utf-8")
    (root / "llm_bridge.mod").write_text('name="LLM Bridge"\n', encoding="utf-8")
    return root


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def run(user, *args, lister=NOT_RUNNING, repo_mod=None):
    lines: list[str] = []
    code = ls.main([*args, "--user-dir", str(user)], lister=lister, repo_mod=repo_mod, out=lines.append)
    return code, "\n".join(lines)


# --- dry run -----------------------------------------------------------------------------------

def test_a_dry_run_prints_the_diff_and_writes_nothing(tmp_path, repo_mod):
    user = make_user(tmp_path)
    before = snapshot(tmp_path)
    code, text = run(user, "prepare", "--expert-ai", repo_mod=repo_mod)
    assert code == 0
    assert "+save_as_binary=no" in text and "-save_as_binary=yes" in text
    assert '+        "mod/ugc_741805475.mod"' in text
    assert "dry run: nothing was written" in text
    assert "- common/old.txt" in text and "+ common/a.txt" in text
    assert snapshot(tmp_path) == before                       # not even a backup folder


def test_restore_is_a_dry_run_by_default_too(tmp_path, repo_mod):
    user = make_user(tmp_path)
    run(user, "prepare", "--apply", repo_mod=repo_mod)
    before = snapshot(tmp_path)
    code, text = run(user, "restore")
    assert code == 0 and "dry run" in text
    assert snapshot(tmp_path) == before


# --- prepare --apply ------------------------------------------------------------------------------

def test_apply_installs_the_mod_enables_it_last_and_sets_text_saves(tmp_path, repo_mod):
    user = make_user(tmp_path)
    code, text = run(user, "prepare", "--expert-ai", "--apply", repo_mod=repo_mod)
    assert code == 0, text
    dlc = json.loads((user / "dlc_load.json").read_text(encoding="utf-8"))
    assert dlc["enabled_mods"] == ["mod/ugc_111.mod", "mod/ugc_222.mod", "mod/ugc_741805475.mod",
                                   "mod/llm_bridge.mod"]
    assert dlc["extra"] == 1                                   # unrelated keys survive
    settings = (user / "settings.txt").read_bytes().decode("utf-8")
    assert settings == SETTINGS.replace("save_as_binary=yes", "save_as_binary=no")   # BOM and CRLF kept
    assert snapshot(user / "mod" / "llm_bridge") == snapshot(repo_mod / "llm_bridge")
    assert (user / "mod" / "llm_bridge.mod").read_bytes() == (repo_mod / "llm_bridge.mod").read_bytes()
    assert (user / "mod" / "ugc_741805475.mod").exists()       # other mods untouched


def test_without_the_flag_expert_ai_is_not_added(tmp_path, repo_mod):
    user = make_user(tmp_path)
    run(user, "prepare", "--apply", repo_mod=repo_mod)
    mods = json.loads((user / "dlc_load.json").read_text(encoding="utf-8"))["enabled_mods"]
    assert "mod/ugc_741805475.mod" not in mods and mods[-1] == "mod/llm_bridge.mod"


def test_expert_ai_that_is_not_subscribed_refuses_before_changing_anything(tmp_path, repo_mod):
    user = make_user(tmp_path, expert=False)
    before = snapshot(tmp_path)
    code, _ = run(user, "prepare", "--expert-ai", "--apply", repo_mod=repo_mod)
    assert code == 2
    assert snapshot(tmp_path) == before


def test_the_backup_holds_the_originals_the_mod_and_a_manifest(tmp_path, repo_mod):
    user = make_user(tmp_path)
    run(user, "prepare", "--apply", repo_mod=repo_mod)
    (backup,) = ls.list_backups(user)
    assert (backup / "dlc_load.json").read_text(encoding="utf-8") == json.dumps(DLC)
    assert (backup / "settings.txt").read_bytes().decode("utf-8") == SETTINGS
    assert (backup / "mod" / "llm_bridge" / "common" / "old.txt").read_text() == "stale"
    assert (backup / "mod" / "llm_bridge.mod").read_text() == "old"
    manifest = json.loads((backup / "manifest.json").read_text())
    assert manifest["kind"] == "prepare" and set(manifest["files"]) == {
        "dlc_load.json", "settings.txt", "pdx_settings.txt"}


def test_two_applies_in_the_same_second_never_share_or_overwrite_a_backup(tmp_path, repo_mod):
    import datetime as dt

    user = make_user(tmp_path)
    moment = dt.datetime(2026, 11, 20, 12, 0, 0)
    first = ls.make_backup(user, "prepare", None, moment)
    marker = first / "marker"
    marker.write_text("keep me")
    second = ls.make_backup(user, "prepare", None, moment)
    assert first != second and marker.read_text() == "keep me"


def test_a_missing_dlc_load_is_created_and_a_missing_settings_is_refused(tmp_path, repo_mod):
    user = make_user(tmp_path, dlc=None)
    assert run(user, "prepare", "--apply", repo_mod=repo_mod)[0] == 0
    assert json.loads((user / "dlc_load.json").read_text())["enabled_mods"] == ["mod/llm_bridge.mod"]
    bare = make_user(tmp_path / "b", settings=None)
    before = snapshot(bare)
    assert run(bare, "prepare", "--apply", repo_mod=repo_mod)[0] == 2
    assert snapshot(bare) == before


def test_a_settings_file_without_the_key_gets_it_appended():
    assert ls.edit_settings("a=1\n") == "a=1\nsave_as_binary=no\n"
    assert ls.edit_settings("a=1") == "a=1\nsave_as_binary=no\n"
    assert ls.edit_settings("a=1\r\n") == "a=1\r\nsave_as_binary=no\r\n"
    assert ls.edit_settings("  save_as_binary = yes # c\n") == "  save_as_binary=no # c\n"
    assert ls.edit_settings("save_as_binary=no\n") == "save_as_binary=no\n"


def test_a_broken_dlc_load_is_not_touched(tmp_path, repo_mod):
    user = make_user(tmp_path)
    (user / "dlc_load.json").write_text("{not json", encoding="utf-8")
    before = snapshot(tmp_path)
    assert run(user, "prepare", "--apply", repo_mod=repo_mod)[0] == 2
    assert snapshot(tmp_path) == before


# --- the running game ---------------------------------------------------------------------------------

@pytest.mark.parametrize("command", ["prepare", "restore"])
def test_both_commands_refuse_while_hoi4_runs_even_with_apply(tmp_path, repo_mod, command, capsys):
    user = make_user(tmp_path)
    if command == "restore":
        run(user, "prepare", "--apply", repo_mod=repo_mod)
    before = snapshot(tmp_path)
    code, _ = run(user, command, "--apply", lister=RUNNING, repo_mod=repo_mod)
    assert code == 2 and "HOI4 is running" in capsys.readouterr().err
    assert snapshot(tmp_path) == before


def test_an_unreadable_process_list_refuses_rather_than_guessing(tmp_path, repo_mod, capsys):
    def unreadable():
        raise ls.SetupError("cannot read the process list, so I cannot tell; refusing")

    user = make_user(tmp_path)
    assert run(user, "prepare", "--apply", lister=unreadable, repo_mod=repo_mod)[0] == 2


def test_the_process_check_matches_the_image_name_exactly():
    ls.refuse_if_running(lambda: ["hoi4-harness", "hoi4launcher.exe", "python.exe"])
    with pytest.raises(ls.SetupError):
        ls.refuse_if_running(lambda: ["HOI4.EXE"])


# --- restore --------------------------------------------------------------------------------------------

def test_restore_puts_back_exactly_the_backed_up_files(tmp_path, repo_mod):
    user = make_user(tmp_path)
    original = snapshot(user)
    run(user, "prepare", "--expert-ai", "--apply", repo_mod=repo_mod)
    code, text = run(user, "restore", "--apply")
    assert code == 0, text
    now = snapshot(user)
    assert now["dlc_load.json"] == original["dlc_load.json"]
    assert now["settings.txt"] == original["settings.txt"]      # byte for byte, BOM included
    kinds = sorted(json.loads((b / "manifest.json").read_text())["kind"] for b in ls.list_backups(user))
    assert kinds == ["pre-restore", "prepare"]


def test_restore_saves_what_it_replaces_and_no_backup_is_ever_deleted(tmp_path, repo_mod):
    user = make_user(tmp_path)
    run(user, "prepare", "--apply", repo_mod=repo_mod)
    (user / "settings.txt").write_bytes(b"edited after prepare\n")
    before = {b.name: snapshot(b) for b in ls.list_backups(user)}
    run(user, "restore", "--apply")
    after = {b.name: snapshot(b) for b in ls.list_backups(user)}
    for name, files in before.items():
        assert after[name] == files                             # untouched
    (safety,) = [b for b in ls.list_backups(user) if b.name not in before]
    assert (safety / "settings.txt").read_bytes() == b"edited after prepare\n"


def test_restore_of_a_file_that_did_not_exist_sets_it_aside_instead_of_deleting(tmp_path, repo_mod):
    user = make_user(tmp_path, dlc=None)
    run(user, "prepare", "--apply", repo_mod=repo_mod)
    assert (user / "dlc_load.json").exists()
    assert run(user, "restore", "--apply")[0] == 0
    assert not (user / "dlc_load.json").exists()
    assert list(user.glob("dlc_load.json.removed-by-restore-*"))


def test_a_damaged_backup_is_refused(tmp_path, repo_mod, capsys):
    user = make_user(tmp_path)
    run(user, "prepare", "--apply", repo_mod=repo_mod)
    (backup,) = ls.list_backups(user)
    (backup / "settings.txt").write_bytes(b"tampered")
    live = snapshot(user / "mod"), (user / "settings.txt").read_bytes()
    assert run(user, "restore", "--apply")[0] == 2
    assert "damaged" in capsys.readouterr().err
    assert (snapshot(user / "mod"), (user / "settings.txt").read_bytes()) == live


def test_restore_with_no_backup_says_so(tmp_path, capsys):
    user = make_user(tmp_path)
    assert run(user, "restore", "--apply")[0] == 2
    assert "nothing to restore" in capsys.readouterr().err


def test_a_named_backup_that_does_not_exist_is_refused(tmp_path, capsys):
    user = make_user(tmp_path)
    assert run(user, "restore", "--backup", "20260101-000000")[0] == 2


# --- finding the folder -----------------------------------------------------------------------------------

def test_without_user_dir_the_harness_discovery_is_used_and_a_miss_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr(ls.paths, "find_game_dir", lambda explicit=None: None)
    monkeypatch.setattr(ls.paths, "game_dirs", lambda: iter([Path("C:/nowhere/Hearts of Iron IV")]))
    assert ls.main(["prepare"], lister=NOT_RUNNING) == 2
    assert "no Hearts of Iron IV user folder" in capsys.readouterr().err


def test_a_named_user_dir_that_does_not_exist_is_an_error_with_no_fallback(tmp_path, capsys):
    assert ls.main(["prepare", "--user-dir", str(tmp_path / "missing")], lister=NOT_RUNNING) == 2
