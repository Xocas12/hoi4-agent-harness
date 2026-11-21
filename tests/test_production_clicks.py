"""Production by click (#112): edit the line that exists, never duplicate it.

Everything runs against the fake game: its production screen (`FakeGame.production_ui`) takes the
clicks, its keyboard takes the scan codes, and the points are the test's own numbers. Nothing here
sends real input to this machine or calls a model. The click path is NOT verified in the real game
(docs/live-session.md section 14b).
"""

from __future__ import annotations

import pytest

from campaign_helpers import build_env, campaign_config, say
from hoi4_harness.adapters import production_ui as pu
from hoi4_harness.adapters.headless import excluded_actions
from hoi4_harness.calibration import Calibration, save_calibration
from hoi4_harness.control import DOMAIN_ACTIONS, resolve
from hoi4_harness.types import ActionCall, GameState, ProductionLine

SCREEN = (1920, 1080)
ROWS = {1: (1500, 300), 2: (1500, 360), 3: (1500, 420)}
PLUS, MINUS, REMOVE, CHANGE = (1700, 200), (1740, 200), (1780, 200), (1500, 200)
PICKER = {"artillery_equipment_1": (1000, 500), "infantry_equipment_1": (1000, 540)}
ALL_POINTS = {
    **{pu.row_point(n): p for n, p in ROWS.items()},
    pu.PLUS: PLUS, pu.MINUS: MINUS, pu.REMOVE: REMOVE, pu.CHANGE: CHANGE,
    **{pu.pick_point(e): p for e, p in PICKER.items()},
}
EDIT_ONLY = {k: v for k, v in ALL_POINTS.items()
             if k in (pu.row_point(1), pu.row_point(2), pu.PLUS, pu.MINUS)}


def make(tmp_path, points=ALL_POINTS, lines=(("infantry_equipment_1", 3),), factories=5):
    """A campaign adapter on the fake game: log+save reader, the mouse, the points recorded."""
    config = campaign_config(tmp_path, country="SWE", campaign_control="llm",
                             headless_reader="log+save")
    if points:
        save_calibration(Calibration(SCREEN, dict(points)), tmp_path / "run" / "calibration.json")
    env = build_env(config)
    fake = env.adapter.fake_game
    fake.world.state.production = [ProductionLine(e, n, 0.5, 4.0) for e, n in lines]
    fake.world.state.military_factories = factories
    fake.production_ui = {"rows": ROWS, "plus": PLUS, "minus": MINUS, "remove": REMOVE,
                          "change": CHANGE, "picker": PICKER}
    fake.run_days(1)
    return env, fake


def do(env, name, **arguments):
    return env.adapter.apply(ActionCall(name, arguments))


def held(fake):
    return [(x.equipment, x.factories) for x in fake.world.state.production]


def add_effects(fake):
    return [run for run in fake.runs if any("add_equipment_production" in x for x in run)]


# --- (a) the count of an existing line -----------------------------------------------------------


def test_more_factories_on_an_existing_line_edits_it_and_makes_no_second(tmp_path):
    env, fake = make(tmp_path)
    result = do(env, "set_production", equipment="infantry_equipment_1", factories=5)
    assert result.ok, result.message
    assert "done and verified" in result.message and result.changed["channel"] == "mouse"
    assert held(fake) == [("infantry_equipment_1", 5)]
    assert not add_effects(fake)                       # the console effect never ran
    assert fake.ui_log[:1] == ["production: row 1 selected"]
    assert fake.ui_log.count("production: infantry_equipment_1 + -> 4") == 1


def test_fewer_factories_clicks_minus(tmp_path):
    env, fake = make(tmp_path)
    result = do(env, "set_production", equipment="infantry_equipment_1", factories=1)
    assert result.ok, result.message
    assert held(fake) == [("infantry_equipment_1", 1)]
    assert not add_effects(fake)


def test_the_row_clicked_is_the_lines_own_row(tmp_path):
    env, fake = make(tmp_path, lines=(("infantry_equipment_1", 2), ("artillery_equipment_1", 1)))
    result = do(env, "set_production", equipment="artillery_equipment_1", factories=3)
    assert result.ok, result.message
    assert held(fake) == [("infantry_equipment_1", 2), ("artillery_equipment_1", 3)]
    assert fake.ui_log[0] == "production: row 2 selected"


