"""The setup window's logic (setup_model.py, setup_gui.SetupController), the setup file, what a
launch runs, and the CLI flags that mirror every field. No window is opened here, except the one
test that needs a display and skips without one."""

from __future__ import annotations

import copy
import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from hoi4_harness import setup_model
from hoi4_harness.agent.prompts import build_system, system_for
from hoi4_harness.cli import _config_from_args, build_parser, main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.players import PlayersRegistry
from hoi4_harness.rival.multi import member_config, split_budget
from hoi4_harness.rival.setup import instruction_for, planner_config_for
from hoi4_harness.setup_gui import SetupController
from hoi4_harness.setup_model import PlayerSetup, Setup

SECRET = "sk-test-SECRET-0123456789"


def full_setup(**overrides) -> Setup:
    setup = Setup(
        players=[
            PlayerSetup("POL", "claude-cli", "", instruction="go communist"),
            PlayerSetup("FRA", "anthropic", "claude-opus-5", max_usd=5.0, usd_per_m_input=5.0,
                        usd_per_m_output=25.0, instruction="ally the UK"),
            PlayerSetup("ROM", "local", "llama3.1", base_url="http://localhost:11434/v1"),
            PlayerSetup("ITA", "openai", "gpt-5", max_usd=2.0, usd_per_m_input=1.0,
                        usd_per_m_output=8.0),
            PlayerSetup("SWE", "google"),
        ],
        keys={"anthropic": SECRET, "openai": SECRET + "-o", "google": SECRET + "-g"},
        launch_game=True, game_dir="C:/games/hoi4", telemetry_hours=6, keep_running=True,
        until="1941-06-22", my_country="GER")
    for key, value in overrides.items():
        setattr(setup, key, value)
    return setup


def _config(argv):
    return _config_from_args(build_parser().parse_args(argv))


def _saved(tmp_path, **overrides):
    return str(full_setup(my_country="", **overrides).save(tmp_path / "setup.json"))


# --- the file -------------------------------------------------------------------------------


def test_setup_json_round_trips(tmp_path):
    setup = full_setup()
    path = setup.save(tmp_path / "setup.json")
    assert Setup.load(path) == setup


