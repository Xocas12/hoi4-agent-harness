"""Issue #157: the OpenGL renderer leak, handled by the harness, and the performance budget."""

import importlib.util
import json
from pathlib import Path

import pytest

from hoi4_harness.campaign.budget import load_budget
from hoi4_harness.campaign.preflight import FAIL, PASS, renderer_check
from hoi4_harness.cli import main

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_setup.py"
spec = importlib.util.spec_from_file_location("live_setup_157", SCRIPT)
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

PDX = '"Graphics"={\r\n\t"renderer"={\r\n\t\tvalue="opengl"\r\n\t\tversion=0\r\n\t}\r\n}\r\nother=1\r\n'


def make_user(tmp_path, pdx=PDX):
    user = tmp_path / "Hearts of Iron IV"
    (user / "mod" / "llm_bridge").mkdir(parents=True)
    (user / "dlc_load.json").write_text(json.dumps({"enabled_mods": [], "disabled_dlcs": []}))
    (user / "settings.txt").write_bytes(b"save_as_binary=yes\r\n")
    if pdx is not None:
        (user / "pdx_settings.txt").write_bytes(pdx.encode())
    repo = tmp_path / "repo" / "mod"
    (repo / "llm_bridge").mkdir(parents=True)
    (repo / "llm_bridge" / "a.txt").write_text("a")
    (repo / "llm_bridge.mod").write_text('name="x"\n')
    return user, repo


def run(user, repo, *args):
    lines = []
    code = ls.main([*args, "--user-dir", str(user)], lister=lambda: ["explorer.exe"], repo_mod=repo,
                   out=lines.append)
    return code, "\n".join(lines)


def test_157_prepare_sets_dx11_and_says_so(tmp_path):
    user, repo = make_user(tmp_path)
    code, text = run(user, repo, "prepare", "--apply")
    assert code == 0
    assert "renderer: opengl -> dx11" in text
    new = (user / "pdx_settings.txt").read_bytes().decode()
    assert 'value="dx11"' in new and "opengl" not in new
    assert new == PDX.replace('"opengl"', '"dx11"')            # nothing else touched, CRLF kept


def test_157_dry_run_leaves_pdx_settings_alone(tmp_path):
    user, repo = make_user(tmp_path)
    code, text = run(user, repo, "prepare")
    assert code == 0 and "renderer: opengl -> dx11" in text
    assert (user / "pdx_settings.txt").read_bytes().decode() == PDX


def test_157_restore_puts_the_renderer_back(tmp_path):
    user, repo = make_user(tmp_path)
    run(user, repo, "prepare", "--apply")
    assert run(user, repo, "restore", "--apply")[0] == 0
    assert (user / "pdx_settings.txt").read_bytes().decode() == PDX


def test_157_dx11_or_no_file_is_left_alone(tmp_path):
    dx = PDX.replace("opengl", "dx11")
    user, repo = make_user(tmp_path, pdx=dx)
    _, text = run(user, repo, "prepare", "--apply")
    assert "already the leak-free one" in text
    assert (user / "pdx_settings.txt").read_bytes().decode() == dx
    user2, repo2 = make_user(tmp_path / "b", pdx=None)
    run(user2, repo2, "prepare", "--apply")
    assert not (user2 / "pdx_settings.txt").exists()            # prepare never creates it
    assert run(user2, repo2, "restore", "--apply")[0] == 0


def test_157_restore_from_a_backup_made_before_pdx_settings_was_tracked(tmp_path):
    user, repo = make_user(tmp_path)
    run(user, repo, "prepare", "--apply")
    backup = ls.list_backups(user)[-1]
    manifest = json.loads((backup / "manifest.json").read_text())
    del manifest["files"]["pdx_settings.txt"]
    (backup / "manifest.json").write_text(json.dumps(manifest))
    (backup / "pdx_settings.txt").unlink()
    assert run(user, repo, "restore", "--apply")[0] == 0
    assert (user / "pdx_settings.txt").exists()                 # not renamed away


def test_157_preflight_fails_on_opengl_with_the_fix(tmp_path):
    user, _ = make_user(tmp_path)
    check = renderer_check(user)
    assert check.status == FAIL
    assert "live_setup.py prepare --apply" in check.detail and "dx11" in check.detail
    (user / "pdx_settings.txt").write_text(PDX.replace("opengl", "dx11"))
    assert renderer_check(user).status == PASS


