# ruff: noqa: F811, F401
"""#7: a directive, posture or steer that names a country acts on exactly that tag.

No fallback to the previous target, no silent default, and the result says which tag was acted
on. Covered across the env (every channel passes through it), the console planner, the ui
verifiers, the mock, and the blocks the mod generator writes.
"""

from __future__ import annotations

import re

import pytest

from hoi4_harness import modgen
from hoi4_harness.adapters.console import Refused
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.adapters.ui_scripts import VERIFIERS
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall, DivisionGroup, Front, GameState, ScenarioStart, War
from test_console_channel import Game, index, plan, rig

DIRECTIVE = "set_ai_directive"


def _env() -> HOI4Env:
    adapter = MockAdapter(
        country="GER", start="1939-09-01",
        start_state=ScenarioStart(
            wars=[War(against="POL")],
            fronts=[Front(name="east", enemy="POL", divisions_enemy=10)],
            divisions=[DivisionGroup(template="Infantry", count=4, location="east")]),
    )
    env = HOI4Env(adapter, HarnessConfig(operational_control="ai", dry_run=False,
                                         require_confirmation=False))
    env.reset()
    return env


# --- env: every channel goes through here -------------------------------------------------------


def test_a_directive_without_a_target_is_refused_not_defaulted():
    env = _env()
    for args in ({"directive": "protect"}, {"directive": "protect", "target": ""},
                 {"directive": "protect", "target": "  "}, {"directive": "protect", "target": None},
                 {"directive": "protect", "target": 7}):
        result = env.act(ActionCall(DIRECTIVE, dict(args)))
        assert not result.ok and result.error_kind == "invalid_args", args
        assert "names no country" in result.message
    assert env.adapter.state.ai_directives == []


def test_a_target_that_is_not_a_tag_is_refused():
    result = _env().act(ActionCall(DIRECTIVE, {"directive": "protect", "target": "Poland"}))
    assert not result.ok and "not a country tag" in result.message


def test_a_lower_case_target_acts_on_the_upper_case_tag_and_the_result_says_so():
    env = _env()
    result = env.act(ActionCall(DIRECTIVE, {"directive": "protect", "target": " pol "}))
    assert result.ok
    assert "[acted on POL]" in result.message and result.changed["target"] == "POL"
    assert env.adapter.state.ai_directives == ["protect POL (100)"]


def test_a_theater_posture_names_its_tag_and_an_empty_theater_is_not_every_front():
    env = _env()
    env.act(ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True}))
    ok = env.act(ActionCall("set_ai_posture", {"posture": "offensive", "theater": "pol"}))
    assert ok.ok and "[acted on POL]" in ok.message
    assert env.adapter.state.theater_postures == {"POL": "offensive"}
    blank = env.act(ActionCall("set_ai_posture", {"posture": "defensive", "theater": ""}))
    assert not blank.ok and "theater is empty" in blank.message
    assert env.adapter.state.posture is None          # the blank did not become a global posture
    whole = env.act(ActionCall("set_ai_posture", {"posture": "defensive"}))
    assert whole.ok and "[acted on" not in whole.message


# --- console: the lines name the tag, and verification asks for the same tag --------------------


@pytest.mark.parametrize("target", ["POL", "pol", " Pol "])
def test_the_console_selects_the_named_tag_then_raises(tmp_path, index, target):
    _, _, _, channel = rig(tmp_path, index)
    planned = channel.plan(ActionCall(DIRECTIVE, {"directive": "protect", "target": target}),
                           channel._read_state())
    assert planned.lines == ["effect SWE llm_bridge_select_POL",
                             "effect SWE llm_bridge_raise_protect"]
    assert planned.verify_call.arguments["target"] == "POL"      # verified against the tag sent


def test_the_console_refuses_a_missing_target_instead_of_raising_on_the_last_one(
        tmp_path, index):
    _, keys, _, channel = rig(tmp_path, index)
    for args in ({"directive": "protect"}, {"directive": "protect", "target": ""},
                 {"directive": "protect", "target": None}):
        with pytest.raises(Refused, match="no country was named"):
            plan(channel, ActionCall(DIRECTIVE, dict(args)))
    with pytest.raises(Refused, match="no country was named"):
        plan(channel, ActionCall("set_ai_posture", {"posture": "offensive", "theater": ""}))
    assert keys.sent == []


def test_the_decision_route_selects_the_named_tag_too(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index, hybrid_via="decision")
    lines = plan(channel, ActionCall(DIRECTIVE, {"directive": "invade", "target": "ger"}))
    assert lines[0] == "eval_effect SWE = { activate_decision = llmb_target_GER }"
    assert lines[1] == "eval_effect SWE = { activate_decision = llmb_directive_invade }"