def test_the_production_screen_is_opened_by_research_then_production_keys(tmp_path):
    env, fake = make(tmp_path)
    fake.panel = "production"             # already open: the production key alone would close it
    assert do(env, "set_production", equipment="infantry_equipment_1", factories=4).ok
    # the clicks only land on the production screen, so it was open (not toggled shut) for them
    assert fake.ui_log[0] == "production: row 1 selected" and fake.panel == "production"
    assert held(fake) == [("infantry_equipment_1", 4)]


def test_more_than_the_free_factories_is_refused_before_any_input(tmp_path):
    env, fake = make(tmp_path, factories=4)             # 3 on the line, 1 free
    result = do(env, "set_production", equipment="infantry_equipment_1", factories=9)
    assert not result.ok and result.error_kind == "rejected"
    assert "6 more factories asked for, 1 free" in result.message and "nothing was sent" in result.message
    assert fake.ui_log == [] and held(fake) == [("infantry_equipment_1", 3)]


def test_the_same_number_is_a_refusal_not_a_click(tmp_path):
    env, fake = make(tmp_path)
    result = do(env, "set_production", equipment="infantry_equipment_1", factories=3)
    assert not result.ok and "already has 3" in result.message and fake.ui_log == []


# --- (d) remove --------------------------------------------------------------------------------


def test_zero_removes_the_line_through_its_remove_button(tmp_path):
    env, fake = make(tmp_path)
    result = do(env, "set_production", equipment="infantry_equipment_1", factories=0)
    assert result.ok, result.message
    assert held(fake) == []
    assert "production: infantry_equipment_1 line removed" in fake.ui_log


def test_removing_a_line_that_does_not_exist_is_refused(tmp_path):
    env, fake = make(tmp_path)
    result = do(env, "set_production", equipment="artillery_equipment_1", factories=0)
    assert not result.ok and "no artillery_equipment_1 line to remove" in result.message
    assert fake.ui_log == []


# --- (c) a genuinely new line ------------------------------------------------------------------


def test_a_line_is_opened_only_when_none_exists_and_through_the_console(tmp_path):
    env, fake = make(tmp_path)
    result = do(env, "set_production", equipment="artillery_equipment_1", factories=2)
    assert result.ok, result.message
    assert held(fake) == [("infantry_equipment_1", 3), ("artillery_equipment_1", 2)]
    assert len(add_effects(fake)) == 1 and fake.ui_log == []     # no click: nothing to edit
    # and now that it exists, the same call edits it instead of adding a third
    result = do(env, "set_production", equipment="artillery_equipment_1", factories=1)
    assert result.ok, result.message
    assert held(fake)[1] == ("artillery_equipment_1", 1) and len(add_effects(fake)) == 1


# --- (b) the equipment of an existing line -----------------------------------------------------


def test_changing_the_equipment_keeps_the_factories_and_duplicates_nothing(tmp_path):
    env, fake = make(tmp_path)
    result = do(env, "change_production_equipment", from_equipment="infantry_equipment_1",
                to_equipment="artillery_equipment_1")
    assert result.ok, result.message
    assert held(fake) == [("artillery_equipment_1", 3)]
    assert not add_effects(fake)
    assert "production: infantry_equipment_1 -> artillery_equipment_1" in fake.ui_log


def test_changing_to_equipment_that_has_a_line_is_refused_as_a_duplicate(tmp_path):
    env, fake = make(tmp_path, lines=(("infantry_equipment_1", 2), ("artillery_equipment_1", 1)))
    result = do(env, "change_production_equipment", from_equipment="infantry_equipment_1",
                to_equipment="artillery_equipment_1")
    assert not result.ok and "second one" in result.message and "set_production" in result.message
    assert fake.ui_log == [] and len(held(fake)) == 2


def test_changing_a_line_that_does_not_exist_is_refused(tmp_path):
    env, fake = make(tmp_path)
    result = do(env, "change_production_equipment", from_equipment="artillery_equipment_1",
                to_equipment="infantry_equipment_1")
    assert not result.ok and "no artillery_equipment_1 line to change" in result.message


def test_equipment_not_in_the_playset_is_refused(tmp_path):
    env, fake = make(tmp_path)
    result = do(env, "change_production_equipment", from_equipment="infantry_equipment_1",
                to_equipment="nope_9")
    assert not result.ok and "no buildable equipment 'nope_9'" in result.message
    assert fake.ui_log == []


