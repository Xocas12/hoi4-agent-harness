"""Direct control of the economy, at a human's level (#137, the economy part).

``control = "economy"`` gives the model research, production and construction by hand, with the
game's AI owning nothing. Everything runs against the fake game and its research and production
screens: no real input, no model call. The army half of #137 is another change, and none of this is
verified in the real game (docs/modular-control.md, "Direct economy").
"""

from __future__ import annotations

import pytest

from campaign_helpers import say
from hoi4_harness import economy
from hoi4_harness.adapters import production_ui as pu
from hoi4_harness.agent.prompts import system_for
from hoi4_harness.control import ControlError, resolve
from hoi4_harness.types import ProductionLine
from test_independent_hands import Recorder, make_world, play, results

ROWS = {1: (1500, 300), 2: (1500, 360)}
PLUS, MINUS, REMOVE, CHANGE = (1700, 200), (1740, 200), (1780, 200), (1500, 200)
PICKER = {"artillery_equipment_1": (1000, 500), "infantry_equipment_1": (1000, 540)}
PRODUCTION_POINTS = {
    **{pu.row_point(n): p for n, p in ROWS.items()},
    pu.PLUS: PLUS, pu.MINUS: MINUS, pu.REMOVE: REMOVE, pu.CHANGE: CHANGE,
    **{pu.pick_point(e): p for e, p in PICKER.items()},
}


def economy_world(tmp_path, *, points=True, control="economy"):
    """The economy map on the fake game: SWE, research screen, one infantry line of 3 factories."""
    world = make_world(tmp_path, army=False, army_points=False, control=control,
                       points=PRODUCTION_POINTS if points else None,
                       headless_reader="log+save")
    fake = world.fake
    fake.world.state.production = [ProductionLine("infantry_equipment_1", 3, 0.5, 4.0)]
    fake.world.state.military_factories = 6
    for state in fake.states.values():
        state.slots, state.used = 3, 0            # the grid map's states have no slots of their own
    fake.production_ui = {"rows": ROWS, "plus": PLUS, "minus": MINUS, "remove": REMOVE,
                          "change": CHANGE, "picker": PICKER}
    fake.run_days(1)
    return world


# --- the map -----------------------------------------------------------------------------------


def test_137_economy_is_a_map_that_runs_the_desk_by_hand_and_hands_nothing_to_the_ai():
    cmap = resolve("economy")
    assert cmap.alias == "economy" and cmap.explicit
    assert {cmap.controller(d) for d in ("research", "production", "construction")} == {"llm"}
    assert {cmap.controller(d) for d in ("army", "navy", "air")} == {"off"}
    assert not cmap.human_ai and not cmap.ai_owns_something and cmap.steer == "off"
    assert not any(c == "game" for c in cmap.domains.values())


def test_137_the_model_gets_the_economy_tools_and_no_army_or_steering_tool():
    cmap = resolve("economy")
    every = {"start_research", "set_production", "change_production_equipment",
             "queue_construction", "design_equipment", "move_division", "set_army_order",
             "set_ai_directive", "delegate_army_to_ai", "note"}
    got = cmap.actions_for_model(set(every))
    assert {"start_research", "set_production", "change_production_equipment",
            "queue_construction", "design_equipment", "note"} <= got
    assert not got & {"move_division", "set_army_order", "set_ai_directive",
                      "delegate_army_to_ai"}


def test_137_the_alias_takes_overrides_in_either_order_and_as_a_dict():
    assert resolve("economy,army=script").controller("army") == "script"
    assert resolve("army=script,economy").controller("army") == "script"
    assert resolve({"base": "economy", "diplomacy": "off"}).controller("diplomacy") == "off"
    assert resolve({"base": "economy"}).alias == "economy"
    with pytest.raises(ControlError, match="unknown control base"):
        resolve({"base": "ledger"})


def test_137_economy_cannot_be_mixed_with_the_games_ai_owning_a_domain_the_model_runs():
    with pytest.raises(ControlError, match="contested"):
        resolve("economy,focus=game")


def test_137_a_plain_map_is_not_an_economy_map():
    assert resolve("default=llm").alias is None and not economy.is_economy(resolve("default=llm"))
    assert economy.lines(resolve("default=llm"), {"start_research"}) == []
    assert economy.describe(resolve("default=llm"), {"start_research"}) == ""


# --- how each part reaches the game ------------------------------------------------------------