def test_the_console_posture_on_a_theater_selects_that_theater(tmp_path, index):
    _, _, _, channel = rig(tmp_path, index)
    lines = plan(channel, ActionCall("set_ai_posture", {"posture": "offensive", "theater": "ger"}))
    assert lines == ["effect SWE llm_bridge_select_GER",
                     "effect SWE llm_bridge_raise_theater_offensive"]


def test_a_directive_that_landed_on_another_target_is_not_verified_and_names_it(
        tmp_path, index):
    game = Game()
    game.state.ai_directives = ["protect FIN (100)"]       # the stale target is the one that stood
    _, _, _, channel = rig(tmp_path, index, game=game)
    result = channel.execute([ActionCall(DIRECTIVE, {"directive": "protect", "target": "POL"})])[0]
    assert not result.ok
    assert "'protect POL'" in result.message and "protect FIN (100)" in result.message


# --- ui verifiers --------------------------------------------------------------------------------


def test_the_ui_verifier_matches_the_tag_not_its_spelling_and_names_what_stands():
    args = {"directive": "protect", "target": "pol"}
    standing = GameState(ai_directives=["protect POL (100)"])
    assert VERIFIERS[DIRECTIVE](GameState(), standing, args) is None
    why = VERIFIERS[DIRECTIVE](GameState(), GameState(ai_directives=["protect FIN (100)"]), args)
    assert "POL" in why and "protect FIN (100)" in why
    post = {"posture": "offensive", "theater": "pol"}
    after = GameState(theater_postures={"POL": "offensive"})
    assert VERIFIERS["set_ai_posture"](GameState(), after, post) is None


# --- the mod's generated blocks ------------------------------------------------------------------


def _file(tags, path: str) -> str:
    return {f.path: f.text for f in modgen.generate(tags)}[path]


def _blocks(text: str) -> dict[str, str]:
    return {m.group(1): m.group(2)
            for m in re.finditer(r"^(\w+) = \{\n(.*?)^\}", text, re.S | re.M)}


def _effects(tags) -> dict[str, str]:
    return _blocks(_file(tags, "common/scripted_effects/llm_bridge_targets.txt"))


def test_every_raise_branch_is_gated_on_its_own_tag_and_logs_that_tag():
    tags = ["POL", "GER", "FIN"]
    blocks = _effects(tags)
    for directive in modgen.AI_DIRECTIVES:
        branches = [x for x in blocks[f"llm_bridge_raise_{directive}"].splitlines() if x.strip()]
        assert len(branches) == len(tags)
        for tag, line in zip(tags, branches, strict=True):
            assert f"has_country_flag = llmb_target_{tag}" in line
            assert f"set_country_flag = llmb_{directive}_{tag}" in line
            assert f'detail={directive} {tag}"' in line
            for other in set(tags) - {tag}:
                assert other not in line


def test_selecting_a_target_clears_the_others_first_so_only_one_is_ever_selected():
    blocks = _effects(["POL", "GER"])
    select = blocks["llm_bridge_select_POL"].splitlines()
    assert select[0].strip() == "llm_bridge_clear_target = yes"
    assert "set_country_flag = llmb_target_POL" in select[1]
    assert "GER" not in blocks["llm_bridge_select_POL"]
    assert "clr_country_flag = llmb_target_GER" in blocks["llm_bridge_clear_target"]


def test_theater_posture_branches_use_their_own_tag():
    body = _effects(["POL", "GER"])["llm_bridge_raise_theater_offensive"]
    pol, ger = [x for x in body.splitlines() if x.strip()]
    assert "llmb_target_POL" in pol and "detail=offensive POL" in pol and "GER" not in pol
    assert "llmb_target_GER" in ger and "detail=offensive GER" in ger and "POL" not in ger


def test_each_ai_strategy_block_points_at_its_own_tag_only():
    blocks = _blocks(_file(["POL", "GER"], "common/ai_strategy/llm_bridge_targets.txt"))
    for name, body in blocks.items():
        m = re.match(r"llmb_(?:posture_)?(\w+?)_(POL|GER)(_strong)?$", name)
        if not m:
            continue
        tag, other = m.group(2), {"POL", "GER"} - {m.group(2)}
        assert f"has_country_flag = {name.removesuffix('_strong')}" in body, name
        assert tag in body and not any(o in body for o in other), name


# --- inbox: the same tag reaches the game's run file ---------------------------------------------


def test_the_inbox_raises_the_directive_on_the_named_tag_whatever_its_spelling(tmp_path):
    from test_inbox import _inbox_run

    call = ("set_ai_directive", {"directive": "invade", "target": " pol"})
    _, _, fake = _inbox_run(tmp_path, [("go", [call])])
    assert "invade POL" in fake.directives
    assert not any(d.endswith(" pol") or "FIN" in d for d in fake.directives)
