"""Front orders: the LLM steers a stretch of front by state, through the inbox; the game's AI
carries it out (front_control / front_unit_request keyed on state flags)."""

from __future__ import annotations

from pathlib import Path

from hoi4_harness import front_orders, modgen
from hoi4_harness.adapters.console import ConsoleChannel
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.control import STEER_ACTIONS
from hoi4_harness.fakegame import FakeGame, demo_index
from hoi4_harness.types import ActionCall, GameState
from hoi4_harness.world import WorldObserver

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"


def test_the_mod_ships_one_block_per_order_keyed_on_a_state_flag():
    text = (MOD / front_orders.FILE).read_text(encoding="utf-8")
    assert modgen.drift(MOD) == []
    for order in ("push", "attack", "probe", "hold"):
        flag = f"llmb_front_{order}"
        assert f"type = front_control state_trigger = {{ has_state_flag = {flag} }}" in text
        assert f"type = front_unit_request state_trigger = {{ has_state_flag = {flag} }}" in text
    assert "execution_type = rush execute_order = yes" in text
    assert "execute_order = no manual_attack = no" in text           # hold stops the attack
    # push aims the AI's concentrated offensives at the flagged state
    assert ("type = force_concentration_target_weight state_trigger = { has_state_flag = "
            "llmb_front_push } value = 300") in text


def test_reserve_blocks_are_generated_per_state_and_committed_for_vanilla():
    text = front_orders.reserves(["12", "3"])
    assert text.index("llmb_reserve_3 =") < text.index("llmb_reserve_12 =")
    assert "enable = { llm_bridge_is_harness = yes 12 = { has_state_flag = llmb_front_reserve } }" in text
    assert "type = put_unit_buffers ratio = 0.1 order_id = 12 states = { 12 }" in text
    committed = (MOD / front_orders.RESERVES_FILE).read_text(encoding="utf-8")
    assert committed.count("type = put_unit_buffers") > 900          # every vanilla state
    assert front_orders.effect(7, "reserve").endswith("set_state_flag = llmb_front_reserve }")
    assert text.count("{") == text.count("}")


def test_an_order_leaves_one_flag_on_the_state_and_clear_leaves_none():
    assert front_orders.effect(42, "push").endswith("set_state_flag = llmb_front_push }")
    clear = front_orders.effect(42, "clear")
    assert "set_state_flag" not in clear and clear.count("clr_state_flag") == 5


def test_front_orders_are_steering_so_a_game_ai_country_can_give_them():
    assert "set_front_order" in STEER_ACTIONS


class _Driver:
    dry_run = False

    class config:
        settle_seconds = 0.0

    def _sleep(self, seconds):
        pass


def _channel(tmp_path):
    index = demo_index(tmp_path / "playset")
    game = FakeGame(tmp_path / "user", index=index, variance=0)
    game.paused = False
    reader = LogTailAdapter(game.log_path, country=game.country)
    game.emit_state()
    channel = ConsoleChannel(_Driver(), index=index, read_state=reader.read_state,
                             sleep=game.sleep, verify_timeout=1.0)
    channel.config.archive_dir = tmp_path / "run"
    from hoi4_harness.front_orders import Standing
    channel.front_standing = Standing(tmp_path / "run")
    channel.inbox = game.inbox()
    channel.config.inbox_ack_seconds = 30.0
    return game, channel, index


def _order(channel, state, order):
    (result,) = channel.execute([ActionCall("set_front_order", {"state": state, "order": order})])
    return result


def test_on_the_fake_game_an_order_lands_through_the_inbox_and_stands(tmp_path):
    game, channel, index = _channel(tmp_path)
    result = _order(channel, "Norrland", "push")
    assert result.ok, result.message
    assert "front order push set on state 11" in result.message
    assert game.state_flags["11"] == {"llmb_front_push"}
    assert game.keylog == []
    # a new order replaces it; the brief lists what stands
    assert _order(channel, "11", "hold").ok
    assert game.state_flags["11"] == {"llmb_front_hold"}
    observer = WorldObserver("SWE", None, index=index, run_dir=tmp_path / "run")
    assert observer.brief_lines(GameState(country="SWE")) == ["  Front orders standing: hold Norrland"]
    assert _order(channel, "11", "clear").ok
    assert game.state_flags["11"] == set()
    assert observer.brief_lines(GameState(country="SWE")) == []


def test_an_unknown_state_or_order_sends_nothing(tmp_path):
    _game, channel, _index = _channel(tmp_path)
    assert "no state" in _order(channel, "Atlantis", "push").message
    assert "not a front order" in _order(channel, "11", "blitz").message
    assert channel.inbox.pending == []
