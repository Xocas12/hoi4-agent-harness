"""Co-op (#31, #33) and multiplayer (#91): handover modes, the domains a person holds, the
countries another person plays.

Nothing here touches the OS: the idle clock is an ``InputWatch`` built from plain attributes,
the focus check and the wall clock are injected, and the hotkey listener is the fake. A test
that reached ``GetLastInputInfo`` or ``RegisterHotKey`` would be a bug in the test.
"""

from __future__ import annotations

import json

import pytest

from hoi4_harness import coop
from hoi4_harness.adapters.input_driver import InputInterrupted
from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.adapters.window import FocusCheck
from hoi4_harness.agent.hybrid import describe_map
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.control import ControlError, resolve
from hoi4_harness.coop import (
    FakeHotkeyListener,
    GrantWindow,
    build_hotkey_listener,
    parse_hotkey,
    refusal,
    validate,
    wire_handover,
)
from hoi4_harness.env import HOI4Env
from hoi4_harness.eval import run_scenario
from hoi4_harness.handover import QUEUE_FILENAME, HandoverQueue
from hoi4_harness.rival.safety import InputWatch
from hoi4_harness.types import ActionCall


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Player:
    """The person at the keyboard: the OS idle clock as two attributes."""

    def __init__(self):
        self.now_ms = 100_000
        self.last_input_ms = 0
        self.focused = True
        self.watch = InputWatch(lambda: self.last_input_ms, lambda: self.now_ms)

    def touch(self) -> None:
        self.last_input_ms = self.now_ms

    def focus(self) -> FocusCheck:
        return FocusCheck(self.focused, "Hearts of Iron IV")


def stamp(date: str) -> str:
    y, m, d = date.split("-")
    return f"[12:00:00][{y}.{int(m)}.{int(d)}.21]: LLMB|v5|state|tag=SWE"


def _env(tmp_path, **kw) -> HOI4Env:
    config = HarnessConfig(handover=True, run_dir=tmp_path, **kw)
    env = HOI4Env(MockAdapter(), config)
    env.reset()
    return env


FOCUS = ActionCall("set_national_focus", {"focus_id": "industrial_effort"})


# --- #33 mode 1: hotkey, with the abort when the player comes back --------------------------


def test_hotkey_mode_runs_the_queue_on_a_grant_and_hands_back(tmp_path):
    player = Player()
    env = _env(tmp_path)
    wire_handover(env, env.config, watch=player.watch)
    env.act(FOCUS)
    assert env.run_handover() is None                      # no grant: nothing runs
    env.handover.grant()
    report = env.run_handover()
    assert [r["action"] for r in report.ran] == ["set_national_focus"]
    assert report.mode == "hotkey" and not env.handover.granted()


def test_the_player_touching_the_input_mid_queue_aborts_and_hands_back(tmp_path):
    player = Player()
    env = _env(tmp_path)
    wire_handover(env, env.config, watch=player.watch)
    for tech in ("a", "b", "c"):
        env.act(ActionCall("start_research", {"technology": tech}))
    env.handover.grant()
    real = env.adapter.read_state
    calls = []

    def read_state():
        calls.append(1)
        if len(calls) == 3:          # the player grabs the mouse while the 2nd action is checked
            player.now_ms += 500
            player.touch()
        return real()

    report = env.handover.run(read_state, env._apply)
    assert report.aborted and "keyboard or mouse" in report.stopped_because
    assert report.left_queued == 2 and len(report.ran) == 1
    assert not env.handover.granted()                      # handed back, not left open
    assert [p.call.arguments["technology"] for p in env.handover.pending] == ["b", "c"]
    assert "player took control back (the player started using the keyboard" in report.summary()


def test_our_own_input_is_not_the_player(tmp_path):
    """The harness's keys move the OS idle clock too; the guard must tell them apart."""
    player = Player()
    env = _env(tmp_path)
    wire_handover(env, env.config, watch=player.watch)
    env.act(ActionCall("start_research", {"technology": "a"}))
    env.act(ActionCall("start_research", {"technology": "b"}))
    env.handover.grant()

    def apply(call):
        player.now_ms += 50
        player.touch()               # SendInput: the stamp moves
        return env._apply(call)

    report = env.handover.run(env.adapter.read_state, apply)
    assert not report.aborted and len(report.ran) == 2


