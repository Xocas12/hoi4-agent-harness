"""Sovereign mode (#161 step 2): the model owns the country, the game's AI runs only the armies.

``control = "sovereign"`` is the economy by hand plus focus, politics and diplomacy, with
``army: game`` and NO ``human_ai``: every army is handed to the game's AI by giving it a front
(``assign_army_front``). All of it runs against the fake game, which models the click path's
effect on the armies the save records (``FakeGame.army_groups``); no real input, no model call, and
none of it verified in the real game (docs/modular-control.md, "Sovereign mode").
"""

from __future__ import annotations

import json

import pytest

from campaign_helpers import say
from hoi4_harness import sovereign
from hoi4_harness.adapters import production_ui as pu
from hoi4_harness.agent.policy import CampaignPacing, Policy
from hoi4_harness.agent.prompts import system_for
from hoi4_harness.campaign.runner import CampaignStartError, run_campaign
from hoi4_harness.control import (
    DELEGATE_ACTION,
    SOVEREIGN_ARMY_ACTIONS,
    STEER_ACTIONS,
    ControlError,
    resolve,
)
from hoi4_harness.types import ArmyGroup, ArmyPlan, DivisionGroup, GameState, ProductionLine, War
from test_137_economy_control import PRODUCTION_POINTS, ROWS
from test_independent_hands import ARMY_POINTS, Recorder, make_world, play

EXECUTE = {"careful": (300, 300), "balanced": (330, 300), "rush": (360, 300)}
CREATE = (130, 100)
SOVEREIGN_POINTS = {
    **PRODUCTION_POINTS,
    "army.1st Army": ARMY_POINTS["army.1st Army"], "army.2nd Army": (500, 1000),
    "army.Army 3": (600, 1000), "army.create": CREATE,
    **{f"plan.execute_{m}": p for m, p in EXECUTE.items()},
}


def army(ident, name, members, plans=(), execution=None):
    return {"id": str(ident), "name": name, "members": members, "plans": list(plans),
            "execution": execution}


def sovereign_world(tmp_path, *, points=True, groups=True, **options):
    """SWE on the grid map at war with GER: two armies of 2 and 1 divisions that hold no front,
    the economy's screens, and the army click path's own points."""
    world = make_world(tmp_path, control="sovereign", camera="fresh",
                       points=SOVEREIGN_POINTS if points else {}, army_points=points,
                       headless_reader="log+save", **options)
    fake = world.fake
    # The one focus there is finishes and the model fills one slot of three: neither is what is
    # tested here, and each would wake it every turn.
    world.config.wake_on_no_focus = world.config.wake_on_free_research_slot = False
    fake.world.state.production = [ProductionLine("infantry_equipment_1", 3, 0.5, 4.0)]
    fake.world.state.military_factories = 6
    fake.world.state.divisions = [DivisionGroup("Infantry", 3)]
    for state in fake.states.values():
        state.slots, state.used = 3, 0
    fake.production_ui = {"rows": ROWS, "plus": (1700, 200), "minus": (1740, 200),
                          "remove": (1780, 200), "change": (1500, 200),
                          "picker": {"artillery_equipment_1": (1000, 500),
                                     "infantry_equipment_1": (1000, 540)}}
    fake.army_ui = {"entries": {"1st Army": ARMY_POINTS["army.1st Army"],
                                "2nd Army": (500, 1000), "Army 3": (600, 1000)},
                    "execute": EXECUTE, "create": CREATE}
    if groups:
        fake.army_groups = [army(1, "1st Army", 2), army(2, "2nd Army", 1)]
    fake.world.state.wars = [War("GER")]
    fake.run_days(1)
    return world


def groups_of(world):
    return {g["name"]: g for g in world.fake.army_groups}


def holds(group) -> bool:
    return any(p["assigned"] and not p.get("fallback") for p in group["plans"])


