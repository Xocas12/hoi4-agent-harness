# ruff: noqa: E501
"""Issue #161 step 1: contested production under the game's AI.

With ``campaign_contested_production`` on, ``add_production_line`` and ``design_equipment`` (and
``start_research`` where the adapter has it) are offered although the game's AI runs the country;
``add_production_line`` goes through the console with a marker logged inside the guard, and every
line the game confirmed is recorded in the run for ``scripts/contested_report.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from hoi4_harness.actions import catalog
from hoi4_harness.adapters.console import (
    CONSOLE_ACTIONS,
    CONTESTED_LINES_FILE,
    MARKED_ACTIONS,
)
from hoi4_harness.agent.prompts import CONTESTED, system_for
from hoi4_harness.config import HarnessConfig
from hoi4_harness.control import CONTESTED_PRODUCTION, DOMAIN_ACTIONS
from hoi4_harness.types import ActionCall
from test_designers import _rig, variant

ALL = {a.name for a in catalog.ACTIONS}


def _add(channel, **arguments):
    (result,) = channel.execute([ActionCall("add_production_line", arguments)])
    return result


def _recorded(channel):
    path = channel.config.archive_dir / CONTESTED_LINES_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- what is offered ---------------------------------------------------------------------------


def _config(flag: bool) -> HarnessConfig:
    return HarnessConfig(operational_control="game_ai", campaign=True, campaign_control="game_ai",
                         campaign_contested_production=flag)


def test_161_the_flag_offers_exactly_the_contested_actions_under_game_ai():
    off, on = _config(False).allowed_actions(ALL), _config(True).allowed_actions(ALL)
    assert not (off & CONTESTED_PRODUCTION)
    assert on - off == {"add_production_line", "design_equipment", "start_research"}
    # Nothing else of the AI's is handed back: no other production action, no focus, no construction.
    for name in ("set_production", "change_production_equipment", "set_trade", "set_national_focus",
                 "queue_construction", "design_division_template", "deploy_divisions"):
        assert name not in on


def test_161_start_research_is_offered_only_where_the_adapter_has_it():
    supported = ALL - {"start_research"}
    assert "start_research" not in _config(True).allowed_actions(supported)
    assert {"add_production_line", "design_equipment"} <= _config(True).allowed_actions(supported)


def test_161_add_production_line_is_never_offered_without_the_flag():
    for mode in ("llm", "ai", "game_ai"):
        assert "add_production_line" not in HarnessConfig(operational_control=mode).allowed_actions(ALL)


def test_161_the_prompt_warns_that_the_ai_may_undo_the_lines():
    assert CONTESTED not in system_for(_config(False))
    prompt = system_for(_config(True))
    assert CONTESTED in prompt and "may switch your line" in prompt and "delete it" in prompt


def test_161_add_production_line_is_a_marked_console_action_in_the_production_domain():
    assert "add_production_line" in CONSOLE_ACTIONS and "add_production_line" in MARKED_ACTIONS
    assert "add_production_line" in DOMAIN_ACTIONS["production"]
    spec = catalog.get("add_production_line")
    assert spec is not None and spec.parameters["required"] == ["equipment", "factories"]


# --- through the console, on the fake game -----------------------------------------------------


def test_161_a_line_that_ran_is_proven_by_its_marker_and_recorded(tmp_path):
    game, channel, _ = _rig(tmp_path)
    planned = channel.plan(ActionCall("add_production_line",
                                      {"equipment": "infantry_equipment_1", "factories": 3}),
                           channel._read_state())
    (line,) = planned.lines
    assert line.startswith("eval_effect SWE = { if = { limit = { always = yes } add_equipment_production = {")
    assert "requested_factories = 3" in line and line.index("LLMB|v5|click|") > line.index("requested_factories")
    result = _add(channel, equipment="infantry_equipment_1", factories=3)
    assert result.ok, result.message
    assert game.added_lines == [{"type": "infantry_equipment_1", "factories": 3,
                                 "version_name": None, "creator": None}]
    (record,) = _recorded(channel)
    assert record["equipment"] == "infantry_equipment_1" and record["factories"] == 3
    assert record["variant"] is None and record["country"] == "SWE" and record["date"]


def test_161_a_second_line_for_equipment_that_has_one_is_allowed(tmp_path):
    game, channel, _ = _rig(tmp_path)
    assert _add(channel, equipment="infantry_equipment_1", factories=1).ok
    assert _add(channel, equipment="infantry_equipment_1", factories=2).ok
    assert [r["factories"] for r in _recorded(channel)] == [1, 2]
    assert len(game.added_lines) == 2


def test_161_a_line_the_game_declines_is_rejected_and_not_recorded(tmp_path):
    game, channel, _ = _rig(tmp_path)
    game.researched.discard("demo_chassis_a")       # the guard's has_tech fails: no marker
    result = _add(channel, equipment="demo_tank_chassis_1", factories=2)
    assert not result.ok and result.error_kind in ("rejected", "unverified")
    assert game.added_lines == [] and _recorded(channel) == []


def test_161_a_design_is_referenced_by_its_name_and_creator(tmp_path):
    game, channel, _ = _rig(tmp_path)
    channel.designs = SimpleNamespace(variants=lambda state: [
        variant("demo_tank_chassis_1", 1, {}, frame=True),
        variant("demo_tank_chassis_1", 2, {"gun_slot": "gun_small_1"}, name="Tank_A", vid=2)])
    by_name = _add(channel, equipment="demo_tank_chassis_1", factories=2, variant="Tank_A")
    assert by_name.ok, by_name.message
    by_version = _add(channel, equipment="demo_tank_chassis_1", factories=1, variant=2)
    assert by_version.ok, by_version.message
    assert [(a["type"], a["version_name"], a["creator"]) for a in game.added_lines] == [
        ("demo_tank_chassis_1", "Tank_A", "SWE")] * 2
    assert [r["variant"] for r in _recorded(channel)] == ["Tank_A", "Tank_A"]


def test_161_a_design_with_a_space_cannot_be_typed_and_says_why(tmp_path):
    _game, channel, _ = _rig(tmp_path)
    channel.designs = SimpleNamespace(variants=lambda state: [
        variant("demo_tank_chassis_1", 2, {}, name="Test Tank", vid=2)])
    inbox_plan = channel.plan(ActionCall("add_production_line", {
        "equipment": "demo_tank_chassis_1", "factories": 1, "variant": "Test Tank"}),
        channel._read_state())
    assert 'version_name = "Test Tank"' in inbox_plan.lines[0]      # the inbox has no such limit
    channel.inbox = None                                            # the keys transport
    result = _add(channel, equipment="demo_tank_chassis_1", factories=1, variant="Test Tank")
    assert not result.ok and "space" in result.message and "cannot be referenced" in result.message


def test_161_a_default_design_and_a_missing_design_are_refused_with_the_reason(tmp_path):
    _game, channel, _ = _rig(tmp_path)
    channel.designs = SimpleNamespace(variants=lambda state: [
        variant("demo_tank_chassis_1", 1, {}, frame=True)])
    default = _add(channel, equipment="demo_tank_chassis_1", factories=1, variant=1)
    assert not default.ok and "default design" in default.message
    missing = _add(channel, equipment="demo_tank_chassis_1", factories=1, variant="Nope")
    assert not missing.ok and "no demo_tank_chassis_1 design" in missing.message
    channel.designs = None
    unread = _add(channel, equipment="demo_tank_chassis_1", factories=1, variant="Tank_A")
    assert not unread.ok and "newest text save" in unread.message


def test_161_unknown_equipment_and_unknown_unlocks_are_refused(tmp_path):
    _game, channel, index = _rig(tmp_path)
    nothing = _add(channel, equipment="no_such_equipment", factories=1)
    assert not nothing.ok and "no buildable equipment" in nothing.message
    sealed = _add(channel, equipment="sealed_equipment_1", factories=1)
    assert not sealed.ok and "no technology" in sealed.message
    bad = index.check(ActionCall("add_production_line",
                                 {"equipment": "no_such_equipment", "factories": 1}), "SWE")
    assert bad is not None and not bad.ok and "no_such_equipment" in bad.message
    assert index.check(ActionCall("add_production_line", {
        "equipment": "infantry_equipment_1", "factories": 1}), "SWE") is None


def test_161_design_equipment_is_unchanged_under_the_flag(tmp_path):
    from test_designers import ARMS

    _game, channel, _ = _rig(tmp_path)
    (result,) = channel.execute([ActionCall("design_equipment", {**ARMS, "name": "Tank_1"})])
    assert result.ok, result.message
    assert _recorded(channel) == []       # a design is not a production line: nothing recorded


def test_161_the_live_profile_turns_the_flag_on_and_keeps_the_saves_the_measurement_needs():
    from pathlib import Path

    config = HarnessConfig.from_file(Path(__file__).parent.parent / "profiles" / "contested-ger.json")
    assert config.campaign_contested_production and config.operational_control == "game_ai"
    assert config.campaign_save_months == 1 and config.campaign_keep_saves >= 24
    assert {"add_production_line", "design_equipment"} <= config.allowed_actions(ALL)
    assert CONTESTED in system_for(config)