def test_a_key_guard_interruption_keeps_the_action_queued(tmp_path):
    env = _env(tmp_path)
    wire_handover(env, env.config, watch=Player().watch)
    env.act(FOCUS)
    env.handover.grant()

    def apply(call):
        raise InputInterrupted("the player started using the keyboard or mouse")

    report = env.handover.run(env.adapter.read_state, apply)
    assert report.aborted and report.left_queued == 1 and not report.failed
    assert not env.handover.granted()


def test_actions_record_how_long_they_waited(tmp_path):
    clock = Clock()
    env = _env(tmp_path)
    env.handover.clock = clock
    wire_handover(env, env.config, watch=Player().watch)
    env.act(FOCUS)
    queued = json.loads((tmp_path / QUEUE_FILENAME).read_text())
    assert queued[0]["queued_at"] == 1000.0
    clock.t = 1090.5
    env.handover.grant()
    report = env.run_handover()
    assert report.ran[0]["waited_seconds"] == 90.5
    assert report.granted_at == 1090.5 and report.window_seconds == 0.0


def test_waiting_time_survives_a_restart(tmp_path):
    clock = Clock()
    env = _env(tmp_path)
    env.handover.clock = clock
    env.act(FOCUS)
    again = HandoverQueue(tmp_path, clock=clock)
    assert again.restore() == 1 and again.pending[0].queued_at == 1000.0


# --- #33 mode 2: act only while paused and the player is idle -------------------------------


class IdleWorld:
    def __init__(self, tmp_path):
        self.player = Player()
        self.now = Clock(0.0)
        self.log = tmp_path / "game.log"
        self.log.write_text(stamp("1936-01-01") + "\n", encoding="utf-8")
        self.env = _env(tmp_path, handover_mode="idle", handover_idle_seconds=5,
                        handover_paused_seconds=10)
        self.window = wire_handover(self.env, self.env.config, watch=self.player.watch,
                                    log_path=self.log, now=self.now, focus=self.player.focus)
        self.window.clock.update()

    def advance(self, seconds: float) -> None:
        self.now.t += seconds
        self.player.now_ms += int(seconds * 1000)


def test_idle_mode_waits_until_paused_and_the_player_is_quiet(tmp_path):
    w = IdleWorld(tmp_path)
    w.env.act(FOCUS)
    assert w.env.run_handover() is None                    # the date has not stood still yet
    w.advance(12)
    w.player.touch()                                       # paused, but the player just clicked
    assert w.env.run_handover() is None
    w.advance(6)
    report = w.env.run_handover()
    assert report.mode == "idle" and [r["action"] for r in report.ran] == ["set_national_focus"]


def test_idle_mode_does_not_act_when_the_game_runs_or_is_not_in_front(tmp_path):
    w = IdleWorld(tmp_path)
    w.env.act(FOCUS)
    w.advance(30)
    with w.log.open("a", encoding="utf-8") as handle:
        handle.write(stamp("1936-01-02") + "\n")           # the date moved: not paused
    assert w.env.run_handover() is None
    w.advance(30)
    w.player.focused = False
    assert w.env.run_handover() is None
    w.player.focused = True
    assert w.env.run_handover() is not None


def test_idle_mode_aborts_when_the_player_comes_back_mid_queue(tmp_path):
    w = IdleWorld(tmp_path)
    for tech in ("a", "b"):
        w.env.act(ActionCall("start_research", {"technology": tech}))
    w.advance(30)
    real = w.env.adapter.read_state
    reads = []

    def read_state():
        reads.append(1)
        if len(reads) == 3:
            w.advance(0.5)
            w.player.touch()
        return real()

    report = w.env.handover.run(read_state, w.env._apply)
    assert report.aborted and report.left_queued == 1