class Playing(Recorder):
    """The recorder, plus ``wake_briefs`` (the brief of each wake, scripted or not) and ``events``:
    ``{wake index: callable(fake)}`` that change the fake game while the model reads that wake's
    brief, so the NEXT turn sees it (an army appears, a division is raised)."""

    def __init__(self, script=None, nap=15, events=None, fake=None):
        super().__init__(script=script, nap=nap)
        self.events, self.fake, self.wake_briefs = events or {}, fake, []

    def complete(self, system, messages, tools=None, max_tokens=None):
        if not (messages and messages[-1].tool_results):
            n = len(self.wake_briefs)
            self.wake_briefs.append(next((m.text for m in reversed(messages)
                                          if m.role == "user" and m.text), ""))
            if n in self.events:
                self.events[n](self.fake)
        return super().complete(system, messages, tools, max_tokens)


def records(tmp_path) -> list[dict]:
    path = tmp_path / "run" / "transcript.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def results(tmp_path) -> dict[str, dict]:
    return {r["name"]: r for rec in records(tmp_path) for r in rec.get("results", [])
            if "name" in r}


def wakes(tmp_path) -> list[str]:
    """The reason the harness gave for each turn's wake, in order (``observe`` records)."""
    return [r["wake_reason"] for r in records(tmp_path) if "wake_reason" in r]


# --- the map ----------------------------------------------------------------------------------


def test_161_sovereign_is_the_economy_plus_the_countrys_choices_with_the_armies_to_the_game():
    cmap = resolve("sovereign")
    assert cmap.alias == "sovereign" and cmap.explicit
    assert {cmap.controller(d) for d in ("focus", "research", "production", "construction",
                                         "politics", "diplomacy")} == {"llm"}
    assert cmap.controller("army") == "game"
    assert {cmap.controller("navy"), cmap.controller("air")} == {"off"}
    assert not cmap.human_ai, "the game's AI is never handed the country"
    assert cmap.delegated and cmap.steer == "off"


def test_161_the_model_gets_the_economy_and_the_front_tools_but_no_toggle_or_steering():
    cmap = resolve("sovereign")
    every = {"set_national_focus", "start_research", "set_production", "queue_construction",
             "design_equipment", "move_division", "set_army_order", DELEGATE_ACTION,
             "set_ai_directive", "set_ai_posture", "draw_fallback_line", "set_naval_mission",
             "set_air_mission", *SOVEREIGN_ARMY_ACTIONS}
    got = cmap.actions_for_model(every)
    assert {"set_national_focus", "start_research", "set_production",
            "queue_construction"} <= got
    assert SOVEREIGN_ARMY_ACTIONS <= got
    assert not got & {DELEGATE_ACTION, "move_division", "set_army_order", "draw_fallback_line",
                      "set_naval_mission", "set_air_mission", *STEER_ACTIONS}


def test_161_a_domain_on_game_with_the_model_on_the_rest_is_still_refused_as_contested():
    with pytest.raises(ControlError, match="contested"):
        resolve("sovereign,production=game")


def test_161_the_economy_alias_is_unchanged_and_not_sovereign():
    assert resolve("economy").controller("army") == "off"
    assert not sovereign.is_sovereign(resolve("economy"))
    assert sovereign.describe(resolve("economy"), SOVEREIGN_ARMY_ACTIONS) == ""
    assert sovereign.lines(resolve("economy"), SOVEREIGN_ARMY_ACTIONS) == []


# --- the start: calibration and ai=0 ------------------------------------------------------------


def test_161_a_run_without_the_army_points_refuses_to_start_and_names_each(tmp_path):
    world = sovereign_world(tmp_path, points=False)
    with pytest.raises(CampaignStartError) as caught:
        play(world, Recorder([say("x", ("advance_time", {"days": 30, "reason": "x"}))]))
    message = str(caught.value)
    for point in ("army.create", "plan.execute_careful", "plan.execute_balanced",
                  "plan.execute_rush", "army.1st Army", "army.2nd Army"):
        assert point in message, point
    assert "hoi4-harness calibrate" in message
    assert "cannot start" in world.log
    assert world.fake.keylog == [] or not any("human_ai" in line for r in world.fake.runs for line in r)