def test_the_default_file_lives_in_the_harness_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOI4_HARNESS_HOME", str(tmp_path / "home"))
    path = Setup(players=[PlayerSetup("POL")]).save()
    assert path == tmp_path / "home" / "setup.json" and path.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_file_is_private(tmp_path):
    path = full_setup().save(tmp_path / "setup.json")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o644)
    full_setup().save(path)                       # an existing file is tightened too
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_bad_file_says_what_is_wrong(tmp_path):
    (tmp_path / "bad.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        Setup.load(tmp_path / "bad.json")
    with pytest.raises(FileNotFoundError, match="no setup file"):
        Setup.load(tmp_path / "missing.json")


def test_checks_name_what_is_missing():
    assert Setup().problems()
    assert full_setup(my_country="").problems(environ={}) == []
    broken = Setup(players=[PlayerSetup("PO", "anthropic"), PlayerSetup("FRA", "local"),
                            PlayerSetup("FRA", "claude-cli", max_usd=1.0),
                            PlayerSetup("ITA", "anthropic", max_usd=3.0)], launch_game=True,
                   until="soon", telemetry_hours=5)
    text = " ".join(broken.problems(environ={}))
    for needle in ("three letters", "no Anthropic API key", "address", "name", "listed twice",
                   "only applies to paid", "price per million", "game folder", "like 1941-06-22",
                   "telemetry_hours"):
        assert needle in text, needle
    assert any("your own country" in p for p in full_setup(my_country="FRA").problems(environ={}))


def test_a_key_in_the_environment_counts():
    setup = Setup(players=[PlayerSetup("FRA", "anthropic")])
    assert setup.problems(environ={})
    assert setup.problems(environ={"ANTHROPIC_API_KEY": "x"}) == []


# --- no key leaks ---------------------------------------------------------------------------


def test_keys_stay_out_of_the_run_copy_the_argv_and_the_flags(tmp_path):
    setup = full_setup()
    plan = setup_model.build_launch(setup, home=tmp_path, python="python", base_env={})
    assert SECRET not in " ".join(plan.argv)
    assert SECRET not in plan.run_copy.read_text(encoding="utf-8")
    assert SECRET not in " ".join(setup.native_args())
    assert SECRET not in repr(plan) and SECRET not in str(setup.players_config())
    assert plan.env["ANTHROPIC_API_KEY"] == SECRET
    assert plan.env["OPENAI_API_KEY"] == SECRET + "-o"
    assert plan.env["GOOGLE_API_KEY"] == SECRET + "-g"
    assert plan.env[setup_model.LOCAL_KEY_ENV] == "not-needed"
    assert plan.argv[1:5] == ["-m", "hoi4_harness.cli", "start", "--setup"]
    assert Setup.load(plan.run_copy).keys == {}


def test_launch_runs_one_detached_process_and_logs_to_a_file(tmp_path):
    seen = {}

    def spawn(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return SimpleNamespace(pid=4242)

    plan, pid = setup_model.launch(full_setup(), spawn=spawn, home=tmp_path, base_env={})
    assert pid == 4242 and plan.log_path.parent == tmp_path / "logs"
    assert seen["env"]["ANTHROPIC_API_KEY"] == SECRET
    assert SECRET not in " ".join(seen["argv"])
    assert "creationflags" in seen or seen.get("start_new_session") is True


def test_the_environment_of_a_run_only_fills_what_is_unset():
    env = {"ANTHROPIC_API_KEY": "already"}
    setup_model.inject_environment(full_setup(), env)
    assert env["ANTHROPIC_API_KEY"] == "already" and env["OPENAI_API_KEY"] == SECRET + "-o"


def test_building_a_launch_leaves_this_processs_environment_alone(tmp_path):
    before = dict(os.environ)
    setup_model.build_launch(full_setup(), home=tmp_path)
    assert dict(os.environ) == before


def test_a_run_never_prints_a_key(tmp_path, capsys, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("hoi4_harness.native.installed_mod_problems", lambda _dir: ["no mod here"])
    main(["start", "--setup", _saved(tmp_path, launch_game=False), "--run-dir", str(tmp_path / "run")])
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err


# --- one command line, one meaning ----------------------------------------------------------


def test_start_setup_and_the_flags_are_the_same_run(tmp_path):
    setup = full_setup(my_country="")
    from_file = _config(["native", "--setup", _saved(tmp_path), "--live"])
    from_flags = _config(setup.native_args() + ["--live"])
    assert from_file.rivals == from_flags.rivals == ["POL", "FRA", "ROM", "ITA", "SWE"]
    for tag in from_file.rivals:
        assert planner_config_for(from_file, tag) == planner_config_for(from_flags, tag)
        assert instruction_for(from_file, tag) == instruction_for(from_flags, tag)
    assert split_budget(from_file, from_file.rivals) == split_budget(from_flags, from_flags.rivals)
    assert from_file.rival_players["FRA"]["usd_per_m_output"] == 25.0 == (
        from_flags.rival_players["FRA"]["usd_per_m_output"])
    assert from_file.telemetry_hours == from_flags.telemetry_hours == 6


def test_each_provider_builds_its_planner(tmp_path):
    cfg = _config(["native", "--setup", _saved(tmp_path), "--live"])
    got = {t: planner_config_for(cfg, t) for t in cfg.rivals}
    assert (got["POL"].provider, got["POL"].model, got["POL"].base_url) == ("claude-cli", "haiku", None)
    assert (got["FRA"].provider, got["FRA"].model) == ("anthropic", "claude-opus-5")
    assert (got["ROM"].provider, got["ROM"].model, got["ROM"].base_url) == (
        "openai", "llama3.1", "http://localhost:11434/v1")
    assert got["ROM"].api_key_env == setup_model.LOCAL_KEY_ENV
    assert (got["ITA"].provider, got["ITA"].model, got["ITA"].api_key_env) == ("openai", "gpt-5", None)
    assert (got["SWE"].provider, got["SWE"].model) == ("google", "gemini-2.5-pro")
    assert cfg.planner.provider == "scripted"            # the shared planner: no country's own leaked


def test_a_cap_per_country_and_none_for_the_free_ones(tmp_path):
    cfg = _config(["native", "--setup", _saved(tmp_path), "--live"])
    assert split_budget(cfg, cfg.rivals) == {"POL": None, "FRA": 5.0, "ROM": None, "ITA": 2.0,
                                             "SWE": None}


def test_one_country_gets_its_cap_prices_and_instruction(tmp_path):
    setup = Setup(players=[PlayerSetup("FRA", "anthropic", max_usd=4.0, usd_per_m_input=3.0,
                                       usd_per_m_output=15.0, instruction="hold the Maginot line")],
                  keys={"anthropic": SECRET})
    cfg = _config(["native", "--setup", str(setup.save(tmp_path / "s.json")), "--live"])
    assert cfg.rival == "FRA" and cfg.rivals == []
    assert cfg.budget.max_usd == 4.0 and cfg.budget.usd_per_m_output == 15.0
    assert cfg.planner.provider == "anthropic" and cfg.instruction == "hold the Maginot line"


def test_flags_beat_the_file(tmp_path):
    cfg = _config(["native", "--setup", _saved(tmp_path), "--rival", "FRA", "--provider", "openai",
                   "--model", "gpt-5-mini", "--max-usd", "9", "--live"])
    assert cfg.rival == "FRA" and cfg.planner.provider == "openai"
    assert cfg.planner.model == "gpt-5-mini" and cfg.budget.max_usd == 9.0


def test_per_country_flags_without_a_file():
    cfg = _config(["native", "--rival", "FRA,ENG", "--player-provider", "ENG=openai",
                   "--player-model", "ENG=gpt-5", "--player-instruction", "FRA=go fascist",
                   "--player-max-usd", "ENG=3", "--player-price", "ENG=1,2", "--instruction",
                   "play it safe", "--live"])
    assert cfg.rivals == ["FRA", "ENG"]
    assert instruction_for(cfg, "FRA") == "go fascist"
    assert instruction_for(cfg, "ENG") == "play it safe"
    assert cfg.rival_players["ENG"]["usd_per_m_output"] == 2.0


def test_bad_player_flags_are_refused_in_words():
    with pytest.raises(SystemExit, match="TAG=VALUE"):
        _config(["native", "--rival", "FRA", "--player-model", "gpt-5"])
    with pytest.raises(SystemExit, match="not a number"):
        _config(["native", "--rival", "FRA", "--player-max-usd", "FRA=lots"])


def test_a_setup_with_problems_does_not_run(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    path = Setup(players=[PlayerSetup("FRA", "anthropic")]).save(tmp_path / "s.json")
    with pytest.raises(SystemExit, match="no Anthropic"):
        _config(["native", "--setup", str(path)])


def test_start_wants_a_setup_file(capsys):
    assert main(["start"]) == 2 and "--setup FILE" in capsys.readouterr().err


def test_native_wants_a_country(capsys):
    assert main(["native"]) == 2 and "--rival" in capsys.readouterr().err


def test_native_refuses_the_country_you_play(capsys):
    assert main(["native", "--rival", "POL", "--my-country", "pol"]) == 2
    assert "the country you play" in capsys.readouterr().err


# --- the instruction ------------------------------------------------------------------------


def test_the_instruction_reaches_the_system_prompt():
    text = build_system(instruction="go alt-history Germany and ally the UK")
    assert "standing instruction" in text and "go alt-history Germany and ally the UK" in text
    assert "standing instruction" not in build_system()
    assert "standing instruction" not in build_system(instruction="   ")


def test_the_instruction_flag_reaches_a_rivals_prompt():
    cfg = _config(["native", "--rival", "POL", "--instruction", "go communist", "--live"])
    assert "go communist" in system_for(cfg)
    assert "standing instruction" not in system_for(_config(["native", "--rival", "POL", "--live"]))


def test_each_country_has_its_own_instruction_in_its_own_prompt(tmp_path):
    cfg = _config(["native", "--setup", _saved(tmp_path), "--live"])
    prompts = {t: system_for(member_config(copy.deepcopy(cfg), t, None)) for t in ("POL", "FRA", "SWE")}
    assert "go communist" in prompts["POL"] and "go communist" not in prompts["FRA"]
    assert "ally the UK" in prompts["FRA"]
    assert "standing instruction" not in prompts["SWE"]


def test_the_instruction_works_for_play_too():
    assert _config(["play", "--instruction", "be cautious"]).instruction == "be cautious"
    assert HarnessConfig().instruction == ""


# --- the controller -------------------------------------------------------------------------


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "hoi4"
    (root / "common" / "country_tags").mkdir(parents=True)
    (root / "localisation" / "english").mkdir(parents=True)
    (root / "common" / "country_tags" / "00_countries.txt").write_text(
        'GER = "countries/Germany.txt"\nPOL = "countries/Poland.txt"  # comment\n'
        '# SOV = "countries/Soviet.txt"\nENG = "countries/England.txt"\nZZZ = "countries/Z.txt"\n',
        encoding="utf-8")
    (root / "localisation" / "english" / "countries_l_english.yml").write_text(
        'l_english:\n GER:0 "Germany"\n POL:0 "Poland"\n ENG:0 "United Kingdom"\n'
        ' GER_DEF:0 "The Reich"\n', encoding="utf-8")
    return root


def test_the_country_list_is_searchable(game, tmp_path):
    ctl = SetupController(Setup(game_dir=str(game)), tmp_path / "s.json", environ={})
    assert ctl.install == game
    assert ("POL", "Poland") in ctl.search("pol") and ("ENG", "United Kingdom") in ctl.search("kingdom")
    assert ctl.search("pol")[0][0] == "POL"
    assert len(ctl.search("")) == 4
    assert ctl.name_of("ger") == "Germany" and ctl.name_of("ZZZ") == "ZZZ"
    assert "SOV" not in dict(ctl.countries)        # commented out


def test_no_game_folder_means_free_text(tmp_path):
    ctl = SetupController(Setup(), tmp_path / "s.json", countries=[], environ={})
    assert ctl.search("x") == []
    assert ctl.add_player("pol").tag == "POL"


def test_the_game_folder_comes_from_the_environment(game):
    assert setup_model.find_install(environ={"HOI4_GAME_DIR": str(game)}) == game
    assert setup_model.find_install(game) == game


def test_add_edit_save_start(tmp_path):
    launched = []

    def fake_launch(setup):
        launched.append(setup)
        return SimpleNamespace(log_path=tmp_path / "run.log"), 77

    ctl = SetupController(Setup(), tmp_path / "setup.json", countries=[], launcher=fake_launch,
                          registry=PlayersRegistry(tmp_path / "players.json"), environ={})
    with pytest.raises(ValueError, match="three letters"):
        ctl.add_player("PO")
    ctl.add_player("pol")
    with pytest.raises(ValueError, match="already"):
        ctl.add_player("POL")
    assert ctl.start().ok is True          # the default Claude Code choice needs no key
    ctl.set_provider("POL", "anthropic")
    assert ctl.player("POL").model == "claude-opus-5" and ctl.player("POL").max_usd == 5.0
    bad = ctl.start()
    assert not bad.ok and any("no Anthropic" in p for p in bad.problems)
    ctl.set_key("anthropic", SECRET)
    ctl.set_provider("POL", "local")
    assert ctl.player("POL").base_url == setup_model.DEFAULT_LOCAL_URL
    assert ctl.player("POL").max_usd is None
    ctl.update_player("POL", model="llama3.1", instruction="go communist")
    result = ctl.start()
    assert result.ok and result.pid == 77 and len(launched) == 2
    saved = json.loads((tmp_path / "setup.json").read_text(encoding="utf-8"))
    assert saved["players"][0]["provider"] == "local" and saved["keys"] == {"anthropic": SECRET}


def test_a_saved_key_is_never_handed_back(tmp_path):
    ctl = SetupController(Setup(), tmp_path / "s.json", countries=[], environ={})
    assert ctl.key_status("anthropic") == "missing"
    ctl.set_key("anthropic", f"  {SECRET} ")
    assert ctl.key_status("anthropic") == "saved"
    ctl.set_key("anthropic", "")                  # the blank field after saving changes nothing
    assert ctl.setup.keys["anthropic"] == SECRET
    ctl.save()
    reopened = SetupController(path=tmp_path / "s.json", countries=[], environ={})
    assert reopened.key_status("anthropic") == "saved"
    ctl.remove_key("anthropic")
    assert ctl.key_status("anthropic") == "missing"
    other = SetupController(Setup(), tmp_path / "x.json", countries=[],
                            environ={"OPENAI_API_KEY": "k"})
    assert other.key_status("openai") == "environment"
    with pytest.raises(KeyError):
        ctl.set_key("claude-cli", "x")


def test_status_reads_the_registry_and_the_run_log(tmp_path):
    registry = PlayersRegistry(tmp_path / "players.json", alive=lambda pid: True)
    registry.register("POL", "claude-cli", "haiku", status="running")
    ctl = SetupController(Setup(players=[PlayerSetup("POL"), PlayerSetup("FRA")]),
                          tmp_path / "s.json", countries=[], registry=registry, environ={})
    assert [(s.tag, s.text) for s in ctl.status()] == [("POL", "playing"), ("FRA", "not started")]
    log = tmp_path / "run.log"
    log.write_text("one\ntwo\nthree\n", encoding="utf-8")
    ctl.launched = (log, 1)
    assert ctl.log_tail(2) == ["two", "three"]


def test_options_are_stored_and_checked(tmp_path):
    ctl = SetupController(Setup(), tmp_path / "s.json", countries=[], environ={})
    ctl.set_options(telemetry_hours=6, keep_running=True, until="1940-01-01")
    assert ctl.setup.telemetry_hours == 6
    with pytest.raises(KeyError):
        ctl.set_options(nonsense=1)


@pytest.mark.skipif(not os.environ.get("HOI4_TEST_TK"),
                    reason="opens a real window; set HOI4_TEST_TK=1 on a machine with a display")
def test_the_window_opens_when_there_is_a_display(tmp_path):
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    root.destroy()
    from hoi4_harness.setup_gui import SetupWindow

    window = SetupWindow(SetupController(Setup(players=[PlayerSetup("POL")]), tmp_path / "s.json",
                                         countries=[("POL", "Poland")], environ={}))
    window.root.update()
    window.root.destroy()
