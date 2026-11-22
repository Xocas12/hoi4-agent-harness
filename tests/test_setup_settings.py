"""The setup window's Settings / Countries / Run tabs (settings_model.py, #166), with no window.

Acceptance: a field that cannot be reached fails a test; the countries table writes a profile that
play and native read back; load, edit and save round-trips every field; the command line shown
builds the same config; the window opens and closes (skipped with no display)."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

from hoi4_harness import settings_model as sm
from hoi4_harness.cli import _config_from_args, build_parser
from hoi4_harness.config import HarnessConfig
from hoi4_harness.settings_model import CountryRow, SettingsModel


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("HOI4_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOI4_HARNESS_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "present-not-a-real-key")


def _specs():
    return {s.path: s for s in sm.field_specs()}


# --- #166 every HarnessConfig field is reachable ------------------------------------------------


def test_166_every_field_is_in_the_window_or_hidden_with_a_reason():
    specs = _specs()
    for path in sm.all_field_paths():
        assert path in specs or sm.HIDDEN.get(path), (
            f"{path} is neither a setting in the window nor listed in settings_model.HIDDEN with "
            f"a reason: add it to HIDDEN, or it appears by itself once it is a dataclass field")
    for path, reason in sm.HIDDEN.items():
        assert path in sm.all_field_paths(), f"HIDDEN names {path}, which is not a field"
        assert reason.strip()
        assert path not in specs


def test_166_nested_fields_are_reached():
    specs = _specs()
    for nested in ("planner.model", "planner.effort", "budget.max_usd", "budget.reserve_usd"):
        assert nested in specs


@dataclasses.dataclass
class Extended(HarnessConfig):
    #: Brand new thing a branch added.
    campaign_new_thing: int = 4
    rival_new_flag: bool = False


def test_166_a_new_dataclass_field_appears_without_a_ui_edit(monkeypatch):
    monkeypatch.setattr(sm, "HarnessConfig", Extended)
    specs = _specs()
    assert specs["campaign_new_thing"].group == "Campaign mode"
    assert specs["campaign_new_thing"].kind == sm.INT
    assert specs["campaign_new_thing"].help == "Brand new thing a branch added."
    assert specs["rival_new_flag"].group == "Rival mode"


def test_166_a_field_with_no_rule_lands_in_other_not_nowhere():
    assert sm.group_of("something_unheard_of") == sm.OTHER
    assert sm.group_of("campaign_whatever") == "Campaign mode"
    assert sm.group_of("rival_whatever") == "Rival mode"
    assert sm.group_of("headless_whatever") == "Headless play"


def test_166_help_default_and_widget_kind_for_each_setting():
    specs = _specs()
    for spec in specs.values():
        assert spec.help.strip() and spec.group
    assert "pre-set" not in specs["campaign_memory_limit_gb"].help
    assert "private memory" in specs["campaign_memory_limit_gb"].help      # the #: comment
    assert specs["campaign_take_focus_idle_seconds"].default == 120.0
    assert specs["campaign_take_focus"].kind == sm.BOOL
    assert specs["days_per_turn"].kind == sm.INT
    assert specs["clock_probe_seconds"].kind == sm.FLOAT
    assert specs["game_dir"].kind == sm.PATH
    assert specs["mod_dirs"].kind == sm.PATHS
    assert specs["skills"].kind == sm.LIST
    assert specs["unit_orders"].kind == sm.TRISTATE
    assert specs["action_channels"].kind == sm.JSON
    assert specs["control"].kind == sm.MAPPING_OR_TEXT
    assert specs["campaign_on_memory"].kind == sm.TEXT or specs["campaign_on_memory"].choices
    assert specs["headless_reader"].kind == sm.CHOICE
    assert "logtail" in specs["headless_reader"].choices
    assert "anthropic" in specs["planner.provider"].choices
    assert "paused" in specs["rival_act_when"].choices
    assert specs["guidance"].open_choices and "doctrine" in specs["guidance"].choices


# --- values: text <-> value ---------------------------------------------------------------------


def _sample(spec: sm.FieldSpec):
    """A value that differs from the default, of the right type."""
    if spec.choices and not spec.open_choices:
        return next(c for c in spec.choices if c != spec.default)
    kind, default = spec.kind, spec.default
    if kind == sm.BOOL:
        return not default
    if kind == sm.INT:
        return (default or 0) + 3
    if kind == sm.FLOAT:
        return (default or 0.0) + 1.5
    return {
        sm.TRISTATE: True,
        sm.TEXT: "sample text",
        sm.CHOICE: "sample-choice",
        sm.PATH: Path("some") / "place",
        sm.PATHS: [Path("a"), Path("b") / "c"],
        sm.LIST: ["one", "two"],
        sm.JSON: {"k": "v"},
        sm.MAPPING_OR_TEXT: "focus=llm,research=script",
    }[kind]


def test_166_text_round_trips_for_every_setting():
    for spec in sm.field_specs():
        value = _sample(spec)
        assert sm.parse_value(spec, sm.format_value(spec, value)) == value, spec.path


def test_166_a_bad_value_is_refused_naming_the_field():
    model = SettingsModel()
    with pytest.raises(ValueError, match="days_per_turn"):
        model.set_text("days_per_turn", "soon")
    with pytest.raises(ValueError, match="campaign_take_focus"):
        model.set_text("campaign_take_focus", "maybe")
    with pytest.raises(ValueError, match="headless_reader"):
        model.set_text("headless_reader", "psychic")
    assert model.config.days_per_turn == 7


def test_166_an_optional_box_left_empty_is_none():
    model = SettingsModel()
    model.set_text("unit_orders", "true")
    assert model.config.unit_orders is True
    model.set_text("unit_orders", "")
    assert model.config.unit_orders is None
    model.set_text("telemetry_hours", "6")
    assert model.config.telemetry_hours == 6


# --- #166 load, edit and save round-trips every field -------------------------------------------


def test_166_profile_round_trips_every_field(tmp_path):
    for spec in sm.field_specs():
        model = SettingsModel(profile_path=tmp_path / "p.json")
        model.set_value(spec.path, _sample(spec))
        path = model.save()
        loaded = SettingsModel()
        loaded.load(path)
        assert dataclasses.asdict(loaded.config) == dataclasses.asdict(model.config), spec.path
        assert loaded.get_text(spec.path) == model.get_text(spec.path), spec.path


def test_166_a_profile_holds_only_what_differs_from_the_defaults(tmp_path):
    model = SettingsModel()
    assert model.profile() == {}
    model.set_text("turns", "30")
    model.set_text("planner.effort", "low")
    assert model.profile() == {"turns": 30, "planner": {"effort": "low"}}


def test_166_existing_profiles_load_and_save_back_to_the_same_config(tmp_path):
    for src in sorted(Path(__file__).resolve().parent.parent.glob("profiles/*.json")):
        if "ui_scripts" in src.name or "script" in src.name:
            continue
        try:
            want = HarnessConfig.from_file(src)
        except KeyError:
            continue
        model = SettingsModel()
        model.load(src)
        out = model.save(tmp_path / src.name)
        got = HarnessConfig.from_file(out)
        assert dataclasses.asdict(got) == dataclasses.asdict(want), src.name


def test_166_loading_a_profile_with_an_unknown_key_is_refused_and_changes_nothing(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"turns": 5, "no_such_setting": 1}))
    model = SettingsModel()
    with pytest.raises(KeyError):
        model.load(bad)
    assert model.config.turns == 10


# --- #166 the countries table -------------------------------------------------------------------


def _table() -> list[CountryRow]:
    return [
        CountryRow("FRA", "llm", "rival", "anthropic", "claude-sonnet-5-5", key_env="ANTHROPIC_API_KEY",
                   instruction="ally the UK", max_usd=5.0),
        CountryRow("POL", "llm", "rival", "claude-cli", "sonnet", instruction="go communist"),
        CountryRow("GER", "person", "coop", "claude-cli"),
    ]


def test_166_rival_rows_write_a_profile_that_play_and_native_read_back(tmp_path):
    model = SettingsModel(profile_path=tmp_path / "rivals.json")
    model.set_rows(_table())
    path = model.save()
    data = json.loads(path.read_text())
    assert data["rival"] == "FRA,POL"
    assert data["rival_players"]["FRA"]["provider"] == "anthropic"
    assert data["rival_players"]["POL"]["instruction"] == "go communist"
    assert data["mp_human_tags"] == ["GER"]
    assert "present-not-a-real-key" not in path.read_text()      # the variable's name, never its value
    # native reads it back: each country keeps its own model, instruction and cap
    from hoi4_harness.rival.setup import instruction_for, planner_config_for

    args = build_parser().parse_args(model.argv())
    assert args.command == "native"
    args.live, args.transport, args.rival_act_when = True, "keys", "paused"
    config = _config_from_args(args)
    assert (planner_config_for(config, "FRA").provider, planner_config_for(config, "FRA").model) == (
        "anthropic", "claude-sonnet-5-5")
    assert planner_config_for(config, "POL").provider == "claude-cli"
    assert instruction_for(config, "FRA") == "ally the UK"
    assert config.rival_players["FRA"]["max_usd"] == 5.0
    # and the rows come back the same from the saved file
    again = SettingsModel()
    again.load(path)
    assert again.rows == model.rows


def test_166_native_keeps_each_countrys_provider_when_no_flag_names_one(tmp_path):
    """The Start path: native used to force --provider claude-cli over every country's own."""
    model = SettingsModel(profile_path=tmp_path / "r.json")
    model.set_rows([CountryRow("FRA", "llm", "rival", "openai", "gpt-5")])
    model.save()
    args = build_parser().parse_args(model.argv())
    args.live, args.transport, args.rival_act_when = True, "keys", "paused"
    from hoi4_harness.cli import cmd_native  # noqa: F401  (the default is applied inside it)

    config = _config_from_args(args)
    assert config.rival_players["FRA"]["provider"] == "openai"