def test_161_one_missing_point_is_the_only_one_named(tmp_path):
    points = {k: v for k, v in SOVEREIGN_POINTS.items() if k != "plan.execute_rush"}
    world = make_world(tmp_path, control="sovereign", points=points, headless_reader="log+save")
    world.fake.army_groups = [army(1, "1st Army", 2)]
    world.fake.army_ui["execute"] = EXECUTE
    world.fake.run_days(1)
    with pytest.raises(CampaignStartError) as caught:
        play(world, Recorder([say("x", ("advance_time", {"days": 30, "reason": "x"}))]))
    message = str(caught.value)
    assert "plan.execute_rush" in message
    assert "plan.execute_balanced" not in message and "army.create" not in message


def test_161_a_game_whose_ai_runs_the_country_refuses_to_start_and_human_ai_is_not_sent(tmp_path):
    world = sovereign_world(tmp_path)
    world.fake.human_ai = True               # an earlier game_ai run left it on
    world.fake.flags.add("llmb_harness")
    world.fake.emit_state()
    with pytest.raises(CampaignStartError, match="ai=1"):
        play(world, Recorder([say("x", ("advance_time", {"days": 30, "reason": "x"}))]))
    assert not any("human_ai" in line for run in world.fake.runs for line in run)


def test_161_the_start_log_says_who_runs_what_and_that_human_ai_is_not_sent(tmp_path):
    world = sovereign_world(tmp_path)
    play(world, Recorder([say("wait", ("advance_time", {"days": 30, "reason": "x"}))]))
    log = world.log
    assert "sovereign mode: the model owns the country" in log
    assert "human_ai is not sent" in log
    assert "assign_army_front" in log and "navy and air" in log
    assert "economy research: start_research (mouse)" in log
    assert world.fake.world.state.ai_controlled is not True


# --- the prompt and the brief -------------------------------------------------------------------


def test_161_the_prompt_says_who_runs_what_and_how_an_army_is_handed_over(tmp_path):
    world = sovereign_world(tmp_path)
    play(world, Recorder([say("wait", ("advance_time", {"days": 30, "reason": "x"}))]))
    text = system_for(world.config)
    assert "You own the country" in text and "human_ai is off" in text
    assert "assign_army_front" in text and "giving it a front" in text
    assert "Nobody orders the navy and the air force" in text
    assert "You run the economy by hand" in text


def test_161_every_brief_says_who_runs_what_and_which_armies_hold_a_front(tmp_path):
    world = sovereign_world(tmp_path)
    client = Recorder([say("wait", ("advance_time", {"days": 30, "reason": "x"}))])
    play(world, client)
    brief = client.briefs[0]
    assert "Sovereign mode: you run the country" in brief
    assert "navy and air" in brief and "the game's AI runs only the armies" in brief
    assert "Armies: 2; 0 hold a front" in brief


# --- the gaps the wake rules read ----------------------------------------------------------------


def group(name, members, *plans):
    return ArmyGroup(name, name, members, list(plans), 1)


def line(assigned=2, kind="offensive"):
    return ArmyPlan(kind, [3], [], assigned)


def test_161_gaps_count_armies_without_a_front_and_divisions_in_no_army():
    state = GameState(divisions=[DivisionGroup("Inf", 7)],
                      army_groups=[group("A", 3, line()), group("B", 2), group("C", 1, line(0)),
                                   group("G", 1, ArmyPlan("garrison", [], [4], 1))])
    found = sovereign.gaps(state)
    assert sorted(found.no_front) == ["B", "C", "G"]      # a garrison or an empty plan is no front
    assert found.unassigned == 0                          # 3+2+1+1 = 7
    state.divisions = [DivisionGroup("Inf", 9)]
    assert sovereign.gaps(state).unassigned == 2
    assert sovereign.gaps(GameState()) is None            # no text save read: nothing claimed