def test_idle_mode_on_a_platform_with_no_idle_clock_never_acts(tmp_path):
    env = _env(tmp_path, handover_mode="idle")
    log = tmp_path / "game.log"
    log.write_text(stamp("1936-01-01") + "\n", encoding="utf-8")
    now = Clock(0.0)
    window = wire_handover(env, env.config, watch=InputWatch(), log_path=log, now=now,
                           focus=Player().focus)
    window.clock.update()
    env.act(FOCUS)
    now.t = 100
    assert env.run_handover() is None
    assert window.check().kind == "no idle clock"


# --- #33 mode 3: approval per action ----------------------------------------------------------


def test_approve_mode_runs_only_what_the_player_says_yes_to(tmp_path):
    env = _env(tmp_path, handover_mode="approve")
    asked = []

    def hook(request):
        asked.append((request.call.name, request.date))
        return request.call.name == "set_national_focus"

    env.confirm_hook = hook
    wire_handover(env, env.config)
    env.act(FOCUS)
    env.act(ActionCall("start_research", {"technology": "a"}))
    assert asked == []                                     # not asked when queued, only when run
    report = env.run_handover()
    assert [r["action"] for r in report.ran] == ["set_national_focus"]
    assert [d["action"] for d in report.declined] == ["start_research"]
    assert "1 declined by the player: start_research" in report.summary()
    assert env.handover.pending == []                      # a no is a decision, not a retry
    assert [a[0] for a in asked] == ["set_national_focus", "start_research"]


def test_approve_mode_without_a_hook_is_a_no(tmp_path):
    env = _env(tmp_path, handover_mode="approve")
    wire_handover(env, env.config)
    env.act(FOCUS)
    report = env.run_handover()
    assert report.ran == [] and len(report.declined) == 1
    assert env.read_state().national_focus is None


def test_approve_mode_does_not_ask_an_idle_queue(tmp_path):
    env = _env(tmp_path, handover_mode="approve")
    env.confirm_hook = lambda request: pytest.fail("asked with nothing queued")
    wire_handover(env, env.config)
    assert env.run_handover() is None


# --- #33 the hotkey listener --------------------------------------------------------------------


def test_the_hotkey_is_off_unless_asked_for():
    assert build_hotkey_listener(HarnessConfig()) is None


def test_a_hotkey_press_grants_a_window_through_the_fake_listener(tmp_path):
    env = _env(tmp_path)
    wire_handover(env, env.config, watch=Player().watch)
    listener = FakeHotkeyListener()
    listener.start(env.handover.grant)
    env.act(FOCUS)
    assert env.run_handover() is None
    listener.press()
    assert env.run_handover().ran
    listener.stop()
    listener.press()                                       # stopped: a press does nothing
    assert not env.handover.granted()


def test_hotkey_specs_parse_and_a_bare_key_is_refused():
    assert parse_hotkey("ctrl+alt+h") == (0x3, ord("H"))
    assert parse_hotkey("Shift+F9") == (0x4, 0x78)
    for bad in ("h", "ctrl+", "ctrl+enter", "hyper+h", "ctrl+f13"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)


def test_the_real_listener_is_refused_off_windows(monkeypatch):
    monkeypatch.setattr(coop.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="needs Windows"):
        build_hotkey_listener(HarnessConfig(handover_hotkey="ctrl+alt+h"))


def test_grant_window_stops_on_a_withdrawn_grant_even_without_an_idle_clock(tmp_path):
    window = GrantWindow(tmp_path / "handover.grant", None)
    (tmp_path / "handover.grant").touch()
    window.begin()
    assert window.interrupted() is None
    (tmp_path / "handover.grant").unlink()
    assert "withdrew" in window.interrupted()


def test_settings_that_cannot_work_are_named():
    assert validate(HarnessConfig(handover_mode="sometimes"))
    assert validate(HarnessConfig(handover_mode="idle", handover_hotkey="ctrl+alt+h"))
    assert validate(HarnessConfig(handover_hotkey="h"))
    assert validate(HarnessConfig(handover_hotkey="ctrl+alt+h")) == []