def test_166_campaign_and_coop_rows_round_trip(tmp_path):
    for mode, controller in (("campaign", "llm"), ("campaign", "game_ai"), ("coop", "llm")):
        model = SettingsModel(profile_path=tmp_path / f"{mode}-{controller}.json")
        model.set_rows([CountryRow("GER", controller, mode, "anthropic", "claude-sonnet-5-5",
                                   key_env="MY_KEY", instruction="stay out of wars",
                                   control="focus=llm,research=script", max_usd=3.0)])
        path = model.save()
        config = HarnessConfig.from_file(path)
        assert config.country == "GER"
        assert config.campaign is (mode == "campaign")
        assert config.handover is (mode == "coop")
        if mode == "campaign":
            assert config.campaign_control == ("llm" if controller == "llm" else "game_ai")
        assert config.planner.provider == "anthropic" and config.planner.model == "claude-sonnet-5-5"
        assert config.planner.api_key_env == "MY_KEY"
        assert config.instruction == "stay out of wars"
        assert config.control == "focus=llm,research=script"
        assert config.budget.max_usd == 3.0
        assert rows_equal(rows_of(path), model.rows)


def rows_of(path):
    return sm.rows_from_config(HarnessConfig.from_file(path))


def rows_equal(a, b):
    return [dataclasses.asdict(r) for r in a] == [dataclasses.asdict(r) for r in b]