# --- the wake rules ------------------------------------------------------------------------------


def policy(**kw):
    kw.setdefault("hands_on", frozenset(SOVEREIGN_ARMY_ACTIONS))
    return Policy(campaign=CampaignPacing(30, 14, 7, 60, 7), sovereign=True,
                  owned=frozenset({"focus", "research"}), steer=False, **kw)


def at_war(*groups, divisions=3, date="1936-03-01"):
    return GameState(date=date, wars=[War("GER")], divisions=[DivisionGroup("Inf", divisions)],
                     army_groups=list(groups))


def test_161_an_army_without_a_front_wakes_the_model_once_and_not_again_for_the_same_gap():
    p = policy()
    state = at_war(group("A", 3))
    first = p.should_wake(state, 10, None)
    assert first.wake and "army without a front: A" in first.reason
    again = p.should_wake(state, 8, state)
    assert not again.wake, "the same gap does not wake it every gap-days"
    state.army_groups = [group("A", 3, line(3))]
    assert not p.should_wake(state, 8, state).wake
    state.army_groups = [group("A", 3, line(3)), group("B", 1)]
    assert "army without a front: B" in p.should_wake(state, 8, state).reason


def test_161_a_division_in_no_army_wakes_the_model():
    p = policy()
    state = at_war(group("A", 2, line(2)), divisions=3)
    decision = p.should_wake(state, 10, None)
    assert decision.wake and "1 division(s) in no army" in decision.reason


def test_161_peace_wakes_nothing_for_armies_and_a_gap_inside_the_wake_gap_waits():
    p = policy()
    peace = GameState(date="1936-03-01", divisions=[DivisionGroup("Inf", 3)],
                      army_groups=[group("A", 3)])
    assert not p.should_wake(peace, 10, None).wake
    assert not p.should_wake(at_war(group("A", 3)), 3, None).wake       # wake_gap_days is 7


def test_161_a_front_the_front_report_reads_as_losing_wakes_the_model_on_onset():
    reading = {"GER": "stuck"}
    p = policy(front_readings=lambda state: reading)
    state = at_war(group("A", 3, line(3)))
    assert not p.should_wake(state, 10, None).wake
    reading["GER"] = "losing"
    decision = p.should_wake(state, 8, state)
    assert decision.wake and "LOSING per the front report" in decision.reason
    assert decision.urgency == "critical"
    assert not p.should_wake(state, 8, state).wake         # onset only
    reading["GER"] = "stuck"
    p.should_wake(state, 8, state)
    reading["GER"] = "losing"
    assert p.should_wake(state, 8, state).wake             # and again when it turns again


def test_161_without_the_front_tools_none_of_it_wakes():
    p = policy(hands_on=frozenset({"set_production"}))
    assert not p.should_wake(at_war(group("A", 3)), 10, None).wake


# --- a campaign on the fake game --------------------------------------------------------------------


def front_calls(*armies, enemy="GER"):
    return [("assign_army_front", {"army": name, "enemy": enemy}) for name in armies]