# --- #31 domains the player holds -----------------------------------------------------------------


def test_human_is_a_controller_and_the_model_gets_no_tools_for_it():
    cmap = resolve("default=llm,army=human,navy=human,diplomacy=human")
    assert cmap.human_domains() == ["diplomacy", "army", "navy"]
    tools = cmap.actions_for_model({"set_army_order", "set_naval_mission", "diplomacy",
                                    "set_national_focus", "note"})
    assert tools == {"set_national_focus", "note"}
    assert not cmap.script_domains()


def test_a_human_domain_cannot_be_scripted_or_sit_under_the_game_ai():
    with pytest.raises(ControlError, match="human_ai"):
        resolve("focus=game,army=human")
    resolve("focus=game,army=human,default=game", allow_contested=True)   # an experiment


def test_human_domains_fold_into_the_control_map():
    config = HarnessConfig(human_domains=["army", "navy"])
    cmap = config.control_map()
    assert cmap.controller("army") == "human" and cmap.controller("focus") == "llm"
    config = HarnessConfig(human_domains=["army"], control="research=script")
    assert config.control_map().controller("research") == "script"
    with pytest.raises(ControlError):
        HarnessConfig(human_domains=["army"], control="llm").control_map()
    with pytest.raises(ControlError, match="unknown control domain"):
        HarnessConfig(human_domains=["cavalry"]).control_map()


def test_the_model_cannot_act_in_a_human_domain_by_any_route(tmp_path):
    env = _env(tmp_path, human_domains=["production"])
    call = ActionCall("set_production", {"equipment": "infantry_equipment_1", "factories": 1})
    result = env.act(call)
    assert not result.ok and "holds" in result.message and env.handover.pending == []
    # an allow-list that names it does not change that
    env = _env(tmp_path / "b", human_domains=["production"],
               enabled_actions=["set_production", "note"])
    assert not env.act(call).ok
    # a script's route (auto=True) does not either
    assert "holds" in env.act(call, auto=True).message
    # nor a queue file written by hand: the call is refused again when it runs
    env2 = _env(tmp_path / "c", human_domains=["production"])
    from hoi4_harness.handover import Pending
    env2.handover.pending.append(Pending(call, "1936-01-01", 0, 1))
    env2.handover.grant()
    report = env2.run_handover()
    assert report.ran == [] and "holds" in report.failed[0]["reason"]


def test_the_other_domains_still_work_and_the_brief_names_the_human_ones(tmp_path):
    env = _env(tmp_path, human_domains=["army", "diplomacy"])
    brief = env.observe().brief
    assert "Held by the player, not yours (do not act there): diplomacy, army" in brief
    assert "set_army_order" not in env.allowed_actions
    assert env.act(FOCUS).ok


def test_the_prompt_says_a_person_holds_the_domain():
    text = describe_map(HarnessConfig(human_domains=["army", "navy"]).control_map())
    assert "A person is playing this country with you and holds: army orders, fleet missions" in text
    assert "You run:" in text


def test_a_human_domain_run_is_not_scoreable():
    with pytest.raises(ValueError, match="for playing, not scoring"):
        run_scenario("economy_ramp", HarnessConfig(human_domains=["army"], planner_enabled=False))


# --- #31 the guidance channel -----------------------------------------------------------------------


def test_guide_reaches_a_co_op_run_and_wakes_the_model_once(tmp_path):
    from hoi4_harness.agent.llm.base import LLMResponse, Usage
    from hoi4_harness.agent.llm.scripted import ScriptedClient
    from hoi4_harness.agent.loop import AgentLoop
    from hoi4_harness.campaign import inbox

    config = HarnessConfig(handover=True, run_dir=tmp_path, human_domains=["army"])
    env = HOI4Env(MockAdapter(), config)
    env.reset()
    planner = ScriptedClient(script=[LLMResponse(text="ok", stop_reason="end_turn",
                                                usage=Usage(1, 1))] * 6)
    loop = AgentLoop(env, planner, config, transcript_path=tmp_path / "t.jsonl")
    loop.supervisor = coop.CoopGuidance(tmp_path, loop)
    inbox.add(tmp_path, "Keep factories on rifles, I will handle the front")
    loop.run(2)
    records = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert [r["text"] for r in records if r["kind"] == "guidance"] == [
        "Keep factories on rifles, I will handle the front"]
    assert any("guidance from the player" in str(r) for r in records)
    assert "PLAYER GUIDANCE" in env.observe().brief