def test_166_rows_a_profile_cannot_hold_are_listed_not_silently_lost():
    model = SettingsModel()
    model.set_rows([CountryRow("ITA", "game_ai", "rival"), CountryRow("SWE", "off", "rival"),
                    CountryRow("POL", "llm", "rival", "claude-cli")])
    assert [r.tag for r in model.unstored_rows()] == ["ITA", "SWE"]
    assert model.config.rival == "POL"
    model.set_text("turns", "12")                    # an edit elsewhere keeps the person's rows
    assert [r.tag for r in model.unstored_rows()] == ["ITA", "SWE"]


def test_166_row_problems_say_what_is_wrong():
    rows = [CountryRow("FR", "llm"), CountryRow("POL", "llm", "rival", "nonsense"),
            CountryRow("GER", "person", "campaign"),
            CountryRow("ITA", "llm", "rival", "openai", key_env="NOPE_NOT_SET"),
            CountryRow("SWE", "llm", "rival", control="focus=llm"),
            CountryRow("DEN", "llm", "campaign", control="focus=wizard")]
    said = " ".join(sm.row_problems(rows, environ={}))
    for fragment in ("FR: a country tag", "unknown provider", "a person plays it in co-op",
                     "NOPE_NOT_SET is not set", "steered", "control map", "different run modes"):
        assert fragment in said, fragment


def test_166_key_status_never_shows_a_key():
    row = CountryRow("FRA", "llm", "rival", "anthropic")
    assert sm.key_status(row, {"ANTHROPIC_API_KEY": "sk-secret"}) == "found"
    assert sm.key_status(row, {}) == "missing"
    assert sm.key_status(CountryRow("POL", provider="claude-cli"), {}) == "not needed"


# --- #166 the command line builds the same config -----------------------------------------------


def _parse(model: SettingsModel):
    return _config_from_args(build_parser().parse_args(model.argv()))


def test_166_the_command_line_builds_the_same_config_for_a_plain_run(tmp_path):
    model = SettingsModel(profile_path=tmp_path / "plain.json")
    for path, text in (("turns", "25"), ("days_per_turn", "3"), ("planner.effort", "low"),
                       ("guidance", "minimal"), ("budget.max_usd", "2.5"), ("dry_run", "false"),
                       ("game_dir", str(tmp_path / "game")), ("skills", "a, b"),
                       ("control", "focus=llm,research=script")):
        model.set_text(path, text)
    model.save()
    assert model.command().startswith("hoi4-harness play --profile")
    assert dataclasses.asdict(_parse(model)) == dataclasses.asdict(model.config)