# --- a click that lands on nothing is reported, not papered over --------------------------------


def test_a_miss_is_reported_as_rejected_not_done(tmp_path):
    env, fake = make(tmp_path)
    fake.production_ui["plus"] = (10, 10)               # the recorded point is wrong
    result = do(env, "set_production", equipment="infantry_equipment_1", factories=4)
    assert not result.ok and result.error_kind == "rejected"
    assert "did not change" in result.message and held(fake) == [("infantry_equipment_1", 3)]


# --- calibration: refuse cleanly, naming the missing points ------------------------------------


def test_an_edit_with_no_recorded_points_is_refused_naming_them_and_sends_nothing(tmp_path):
    env, fake = make(tmp_path, points={})
    fake.keylog.clear()
    result = do(env, "set_production", equipment="infantry_equipment_1", factories=5)
    assert not result.ok and result.error_kind == "rejected"
    for name in ("production.line.1", "production.factories.plus"):
        assert name in result.message
    assert "hoi4-harness calibrate production.line.1 production.factories.plus" in result.message
    assert fake.ui_log == [] and "y" not in fake.keylog
    assert held(fake) == [("infantry_equipment_1", 3)]


def test_only_the_points_the_move_needs_are_named(tmp_path):
    env, fake = make(tmp_path, points=EDIT_ONLY)
    down = do(env, "set_production", equipment="infantry_equipment_1", factories=2)
    assert down.ok, down.message                        # plus and the row; remove not needed
    gone = do(env, "set_production", equipment="infantry_equipment_1", factories=0)
    assert not gone.ok and "production.line.remove" in gone.message
    assert "production.factories" not in gone.message and held(fake) == [("infantry_equipment_1", 2)]


def test_a_row_past_the_recorded_ones_is_named(tmp_path):
    env, fake = make(tmp_path, points=EDIT_ONLY,
                     lines=(("infantry_equipment_1", 1), ("support_equipment_1", 1),
                            ("artillery_equipment_1", 1)))
    result = do(env, "set_production", equipment="artillery_equipment_1", factories=2)
    assert not result.ok and "production.line.3" in result.message and fake.ui_log == []


def test_the_equipment_change_is_not_offered_without_its_points_and_the_log_says_which(tmp_path):
    env, fake = make(tmp_path, points=EDIT_ONLY)
    offered = env.adapter.supported_actions
    assert "set_production" in offered and "change_production_equipment" not in offered
    log = env.adapter.hands.lines(offered)
    assert any("change_production_equipment not offered: record production.equipment.<equipment>"
               in line for line in log)
    assert any(line.startswith("production by mouse live: set_production edits") for line in log)


def test_with_nothing_recorded_set_production_still_opens_a_new_line_and_the_log_says_so(tmp_path):
    env, fake = make(tmp_path, points={})
    log = env.adapter.hands.lines(env.adapter.supported_actions)
    assert any("can only OPEN a line" in line and "production.line.1" in line for line in log)
    result = do(env, "set_production", equipment="artillery_equipment_1", factories=1)
    assert result.ok and len(add_effects(fake)) == 1


def test_a_reader_that_cannot_see_production_gets_no_production_click_path(tmp_path):
    config = campaign_config(tmp_path, country="SWE", campaign_control="llm",
                             headless_reader="logtail")
    save_calibration(Calibration(SCREEN, dict(ALL_POINTS)), tmp_path / "run" / "calibration.json")
    env = build_env(config)
    assert not env.adapter.supported_actions & set(pu.PRODUCTION_ACTIONS)
    assert set(pu.PRODUCTION_ACTIONS) <= excluded_actions("logtail", mouse=True)
    assert "not offered" in " ".join(env.adapter.hands.lines(env.adapter.supported_actions))


def test_a_dry_run_sends_nothing_and_says_what_it_would_click(tmp_path):
    env, fake = make(tmp_path)
    driver = env.adapter.writer.router.driver
    assert env.adapter.reader.refresh() is None        # a save the dry run can plan from
    driver.dry_run = True
    fake.keylog.clear()
    result = do(env, "set_production", equipment="infantry_equipment_1", factories=4)
    assert result.error_kind == "not_executed" and "click production.factories.plus" in result.message
    assert fake.ui_log == [] and fake.keylog == []