def test_only_co_op_runs_get_the_guidance_supervisor():
    assert coop.wants_guidance(HarnessConfig(handover=True))
    assert coop.wants_guidance(HarnessConfig(human_domains=["army"]))
    assert not coop.wants_guidance(HarnessConfig())
    assert not coop.wants_guidance(HarnessConfig(handover=True, campaign=True))


# --- #91 multiplayer ----------------------------------------------------------------------------------


def _mp(**kw) -> HarnessConfig:
    return HarnessConfig(multiplayer=True, country="FRA", mp_human_tags=["GER"],
                         clock_owner="player", **kw)


def test_an_action_aimed_at_the_human_country_is_refused():
    config = _mp()
    war = ActionCall("diplomacy", {"action": "declare_war", "target": "GER"})
    assert "a person plays" in refusal(config, war)
    assert refusal(config, ActionCall("diplomacy", {"action": "declare_war", "target": "ger"}))
    assert refusal(config, ActionCall("set_ai_directive", {"directive": "invade", "target": "GER"}))
    assert refusal(config, ActionCall("diplomacy", {"action": "declare_war", "target": "POL"})) is None
    assert refusal(config, FOCUS) is None


def test_the_env_refuses_it_and_the_brief_says_who_plays_whom(tmp_path):
    env = HOI4Env(MockAdapter(), _mp(run_dir=tmp_path))
    env.reset()
    result = env.act(ActionCall("diplomacy", {"action": "declare_war", "target": "GER"}))
    assert not result.ok and result.error_kind == "rejected"
    assert "Multiplayer: you play FRA; people play GER" in env.observe().brief


def test_multiplayer_settings_are_checked():
    assert validate(_mp()) == []
    assert validate(HarnessConfig(multiplayer=True, mp_human_tags=["GER"], clock_owner="player"))
    assert validate(HarnessConfig(multiplayer=True, country="GER", mp_human_tags=["GER"],
                                  clock_owner="player"))
    assert validate(HarnessConfig(multiplayer=True, country="FRA", clock_owner="player"))
    assert validate(HarnessConfig(multiplayer=True, country="FRA", mp_human_tags=["GER"]))


def test_a_multiplayer_run_is_not_scoreable():
    with pytest.raises(ValueError, match="for playing, not scoring"):
        run_scenario("economy_ramp", _mp(planner_enabled=False))


# --- the command line ------------------------------------------------------------------------------------


def test_the_cli_refuses_a_hotkey_in_a_mode_that_has_no_use_for_it(tmp_path, capsys):
    code = main(["play", "--handover-mode", "idle", "--handover-hotkey", "ctrl+alt+h",
                 "--run-dir", str(tmp_path), "--no-llm", "--turns", "1"])
    assert code == 2 and "only applies to handover_mode hotkey" in capsys.readouterr().err


def test_the_cli_refuses_multiplayer_with_no_country(tmp_path, capsys):
    code = main(["play", "--mp-human", "GER", "--run-dir", str(tmp_path), "--no-llm",
                 "--turns", "1"])
    assert code == 2 and "needs country" in capsys.readouterr().err


def test_the_cli_runs_a_co_op_game_with_human_domains_on_the_mock(tmp_path, capsys):
    code = main(["play", "--human", "army,navy", "--handover-mode", "approve", "--no-llm",
                 "--turns", "2", "--run-dir", str(tmp_path), "--quiet"])
    assert code == 0