def test_166_the_command_line_builds_the_same_config_for_a_campaign(tmp_path):
    model = SettingsModel(profile_path=tmp_path / "camp.json")
    model.set_rows([CountryRow("GER", "game_ai", "campaign", "claude-cli", "sonnet")])
    model.set_text("campaign_until", "1941-06-22")
    model.save()
    assert dataclasses.asdict(_parse(model)) == dataclasses.asdict(model.effective_config())


def test_166_the_command_line_builds_the_same_config_for_rivals(tmp_path):
    model = SettingsModel(profile_path=tmp_path / "rival.json")
    model.set_rows(_table()[:2])
    model.save()
    assert model.subcommand() == "native" and "--rival FRA,POL" in model.command()
    args = build_parser().parse_args(model.argv())
    args.live, args.transport, args.rival_act_when = True, "keys", "paused"
    got = dataclasses.asdict(_config_from_args(args))
    want = model.effective_config()
    want.dry_run = False                       # native is always live
    want.console_transport, want.rival_act_when = "keys", "paused"
    assert got == dataclasses.asdict(want)


def test_166_the_command_quotes_a_profile_path_with_spaces(tmp_path):
    model = SettingsModel(profile_path=tmp_path / "my profile" / "p.json")
    assert '"' in model.command() or "'" in model.command()
    assert "my profile" in model.command()


# --- #166 the run starts after a preflight ------------------------------------------------------


class _Check:
    def __init__(self, status):
        self.status = status

    def line(self):
        return f"{self.status} x: y"


def test_166_start_runs_the_preflight_first_and_a_failure_starts_nothing(tmp_path):
    model = SettingsModel(profile_path=tmp_path / "p.json", environ={})
    spawned = []
    result, plan = model.start(run_checks=lambda *_a, **_k: [_Check("FAIL")],
                               spawn=lambda *a, **k: spawned.append(a))
    assert not result.ok and plan is None and spawned == []
    assert not (tmp_path / "p.json").exists()


def test_166_start_with_a_passing_preflight_saves_the_profile_and_spawns_the_command(tmp_path):
    model = SettingsModel(profile_path=tmp_path / "p.json", environ={})
    model.set_text("turns", "4")
    seen = {}

    def spawn(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs

    result, plan = model.start(run_checks=lambda *_a, **_k: [_Check("PASS"), _Check("WARN")],
                               spawn=spawn, python="py")
    assert result.ok and plan is not None
    assert seen["argv"] == ["py", "-m", "hoi4_harness.cli", *model.argv(plan.profile_path)]
    assert json.loads(plan.profile_path.read_text()) == {"turns": 4}
    assert plan.log_path.exists()


def test_166_a_countries_problem_blocks_the_start_even_when_the_checks_pass(tmp_path):
    model = SettingsModel(profile_path=tmp_path / "p.json", environ={})
    model.set_rows([CountryRow("FRA", "llm", "rival", "anthropic")])       # key variable not set
    result, plan = model.start(run_checks=lambda *_a, **_k: [_Check("PASS")],
                               spawn=lambda *a, **k: pytest.fail("spawned"))
    assert plan is None and any("ANTHROPIC_API_KEY" in p for p in result.problems)


def test_166_the_real_preflight_runs_without_touching_the_game(tmp_path):
    model = SettingsModel(profile_path=tmp_path / "p.json")
    model.set_text("run_dir", str(tmp_path / "runs"))
    result = model.preflight()
    assert result.checks and all(hasattr(c, "line") for c in result.checks)
    assert all("sk-" not in line and "present-not-a-real-key" not in line for line in result.lines())


# --- #166 the window opens and closes -----------------------------------------------------------


def test_166_the_window_builds_and_is_destroyed(tmp_path):
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    probe.destroy()
    from hoi4_harness.setup_gui import SetupController, SetupWindow
    from hoi4_harness.setup_model import PlayerSetup, Setup

    window = SetupWindow(SetupController(Setup(players=[PlayerSetup("POL")]), tmp_path / "s.json",
                                         countries=[("POL", "Poland")], environ={}),
                         settings=SettingsModel(profile_path=tmp_path / "p.json", environ={}))
    try:
        window.root.update_idletasks()
        window.root.update()
        assert window.root.winfo_width() > 1
    finally:
        window.root.destroy()