# --- the unit under a mocked driver: no input path at all ---------------------------------------


class StubDriver:
    """Stands where the input driver does: records, and can never send anything."""

    def __init__(self, state, coordinates):
        self.sent, self.dry_run, self._state = [], False, state
        self.config = type("C", (), {"coordinates": coordinates})()
        self._read_state = lambda: self._state
        self._sleep = lambda seconds: None

    def press(self, key):
        self.sent.append(("press", key))

    def click(self, target):
        self.sent.append(("click", target))


def stub_state(lines):
    return GameState(country="SWE", date="1936-01-02", production=lines, military_factories=6)


def test_the_executor_under_a_mock_driver_sends_exactly_the_planned_gestures():
    state = stub_state([ProductionLine("infantry_equipment_1", 3)])
    driver = StubDriver(state, dict(ALL_POINTS))
    executor = pu.ProductionExecutor(driver, console=None)
    result = executor(ActionCall("set_production", {"equipment": "infantry_equipment_1",
                                                    "factories": 5}))
    assert driver.sent == [("press", "research"), ("press", "production"),
                           ("click", "production.line.1"),
                           ("click", "production.factories.plus"),
                           ("click", "production.factories.plus")]
    assert result.error_kind == "rejected"             # the stub game did not change: reported


def test_the_executor_refuses_before_any_gesture_under_a_mock_driver():
    state = stub_state([ProductionLine("infantry_equipment_1", 3)])
    driver = StubDriver(state, {})
    executor = pu.ProductionExecutor(driver, console=None)
    result = executor(ActionCall("set_production", {"equipment": "infantry_equipment_1",
                                                    "factories": 5}))
    assert driver.sent == [] and "production.line.1" in result.message


def test_an_unseen_production_panel_is_refused_not_guessed():
    state = GameState(country="SWE", date="1936-01-02", unknown_fields=["production"])
    driver = StubDriver(state, dict(ALL_POINTS))
    result = pu.ProductionExecutor(driver, console=None)(
        ActionCall("set_production", {"equipment": "infantry_equipment_1", "factories": 2}))
    assert driver.sent == [] and "cannot see production" in result.message


# --- the control map ---------------------------------------------------------------------------


def test_the_control_map_gives_both_actions_to_a_production_llm_domain():
    assert {"set_production", "change_production_equipment"} <= DOMAIN_ACTIONS["production"]
    allowed = {"set_production", "change_production_equipment", "note"}
    cmap = resolve("default=llm")
    assert allowed <= cmap.actions_for_model(set(allowed))
    scripted = resolve("production=script,default=llm")
    assert scripted.actions_for_model(set(allowed)) == {"note"}
    off = resolve("production=off,default=llm")
    assert off.actions_for_model(set(allowed)) == {"note"}


def test_an_independent_campaign_offers_the_model_both_production_tools(tmp_path):
    env, fake = make(tmp_path)
    allowed = env.config.allowed_actions(set(env.adapter.supported_actions))
    assert {"set_production", "change_production_equipment"} <= allowed
    assert env.config.control_map().controller("production") == "llm"


def test_the_game_ai_campaign_never_gets_them(tmp_path):
    env = build_env(campaign_config(tmp_path))
    assert not env.adapter.supported_actions & set(pu.PRODUCTION_ACTIONS) - {"set_production"}
    assert not env.config.allowed_actions(set(env.adapter.supported_actions)) & set(
        pu.PRODUCTION_ACTIONS)


# --- what the model is told --------------------------------------------------------------------


def test_the_catalog_says_edit_not_add_and_the_prompt_follows_the_run():
    from hoi4_harness.actions.catalog import ACTIONS
    from hoi4_harness.agent.prompts import CAMPAIGN_LLM, campaign_llm

    by_name = {a.name: a for a in ACTIONS}
    text = by_name["set_production"].description
    assert "edited in place" in text and "never makes a second line" in text
    assert "NOT duplicated" in by_name["change_production_equipment"].description
    on = campaign_llm({"offered": [], "production": {"edit": True, "equipment": True}})
    assert "set_production EDITS the line" in on and "change_production_equipment" in on
    edit_only = campaign_llm({"offered": [], "production": {"edit": True, "equipment": False}})
    assert "change_production_equipment" not in edit_only
    assert campaign_llm({"offered": [], "production": {}}) == CAMPAIGN_LLM
    off = campaign_llm({"offered": ["start_research"], "production": {}})
    assert "changing a running production line" in off


