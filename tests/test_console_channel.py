"""The console channel (#83): effects through `run`, refusals before any input.

Every key goes through a recording fake injected as the driver's ``keys=``, and
``tests/conftest.py`` fails any test that reaches the real sender, so nothing
here can type into the machine it runs on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness import modgen
from hoi4_harness.adapters import build_adapter
from hoi4_harness.adapters.channels import ChannelRouter, resolve_channels
from hoi4_harness.adapters.console import (
    CONSOLE_ACTIONS,
    DEFAULT_CHANNELS,
    ConsoleChannel,
    ConsoleConfig,
)
from hoi4_harness.adapters.input_driver import InputConfig, InputDriverAdapter
from hoi4_harness.adapters.keys import parse_chord, scan_code
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.config import HarnessConfig
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.types import ActionCall, ConstructionItem, GameState, ProductionLine

BRIDGE = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"

CHARACTERS = """characters = {
	SWE_cheap_one = {
		advisor = { slot = political_advisor idea_token = SWE_cheap_tok cost = 25 }
	}
	SWE_two_roles = {
		instance = { advisor = { slot = theorist idea_token = SWE_two_a cost = 100 } }
		instance = { advisor = { slot = high_command idea_token = SWE_two_b cost = 150 } }
	}
	SWE_no_cost = {
		advisor = { slot = political_advisor idea_token = SWE_no_cost }
	}
}
"""
EQUIPMENT = """equipments = {
	infantry_equipment = { is_archetype = yes is_buildable = no }
	infantry_equipment_1 = { archetype = infantry_equipment }
	support_equipment_1 = { archetype = support_equipment }
}
"""


@pytest.fixture()
def index(tmp_path) -> IdentifierIndex:
    game = tmp_path / "game"
    for folder in ("country_tags", "characters", "units/equipment"):
        (game / "common" / folder).mkdir(parents=True)
    (game / "history" / "states").mkdir(parents=True)
    (game / "common" / "country_tags" / "t.txt").write_text(
        'SWE = "countries/Sweden.txt"\nGER = "countries/Germany.txt"\n'
        'POL = "countries/Poland.txt"\nXYZ = "countries/Xyz.txt"\n', encoding="utf-8")
    (game / "common" / "characters" / "c.txt").write_text(CHARACTERS, encoding="utf-8")
    (game / "common" / "units" / "equipment" / "e.txt").write_text(EQUIPMENT, encoding="utf-8")
    (game / "history" / "states" / "1.txt").write_text(
        'state = { id = 1 name = "STATE_1" history = { owner = SWE } }', encoding="utf-8")
    (game / "history" / "states" / "2.txt").write_text(
        'state = { id = 2 name = "STATE_2" history = { owner = GER } }', encoding="utf-8")
    return IdentifierIndex.build(Playset.from_paths(game))


class FakeKeys:
    """What the scan-code layer would be: records chords and typed text."""

    def __init__(self, on_enter=None):
        self.sent: list[str] = []
        self.on_enter = on_enter

    def chord(self, spec):
        self.sent.append(f"press {spec}")
        if spec == "enter" and self.on_enter:
            self.on_enter()

    def type_text(self, text):
        self.sent.append(f"type {text}")


class Game:
    def __init__(self, **fields):
        fields.setdefault("country", "SWE")
        fields.setdefault("political_power", 100.0)
        fields.setdefault("military_factories", 5)
        self.state = GameState(**fields)

    def read(self):
        return self.state


def rig(tmp_path, index, game=None, dry_run=False, focused=True, **console_options):
    game = game or Game()
    keys = FakeKeys()
    driver = InputDriverAdapter(
        InputConfig(), dry_run=dry_run, keys=keys, read_state=game.read, sleep=lambda s: None,
        verify_timeout=1.0, focus_check=lambda title: FocusCheck(focused, "Hearts of Iron IV"),
    )
    options = {"directory": tmp_path / "userdir", "archive_dir": tmp_path / "run"}
    options.update(console_options)
    (tmp_path / "userdir").mkdir(exist_ok=True)
    channel = ConsoleChannel(driver, ConsoleConfig(**options), index=index, read_state=game.read,
                             sleep=lambda s: None, verify_timeout=1.0)
    return game, keys, driver, channel


def run_file(tmp_path) -> list[str]:
    return (tmp_path / "userdir" / "llmb_turn.txt").read_text(encoding="utf-8").splitlines()


def nothing_happened(tmp_path, keys):
    assert keys.sent == []
    assert not (tmp_path / "userdir" / "llmb_turn.txt").exists()


BUILD = ActionCall("queue_construction", {"building": "military_factory", "state": "1", "count": 2})
PRODUCE = ActionCall("set_production", {"equipment": "infantry_equipment_1", "factories": 3})
HIRE = ActionCall("hire_advisor", {"advisor_id": "SWE_cheap_one"})


# --- keys -----------------------------------------------------------------------


def test_the_console_key_is_the_scan_code_left_of_one():
    assert scan_code("grave") == 0x29
    assert parse_chord("0x29") == [0x29] and scan_code("0x35") == 0x35
    with pytest.raises(ValueError):
        scan_code("0x99")
    with pytest.raises(ValueError):
        scan_code("0xzz")


# --- the effect text, one example of each action ---------------------------------


def plan(channel, call, state=None):
    return channel.plan(call, state or channel._read_state()).lines


def test_construction_is_one_guarded_level_per_block_in_the_state(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    (line,) = plan(channel, BUILD)
    block = ("if = { limit = { is_owned_by = SWE free_building_slots = { building = arms_factory "
             "size > 0 } } add_building_construction = { type = arms_factory level = 1 "
             "instant_build = no } }")
    assert line == f"eval_effect SWE = {{ 1 = {{ {block} {block} }} }}"


def test_construction_without_guards_is_the_bare_effect(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index, guards=False)
    call = ActionCall("queue_construction", {"building": "civilian_factory", "state": "STATE_1"})
    (line,) = plan(channel, call)
    assert line == ("eval_effect SWE = { 1 = { add_building_construction = "
                    "{ type = industrial_complex level = 1 instant_build = no } } }")


def test_capital_is_the_capital_scope(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index, guards=False)
    call = ActionCall("queue_construction", {"building": "dockyard", "state": "capital"})
    assert plan(channel, call)[0].startswith("eval_effect SWE = { capital_scope = {")


def test_production_names_the_equipment_and_the_factories(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    assert plan(channel, PRODUCE) == [
        "eval_effect SWE = { add_equipment_production = { equipment = "
        "{ type = infantry_equipment_1 } requested_factories = 3 } }"]


def test_an_advisor_is_activated_and_charged(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    assert plan(channel, HIRE) == [
        "eval_effect SWE = { if = { limit = { has_political_power > 24.99 } "
        "activate_advisor = SWE_cheap_one add_political_power = -25 } }"]


def test_an_advisor_may_be_named_by_idea_token(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index, guards=False)
    call = ActionCall("hire_advisor", {"advisor_id": "SWE_cheap_tok"})
    assert plan(channel, call) == [
        "eval_effect SWE = { activate_advisor = SWE_cheap_one add_political_power = -25 }"]


def test_a_directive_selects_its_target_then_raises_through_the_mod(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    call = ActionCall("set_ai_directive", {"directive": "protect", "target": "POL"})
    assert plan(channel, call) == ["effect SWE llm_bridge_select_POL",
                                   "effect SWE llm_bridge_raise_protect"]


def test_posture_global_theater_and_clear(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    assert plan(channel, ActionCall("set_ai_posture", {"posture": "defensive"})) == [
        "effect SWE llm_bridge_set_posture_defensive"]
    theater = ActionCall("set_ai_posture", {"posture": "offensive", "theater": "GER"})
    assert plan(channel, theater) == ["effect SWE llm_bridge_select_GER",
                                      "effect SWE llm_bridge_raise_theater_offensive"]
    assert plan(channel, ActionCall("clear_ai_directives", {})) == [
        "effect SWE llm_bridge_clear_directives"]


def test_hybrid_actions_can_go_through_the_decisions_instead(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index, hybrid_via="decision")
    call = ActionCall("set_ai_directive", {"directive": "protect", "target": "POL"})
    assert plan(channel, call) == [
        "eval_effect SWE = { activate_decision = llmb_target_POL }",
        "eval_effect SWE = { activate_decision = llmb_directive_protect }"]
    assert plan(channel, ActionCall("set_ai_posture", {"posture": "defensive"})) == [
        "eval_effect SWE = { activate_decision = llmb_posture_defensive }"]


def test_the_effects_name_things_the_mod_defines():
    scripted = "\n".join(p.read_text(encoding="utf-8")
                         for p in (BRIDGE / "common" / "scripted_effects").glob("*.txt"))
    decisions = "\n".join(p.read_text(encoding="utf-8")
                          for p in (BRIDGE / "common" / "decisions").rglob("*.txt"))
    for name in ("llm_bridge_set_posture_defensive", "llm_bridge_set_posture_offensive",
                 "llm_bridge_clear_directives", "llm_bridge_select_POL",
                 "llm_bridge_raise_protect", "llm_bridge_raise_theater_offensive"):
        assert f"{name} = {{" in scripted
    for name in ("llmb_posture_defensive", "llmb_clear_directives", "llmb_target_POL",
                 "llmb_directive_protect", "llmb_theater_offensive"):
        assert f"{name} = {{" in decisions


# --- running: file, keys, verification ----------------------------------------------


def test_a_run_writes_one_file_and_sends_the_same_four_steps(tmp_path, index):
    game, keys, _, channel = rig(tmp_path, index)
    keys.on_enter = lambda: game.state.construction.extend(
        [ConstructionItem("military_factory", "1")] * 2)
    (result,) = channel.execute([BUILD])
    assert keys.sent == ["press grave", "type run llmb_turn.txt", "press enter", "press grave"]
    assert run_file(tmp_path) == channel.last_lines and len(run_file(tmp_path)) == 1
    assert result.ok and result.message == "done and verified"
    assert result.changed["channel"] == "console"
    assert "state ownership" in result.changed["unchecked"]
    # a copy is kept with the run
    assert list((tmp_path / "run" / "console").glob("*-llmb_turn.txt"))


def test_a_batch_shares_one_file_and_one_run(tmp_path, index):
    game, keys, _, channel = rig(tmp_path, index)
    results = channel.execute([PRODUCE, HIRE])
    assert keys.sent.count("press enter") == 1
    assert len(run_file(tmp_path)) == 2
    assert [r.action for r in results] == ["set_production", "hire_advisor"]


def test_a_change_that_did_not_happen_is_not_reported_done(tmp_path, index):
    _, keys, _, channel = rig(tmp_path, index)
    (result,) = channel.execute([PRODUCE])
    assert keys.sent and not result.ok and result.error_kind == "rejected"
    assert "did not change" in result.message


def test_an_unseen_field_is_unverified(tmp_path, index):
    game = Game(unknown_fields=["construction"])
    _, _, _, channel = rig(tmp_path, index, game=game)
    (result,) = channel.execute([BUILD])
    assert result.error_kind == "unverified" and "cannot see construction" in result.message


def test_a_dry_run_writes_to_the_run_dir_and_logs_the_keys_without_sending(tmp_path, index):
    _, keys, driver, channel = rig(tmp_path, index, dry_run=True)
    (result,) = channel.execute([PRODUCE])
    assert result.error_kind == "not_executed" and not result.ok
    assert "dry run: would write 1 line(s)" in result.message
    assert keys.sent == []
    nothing_happened(tmp_path, keys)
    written = list((tmp_path / "run" / "console").glob("*-llmb_turn.txt"))
    assert len(written) == 1 and "add_equipment_production" in written[0].read_text("utf-8")
    assert driver.log == ["press grave", "type run llmb_turn.txt", "press enter", "press grave"]


def test_an_unfocused_game_gets_nothing_written_or_sent(tmp_path, index):
    _, keys, _, channel = rig(tmp_path, index, focused=False)
    (result,) = channel.execute([PRODUCE])
    assert not result.ok and "not focused" in result.message and "nothing was sent" in result.message
    nothing_happened(tmp_path, keys)


def test_a_filename_the_keyboard_cannot_type_is_refused_before_writing(tmp_path, index):
    _, keys, _, channel = rig(tmp_path, index, filename="tûrn.txt")
    (result,) = channel.execute([PRODUCE])
    assert not result.ok and "nothing was sent" in result.message
    nothing_happened(tmp_path, keys)


def test_without_a_folder_for_run_nothing_is_written(tmp_path, index, monkeypatch):
    monkeypatch.setattr("hoi4_harness.paths.find_game_dir", lambda explicit=None: None)
    _, keys, _, channel = rig(tmp_path, index, directory=None)
    (result,) = channel.execute([PRODUCE])
    assert not result.ok and "console_dir" in result.message and keys.sent == []


def test_the_console_key_and_filename_are_configurable(tmp_path, index):
    game, keys, _, channel = rig(tmp_path, index, open_key="0x29", close_key="0x29",
                                 filename="my_turn.txt")
    channel.execute([HIRE])
    assert keys.sent[:2] == ["press 0x29", "type run my_turn.txt"]
    assert (tmp_path / "userdir" / "my_turn.txt").exists()


# --- refusals: the checks the UI would have made, before anything is written -------------


@pytest.mark.parametrize("call, state, why", [
    (ActionCall("queue_construction", {"building": "fortification", "state": "1"}), {},
     "province building"),
    (ActionCall("queue_construction", {"building": "dockyard", "state": "999"}), {},
     "no state '999'"),
    (ActionCall("set_production", {"equipment": "infantry_equipment", "factories": 1}), {},
     "no buildable equipment 'infantry_equipment'"),
    (ActionCall("set_production", {"equipment": "nope_1", "factories": 1}), {},
     "no buildable equipment"),
    (ActionCall("set_production", {"equipment": "support_equipment_1", "factories": 0}), {},
     "removes a line"),
    (ActionCall("set_production", {"equipment": "support_equipment_1", "factories": 4}),
     {"production": [ProductionLine("infantry_equipment_1", 3)]}, "4 factories asked for, 2 free"),
    (PRODUCE, {"production": [ProductionLine("infantry_equipment_1", 1)]}, "already exists"),
    (PRODUCE, {"unknown_fields": ["production"]}, "cannot see production"),
    (PRODUCE, {"unknown_fields": ["military_factories"]}, "cannot see military_factories"),
    (HIRE, {"political_power": 24.0}, "costs 25 political power, 24 held"),
    (HIRE, {"unknown_fields": ["political_power"]}, "cannot see political power"),
    (ActionCall("hire_advisor", {"advisor_id": "SWE_ghost"}), {}, "no advisor 'SWE_ghost'"),
    (ActionCall("hire_advisor", {"advisor_id": "SWE_no_cost"}), {}, "names no cost"),
    (ActionCall("hire_advisor", {"advisor_id": "SWE_two_roles"}), {}, "pass slot"),
    (ActionCall("hire_advisor", {"advisor_id": "SWE_two_roles", "slot": "chief_of_navy"}), {},
     "no chief_of_navy role"),
    (ActionCall("set_ai_directive", {"directive": "invade", "target": "QQQ"}), {},
     "no country 'QQQ'"),
    (ActionCall("set_ai_directive", {"directive": "invade", "target": "XYZ"}), {},
     "no directive plumbing for XYZ"),
    (ActionCall("set_ai_posture", {"posture": "balanced"}), {}, "no effect that sets a global"),
    (ActionCall("set_ai_posture", {"posture": "offensive", "theater": "QQQ"}), {},
     "no country 'QQQ'"),
    (PRODUCE, {"country": "QQQ"}, "no country 'QQQ'"),
    (PRODUCE, {"country": "___"}, "country is not known"),
])
def test_a_failed_precondition_refuses_before_writing_and_sends_no_input(
        tmp_path, index, call, state, why):
    _, keys, _, channel = rig(tmp_path, index, game=Game(**state))
    (result,) = channel.execute([call])
    assert not result.ok and result.error_kind == "rejected"
    assert why in result.message and "nothing was sent" in result.message
    assert result.changed["channel"] == "console"
    nothing_happened(tmp_path, keys)


def test_a_refused_call_does_not_stop_the_others_in_a_batch(tmp_path, index):
    _, keys, _, channel = rig(tmp_path, index)
    bad = ActionCall("hire_advisor", {"advisor_id": "SWE_ghost"})
    refused, ran = channel.execute([bad, PRODUCE])
    assert not refused.ok and "nothing was sent" in refused.message
    assert ran.error_kind == "rejected" and "did not change" in ran.message   # sent, nothing happened
    assert len(run_file(tmp_path)) == 1 and keys.sent.count("press enter") == 1


def test_without_the_game_files_the_checks_that_need_them_refuse(tmp_path):
    game, keys = Game(), FakeKeys()
    driver = InputDriverAdapter(InputConfig(), dry_run=False, keys=keys, read_state=game.read,
                                sleep=lambda s: None,
                                focus_check=lambda t: FocusCheck(True, "Hearts of Iron IV"))
    (tmp_path / "u").mkdir()
    channel = ConsoleChannel(driver, ConsoleConfig(directory=tmp_path / "u",
                                                   archive_dir=tmp_path / "r"),
                             index=None, read_state=game.read, sleep=lambda s: None)
    results = channel.execute([PRODUCE, HIRE, BUILD])
    assert all(not r.ok and "pass --game-dir" in r.message for r in results)
    assert keys.sent == []
    # script with no lookups still works without an index
    assert channel.plan(ActionCall("clear_ai_directives", {}), game.state).lines


def test_a_missing_reader_sends_nothing(tmp_path, index):
    _, keys, _, channel = rig(tmp_path, index)
    channel._read_state = None
    (result,) = channel.execute([PRODUCE])
    assert result.error_kind == "unverified" and keys.sent == []


# --- the channel per action -----------------------------------------------------------


def test_everything_is_on_the_ui_until_the_console_is_verified_live():
    channels = resolve_channels(None)
    assert set(channels.values()) == {"ui"}
    assert CONSOLE_ACTIONS == {
        "queue_construction", "set_production", "hire_advisor",
        "set_ai_directive", "set_ai_posture", "clear_ai_directives"}
    opted_in = resolve_channels({name: "console" for name in CONSOLE_ACTIONS})
    assert {n for n, c in opted_in.items() if c == "console"} == CONSOLE_ACTIONS
    assert DEFAULT_CHANNELS["delegate_army_to_ai"] == "ui"


def test_a_channel_can_be_moved_but_not_to_one_that_does_not_exist_or_cheats():
    assert resolve_channels({"queue_construction": "ui"})["queue_construction"] == "ui"
    with pytest.raises(ValueError, match="known: ui, console"):
        resolve_channels({"queue_construction": "telepathy"})
    with pytest.raises(ValueError, match="no faithful console effect"):
        resolve_channels({"set_national_focus": "console"})


ALL_CONSOLE = {name: "console" for name in CONSOLE_ACTIONS}


def make_router(tmp_path, index, channels=ALL_CONSOLE, game=None):
    game, keys, driver, channel = rig(tmp_path, index, game=game)
    return game, keys, ChannelRouter(driver, channel, channels)


def test_the_router_runs_console_actions_on_the_console_and_records_which(tmp_path, index):
    game, keys, router = make_router(tmp_path, index)
    result = router.apply(HIRE)
    assert result.changed["channel"] == "console" and "press enter" in keys.sent
    assert HIRE.name in router.supported_actions and "note" in router.supported_actions


def test_a_ui_action_goes_to_the_driver_and_is_stamped_ui(tmp_path, index):
    game, keys, router = make_router(tmp_path, index)
    result = router.apply(ActionCall("set_national_focus", {"focus_id": "SWE_x"}))
    # no recorded script in this rig: the driver refuses, and the result says it was the ui
    assert result.error_kind == "unsupported" and result.changed["channel"] == "ui"
    assert keys.sent == []


def test_a_console_action_moved_to_ui_never_touches_the_console(tmp_path, index):
    game, keys, router = make_router(tmp_path, index, {"hire_advisor": "ui"})
    result = router.apply(HIRE)
    assert result.changed["channel"] == "ui" and result.error_kind == "unsupported"
    assert keys.sent == [] and "hire_advisor" not in router.supported_actions


def test_built_in_actions_pass_through_unstamped(tmp_path, index):
    _, _, router = make_router(tmp_path, index)
    result = router.apply(ActionCall("note", {"text": "x"}))
    assert result.ok and "channel" not in result.changed


def test_apply_batch_sends_one_run_for_the_console_actions(tmp_path, index):
    game, keys, router = make_router(tmp_path, index)
    results = router.apply_batch([PRODUCE, ActionCall("note", {"text": "x"}), HIRE])
    assert [r.action for r in results] == ["set_production", "note", "hire_advisor"]
    assert keys.sent.count("press enter") == 1 and len(run_file(tmp_path)) == 2


def test_the_router_exposes_the_drivers_config(tmp_path, index):
    _, _, router = make_router(tmp_path, index)
    assert router.config.window_title == "Hearts of Iron IV"


def test_build_adapter_wraps_the_input_driver_and_reads_the_console_settings(tmp_path):
    log = tmp_path / "game.log"
    log.write_text("", encoding="utf-8")
    adapter = build_adapter(HarnessConfig(
        adapter="logtail+input", run_dir=tmp_path, log_path=log,
        console_dir=tmp_path / "u", console_key="0x29",
        action_channels={**{n: "console" for n in CONSOLE_ACTIONS}, "hire_advisor": "ui"}))
    router = adapter.writer
    assert isinstance(router, ChannelRouter)
    assert router.console.config.directory == tmp_path / "u"
    assert router.console.config.open_key == "0x29"
    assert "queue_construction" in adapter.supported_actions
    assert "hire_advisor" not in adapter.supported_actions


def test_the_console_settings_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HOI4_CONSOLE_DIR", str(tmp_path))
    monkeypatch.setenv("HOI4_CONSOLE_FILE", "x.txt")
    monkeypatch.setenv("HOI4_CONSOLE_KEY", "0x35")
    config = HarnessConfig.from_env()
    assert (config.console_dir, config.console_filename, config.console_key) == (
        tmp_path, "x.txt", "0x35")
    config.merge({"console_dir": "elsewhere", "action_channels": {"set_production": "ui"}})
    assert config.console_dir == Path("elsewhere")


# --- the index lookups behind the checks -------------------------------------------------


def test_the_index_reads_advisor_costs_and_buildable_equipment(index):
    roles = index.advisor_roles("SWE_two_roles")
    assert {(r.slot, r.cost) for r in roles} == {("theorist", 100.0), ("high_command", 150.0)}
    assert index.advisor_roles("SWE_no_cost")[0].cost is None
    assert index.advisor_character("SWE_two_b") == "SWE_two_roles"
    assert index.advisor_roles("nobody") is None
    assert index.equipment_types() == {"infantry_equipment_1", "support_equipment_1"}


def test_generated_targets_match_the_default_tag_list():
    assert ConsoleConfig().target_tags == modgen.DEFAULT_TAGS