def test_157_doctor_reports_the_renderer(tmp_path, capsys):
    user, _ = make_user(tmp_path)
    main(["doctor", "--user-dir", str(user)])
    out = capsys.readouterr().out
    assert "renderer" in out and "LEAKS" in out
    (user / "pdx_settings.txt").write_text(PDX.replace("opengl", "dx11"))
    main(["doctor", "--user-dir", str(user)])
    assert "renderer         dx11" in capsys.readouterr().out


# --- the budget ---------------------------------------------------------------------------------

T0 = 1_000_000.0


def write_run(run_dir: Path, records: list[dict], log: str = "") -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "transcript.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    if log:
        (run_dir / "campaign.log").write_text(log)
    return run_dir


def turn(t, frm, to, model=60, actions=5, observe=10, game=30):
    """One turn's records, starting at wall time t; returns (records, end time)."""
    recs = [{"t": t + observe, "kind": "observe", "date": frm},
            {"t": t + observe + model, "kind": "llm", "tool_calls": []},
            {"t": t + observe + model + actions, "kind": "actions"},
            {"t": t + observe + model + actions + game, "kind": "turn_end", "date": to, "from_date": frm}]
    return recs, recs[-1]["t"]


@pytest.fixture
def fixture_run(tmp_path):
    recs = [{"t": T0, "kind": "playset"}]
    t = T0
    for frm, to in (("1937-01-01", "1937-02-01"), ("1937-02-01", "1937-04-02"),
                    ("1938-01-01", "1938-01-31")):
        new, t = turn(t, frm, to)
        recs += new
    log = ("[2026-11-22 01:00:00] 1937-02-01  wake: scheduled review | wars: none\n"
           "[2026-11-22 06:04:10] HOI4 holds 49 GB of private memory (limit 48 GB): it leaks\n")
    return write_run(tmp_path / "run", recs, log)


def test_157_budget_splits_model_game_and_overhead_per_year(fixture_run):
    b = load_budget(fixture_run)
    y37, y38 = b.by_year[1937], b.by_year[1938]
    assert (y37.turns, y37.days, y37.calls) == (2, 31 + 60, 2)
    assert y37.model == 120 and y37.game == 60 and y37.overhead == 30    # 2 x (60 / 30 / 5+10 s)
    assert y38.turns == 1 and y38.days == 30
    assert b.total.turns == 3 and b.total.model == 180
    assert y37.per_month(y37.model) == pytest.approx(2.0 / (91 / 30.4375))


def test_157_budget_skips_the_time_the_run_was_down(tmp_path):
    first, t = turn(T0, "1937-01-01", "1937-02-01")
    gap = [{"t": t + 5000, "kind": "resumed"}]
    second, _ = turn(t + 5000, "1937-02-01", "1937-03-01")
    run = write_run(tmp_path / "run", [{"t": T0, "kind": "playset"}, *first, *gap, *second])
    b = load_budget(run)
    assert b.total.turns == 2 and b.down_seconds == 5000
    assert b.total.total == 2 * 105                           # 10 + 60 + 5 + 30 a turn


def test_157_budget_memory_comes_from_the_log(fixture_run):
    b = load_budget(fixture_run)
    assert b.memory == [("2026-11-22 06:04:10", 49.0, "1937-02-01")]
    text = b.render()
    assert "| 1937 |" in text and "| 49 | 1937-02-01 |" in text
    assert "min per game month" in text


def test_157_budget_says_so_when_the_log_has_no_memory(tmp_path):
    recs, _ = turn(T0 - 100, "1937-01-01", "1937-02-01")
    run = write_run(tmp_path / "run", [{"t": T0 - 200, "kind": "playset"}, *recs])
    assert "no `HOI4 holds N GB` line" in load_budget(run).render()


def test_157_budget_with_no_transcript_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_budget(tmp_path)


def test_157_campaign_report_budget_command(fixture_run, capsys, tmp_path):
    out = tmp_path / "budget.md"
    assert main(["campaign-report", "--run-dir", str(fixture_run), "--budget", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "# Performance budget: run" in text and "| all |" in text
    assert out.read_text(encoding="utf-8").startswith("# Performance budget")
    assert main(["campaign-report", "--run-dir", str(tmp_path / "nope"), "--budget"]) == 1