def test_the_prompt_the_run_builds_names_production_when_it_is_live(tmp_path):
    env, fake = make(tmp_path)
    assert env.config.campaign_hands["production"] == {"edit": True, "equipment": True}
    env2, _ = make(tmp_path / "no", points={})
    assert env2.config.campaign_hands["production"] == {"edit": False, "equipment": False}


def test_the_scripted_model_can_drive_a_whole_wake_through_it(tmp_path):
    from campaign_helpers import LazyClient
    from hoi4_harness.campaign.runner import run_campaign

    env, fake = make(tmp_path)
    env.config.campaign_until = "1936-03-01"
    script = [say("tune production",
                  ("set_production", {"equipment": "infantry_equipment_1", "factories": 5}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    run_campaign(env.config, planner=LazyClient(script=script), env=env)
    assert held(fake) == [("infantry_equipment_1", 5)] and not add_effects(fake)


@pytest.mark.parametrize("name", ["set_production", "change_production_equipment"])
def test_both_are_catalog_actions(name):
    from hoi4_harness.actions.catalog import ACTIONS

    assert name in {a.name for a in ACTIONS}


# --- the 1.20 layout, seen live 2026-11-21: -, + and remove on every line ------------------------

ROW_BUTTONS = {n: {"minus": (x - 90, y), "plus": (x - 40, y), "remove": (x, y)}
               for n, (x, y) in {1: (630, 480), 2: (630, 620), 3: (630, 760)}.items()}
PER_ROW = {
    **{pu.row_point(n): p for n, p in ROWS.items()},
    **{pu.row_button(n, b): pt for n, buttons in ROW_BUTTONS.items() for b, pt in buttons.items()},
    **{pu.pick_point(e): p for e, p in PICKER.items()},
}
TWO_LINES = (("infantry_equipment_1", 3), ("support_equipment_1", 1))


def make_120(tmp_path, points=PER_ROW, lines=TWO_LINES, factories=8):
    env, fake = make(tmp_path, points=points, lines=lines, factories=factories)
    fake.production_ui = {"rows": ROWS, "row_buttons": ROW_BUTTONS, "row_opens_picker": True,
                          "picker": PICKER}
    return env, fake


def test_a_rows_own_plus_edits_that_row_not_row_1(tmp_path):
    env, fake = make_120(tmp_path)
    result = do(env, "set_production", equipment="support_equipment_1", factories=3)
    assert result.ok, result.message
    assert held(fake) == [("infantry_equipment_1", 3), ("support_equipment_1", 3)]
    assert not any("selected" in line or "list opened" in line for line in fake.ui_log)
    assert not add_effects(fake)


def test_a_rows_own_minus_and_remove(tmp_path):
    env, fake = make_120(tmp_path)
    assert do(env, "set_production", equipment="infantry_equipment_1", factories=1).ok
    assert held(fake)[0] == ("infantry_equipment_1", 1)
    assert do(env, "set_production", equipment="support_equipment_1", factories=0).ok
    assert held(fake) == [("infantry_equipment_1", 1)]


def test_without_shared_buttons_the_row_buttons_are_enough_to_offer_editing(tmp_path):
    assert pu.missing_for_edit(PER_ROW) == []
    assert pu.recorded_summary(PER_ROW)["row_buttons"] == [1, 2, 3]


def test_a_row_with_no_own_button_and_no_shared_one_is_refused_by_name(tmp_path):
    points = {k: v for k, v in PER_ROW.items() if not k.startswith("production.line.2.")}
    env, fake = make_120(tmp_path, points=points)
    result = do(env, "set_production", equipment="support_equipment_1", factories=2)
    assert not result.ok and "production.factories.plus" in result.message
    assert fake.ui_log == []


def test_equipment_change_clicks_the_row_then_the_pick_when_no_change_button_is_recorded(tmp_path):
    env, fake = make_120(tmp_path)
    result = do(env, "change_production_equipment", from_equipment="support_equipment_1",
                to_equipment="artillery_equipment_1")
    assert result.ok, result.message
    assert held(fake) == [("infantry_equipment_1", 3), ("artillery_equipment_1", 1)]
    assert "production: the equipment list opened" in fake.ui_log