def test_137_channels_say_mouse_where_the_mouse_does_it_and_missing_where_nothing_does():
    cmap = resolve("economy")
    offered = {"start_research", "set_production", "queue_construction", "design_equipment"}
    hands = {"production": {"edit": True, "equipment": False}}
    by = {c.action: c for c in economy.channels(cmap, offered, hands)}
    assert by["start_research"].route == "mouse"
    assert by["set_production"].route == "mouse"
    assert by["queue_construction"].route == "console" and by["design_equipment"].route == "console"
    assert by["change_production_equipment"].route == "missing"
    no_points = {c.action: c for c in economy.channels(cmap, offered, {})}
    assert no_points["set_production"].route == "console"
    assert "cannot be edited" in no_points["set_production"].note
    out = economy.lines(cmap, {"queue_construction"}, {})
    assert any("economy WARNING: research" in line for line in out)


def test_137_the_prompt_does_not_promise_what_the_run_cannot_do():
    cmap = resolve("economy")
    live = economy.describe(cmap, {"start_research", "set_production", "queue_construction",
                                   "change_production_equipment"},
                            {"production": {"edit": True, "equipment": True}})
    assert "clicking the tech tree" in live and "edits the line that already makes" in live
    assert "change_production_equipment switches" in live and "queue_construction" in live
    bare = economy.describe(cmap, {"set_production", "queue_construction"}, {})
    assert "no way to pick a technology" in bare and "can only open a line" in bare


# --- on the fake game ----------------------------------------------------------------------------


def test_137_a_run_does_research_production_and_construction_by_hand(tmp_path):
    world = economy_world(tmp_path)
    script = [say("desk work",
                  ("start_research", {"technology": "infantry_at"}),
                  ("set_production", {"equipment": "infantry_equipment_1", "factories": 5}),
                  ("queue_construction", {"building": "civilian_factory", "state": "10"}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    client = Recorder(script)
    play(world, client)
    got = results(tmp_path)
    assert not got["start_research"]["is_error"], got["start_research"]
    assert "infantry_at" in {s.technology for s in world.fake.world.state.research}
    assert not got["set_production"]["is_error"], got["set_production"]
    assert [(x.equipment, x.factories) for x in world.fake.world.state.production] == [
        ("infantry_equipment_1", 5)]                       # the SAME line, edited, no second
    assert not got["queue_construction"]["is_error"], got["queue_construction"]
    assert client.offered.isdisjoint({"move_division", "attack_province", "set_ai_directive",
                                      "delegate_army_to_ai", "set_army_order"})
    assert {"start_research", "set_production", "queue_construction"} <= client.offered


def test_137_the_games_ai_is_never_handed_the_country(tmp_path):
    world = economy_world(tmp_path)
    play(world, Recorder([say("wait", ("advance_time", {"days": 30, "reason": "x"}))]))
    assert "human_ai is not sent" in world.log
    assert not world.config.campaign_human_ai
    assert world.fake.world.state.ai_controlled is not True
    assert not any("human_ai" in line for run in world.fake.runs for line in run)


def test_137_the_start_log_says_how_each_domain_is_reached(tmp_path):
    world = economy_world(tmp_path)
    play(world, Recorder([say("wait", ("advance_time", {"days": 30, "reason": "x"}))]))
    log = world.log
    assert "economy mode:" in log
    assert "economy research: start_research (mouse)" in log
    assert "set_production (mouse)" in log and "queue_construction (console)" in log


def test_137_without_the_production_points_an_existing_line_is_refused_and_the_model_told(tmp_path):
    world = economy_world(tmp_path, points=False)
    script = [say("more", ("set_production", {"equipment": "infantry_equipment_1", "factories": 5}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    play(world, Recorder(script))
    result = results(tmp_path)["set_production"]
    assert result["is_error"] and "nothing was sent" in result["content"]
    assert [(x.equipment, x.factories) for x in world.fake.world.state.production] == [
        ("infantry_equipment_1", 3)]
    assert "set_production (console)" in world.log
    assert "can only open a line" in system_for(world.config)


def test_137_the_prompt_the_run_builds_describes_the_economy(tmp_path):
    world = economy_world(tmp_path)
    play(world, Recorder([say("wait", ("advance_time", {"days": 30, "reason": "x"}))]))
    text = system_for(world.config)
    assert "You run the economy by hand" in text
    assert "edits the line that already makes" in text
    assert "clicking the tech tree" in text


def test_137_economy_with_army_script_still_offers_no_army_tool_to_the_model(tmp_path):
    cmap = resolve("economy,army=script")
    assert cmap.script_domains() == ["army"]
    assert not cmap.actions_for_model({"move_division", "set_army_order", "start_research"}) & {
        "move_division", "set_army_order"}


def test_137_a_config_takes_economy_as_its_control():
    from hoi4_harness.config import HarnessConfig

    config = HarnessConfig(control="economy")
    assert config.control_map().alias == "economy"
