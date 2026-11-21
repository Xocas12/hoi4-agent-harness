"""Region orders: the navy and air force steered by strategic region, through the game's own AI."""

from __future__ import annotations

from pathlib import Path

from hoi4_harness import front_orders
from hoi4_harness import regions as R
from hoi4_harness.adapters.console import ConsoleChannel
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.control import STEER_ACTIONS
from hoi4_harness.fakegame import FakeGame, demo_index
from hoi4_harness.identifiers import Playset
from hoi4_harness.types import ActionCall

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"


def _game(root: Path) -> Path:
    (root / "map" / "strategicregions").mkdir(parents=True)
    (root / "common").mkdir()
    (root / "map" / "definition.csv").write_text(
        "0;0;0;0;land;false;unknown;0\n1;1;1;1;sea;true;ocean;0\n2;2;2;2;sea;true;ocean;0\n"
        "3;3;3;3;land;true;plains;1\n4;4;4;4;land;false;hills;1\n", encoding="utf-8")
    (root / "map" / "strategicregions" / "7-North Water.txt").write_text(
        'strategic_region={\n\tid=7\n\tname="STRATEGICREGION_7"\n'
        '\tprovinces={\n\t\t1 2 3 #the coast\n\t}\n}\n',
        encoding="utf-8")
    (root / "map" / "strategicregions" / "8-Inland.txt").write_text(
        'strategic_region={\n\tid=8\n\tname="STRATEGICREGION_8"\n\tprovinces={\n\t\t4\n\t}\n}\n',
        encoding="utf-8")
    return root


def test_regions_are_read_and_a_mostly_sea_region_is_a_sea_region(tmp_path):
    regions = R.read_regions(Playset.from_paths(_game(tmp_path / "g")))
    assert regions[7].sea and not regions[8].sea and regions[7].provinces == [1, 2, 3]


def test_naval_orders_are_generated_for_sea_regions_only_and_air_for_all(tmp_path):
    text = R.strategies(R.read_regions(Playset.from_paths(_game(tmp_path / "g"))))
    assert "ai_strategy = { type = naval_convoy_raid_region id = 7 value = 300 }" in text
    assert "llmb_region_raid_convoys_8" not in text
    assert "ai_strategy = { type = strategic_air_importance id = 8 value = 40000 }" in text
    committed = (MOD / R.FILE).read_text(encoding="utf-8")
    assert "llmb_region_raid_convoys_16 = {" in committed          # the North Sea, vanilla
    assert committed.count("naval_dominance") > 60


def test_an_order_leaves_one_flag_on_the_country():
    assert R.effect(16, "raid_convoys").endswith("set_country_flag = llmb_region_raid_convoys_16")
    assert "set_country_flag" not in R.effect(16, "clear")
    assert "set_region_order" in STEER_ACTIONS


def test_invade_aims_the_invasion_planner_at_a_state():
    text = (MOD / front_orders.FILE).read_text(encoding="utf-8")
    assert "ordertype = invasion execution_type = rush execute_order = yes" in text
    assert "type = invasion_unit_request state_trigger = { has_state_flag = llmb_front_invade }" in text


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
    channel._regions = R.read_regions(Playset.from_paths(_game(tmp_path / "g")))
    R.name_regions(channel._regions, lambda keys: {"STRATEGICREGION_7": "North Water"})
    channel.front_standing = front_orders.Standing(tmp_path / "run")
    channel.inbox = game.inbox()
    channel.config.inbox_ack_seconds = 30.0
    return game, channel


def _order(channel, region, order):
    (result,) = channel.execute([ActionCall("set_region_order", {"region": region, "order": order})])
    return result


def test_on_the_fake_game_a_region_order_lands_and_stands(tmp_path):
    game, channel = _channel(tmp_path)
    result = _order(channel, "North Water", "raid_convoys")
    assert result.ok, result.message
    assert "llmb_region_raid_convoys_7" in game.flags and game.keylog == []
    assert channel.front_standing.orders == {"region:7": "raid_convoys"}
    assert "raid_convoys in region 7" in channel.front_standing.line()
    assert _order(channel, "7", "clear").ok
    assert "llmb_region_raid_convoys_7" not in game.flags


def test_a_naval_order_on_a_land_region_is_refused(tmp_path):
    _game_, channel = _channel(tmp_path)
    result = _order(channel, "8", "dominate")
    assert not result.ok and "land region" in result.message
    assert _order(channel, "8", "air_superiority").ok
