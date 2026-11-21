"""The rows of docs/observation.md the save cannot hold, read from the mod's weekly ``observe`` lines.

Schema v5 additive kinds: ``obsbegin``, ``res``, ``tension``, ``equip``, ``garrison``, ``peace``,
``fac``, ``vol``, ``collab``. A token the game did not expand prints ``[?llmb_x]`` and is missing,
never 0. The fake game prints the same lines.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from hoi4_harness import world as W
from hoi4_harness.adapters.keys import ScanCodeKeys
from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.fakegame import FakeGame, demo_index
from hoi4_harness.observation import mechanics
from hoi4_harness.observation.render import render_full
from hoi4_harness.types import GameState

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"
STAMP = "[10:00:00][1941.08.31.21]"
STATE = f"{STAMP}: LLMB|v5|state|date=x|tag=GER|pp=42|stab=63|ws=18|civ=12|mil=5|doc=4|mp=1\n"


def line(kind, **fields):
    body = "|".join(f"{k}={v}" for k, v in fields.items())
    return f"{STAMP}: LLMB|v5|{kind}|tag=GER|{body}\n"


WEEK = (
    line("obsbegin")
    + line("res", r="steel", prod=60, cons=48, imp=10, exp=0, net=22)
    + line("res", r="oil", prod=5, cons=45, imp=0, exp=0, net=-40)
    + line("res", r="rubber", prod=0, cons=0, imp=0, exp=0, net="[?llmb_rn]")
    + line("tension", threat=43.5)
    + line("equip", a="infantry_equipment", stock=1100, army=8000, need=12200)
    + line("equip", a="support_equipment", stock=0, army=500, need=500)
    + line("equip", a="light_tank_chassis", stock="[?llmb_es]", army=10, need=30)
    + line("garrison", need=12000, foreign=3000, occupied=14)
    + line("peace", **{"in": 1}, score=120, total=400, turn=2)
    + line("fac", score=340, ratio=28, rank=2, manifest=45, power=1200)
    + line("fac", of="ITA", score=120, ratio=10, rank=3)
    + line("vol", of="SPR", **{"in": 10, "out": 0})
    + line("vol", of="ITA", **{"in": 0, "out": 5})
    + line("collab", of="POL", v=35)
    + line("collab", of="NOR", v="[?llmb_col]"))


def read(tmp_path, text):
    path = tmp_path / "game.log"
    path.write_text(text, encoding="utf-8")
    return LogTailAdapter(path, country="GER").read_state()


# --- the reader -----------------------------------------------------------------------------------------


def test_every_line_kind_lands_in_the_game_state(tmp_path):
    seen = read(tmp_path, STATE + WEEK).observed
    assert seen is not None and seen.date == "1941-08-31"
    assert seen.resources["steel"] == {"prod": 60, "cons": 48, "imp": 10, "exp": 0, "net": 22}
    assert seen.tension == pytest.approx(0.435)
    assert seen.equipment["infantry_equipment"] == {"stock": 1100, "army": 8000, "need": 12200}
    assert seen.garrison == {"need": 12000, "foreign": 3000, "occupied": 14}
    assert seen.peace == {"in": True, "score": 120, "total": 400, "turn": 2}
    assert seen.faction == {"score": 340, "ratio": 0.28, "rank": 2, "manifest": 0.45, "power": 1200}
    assert seen.faction_members == {"ITA": {"score": 120, "ratio": 0.10, "rank": 3}}
    assert seen.volunteers == {"SPR": {"in": 10, "out": 0}, "ITA": {"in": 0, "out": 5}}
    assert seen.collaboration == {"POL": 0.35}


def test_a_token_the_game_did_not_expand_is_missing_never_zero(tmp_path):
    seen = read(tmp_path, STATE + WEEK).observed
    # rubber's net did not expand: the line keeps its other four numbers and has no net
    assert "net" not in seen.resources["rubber"] and seen.resources["rubber"]["prod"] == 0
    assert "stock" not in seen.equipment["light_tank_chassis"]
    assert "NOR" not in seen.collaboration                      # its only number did not expand
    unknown = read(tmp_path, STATE + line("tension", threat="[?llmb_threat]")
                   + line("res", r="steel", prod="[?a]", cons="[?b]", imp="[?c]", exp="[?d]", net="[?e]"))
    assert unknown.observed.tension is None and unknown.observed.resources == {}


def test_a_state_with_no_observe_line_has_none(tmp_path):
    assert read(tmp_path, STATE).observed is None


def test_a_new_batch_empties_the_per_country_sets_but_keeps_the_rest(tmp_path):
    again = (line("obsbegin") + line("fac", of="JAP", score=1, ratio=5, rank=4)
             + line("tension", threat=50))
    seen = read(tmp_path, STATE + WEEK + again).observed
    assert set(seen.faction_members) == {"JAP"} and seen.volunteers == {} and seen.collaboration == {}
    assert seen.tension == 0.5
    assert seen.resources["steel"]["net"] == 22                  # a number stays until the next reading


def test_lines_of_another_country_are_left_to_it(tmp_path):
    other = WEEK.replace("tag=GER", "tag=FRA")
    assert read(tmp_path, STATE + other).observed is None


# --- the brief and the tool ---------------------------------------------------------------------------------


def test_the_brief_says_what_is_short_and_where(tmp_path):
    state = read(tmp_path, STATE + WEEK)
    out = mechanics.lines(state)
    assert out[0] == "Resources (net a day, mod): oil -40, steel +22 | SHORT: oil"
    assert out[1] == ("Equipment gap: infantry_equipment short 4,200 (armies hold 8,000 of 12,200, "
                      "stock 1,100); light_tank_chassis short 20 (armies hold 10 of 30)")
    assert out[2] == ("World and war: world tension 44%; garrison need 12,000 manpower in 14 occupied "
                      "states; IN A PEACE CONFERENCE (score 120 of 400); faction influence rank 2 "
                      "(28% of it); collaboration POL 35%; volunteers you send ITA >=5, "
                      "SPR sends you >=10")
    assert "support_equipment" not in out[1]                      # armies hold all they need
    brief = render_full(state)
    assert "Equipment gap:" in brief and "Resources (net a day, mod)" in brief


def test_nothing_read_means_no_lines(tmp_path):
    assert mechanics.lines(GameState()) == []
    state = read(tmp_path, STATE + line("tension", threat="[?llmb_threat]"))
    assert mechanics.lines(state) == []


def test_inspect_world_adds_what_the_mod_read_to_the_matching_views(tmp_path):
    state = read(tmp_path, STATE + WEEK)
    economy = mechanics.view_extras(state, "economy")
    assert economy["resources"]["oil"]["net"] == -40
    assert economy["equipment_vs_need"]["infantry_equipment"]["short"] == 4200
    assert "short" not in economy["equipment_vs_need"]["support_equipment"]
    diplomacy = mechanics.view_extras(state, "diplomacy")
    assert diplomacy["world_tension"] == 0.435 and diplomacy["peace_conference"]["in"] is True
    assert diplomacy["volunteers_lower_bounds"]["SPR"] == {"in": 10, "out": 0}
    assert mechanics.view_extras(state, "occupation")["garrison"]["need"] == 12000
    assert mechanics.view_extras(state, "occupation")["collaboration"] == {"POL": 0.35}
    assert mechanics.view_extras(state, "faction")["your_influence"]["rank"] == 2
    assert mechanics.view_extras(state, "navy") == {} and mechanics.view_extras(GameState(), "economy") == {}


def test_the_observer_attaches_them_to_its_own_views_only(tmp_path):
    from battle_helpers import division, save_text
    from orders_helpers import load_layer, write_grid_game

    grid = load_layer(write_grid_game(tmp_path / "game"))
    saves = tmp_path / "save games"
    saves.mkdir()
    (saves / "autosave.hoi4").write_text(save_text(division(1, 6, 1), division(10, 7, 3)),
                                         encoding="utf-8")
    state = GameState(country="SWE", date="1941-08-31",
                      observed=read(tmp_path, STATE + WEEK).observed)
    observer = W.WorldObserver("SWE", saves, layer=grid)
    own = observer.query(state, "diplomacy", None)
    assert own["world_tension"] == 0.435 and own["view"] == "diplomacy"
    assert "world_tension" not in observer.query(state, "diplomacy", "GER")    # another country's
    assert "resources" in observer.query(state, "economy", "SWE")
    assert "no text save" in W.WorldObserver("SWE", tmp_path / "none").query(state, "economy", None)["error"]


# --- the fake game ------------------------------------------------------------------------------------------


@pytest.fixture()
def game(tmp_path):
    return FakeGame(tmp_path / "user", country="GER", index=demo_index(tmp_path / "playset"), variance=0)


def test_the_fake_game_prints_the_lines_weekly_and_on_demand(game):
    game.run_days(14)
    log = game.log_path.read_text(encoding="utf-8")
    assert log.count("LLMB|v5|obsbegin|") == 2 and "LLMB|v5|tension|" in log
    keys = ScanCodeKeys(send=game.send, sleep=game.sleep)
    (game.user_dir / "llmb_turn.txt").write_text("effect GER llm_bridge_emit_observe\n", encoding="utf-8")
    keys.chord("grave")
    keys.type_text("run llmb_turn.txt")
    keys.chord("enter")
    keys.chord("grave")
    assert game.log_path.read_text(encoding="utf-8").count("LLMB|v5|obsbegin|") == 3
    seen = LogTailAdapter(game.log_path, country="GER").read_state().observed
    assert seen is not None and seen.tension is not None and "infantry_equipment" in seen.equipment
    assert seen.peace == {"in": False, "score": 0, "total": 0, "turn": 0}


# --- the mod -----------------------------------------------------------------------------------------------


def _mod_text() -> str:
    return (MOD / "common" / "scripted_effects" / "llm_bridge_observe.txt").read_text(encoding="utf-8")


def test_the_mod_logs_every_kind_the_reader_knows_and_runs_weekly():
    text = _mod_text()
    for kind in ("obsbegin", "res", "tension", "equip", "garrison", "peace", "fac", "vol", "collab"):
        assert f"LLMB|v5|{kind}|" in text, kind
    hooks = (MOD / "common" / "on_actions" / "llm_bridge_on_actions.txt").read_text(encoding="utf-8")
    assert "llm_bridge_emit_observe = yes" in hooks[hooks.index("on_weekly"):]


def test_every_variable_a_line_prints_is_set_first_and_the_unverified_note_is_there():
    text = _mod_text()
    body = "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())
    printed = set(re.findall(r"\[\?(llmb_\w+)\]", body))
    set_ = set(re.findall(r"(?:set|multiply)_(?:temp_)?variable = \{ (llmb_\w+)", body))
    assert printed and printed <= set_, printed - set_
    assert "UNVERIFIED" in text
    assert "[?" in text and "never as 0" in text


def test_the_tokens_are_the_ones_the_installed_documentation_lists():
    game = Path(os.environ.get("HOI4_GAME_DIR")
                or "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV")
    docs = game / "documentation"
    if not (docs / "dynamic_variables_documentation.md").exists():
        pytest.skip("HOI4 is not installed here")
    known = (docs / "dynamic_variables_documentation.md").read_text(encoding="utf-8") + (
        docs / "triggers_documentation.md").read_text(encoding="utf-8")
    body = "\n".join(ln.split("#", 1)[0] for ln in _mod_text().splitlines())
    tokens = set(re.findall(r"= (?:global\.|ROOT\.)?([a-z_]+)(?:@\w+)? \}", body))
    tokens |= set(re.findall(r"\b(is_in_peace_conference|has_volunteers_amount_from|is_in_faction_with"
                             r"|is_in_faction)\b", body))
    wanted = {t for t in tokens if t not in ("yes", "no", "tag", "exists") and not t.startswith("llmb_")
              and not t.isdigit()}
    assert {"resource_produced", "resource_consumed", "resource_imported", "resource_exported",
            "threat", "num_equipment", "num_equipment_in_armies", "num_target_equipment_in_armies",
            "garrison_manpower_need", "foreign_manpower", "pc_current_score", "pc_total_score",
            "faction_influence_score"} <= wanted
    for token in wanted:
        assert re.search(rf"^#+ {re.escape(token)}$", known, re.M), f"{token} is not in the documentation"
