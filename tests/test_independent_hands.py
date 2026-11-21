"""The independent campaign's hands: research, division orders and army delegation through the
mouse (adapters/hands.py), against the fake game only.

Nothing here touches the real game, sends real input or calls a model: the driver's mouse is the
fake's (`FakeMouse`), its keys are scan codes into the fake's keyboard, and the screen points the
fake accepts are the test's own numbers, not ones the harness computed. None of it is verified
against the live game (docs/campaign.md lists what is)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from campaign_helpers import LazyClient, build_env, campaign_config, say
from hoi4_harness.adapters.base import GameLost
from hoi4_harness.adapters.hands import ARMY_ACTIONS, HAND_ACTIONS
from hoi4_harness.adapters.headless import excluded_actions
from hoi4_harness.adapters.unit_orders import stamp_now
from hoi4_harness.agent.hybrid import describe
from hoi4_harness.agent.policy import CampaignPacing, Policy
from hoi4_harness.agent.prompts import CAMPAIGN, CAMPAIGN_LLM, system_for
from hoi4_harness.calibration import Calibration, save_calibration
from hoi4_harness.campaign.runner import run_campaign
from hoi4_harness.campaign.setup import GUIDANCE_LLM, GUIDANCE_LLM_HANDS
from hoi4_harness.fakearmy import FakeArmy, true_camera_factory
from hoi4_harness.fakegame import DEMO_FILES
from hoi4_harness.types import ActionCall, GameState, StateForces, War
from orders_helpers import camera_record, load_layer, true_view, write_grid_game

TECHTREE = Path(__file__).parent / "fixtures" / "techtree"
#: Where the fake puts the controls (the test's own numbers: tests/test_ui_resolve.py computes the
#: same grid cells independently for the same fixture, 1920x1080 at UI scale 1.0).
SLOT, INFANTRY_TAB = (100, 200), (150, 120)
INFANTRY_AT, INFANTRY_WEAPONS1 = (1280, 754), (405, 579)
ARMY_POINTS = {"army.1st Army": (300, 1000), "army.all": (200, 1000),
               "army.ai_control_toggle": (400, 1040)}
SCREEN = (1920, 1080)


class Recorder(LazyClient):
    """Remembers each brief and the tools offered, then does what the script says."""

    def __init__(self, script=None, nap=30):
        super().__init__(script=script, nap=nap)
        self.briefs, self.tools = [], []

    def _policy(self, messages, tools):
        self.briefs.append(next((m.text for m in reversed(messages) if m.role == "user" and m.text), ""))
        self.tools.append({t.name for t in (tools or [])})
        return super()._policy(messages, tools)

    def complete(self, system, messages, tools=None, max_tokens=None):
        self.tools.append({t.name for t in (tools or [])})
        return super().complete(system, messages, tools, max_tokens)

    @property
    def offered(self) -> set[str]:
        return set().union(*self.tools) if self.tools else set()


def write_playset(root: Path) -> Path:
    """The grid map (4x3 provinces, SWE and GER states), the demo's focuses and equipment, and
    the technology fixture the research click points are computed from."""
    write_grid_game(root)
    for relative, text in DEMO_FILES.items():
        if relative.startswith(("history/states", "localisation/english/state_names")):
            continue                        # the grid's own states stand in for these
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    shutil.copytree(TECHTREE / "common/technologies", root / "common/technologies")
    (root / "interface").mkdir(exist_ok=True)
    shutil.copy(TECHTREE / "interface/countrytechtreeview.gui", root / "interface")
    return root


class World:
    def __init__(self, tmp_path, env, fake, config, record_path):
        self.tmp_path, self.env, self.fake, self.config = tmp_path, env, fake, config
        self.record_path = record_path

    @property
    def log(self) -> str:
        return (self.tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")


def make_world(tmp_path, *, camera="fresh", research_points=True, army_points=True, army=True,
               version=None, **options) -> World:
    """A campaign config and fake game for SWE on the grid map, the mouse tools wired as the
    options say. ``camera``: fresh, stale (3 days old, no fingerprint), fingerprinted (3 days old
    with a matching display fingerprint), moved (a fingerprint from another UI scale), missing."""
    game = write_playset(tmp_path / "game")
    if version:
        (game / "launcher-settings.json").write_text(json.dumps({"rawVersion": version}))
    layer = load_layer(game)
    record_path = tmp_path / "camera.json"
    options.setdefault("campaign_control", "llm")
    config = campaign_config(tmp_path, country="SWE", game_dir=game, camera_path=record_path,
                             **options)
    if camera != "missing":
        old = stamp_now(datetime.now() - timedelta(days=3))
        fingerprint = {"gui_scale": 1.0, "mode": None, "resolution": list(SCREEN)}
        record = {
            "fresh": camera_record(layer),
            "stale": camera_record(layer, recorded=old),
            "fingerprinted": camera_record(layer, recorded=old, display=fingerprint),
            "moved": camera_record(layer, recorded=old, display={**fingerprint, "gui_scale": 1.25}),
        }[camera]
        record.save(record_path)
    coordinates = {}
    if research_points:
        coordinates.update({"research.free_slot": SLOT, "research.tab.infantry_folder": INFANTRY_TAB})
    if army_points:
        coordinates.update(ARMY_POINTS)
    if coordinates:
        save_calibration(Calibration(SCREEN, coordinates), tmp_path / "run" / "calibration.json")
    env = build_env(config)
    fake = env.adapter.fake_game
    if army:
        fake.army = FakeArmy(layer, "SWE", [("SWE", 6, 3), ("GER", 7, 2)], {"GER"},
                             true_camera_factory(layer, true_view(), 6, "anchor0"))
    fake.research_ui = {
        "slot": SLOT, "tabs": {"infantry_folder": INFANTRY_TAB},
        "techs": {"infantry_at": ("infantry_folder", INFANTRY_AT),
                  "infantry_weapons1": ("infantry_folder", INFANTRY_WEAPONS1)}}
    fake.army_ui = {"entries": {"1st Army": ARMY_POINTS["army.1st Army"],
                                "all": ARMY_POINTS["army.all"]},
                    "toggle": ARMY_POINTS["army.ai_control_toggle"]}
    fake.ai_armies = {"1st Army": False, "2nd Army": False}
    fake.run_days(1)                      # the first daily tick prints the front picture
    return World(tmp_path, env, fake, config, record_path)


def play(world: World, client, until="1936-03-01"):
    world.config.campaign_until = until
    return run_campaign(world.config, planner=client, env=world.env)


def results(tmp_path) -> dict[str, dict]:
    out = {}
    for line in (tmp_path / "run" / "transcript.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        for result in record.get("results", []):
            out[result["name"]] = result
    return out


# --- 1. the adapter -------------------------------------------------------------------------------


def test_llm_mode_with_the_mouse_offers_research_and_both_army_tools(tmp_path):
    world = make_world(tmp_path)
    client = Recorder([say("go", ("advance_time", {"days": 30, "reason": "x"}))])
    play(world, client)
    assert HAND_ACTIONS <= client.offered
    assert client.offered >= {"set_national_focus", "queue_construction", "take_decision"}
    log = world.log
    assert "mouse tools live: attack_province, delegate_army_to_ai, move_division, start_research" in log
    assert "UNVERIFIED" in log


def test_the_input_driver_has_a_mouse_only_in_this_mode(tmp_path):
    world = make_world(tmp_path)
    assert world.env.adapter.writer.router.driver.config.mouse is True
    other = tmp_path / "none"
    other.mkdir()
    off = build_env(campaign_config(other, campaign_control="llm", campaign_input="none"))
    assert off.adapter.writer.router.driver.config.mouse is False
    game_ai = tmp_path / "gameai"
    game_ai.mkdir()
    assert build_env(campaign_config(game_ai)).adapter.writer.router.driver.config.mouse is False


def test_an_unknown_input_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="campaign_input"):
        campaign_config(tmp_path, campaign_control="llm", campaign_input="screen")


# --- 2. research -----------------------------------------------------------------------------------


def test_the_models_research_call_reaches_the_fake_game_and_is_verified(tmp_path):
    world = make_world(tmp_path)
    script = [say("research", ("start_research", {"technology": "infantry_at"}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    play(world, Recorder(script))
    result = results(tmp_path)["start_research"]
    assert not result["is_error"], result
    assert "verified" in result["content"]
    slots = {s.technology for s in world.fake.world.state.research}
    assert "infantry_at" in slots
    assert any("Return" in line for line in world.fake.ui_log)
    keys = world.fake.keylog
    assert keys.index("y") < keys.index("w") < keys.index("enter")      # production first: hotkeys toggle
    assert keys[:4] == ["y", "w", "enter", "enter"]                     # the second answers the chooser


def test_a_click_that_lands_on_nothing_is_reported_not_papered_over(tmp_path):
    world = make_world(tmp_path)
    world.fake.research_ui["techs"]["infantry_at"] = ("infantry_folder", (1500, 400))   # not where it is
    script = [say("research", ("start_research", {"technology": "infantry_at"}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    play(world, Recorder(script))
    result = results(tmp_path)["start_research"]
    assert result["is_error"] and "not in any research slot" in result["content"]
    assert "infantry_at" not in {s.technology for s in world.fake.world.state.research}


def test_a_technology_off_the_tree_sends_no_click(tmp_path):
    world = make_world(tmp_path)
    clicks = len(world.fake.gui.calls)
    script = [say("research", ("start_research", {"technology": "far_tech"}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    play(world, Recorder(script))
    result = results(tmp_path)["start_research"]
    assert result["is_error"] and "nothing was sent" in result["content"]
    assert world.fake.ui_log == [] and clicks == 0


def test_the_idle_research_slot_wake_fires_for_real(tmp_path):
    focus = [say("focus", ("set_national_focus", {"focus_id": "industrial_effort"}),
                 ("advance_time", {"days": 10, "reason": "x"}))]
    world = make_world(tmp_path)
    play(world, Recorder(focus), until="1936-04-01")
    assert "wake: 2 research slot(s) idle" in world.log
    off = tmp_path / "off"
    off.mkdir()
    config = campaign_config(off, campaign_control="llm", campaign_input="none")
    run_campaign(config, planner=Recorder(focus), env=build_env(config))
    assert "research slot" not in (off / "run" / "campaign.log").read_text(encoding="utf-8")


def test_the_state_line_counts_the_slots_so_an_idle_one_shows(tmp_path):
    world = make_world(tmp_path)
    state = world.env.adapter.read_state()
    assert [s.technology for s in state.research] == ["infantry_weapons2", None, None]
    # An older mod's line has no rslots: only the busy slot is seen, as before.
    from hoi4_harness.adapters.logtail import LogTailAdapter

    log = tmp_path / "old.log"
    log.write_text(
        "[12:00:00][1936.01.02.21]: LLMB|v5|state|date= 21:00, 2 January, 1936|tag=SWE|pp=1|stab=50|"
        "ws=10|civ=1|mil=1|doc=0|owned=2|mp=1|ai=0|techs=3|built_civ=0|built_mil=0|built_dock=0|"
        "built_infra=0\n"
        "[12:00:00][1936.01.02.21]: LLMB|v5|research|tag=SWE|id=infantry_weapons2\n"
        "[12:00:00][1936.01.03.21]: LLMB|v5|state|date= 21:00, 3 January, 1936|tag=SWE|pp=1|stab=50|"
        "ws=10|civ=1|mil=1|doc=0|owned=2|mp=1|ai=0|techs=3|built_civ=0|built_mil=0|built_dock=0|"
        "built_infra=0\n", encoding="utf-8")
    assert [s.technology for s in LogTailAdapter(log, country="SWE").read_state().research] == [
        "infantry_weapons2"]


# --- 3a. division orders -----------------------------------------------------------------------------


def test_an_attack_order_flows_through_plan_order_to_the_fake_army_and_the_ledger_shows(tmp_path):
    world = make_world(tmp_path)
    script = [say("attack", ("attack_province", {"from_state": 11, "to_state": 12}),
                  ("advance_time", {"days": 7, "reason": "let it march"}))]
    client = Recorder(script, nap=7)
    play(world, client, until="1936-02-20")
    result = results(tmp_path)["attack_province"]
    assert result["is_error"] and "order #1 sent" in result["content"]      # always unverified
    assert world.fake.army.log and any("order to province" in x for x in world.fake.army.log)
    assert any("order to province" in x and "for 3 of 3" in x for x in world.fake.army.log)
    assert any("Order: #1 attack state 11 -> 12" in brief for brief in client.briefs)


def test_an_order_plan_order_refuses_is_refused_before_any_click(tmp_path):
    world = make_world(tmp_path)
    script = [say("attack", ("attack_province", {"from_state": 11, "to_state": 10}),
                  ("advance_time", {"days": 7, "reason": "x"}))]
    play(world, Recorder(script))
    result = results(tmp_path)["attack_province"]
    assert result["is_error"] and "nothing was sent" in result["content"]
    assert not any(d.path for d in world.fake.army.divisions.values())


# --- the camera: what to do about its age ------------------------------------------------------------


def offered_after_start(world):
    client = Recorder([say("go", ("advance_time", {"days": 30, "reason": "x"}))])
    play(world, client)
    return client.offered


def test_no_camera_record_refuses_orders_at_the_start_and_says_so_but_delegation_stays(tmp_path):
    world = make_world(tmp_path, camera="missing")
    offered = offered_after_start(world)
    assert not offered & {"move_division", "attack_province"}
    assert {"delegate_army_to_ai", "start_research"} <= offered
    assert "mouse tool refused: move_division: no camera fit at" in world.log
    assert "record" in world.log.split("mouse tool refused: move_division:")[1].splitlines()[0]


def test_a_stale_camera_without_a_fingerprint_is_refused_at_the_start(tmp_path):
    world = make_world(tmp_path, camera="stale")
    assert not offered_after_start(world) & {"move_division", "attack_province"}
    assert "72 h old" in world.log and "fingerprint" in world.log


def test_an_old_camera_recorded_with_a_matching_display_is_accepted_unattended(tmp_path):
    world = make_world(tmp_path, camera="fingerprinted")
    assert {"move_division", "attack_province"} <= offered_after_start(world)


def test_a_camera_recorded_under_another_ui_scale_is_refused_whatever_its_age(tmp_path):
    world = make_world(tmp_path, camera="moved")
    assert not offered_after_start(world) & {"move_division", "attack_province"}
    assert "display changed" in world.log and "gui_scale" in world.log


def test_the_attended_age_limit_is_unchanged(tmp_path):
    from hoi4_harness.adapters.unit_orders import CameraRefused
    from orders_helpers import load_layer

    layer = load_layer(write_playset(tmp_path / "g"))
    old = camera_record(layer, recorded=stamp_now(datetime.now() - timedelta(days=3)),
                        display={"gui_scale": 1.0, "mode": None, "resolution": list(SCREEN)})
    with pytest.raises(CameraRefused, match="72 h old"):
        old.camera_for(layer, 6, screen=SCREEN, display=old.display)         # not unattended
    old.camera_for(layer, 6, screen=SCREEN, display=old.display, unattended=True)


# --- 3b. delegation ------------------------------------------------------------------------------------


def test_delegate_army_to_ai_flips_the_fake_armys_ai_flag(tmp_path):
    world = make_world(tmp_path)
    script = [say("hand it over", ("delegate_army_to_ai", {"army": "1st Army", "delegate": True}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    play(world, Recorder(script))
    assert world.fake.ai_armies == {"1st Army": True, "2nd Army": False}
    result = results(tmp_path)["delegate_army_to_ai"]
    assert result["is_error"]                         # not ok: nothing can read the toggle back
    assert "unverified" in result["content"].lower() or "cannot see" in result["content"]
    take_back = [say("take it back", ("delegate_army_to_ai", {"army": "1st Army", "delegate": False}),
                     ("advance_time", {"days": 30, "reason": "x"}))]
    other = tmp_path / "again"
    other.mkdir()
    world2 = make_world(other)
    world2.fake.ai_armies["1st Army"] = True
    play(world2, Recorder(take_back))
    assert world2.fake.ai_armies["1st Army"] is False


def test_an_army_with_no_recorded_point_is_refused_with_the_calibrate_line(tmp_path):
    world = make_world(tmp_path)
    script = [say("hand it over", ("delegate_army_to_ai", {"army": "2nd Army", "delegate": True}),
                  ("advance_time", {"days": 30, "reason": "x"}))]
    play(world, Recorder(script))
    result = results(tmp_path)["delegate_army_to_ai"]
    assert result["is_error"] and "calibrate" in result["content"] and "2nd Army" in result["content"]
    assert world.fake.ai_armies["2nd Army"] is False and world.fake.ui_log == []


def test_without_recorded_points_delegation_is_not_offered_and_the_log_says_how(tmp_path):
    world = make_world(tmp_path, army_points=False)
    offered = offered_after_start(world)
    assert "delegate_army_to_ai" not in offered
    assert {"start_research", "move_division", "attack_province"} <= offered
    assert "mouse tool refused: delegate_army_to_ai: no recorded click points" in world.log
    assert "hoi4-harness calibrate army.ai_control_toggle" in world.log


# --- the wake rules ------------------------------------------------------------------------------------

PACING = CampaignPacing(peace_days=30, war_days=14, wake_gap_days=7)


def war_state(date, forces):
    state = GameState(country="SWE", date=date, national_focus="x", wars=[War(against="GER")],
                      state_forces=forces)
    state.unknown_fields = []
    return state


def test_a_state_lost_to_the_enemy_wakes_the_model_only_with_an_army_tool():
    before = war_state("1936-03-01", [StateForces(state=11, controller="SWE", own=3, foe=1)])
    after = war_state("1936-03-08", [StateForces(state=11, controller="GER", own=0, foe=3)])
    on = Policy(campaign=PACING, wake_on_losing_ground=True, wake_on_front_quiet=True, country="SWE",
                hands_on=frozenset({"set_national_focus", "delegate_army_to_ai"}))
    decision = on.should_wake(after, days_since_planner=8, previous=before)
    assert decision.wake and "state 11 is now held by the enemy" in decision.reason
    off = Policy(campaign=PACING, wake_on_losing_ground=True, wake_on_front_quiet=True, country="SWE",
                 hands_on=frozenset({"set_national_focus", "start_research"}))
    assert not off.should_wake(after, days_since_planner=8, previous=before).wake
    assert not off.wake_on_losing_ground and not off.wake_on_front_quiet
    game_ai = Policy(campaign=PACING, wake_on_losing_ground=False, country="SWE")
    assert not game_ai.should_wake(after, days_since_planner=8, previous=before).wake


def test_a_front_that_goes_quiet_wakes_the_model_with_an_army_tool():
    before = war_state("1936-03-01", [StateForces(state=12, controller="GER", own=3, foe=2)])
    after = war_state("1936-03-08", [StateForces(state=12, controller="GER", own=3, foe=0)])
    policy = Policy(campaign=PACING, wake_on_losing_ground=True, wake_on_front_quiet=True,
                    country="SWE", hands_on=frozenset({"attack_province"}))
    decision = policy.should_wake(after, days_since_planner=8, previous=before)
    assert decision.wake and "went quiet" in decision.reason
    assert not policy.should_wake(after, days_since_planner=3, previous=before).wake    # the gap


def test_the_wartime_rules_follow_the_mode_in_the_config(tmp_path):
    on = make_world(tmp_path)
    loop_config = on.config
    assert loop_config.wake_on_losing_ground and loop_config.wake_on_front_quiet
    other = tmp_path / "none"
    other.mkdir()
    keyboard = campaign_config(other, campaign_control="llm", campaign_input="none")
    assert not keyboard.wake_on_losing_ground and not keyboard.wake_on_front_quiet
    game_ai = tmp_path / "gameai"
    game_ai.mkdir()
    config = campaign_config(game_ai)
    assert not config.wake_on_losing_ground and not config.wake_on_front_quiet


def test_with_every_army_tool_refused_the_loop_keeps_the_wartime_rules_off(tmp_path):
    world = make_world(tmp_path, camera="missing", army_points=False)
    from hoi4_harness.agent.loop import AgentLoop

    loop = AgentLoop(env=world.env, planner=Recorder(), config=world.config)
    assert not loop.policy.wake_on_losing_ground and not loop.policy.wake_on_front_quiet
    on = make_world(tmp_path / "on")
    loop = AgentLoop(env=on.env, planner=Recorder(), config=on.config)
    assert loop.policy.wake_on_losing_ground and loop.policy.wake_on_front_quiet


# --- the prompt and guidance match the tool list ---------------------------------------------------------


def test_the_prompt_names_what_is_offered_and_the_old_text_stays_when_the_mouse_is_off(tmp_path):
    world = make_world(tmp_path)
    prompt = system_for(world.config, world.env.index)
    assert "start_research puts a technology" in prompt
    assert "move_division and attack_province" in prompt
    assert "Armies you can hand over: 1st Army, all" in prompt
    assert "In this run none of them moves a unit" not in prompt
    assert world.config.guidance == GUIDANCE_LLM_HANDS

    off = campaign_config(tmp_path / "off", campaign_control="llm", campaign_input="none")
    off_prompt = system_for(off)
    assert CAMPAIGN_LLM in off_prompt and describe("llm", campaign=True) in off_prompt
    assert "your armies stand where they are" in off_prompt
    assert off.guidance == GUIDANCE_LLM
    assert "start_research" not in off_prompt.split("Your objective")[0]


def test_a_run_that_could_wire_nothing_gets_the_old_text_and_the_old_guidance(tmp_path):
    game = tmp_path / "bare"
    game.mkdir()
    config = campaign_config(game, campaign_control="llm")          # demo playset: no techs, no map
    build_env(config)
    assert config.campaign_hands["offered"] == []
    assert system_for(config).count(CAMPAIGN_LLM) == 1
    assert config.guidance == GUIDANCE_LLM


def test_the_game_ai_campaign_is_untouched_by_all_of_it(tmp_path):
    config = campaign_config(tmp_path)
    assert config.guidance == "world-conquest" and config.hands_mouse is False
    prompt = system_for(config)
    assert CAMPAIGN in prompt and "start_research puts" not in prompt
    env = build_env(config)
    assert env.adapter.hands.mouse is False
    assert not env.allowed_actions & HAND_ACTIONS
    assert env.adapter.supported_actions.isdisjoint(excluded_actions(config.headless_reader))


# --- the guards -------------------------------------------------------------------------------------------


def test_a_window_that_never_comes_back_gets_no_click(tmp_path):
    world = make_world(tmp_path, campaign_focus_wait_minutes=2)
    world.fake.lose_focus(None)
    adapter = world.env.adapter
    with pytest.raises(GameLost, match="has not come back to the front"):
        adapter.apply(ActionCall("start_research", {"technology": "infantry_at"}))
    assert world.fake.ui_log == [] and world.fake.gui.calls == []
    assert world.fake.stray_keys == [] and world.fake.keylog == []


def test_a_window_that_returns_after_a_wait_is_clicked_only_then(tmp_path):
    world = make_world(tmp_path)
    world.fake.lose_focus(seconds=130)
    result = world.env.adapter.apply(ActionCall("start_research", {"technology": "infantry_at"}))
    assert result.ok, result.message
    assert world.env.adapter.patient_focus.episodes >= 1
    assert world.fake.focused


def test_a_dry_run_campaign_is_still_refused_so_no_mouse_is_ever_dry(tmp_path):
    from hoi4_harness.campaign.runner import CampaignStartError

    config = campaign_config(tmp_path, campaign_control="llm", dry_run=True)
    with pytest.raises(CampaignStartError, match="--live"):
        run_campaign(config, planner=Recorder(), env=build_env(config))


def test_the_fake_game_in_another_process_never_gets_a_real_mouse(tmp_path):
    config = campaign_config(tmp_path, campaign_control="llm", fake_game=False,
                             fake_game_files=tmp_path / "files")
    (tmp_path / "files").mkdir()
    from hoi4_harness.adapters.headless import build_headless

    adapter = build_headless(config)
    assert adapter.hands.mouse is False and "fake_game_files" in adapter.hands.refused["start_research"]
    assert adapter.writer.router.driver.config.mouse is False
    assert not adapter.supported_actions & HAND_ACTIONS


def test_the_hand_actions_are_exactly_the_four_the_catalog_names():
    assert HAND_ACTIONS == {"start_research", "move_division", "attack_province",
                            "delegate_army_to_ai"}
    assert ARMY_ACTIONS == HAND_ACTIONS - {"start_research"}


# --- campaign-check says it before the night ---------------------------------------------------------


def test_campaign_check_names_what_a_person_has_to_provide(tmp_path):
    from hoi4_harness.campaign.preflight import PASS, WARN, check_hands

    assert check_hands(campaign_config(tmp_path / "gameai")) == []
    world = make_world(tmp_path / "w", camera="missing", army_points=False)
    lines = {c.name: c for c in check_hands(world.config)}
    assert lines["camera"].status == WARN and "move_division" in lines["camera"].detail
    assert lines["delegation"].status == WARN and "calibrate" in lines["delegation"].detail
    assert lines["research"].status == PASS
    ok = make_world(tmp_path / "ok", camera="fingerprinted")
    checks = {c.name: c for c in check_hands(ok.config)}
    assert checks["camera"].status == PASS and "any age" in checks["camera"].detail
    assert checks["delegation"].status == "INFO"
    old = make_world(tmp_path / "old", camera="stale")
    detail = {c.name: c for c in check_hands(old.config)}["camera"].detail
    assert "no display fingerprint" in detail and "refused" in detail


# --- battle plans (UNVERIFIED, off unless the profile asks) -------------------------------------------

PLAN_TOOLS = {"draw_front_line", "draw_offensive", "assign_battle_plan"}


def test_battle_plans_are_off_by_default_and_the_log_says_why(tmp_path):
    world = make_world(tmp_path)
    assert not offered_after_start(world) & PLAN_TOOLS
    assert "battle plans not offered: battle_plans is off in the profile" in world.log


def test_battle_plans_are_offered_when_the_profile_asks_and_the_camera_is_good(tmp_path):
    world = make_world(tmp_path, battle_plans=True)
    assert PLAN_TOOLS <= offered_after_start(world)
    assert "battle plans live: assign_battle_plan, draw_front_line, draw_offensive: UNVERIFIED" in world.log


def test_battle_plans_need_what_division_orders_need(tmp_path):
    world = make_world(tmp_path, battle_plans=True, camera="missing")
    assert not offered_after_start(world) & PLAN_TOOLS
    assert "battle plans not offered: battle plans need what division orders need" in world.log