def test_161_a_few_fake_months_in_sovereign_mode(tmp_path):
    """The whole mode on the fake: research, a production edit and a building by hand, both armies
    given a front, then a division raised mid-run that wakes the model, gets an army by click and
    a front -- and human_ai is never sent."""
    world = sovereign_world(tmp_path)
    fake = world.fake

    def raise_division(game):
        game.army.add("SWE", 6)                 # the board: a fourth division stands in state 11
        game.world.state.divisions = [DivisionGroup("Infantry", 4)]

    script = [
        say("desk work and the first fronts",
            ("set_national_focus", {"focus_id": "SWE_defense_act"}),
            ("start_research", {"technology": "infantry_at"}),
            ("set_production", {"equipment": "infantry_equipment_1", "factories": 5}),
            ("queue_construction", {"building": "civilian_factory", "state": "10"}),
            *front_calls("1st Army", "2nd Army"),
            ("advance_time", {"days": 20, "reason": "let the armies work"})),
        say("a quiet review", ("advance_time", {"days": 15, "reason": "x"})),
        say("the new division gets an army and a front",
            ("create_army", {"from_state": 11}),
            *front_calls("Army 3"),
            ("advance_time", {"days": 30, "reason": "x"})),
    ]
    client = Playing(script, nap=20, events={1: raise_division}, fake=fake)
    play(world, client, until="1936-06-01")
    got = results(tmp_path)
    for name in ("start_research", "set_production", "queue_construction"):
        assert not got[name]["is_error"], (name, got[name])
    assert "verified" in got["start_research"]["content"]      # the slot was filled (it finished since)
    assert any("research:" in line and "infantry_at" in line for line in fake.ui_log)
    assert [(x.equipment, x.factories) for x in fake.world.state.production] == [
        ("infantry_equipment_1", 5)]
    groups = groups_of(world)
    assert holds(groups["1st Army"]) and holds(groups["2nd Army"])
    assert groups["1st Army"]["execution"] == 1, "balanced is the default pace"
    assert "Army 3" in groups and holds(groups["Army 3"]), world.fake.ui_log[-12:]
    assert not any("human_ai" in line for run in fake.runs for line in run)
    assert fake.world.state.ai_controlled is not True
    assert client.offered >= SOVEREIGN_ARMY_ACTIONS | {"start_research", "set_production"}
    assert client.offered.isdisjoint({"move_division", "set_ai_directive", DELEGATE_ACTION})
    # the wake after the division was raised is for the division, and the fronts are confirmed
    reasons = wakes(tmp_path)
    assert reasons[0].startswith("army without a front: 1st Army, 2nd Army")
    assert reasons[2] == "1 division(s) in no army", reasons
    assert "Armies: 2; 2 hold a front" in client.wake_briefs[1]
    assert "1 division(s) in no army" in client.wake_briefs[2]
    assert "confirmed" in client.wake_briefs[1], "the ledger only confirms from a save"
    assert "Armies: 3; 3 hold a front" in client.wake_briefs[3]
    assert fake.world.state.ai_controlled is not True


def test_161_run_campaign_is_the_entry_point_and_never_sends_human_ai(tmp_path):
    world = sovereign_world(tmp_path)
    world.config.campaign_until = "1936-02-15"
    run_campaign(world.config, planner=Recorder(
        [say("wait", ("advance_time", {"days": 20, "reason": "x"}))]), env=world.env)
    assert not world.config.campaign_human_ai and world.config.campaign_control == "llm"
    assert world.config.army_ui is True
    assert pu.row_point(1) in PRODUCTION_POINTS
    assert not any("human_ai" in line for run in world.fake.runs for line in run)


# --- the profile ---------------------------------------------------------------------------------


def test_161_the_sovereign_profile_is_a_campaign_where_the_model_owns_the_country():
    from pathlib import Path

    from hoi4_harness.campaign.setup import SOVEREIGN_SAVE_DAYS, apply_defaults
    from hoi4_harness.config import HarnessConfig

    config = HarnessConfig.from_file(Path(__file__).parent.parent / "profiles" / "sovereign-ger.json")
    apply_defaults(config)
    cmap = config.control_map()
    assert cmap.alias == "sovereign" and not cmap.human_ai
    assert config.campaign_control == "llm" and not config.campaign_human_ai
    assert config.army_ui and config.hands_mouse and config.unit_orders
    assert config.headless_save_days == SOVEREIGN_SAVE_DAYS
    assert config.campaign_save_months == 1
    assert SOVEREIGN_ARMY_ACTIONS <= config.allowed_actions(
        SOVEREIGN_ARMY_ACTIONS | {"start_research", "set_production"})
    text = system_for(config)
    assert "You own the country" in text and "assign_army_front" in text
    assert "Production is where the last campaign was lost" in text
