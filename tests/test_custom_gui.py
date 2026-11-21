"""Custom windows (#136): scripted GUI buttons read from the files and pressed through the inbox.

The fixture windows (tests/fixtures/modctx/game/common/scripted_guis/swe.txt) are invented. The
fake game runs the click as the real one would run the inbox: the guard decides, and the marker
line is logged only when it held.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness.actions.catalog import QUERY_ACTIONS
from hoi4_harness.adapters.console import ConsoleChannel
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.control import ALWAYS, DOMAIN_ACTIONS
from hoi4_harness.fakegame import FakeGame, demo_index
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.modcontext.gui import _split_click
from hoi4_harness.types import ActionCall

FIX = Path(__file__).parent / "fixtures" / "modctx" / "game"
WINDOW = "SWE_reform_window"


@pytest.fixture(scope="module")
def index():
    return IdentifierIndex.build(Playset.from_paths(FIX))


@pytest.fixture(scope="module")
def ctx(index):
    return index.mod_context()


# --- reading the windows ---------------------------------------------------------------------------


def test_click_names_split_into_element_and_modifier_keys():
    assert _split_click("swe_fund_click") == ("swe_fund", "")
    assert _split_click("swe_fund_alt_right_click") == ("swe_fund", "alt_right")
    assert _split_click("swe_fund_visible") is None


def test_windows_parse_with_buttons_triggers_lists_and_ai_weights(ctx):
    window = ctx.guis()[WINDOW]
    assert window.context == "player_context" and window.list_names == {"v", "i"}
    fund = window.buttons["swe_fund_click"]
    assert fund.element == "swe_fund" and fund.combo == ""
    assert fund.enabled == [("has_political_power", ">", "30")]
    assert fund.ai_will_do == [("base", "=", "5")]
    assert window.buttons["swe_fund_right_click"].combo == "right"
    assert ctx.scripted_effects()["swe_reach_everyone"]


def test_a_country_sees_its_own_windows_only(ctx):
    assert {g.id for g in ctx.guis_for("SWE")} == {
        "SWE_reform_opener", "SWE_reform_window", "SWE_state_window"}
    assert ctx.guis_for("GER") == []


def test_what_cannot_be_pressed_faithfully_is_refused_with_the_reason(ctx):
    window = ctx.guis()[WINDOW]

    def why(button, window=window):
        return ctx.click_refusal(window, window.buttons[button], "SWE")

    assert why("swe_fund_click") == ""
    assert "set_global_flag" in why("swe_decree_click")
    # reaching other countries through a scripted effect it calls counts too
    assert "every_country" in why("swe_helper_click")
    assert "list row" in why("swe_row_click")
    # PREV inside a scope change is the country again; at the top it is the window's
    assert why("swe_states_click") == ""
    assert "PREV outside a scope change" in why("swe_prev_click")
    state = ctx.guis()["SWE_state_window"]
    assert "selected_state_context" in why("swe_state_click", state)
    # the global one is pressable when the run allows it
    decree = window.buttons["swe_decree_click"]
    assert ctx.click_refusal(window, decree, "SWE", allow_global=True) == ""


def test_the_listing_labels_buttons_weighs_them_and_says_how_to_open_the_window(ctx):
    data = ctx.list_gui_buttons("SWE")
    windows = {w["window"]: w for w in data["list"]}
    reform = windows[WINDOW]
    fund = next(b for b in reform["buttons"] if b["button"] == "swe_fund_click")
    assert fund["label"] == "Fund the Reform" and fund["kind"] == "action"
    assert fund["enabled_when"] == "has_political_power > 30"
    assert fund["game_ai_weight"] == "base 5"
    decree = next(b for b in reform["buttons"] if b["button"] == "swe_decree_click")
    assert decree["label"] == "Decree for All" and "set_global_flag" in decree["cannot_press"]
    assert reform["open_when"] == "has_country_flag swe_reform_open"
    assert reform["opened_by"] == ["SWE_reform_opener/swe_open_click"]
    # the opener only switches a window: left out unless that window is asked for
    assert "SWE_reform_opener" not in windows
    opener = ctx.list_gui_buttons("SWE", gui="SWE_reform_opener")["list"][0]["buttons"][0]
    assert opener["kind"] == "window"


def test_the_click_line_is_guarded_by_the_window_and_the_button_and_logs_a_marker(ctx):
    plan = ctx.plan_click(WINDOW, "swe_fund_click", "SWE", console=False)
    assert plan.executable
    assert plan.line.startswith("eval_effect SWE = { if = { limit = { has_country_flag = "
                                "swe_reform_open has_political_power > 30 }")
    assert "add_political_power = -25 set_country_flag = swe_funded" in plan.line
    assert f'log = "LLMB|v5|click|{plan.nonce}"' in plan.line
    assert ctx.plan_click(WINDOW, "swe_fund", "SWE", console=False).executable   # _click optional
    assert not ctx.plan_click(WINDOW, "swe_decree_click", "SWE").executable


def test_an_unknown_window_or_button_is_rejected_with_the_nearest_ids(ctx):
    bad = ctx.check_click(ActionCall("click_gui_button", {"gui": WINDOW, "button": "swe_fnd_click"}),
                          "SWE")
    assert bad is not None and "swe_fund_click" in bad.message
    assert ctx.check_click(ActionCall("click_gui_button", {"gui": WINDOW, "button": "swe_fund"}),
                           "SWE") is None
    assert ctx.check_click(ActionCall("click_gui_button", {"gui": WINDOW, "button": "swe_fund"}),
                           "GER") is not None


def test_describe_action_shows_a_window_and_one_buttons_script(ctx):
    out = ctx.describe(f"{WINDOW}/swe_fund_click", "SWE")
    assert out["type"] == "custom_window"
    assert [b["button"] for b in out["list"][0]["buttons"]] == ["swe_fund_click"]
    assert "add_political_power" in out["click_effect"]


def test_the_tools_are_registered_where_the_rival_can_use_them():
    assert "click_gui_button" in DOMAIN_ACTIONS["politics"]
    assert "list_gui_buttons" in ALWAYS and "list_gui_buttons" in QUERY_ACTIONS


# --- pressing on the fake game, through the inbox ---------------------------------------------------


class _Driver:
    dry_run = False

    class config:
        settle_seconds = 0.0

    def _sleep(self, seconds):
        pass


def _channel(tmp_path, index):
    game = FakeGame(tmp_path / "user", index=demo_index(tmp_path / "playset"), variance=0)
    game.paused = False
    game.world.state.political_power = 100
    reader = LogTailAdapter(game.log_path, country=game.country)
    game.emit_state()
    channel = ConsoleChannel(_Driver(), index=index, read_state=reader.read_state,
                             sleep=game.sleep, verify_timeout=1.0)
    channel.inbox = game.inbox()
    channel.config.inbox_ack_seconds = 30.0
    return game, channel


def _click(channel, button):
    (result,) = channel.execute([ActionCall("click_gui_button", {"gui": WINDOW, "button": button})])
    return result


def test_on_the_fake_game_a_click_lands_through_the_inbox_and_is_confirmed(tmp_path, index):
    game, channel = _channel(tmp_path, index)
    game.flags.add("swe_reform_open")
    result = _click(channel, "swe_fund_click")
    assert result.ok, result.message
    assert "pressed" in result.message
    assert "swe_funded" in game.flags
    assert game.world.state.political_power < 80          # 100 - 25, plus a few days of income
    assert game.keylog == []


def test_a_click_whose_conditions_fail_runs_nothing_and_says_so(tmp_path, index):
    game, channel = _channel(tmp_path, index)
    # the window is not open: its visible trigger fails, so the guard stops the click
    result = _click(channel, "swe_fund_click")
    assert not result.ok and result.error_kind == "rejected"
    assert "did not hold" in result.message
    assert "swe_funded" not in game.flags
    assert game.world.state.political_power >= 100        # nothing spent


def test_a_refused_button_sends_nothing(tmp_path, index):
    game, channel = _channel(tmp_path, index)
    result = _click(channel, "swe_decree_click")
    assert not result.ok and "nothing was sent" in result.message
    assert channel.inbox.pending == []
