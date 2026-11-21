"""Taking a decision from the console: guarded by its own conditions, or not at all.

The line each decision plans to, and the refusals that send no input. The
fixture is tests/fixtures/modctx (invented ids). Every key goes through the
recording fake from test_console_channel; nothing reaches the machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness.actions import catalog, registry
from hoi4_harness.adapters.console import CONSOLE_ACTIONS, DEFAULT_CHANNELS, Refused
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.types import ActionCall
from test_console_channel import Game, nothing_happened, rig, run_file

FIX = Path(__file__).parent / "fixtures" / "modctx"


@pytest.fixture(scope="module")
def index():
    return IdentifierIndex.build(Playset.from_paths(FIX / "game"))


def take(decision, **args):
    return ActionCall("take_decision", {"decision_id": decision, **args})


def lines(channel, call, state=None):
    return channel.plan(call, state or channel._read_state()).lines


def test_a_decision_is_taken_under_its_own_conditions_and_cost(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    assert lines(channel, take("swe_cheap")) == [
        "eval_effect SWE = { if = { limit = { tag = SWE has_country_flag = swe_open has_war = no "
        "OR = { is_in_faction = no num_of_factories > 5 } has_political_power > 24.99 "
        "NOT = { has_decision = swe_cheap } } activate_decision = swe_cheap "
        "add_political_power = -25 } }"]


def test_a_free_decision_charges_nothing(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    (line,) = lines(channel, take("swe_once"))
    assert "add_political_power" not in line and "has_political_power" not in line
    assert "has_government = democratic" in line


def test_a_targeted_decision_checks_the_target_in_the_targets_scope(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    (line,) = lines(channel, take("swe_aid", target="FIN"))
    assert line == (
        "eval_effect SWE = { if = { limit = { has_war = no has_political_power > 9.99 } "
        "FIN = { if = { limit = { is_ally_with = ROOT } ROOT = { activate_targeted_decision = "
        "{ target = FIN decision = swe_aid } add_political_power = -10 } } } } }")


@pytest.mark.parametrize("call, why", [
    (take("swe_mission"), "a mission"),
    (take("swe_spacer"), "always = no"),
    (take("swe_no_effect"), "no effect block"),
    (take("swe_var_cost"), "swe_cost_var"),
    (take("swe_quoted"), "splits quoted text"),
    (take("swe_aid"), "name a target"),
    (take("swe_aid", target="GER"), "not one of its targets"),
    (take("swe_from_reader", target="FIN"), "FROM/PREV"),
    (take("swe_dynamic"), "computed by the game"),
    (take("ger_thing"), "not a decision SWE can have"),
    (take("swe_missing"), "no decision"),
])
def test_what_cannot_be_guarded_is_refused_before_anything_is_sent(tmp_path, index, call, why):
    _, keys, _, channel = rig(tmp_path, index)
    with pytest.raises(Refused, match=why):
        channel.plan(call, channel._read_state())
    (result,) = channel.execute([call])
    assert not result.ok and result.error_kind == "rejected" and why in result.message
    nothing_happened(tmp_path, keys)


def test_it_refuses_a_cost_the_player_cannot_pay_and_an_unseen_balance(tmp_path, index):
    game = Game(political_power=20.0)
    _, keys, _, channel = rig(tmp_path, index, game=game)
    (result,) = channel.execute([take("swe_cheap")])
    assert not result.ok and "costs 25 political power, 20 held" in result.message
    nothing_happened(tmp_path, keys)
    game.state.unknown_fields = ["political_power"]
    (result,) = channel.execute([take("swe_cheap")])
    assert "cannot see political power" in result.message


def test_a_fire_only_once_decision_is_not_taken_twice_in_a_run(tmp_path, index):
    game = Game()
    _, _, _, channel = rig(tmp_path, index, game=game)
    first, = channel.execute([take("swe_once")])
    assert first.changed["lines"] and "fire_only_once" in " ".join(first.changed["unchecked"])
    second, = channel.execute([take("swe_once")])
    assert not second.ok and "already took it" in second.message


def test_a_dry_run_does_not_use_up_a_fire_only_once_decision(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index, dry_run=True)
    channel.execute([take("swe_once")])
    assert channel.taken_once == set()


def test_a_take_is_verified_only_by_political_power(tmp_path, index):
    game = Game(political_power=100.0)
    _, keys, _, channel = rig(tmp_path, index, game=game)
    original = channel._keys_sequence

    def spend():
        original()
        game.state.political_power = 75.0           # the effect charged 25
    channel._keys_sequence = spend
    (result,) = channel.execute([take("swe_cheap")])
    assert result.ok and "fell from 100 to 75" in result.message
    assert run_file(tmp_path)[0].startswith("eval_effect SWE = { if = { limit = { tag = SWE")


def test_without_a_trace_in_the_telemetry_the_result_is_unverified_not_done(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    (free,) = channel.execute([take("swe_once")])
    assert not free.ok and free.error_kind == "unverified" and "not in the telemetry" in free.message
    (paid,) = channel.execute([take("swe_cheap")])            # power never fell
    assert paid.error_kind == "unverified" and "did not fall" in paid.message


def test_take_decision_is_a_console_action_and_the_queries_are_not_actions():
    assert "take_decision" in CONSOLE_ACTIONS and DEFAULT_CHANNELS["take_decision"] == "console"
    assert catalog.QUERY_ACTIONS == {"list_decisions", "describe_action", "list_gui_buttons"}
    assert catalog.get("enact_decision") is None
    spec = {t.name: t for t in registry.tool_specs()}
    assert "decision_id" in spec["take_decision"].parameters["required"]


def test_the_index_rejects_an_invented_decision_with_the_nearest_real_ones(index):
    result = index.check(take("swe_cheep"), "SWE")
    assert result is not None and result.error_kind == "invalid_args"
    assert "no decision 'swe_cheep'" in result.message and "swe_cheap" in result.message
    other = index.check(take("ger_thing"), "SWE")
    assert "not one SWE can have" in other.message
    assert index.check(take("swe_cheap"), "SWE") is None
