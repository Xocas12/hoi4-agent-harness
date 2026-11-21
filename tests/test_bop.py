"""Balance of power (#135): the bar is read from common/bop, logged by the probe, and in the brief.

The fixture bar (tests/fixtures/modctx/game/common/bop/SWE.txt) is invented. The vanilla test
reads the installed game's Swedish bar when HOI4 is installed and skips otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hoi4_harness import modgen
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.fakegame import FakeGame, demo_index
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.types import GameState, PowerBalance

FIX = Path(__file__).parent / "fixtures" / "modctx" / "game"
BAR = "swe_assembly_balance"


@pytest.fixture(scope="module")
def index():
    return IdentifierIndex.build(Playset.from_paths(FIX))


def stamp(d):
    return f"[11:04:29][1936.01.{d:02d}.21]: "


def state_line(d, tag="SWE"):
    return (f"{stamp(d)}LLMB|v5|state|date= 21:00, {d} January, 1936|tag={tag}|pp=1|stab=80|ws=0"
            f"|civ=9|mil=3|doc=3|mp=5\n")


def bop_line(d, value, weekly="1.500", tag="SWE", ident=BAR):
    return f"{stamp(d)}LLMB|v5|bop|tag={tag}|id={ident}|value={value}|weekly={weekly}\n"


def read(tmp_path, *lines):
    path = tmp_path / "game.log"
    path.write_text("".join(lines), encoding="utf-8")
    return LogTailAdapter(path).read_state()


# --- the bar from the files ------------------------------------------------------------------


def test_a_bar_parses_with_its_sides_ranges_modifiers_and_category(index):
    bar = index.mod_context().balances()[BAR]
    assert (bar.left_side, bar.right_side, bar.decision_category) == (
        "swe_bop_left", "swe_bop_right", "swe_cat")
    assert bar.initial == pytest.approx(0.05)
    assert [(r.id, r.side) for r in bar.ranges] == [
        ("swe_left_far", "swe_bop_left"), ("swe_left_lean", "swe_bop_left"), ("swe_centre", None),
        ("swe_right_lean", "swe_bop_right"), ("swe_right_far", "swe_bop_right")]
    lean = bar.range_at(0.3)
    assert lean.id == "swe_right_lean" and lean.modifiers["production_speed_arms_factory_factor"] == "0.05"
    # a shared edge belongs to the inner band, as on screen
    assert bar.range_at(0.1).id == "swe_centre" and bar.range_at(-0.1).id == "swe_centre"
    below, above = bar.next_ranges(0.3)
    assert below.id == "swe_centre" and above.id == "swe_right_far" and above.has_effects


def test_the_vanilla_swedish_bar_parses_with_all_its_ranges():
    game = Path(os.environ.get("HOI4_GAME_DIR")
                or "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV")
    if not (game / "common" / "bop" / "SWE.txt").exists():
        pytest.skip("HOI4 is not installed here")
    bars = IdentifierIndex.build(Playset.from_paths(game)).mod_context().balances()
    bar = bars["SWE_riksdag_political_balance"]
    assert bar.decision_category == "SWE_riksdag_balance_of_power_category"
    assert len(bar.ranges) == 7
    assert bar.range_at(0.0).id == "SWE_centre_range"
    assert bar.range_at(0.8).id == "SWE_national_socialist_range"


# --- the probe and the log ---------------------------------------------------------------------


def test_the_probe_logs_an_active_bar_and_the_committed_one_covers_vanilla(index):
    text = modgen.generate_probe(index).text
    assert f"has_power_balance = {{ id = {BAR} }}" in text
    assert "set_variable = { llmb_bop = power_balance_value }" in text
    assert f'LLMB|v5|bop|tag=[THIS.GetTag]|id={BAR}|value=[?llmb_bop]|weekly=[?llmb_bop_w]"' in text
    assert text.count("{") == text.count("}")
    committed = (Path(__file__).resolve().parents[1] / "mod" / "llm_bridge" / modgen.PROBE_PATH
                 ).read_text(encoding="utf-8")
    assert "has_power_balance = { id = SWE_riksdag_political_balance }" in committed


def test_the_reader_turns_a_bop_line_into_state_and_drops_a_bar_that_stops(tmp_path):
    s = read(tmp_path, state_line(2), bop_line(2, "-35.000", "-2.000"))
    assert s.power_balances == [PowerBalance(BAR, -0.35, -0.02)]
    # a whole day without the line: the bar is gone (the country lost it)
    s = read(tmp_path, state_line(2), bop_line(2, "-35.000"), state_line(3), state_line(4))
    assert s.power_balances == []
    # an unexpanded token is missing, never zero
    s = read(tmp_path, state_line(2), bop_line(2, "[?llmb_bop]"))
    assert s.power_balances == []


# --- the brief ------------------------------------------------------------------------------------


def test_the_brief_says_where_the_bar_stands_what_it_does_and_what_moves_it(index):
    state = GameState(country="SWE", national_focus="x",
                      power_balances=[PowerBalance(BAR, 0.3, 0.02)])
    (line,) = index.brief_lines(state)
    assert "The Assembly" in line and "+0.30 toward Hawks" in line and "+0.02/week" in line
    assert "band Hawkish Lean [0.1..0.5]: production_speed_arms_factory_factor +0.05" in line
    assert "Hawk Majority at >= 0.5 (runs effects)" in line
    assert "Balanced Assembly at <= 0.1" in line
    assert "list_decisions category=swe_cat" in line
    assert index.brief_lines(GameState(country="SWE", national_focus="x")) == []


def test_describe_action_explains_a_bar(index):
    out = index.mod_context().describe(BAR, "SWE")
    assert out["type"] == "balance_of_power" and out["decision_category"] == "swe_cat"
    assert {r["id"] for r in out["ranges"]} >= {"swe_centre", "swe_right_far"}
    assert "take_decision" in out["headless"]


# --- the fake game: a decision moves the bar, and the brief shows it -----------------------------


def test_on_the_fake_game_an_effect_moves_the_bar_and_the_reader_follows(tmp_path):
    game = FakeGame(tmp_path / "user", index=demo_index(tmp_path / "playset"), variance=0)
    game.world.state.power_balances = [PowerBalance(BAR, 0.05, 0.0)]
    game.emit_state()
    reader = LogTailAdapter(game.log_path, country=game.country)
    assert reader.read_state().power_balances[0].value == pytest.approx(0.05)
    move = f"add_power_balance_value = {{ id = {BAR} value = 0.3 }}"
    game._console(f"eval_effect {game.country} = {{ {move} }}")
    game.run_days(2)
    assert reader.read_state().power_balances[0].value == pytest.approx(0.35)
