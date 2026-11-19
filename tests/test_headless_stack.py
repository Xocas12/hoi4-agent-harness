"""A whole campaign through the real adapters, against the fake game.

The log reader, the console channel, the scan-code key layer, the clock logic and
the agent loop are the real ones; only the game is fake (``fakegame.py``), and it
is reached through the same two files and the same scan codes. A scripted
provider plays, so nothing here calls a model or sends a key to this machine.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from hoi4_harness.adapters import build_adapter
from hoi4_harness.adapters.headless import MOUSE_ACTIONS, HeadlessClock, keyboard_only
from hoi4_harness.adapters.input_driver import ClockUnobservable, MouseDisabled
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.agent.prompts import system_for
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.fakegame import FakeGame, demo_index
from hoi4_harness.types import ActionCall

ROOT = Path(__file__).resolve().parents[1]


def config_for(tmp_path, **overrides) -> HarnessConfig:
    options = dict(adapter="headless", fake_game=True, fake_game_dir=tmp_path / "fake",
                   headless_reader="logtail",
                   run_dir=tmp_path / "run", dry_run=False, operational_control="ai",
                   days_per_turn=30, turns=6)
    options.update(overrides)
    return HarnessConfig(**options)


def stack(tmp_path, **overrides):
    config = config_for(tmp_path, **overrides)
    adapter = build_adapter(config)
    env = HOI4Env(adapter, config, index=config.build_index())
    env.reset()
    return config, adapter, adapter.fake_game, env


def say(*calls: tuple[str, dict]) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(f"t{i}", name, args) for i, (name, args) in enumerate(calls)],
        stop_reason="tool_use", usage=Usage(10, 10))


def play(tmp_path, script, turns, **overrides):
    config, adapter, fake, env = stack(tmp_path, turns=turns, **overrides)
    loop = AgentLoop(env=env, planner=ScriptedClient(script=list(script)), config=config,
                     transcript_path=tmp_path / "run" / "transcript.jsonl")
    loop.run(turns)
    return config, adapter, fake, env, read_transcript(tmp_path / "run" / "transcript.jsonl")


def read_transcript(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def results(transcript):
    """Every action result in order: (name, ok, error kind, message)."""
    out = []
    for entry in transcript:
        if entry["kind"] == "actions":
            for item in entry["results"]:
                body = json.loads(item["content"])
                out.append((item["name"], body["ok"], body.get("error"), body["message"],
                            body.get("changed", {})))
    return out


OPENING = [
    ("set_national_focus", {"focus_id": "industrial_effort"}),
    ("queue_construction", {"building": "civilian_factory", "state": "Svealand", "count": 1}),
    ("set_production", {"equipment": "artillery_equipment_1", "factories": 1}),
    ("hire_advisor", {"advisor_id": "SWE_demo_minister"}),
    ("set_ai_directive", {"directive": "protect", "target": "POL"}),
    ("set_ai_posture", {"posture": "defensive", "theater": "GER"}),
    ("advance_time", {"days": 7, "reason": "let the orders land"}),
]


# --- the campaign ------------------------------------------------------------------


def test_a_campaign_acts_on_the_fake_game_and_sees_completions_arrive(tmp_path):
    _, _, fake, _, transcript = play(tmp_path, [say(*OPENING)], turns=6, seed=3)
    by_name = {name: (ok, kind, message, changed) for name, ok, kind, message, changed
               in results(transcript)}

    # What the fake game now holds is what the model asked for.
    assert fake.world.state.national_focus is None
    assert fake.world.state.completed_focuses == ["industrial_effort"]     # started, then finished
    assert fake.world.state.construction == []                              # queued, then built
    # The log cannot show production, and the effect duplicates an existing line (seen
    # live on 1.19.5), so a log-only headless run does not offer set_production at all.
    assert [x.equipment for x in fake.world.state.production] == ["infantry_equipment_1"]
    assert fake.advisors == {"political_advisor": ["SWE_demo_minister"]}
    assert fake.directives == ["protect POL"]
    assert fake.theater_postures == {"GER": "defensive"}

    # Verified where the log shows it; unverified, honestly, where it does not.
    assert by_name["hire_advisor"][:2] == (True, None)
    assert by_name["set_ai_directive"][:2] == (True, None)
    assert by_name["set_ai_posture"][:2] == (True, None)
    assert by_name["queue_construction"][1] == "unverified"
    assert "cannot see" in by_name["queue_construction"][2]
    assert by_name["set_production"][:2] == (False, "unsupported")
    assert by_name["queue_construction"][3]["channel"] == "console"
    assert by_name["set_national_focus"][3]["channel"] == "ui"

    # Time passed, and what finished was reported to the model in later briefs.
    briefs = " ".join(e["brief"] for e in transcript if e["kind"] == "observe")
    assert "research complete" in briefs
    assert "construction complete" in briefs
    assert "focus complete" in briefs
    assert fake.world.built["built_civ"] >= 1
    assert fake.world.techs > 32


def test_every_console_action_is_one_run_through_the_real_key_sequence(tmp_path):
    _, _, fake, _, _ = play(tmp_path, [say(*OPENING)], turns=1)
    assert fake.runs[0][0].startswith("eval_effect SWE = { 10 = ")           # construction
    assert any(line.startswith("effect SWE llm_bridge_select_POL") for run in fake.runs
               for line in run)
    # Each run ends by asking the mod for fresh telemetry, twice.
    action_runs = [run for run in fake.runs if not run[0].startswith("gamespeed")]
    assert len(action_runs) == 4        # construction, advisor, directive, posture (no production)
    assert all(run[-2:] == ["effect SWE llm_bridge_emit_state"] * 2 for run in action_runs)
    # The keys the game saw: the console key, `run llmb_turn.txt`, enter, the console key.
    keylog = fake.keylog
    first = keylog.index("grave")
    assert keylog[first + 1:first + 4] == ["r", "u", "n"]
    assert fake.errors == []


def test_the_focus_is_typed_into_the_game_with_the_keyboard_only(tmp_path):
    _, _, fake, _, _ = play(tmp_path, [say(OPENING[0], OPENING[-1])], turns=1)
    assert fake.keylog[:2] == ["alt+q", "ctrl+f"]
    assert "shift+enter" in fake.keylog
    typed = fake.keylog[2:fake.keylog.index("shift+enter")]
    assert "".join(" " if k == "space" else k.removeprefix("shift+") for k in typed) == (
        "industrial effort")


def test_a_focus_is_unverified_while_paused_and_in_the_brief_a_day_later(tmp_path):
    config, adapter, fake, env = stack(tmp_path)
    result = env.act(ActionCall("set_national_focus", {"focus_id": "industrial_effort"}))
    assert result.error_kind == "unverified"
    assert fake.world.state.national_focus == "industrial_effort"
    state = env.advance(3)
    assert state.national_focus == "industrial_effort"


# --- failure paths ----------------------------------------------------------------------


def test_a_precondition_the_harness_can_check_refuses_before_any_key(tmp_path):
    _, _, fake, env = stack(tmp_path)
    assert fake.keylog == []
    result = env.act(ActionCall("hire_advisor", {"advisor_id": "SWE_demo_general"}))   # 150 PP
    assert result.error_kind == "rejected"
    assert "150 political power" in result.message and "nothing was sent" in result.message
    assert fake.keylog == [] and fake.runs == []


def test_a_state_the_game_says_is_not_ours_is_declined_by_the_game_not_the_harness(tmp_path):
    _, _, fake, env = stack(tmp_path)
    result = env.act(ActionCall("queue_construction", {
        "building": "civilian_factory", "state": "Pomerania", "count": 1}))
    # The harness cannot see ownership, so it sends and cannot confirm; the effect's own
    # limit made the game do nothing.
    assert result.ok is False and result.error_kind == "unverified"
    assert fake.runs and fake.world.state.construction == []
    assert "state ownership" in result.changed["unchecked"]


def test_a_full_state_is_declined_by_the_game_and_the_queue_holds_what_fitted(tmp_path):
    _, _, fake, env = stack(tmp_path)
    result = env.act(ActionCall("queue_construction", {
        "building": "civilian_factory", "state": "Svealand", "count": 3}))
    assert result.error_kind == "unverified"
    assert len(fake.world.state.construction) == 2          # two slots were free


def test_an_unknown_state_or_advisor_never_reaches_the_game(tmp_path):
    _, _, fake, env = stack(tmp_path)
    a = env.act(ActionCall("queue_construction", {"building": "radar", "state": "Atlantis"}))
    b = env.act(ActionCall("hire_advisor", {"advisor_id": "SWE_nobody"}))
    assert not a.ok and not b.ok
    assert fake.keylog == []


def test_a_hire_the_game_declines_for_a_taken_slot_shows_as_not_verified_done(tmp_path):
    _, _, fake, env = stack(tmp_path)
    first = env.act(ActionCall("hire_advisor", {"advisor_id": "SWE_demo_minister"}))
    second = env.act(ActionCall("hire_advisor", {"advisor_id": "SWE_demo_second_minister"}))
    assert first.ok
    # The PP guard let it through (the country could pay), the slot was taken, so the
    # effect charged anyway -- exactly why the result must not be read as "hired".
    assert fake.advisors == {"political_advisor": ["SWE_demo_minister"]}
    assert second.ok                                   # PP fell: the verifier's only evidence
    assert "no free political_advisor slot" in fake.log_path.read_text(encoding="utf-8")


def test_without_the_refresh_a_paused_game_leaves_an_action_unverified(tmp_path):
    _, _, fake, env = stack(tmp_path, console_refresh=False)
    result = env.act(ActionCall("hire_advisor", {"advisor_id": "SWE_demo_minister"}))
    assert result.error_kind == "unverified"
    assert "has not run a full day" in result.message
    assert fake.advisors                                # it did happen; the log just had no day


def test_with_the_refresh_the_same_action_is_verified_while_paused(tmp_path):
    _, _, fake, env = stack(tmp_path)
    assert fake.paused
    result = env.act(ActionCall("hire_advisor", {"advisor_id": "SWE_demo_minister"}))
    assert result.ok and result.message == "done and verified"
    assert fake.paused


def test_the_dry_run_sends_nothing_to_the_game(tmp_path):
    _, _, fake, env = stack(tmp_path, dry_run=True)
    result = env.act(ActionCall("hire_advisor", {"advisor_id": "SWE_demo_minister"}))
    assert result.error_kind == "not_executed" and "dry run" in result.message
    assert fake.keylog == [] and fake.runs == [] and fake.advisors == {}


# --- the clock ----------------------------------------------------------------------------


def test_the_harness_owns_the_clock_and_a_turn_is_exactly_its_days(tmp_path):
    _, adapter, fake, env = stack(tmp_path)
    assert fake.paused
    start = fake.date
    state = env.advance(7)
    assert fake.paused                                   # paused again after the turn
    assert (fake.world.date - __import__("datetime").date.fromisoformat(start)).days == 7
    assert state.date == fake.date
    assert fake.speed_log == [3]                         # resumed through the console at speed 3


def test_the_pause_is_the_space_key_because_typing_the_command_runs_the_game_on(tmp_path):
    _, adapter, fake, env = stack(tmp_path)
    env.advance(3)
    assert fake.keylog[-1] == "space"
    assert adapter.writer.clock.used_console == 0


def test_set_game_speed_is_kept_for_the_next_advance(tmp_path):
    _, adapter, fake, env = stack(tmp_path)
    result = env.act(ActionCall("set_game_speed", {"speed": 5}))
    assert result.ok and "paused" in result.message
    env.advance(5)
    assert fake.speed_log == [5]
    assert env.act(ActionCall("set_game_speed", {"speed": 0})).message == "Paused."


def test_a_console_that_does_not_take_gamespeed_falls_back_to_the_key(tmp_path):
    _, adapter, fake, env = stack(tmp_path)
    fake._gamespeed = lambda rest: None                  # the game ignores the command
    adapter.writer.clock.resume()
    assert not fake.paused
    assert adapter.writer.clock.used_keys == 1
    adapter.writer.clock.pause()
    assert fake.paused


def test_console_only_clock_says_so_when_the_date_does_not_move(tmp_path):
    _, adapter, fake, env = stack(tmp_path, headless_clock="console")
    fake._gamespeed = lambda rest: None
    with pytest.raises(ClockUnobservable, match="did not move"):
        adapter.writer.clock.resume()


def test_a_game_that_cannot_be_paused_is_an_error_not_a_belief(tmp_path):
    _, adapter, fake, env = stack(tmp_path)
    adapter.writer.clock.resume()
    assert not fake.paused
    fake._gamespeed = lambda rest: None
    fake._key = lambda name, mods: None                  # and the key does nothing either
    with pytest.raises(ClockUnobservable, match="kept moving"):
        adapter.writer.clock.pause()


def test_keys_only_clock_works_without_the_console(tmp_path):
    _, adapter, fake, env = stack(tmp_path, headless_clock="keys")
    env.advance(3)
    assert fake.speed_log == [] and fake.paused
    assert fake.runs == []


def test_a_player_owned_clock_is_never_touched(tmp_path):
    _, adapter, fake, env = stack(tmp_path, clock_owner="player")
    fake.paused = False                                  # the person is playing
    env.advance(3)
    assert not fake.paused and fake.keylog == [] and fake.speed_log == []


def test_clock_modes_are_validated():
    with pytest.raises(ValueError, match="clock mode"):
        HeadlessClock(None, None, mode="telepathy")


# --- no mouse, no screen ------------------------------------------------------------------


def test_the_mouse_actions_are_not_offered_and_the_prompt_says_why(tmp_path):
    config, adapter, fake, env = stack(tmp_path)
    assert MOUSE_ACTIONS == {"start_research", "delegate_army_to_ai"}
    assert not env.allowed_actions & MOUSE_ACTIONS
    assert "set_national_focus" in env.allowed_actions
    prompt = system_for(config, env.index)
    assert "headless" in prompt and "start_research" in prompt and "delegate_army_to_ai" in prompt
    assert "headless" not in system_for(HarnessConfig(), None)


def test_a_mouse_action_called_anyway_is_refused_and_nothing_is_sent(tmp_path):
    _, _, fake, env = stack(tmp_path)
    for call in (ActionCall("start_research", {"technology": "infantry_weapons2"}),
                 ActionCall("delegate_army_to_ai", {"army": "all", "delegate": True})):
        assert env.act(call).error_kind == "unsupported"
    assert fake.keylog == []


def test_the_driver_has_no_mouse(tmp_path):
    _, adapter, _, _ = stack(tmp_path)
    driver = adapter.writer.router.driver
    with pytest.raises(MouseDisabled):
        driver.click("map.centre")


def test_a_recorded_script_that_clicks_is_dropped_and_the_focus_script_stays(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "ui_scripts.json").write_text(json.dumps({"schema": 1, "scripts": {
        "start_research": [{"press": "research"}, {"click": "research.free_slot"}],
        "set_national_focus": [{"press": "national_focus"}, {"click": "focus.search"}]}}),
        encoding="utf-8")
    _, adapter, _, env = stack(tmp_path)
    driver = adapter.writer.router.driver
    assert set(driver.scripts) == {"set_national_focus"}
    assert all(step.kind != "click" for step in driver.scripts["set_national_focus"])
    kept, dropped = keyboard_only(driver.scripts)
    assert dropped == [] and "start_research" not in env.allowed_actions


def test_a_console_action_cannot_be_moved_to_the_ui_channel_in_headless(tmp_path):
    with pytest.raises(ValueError, match="needs the mouse"):
        build_adapter(config_for(tmp_path, action_channels={"queue_construction": "ui"}))


def test_an_idle_research_slot_does_not_wake_a_planner_that_cannot_fill_it(tmp_path):
    config, *_ = stack(tmp_path)
    assert config.wake_on_free_research_slot is False


def test_a_headless_run_never_imports_the_screen_or_the_gui_libraries(tmp_path):
    code = (
        "import sys, json\n"
        "from pathlib import Path\n"
        "from hoi4_harness.adapters import build_adapter\n"
        "from hoi4_harness.config import HarnessConfig\n"
        "from hoi4_harness.env import HOI4Env\n"
        "from hoi4_harness.types import ActionCall\n"
        f"d = Path({str(tmp_path)!r})\n"
        "c = HarnessConfig(adapter='headless', fake_game=True, fake_game_dir=d/'f', run_dir=d/'r',"
        " dry_run=False)\n"
        "env = HOI4Env(build_adapter(c), c, index=c.build_index()); env.reset()\n"
        "env.act(ActionCall('hire_advisor', {'advisor_id': 'SWE_demo_minister'}))\n"
        "env.act(ActionCall('set_national_focus', {'focus_id': 'industrial_effort'}))\n"
        "env.advance(3)\n"
        "bad = [m for m in ('pyautogui', 'mss', 'PIL', 'hoi4_harness.adapters.screen') "
        "if m in sys.modules]\n"
        "print(json.dumps(bad))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**__import__("os").environ, "PYTHONPATH": str(ROOT / "src")},
                         timeout=120)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


# --- the savegame reader ----------------------------------------------------------------


def test_the_savegame_reader_can_be_chosen_and_then_has_no_telemetry_to_refresh(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()
    adapter = build_adapter(config_for(tmp_path, headless_reader="savegame", save_dir=saves))
    assert adapter.reader.info().name == "savegame"
    assert adapter.writer.router.console.config.refresh is False


def test_an_unknown_reader_or_clock_is_refused(tmp_path):
    with pytest.raises(ValueError, match="headless_reader"):
        build_adapter(config_for(tmp_path, headless_reader="screen"))
    with pytest.raises(ValueError, match="headless_clock"):
        build_adapter(config_for(tmp_path, headless_clock="mouse"))


# --- the command line -----------------------------------------------------------------------


def test_the_cli_plays_a_scripted_campaign_against_the_fake_game(tmp_path, capsys):
    from hoi4_harness.cli import main

    script = tmp_path / "script.json"
    script.write_text(json.dumps([{"calls": [
        {"name": "hire_advisor", "arguments": {"advisor_id": "SWE_demo_minister"}},
        {"name": "advance_time", "arguments": {"days": 7}}]}]), encoding="utf-8")
    code = main(["play", "--headless", "--fake-game", "--fake-game-dir", str(tmp_path / "f"),
                 "--run-dir", str(tmp_path / "run"), "--script", str(script), "--turns", "2",
                 "--days", "7", "--quiet"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["turns"] == 2 and report["actions_ok"] >= 1
    transcript = read_transcript(tmp_path / "run" / "transcript.jsonl")
    assert any(name == "hire_advisor" and ok for name, ok, *_ in results(transcript))


def test_the_demo_profile_loads_and_runs(tmp_path, capsys):
    from hoi4_harness.cli import main

    code = main(["play", "--profile", str(ROOT / "profiles" / "headless-fake.json"),
                 "--fake-game-dir", str(tmp_path / "f"), "--run-dir", str(tmp_path / "run"),
                 "--turns", "4", "--quiet"])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["turns"] == 4
    fake_log = (tmp_path / "f" / "user" / "logs" / "game.log").read_text(encoding="utf-8")
    assert "kind=14|detail=protect POL" in fake_log


def test_observe_reads_one_brief_from_the_fake_game(tmp_path, capsys):
    from hoi4_harness.cli import main

    assert main(["observe", "--headless", "--fake-game", "--fake-game-dir", str(tmp_path / "f"),
                 "--run-dir", str(tmp_path / "run")]) == 0
    assert "## SWE -- 1936-01-01" in capsys.readouterr().out


def test_the_headless_flags_are_in_the_parser():
    from hoi4_harness.cli import build_parser

    args = build_parser().parse_args(["play", "--headless", "--fake-game-files", "somewhere"])
    assert args.headless and args.fake_game_files == "somewhere"


# --- the fake as its own process -------------------------------------------------------------


def test_the_harness_plays_against_a_fake_game_running_on_its_own_clock(tmp_path):
    """Two ends that only share files: the game on a thread driven by the wall clock
    (50x fast), the harness in this one with its sleeps shortened to match."""
    base = tmp_path / "game"
    fake = FakeGame(base / "user", index=demo_index(base / "playset"), variance=0)
    stop = threading.Event()
    worker = threading.Thread(target=fake.serve, args=(stop,), kwargs={"time_scale": 20.0},
                              daemon=True)
    worker.start()
    try:
        config = HarnessConfig(
            adapter="headless", fake_game_files=base / "user", game_dir=base / "playset",
            run_dir=tmp_path / "run", dry_run=False, operational_control="ai",
            headless_time_scale=0.05, headless_poll_seconds=0.5, clock_probe_seconds=2.0,
            country="SWE")
        adapter = build_adapter(config)
        env = HOI4Env(adapter, config, index=config.build_index())
        env.reset()
        hired = env.act(ActionCall("hire_advisor", {"advisor_id": "SWE_demo_minister"}))
        raised = env.act(ActionCall("set_ai_directive", {"directive": "protect", "target": "POL"}))
        state = env.advance(5)
    finally:
        stop.set()
        worker.join(timeout=10)
    assert hired.ok and raised.ok, (hired.message, raised.message)
    assert fake.advisors and fake.directives == ["protect POL"]
    assert state.date > "1936-01-04"
    assert fake.paused


# --- the exact clock (pause_in_hours) ---------------------------------------------------


def test_the_exact_clock_lets_the_game_stop_itself_after_the_days_asked(tmp_path):
    _, adapter, fake, env = stack(tmp_path, headless_clock="exact")
    start = fake.world.date
    state = env.advance(7)
    assert (fake.world.date - start).days == 7 and state.date == fake.date
    # No pause key raced the clock: the only spaces are the ones inside `run llmb_turn.txt`.
    assert fake.paused and fake.keylog.count("space") == len(fake.runs)
    assert ["pause_in_hours 180", "gamespeed 3"] in fake.runs


def test_the_exact_clock_still_pauses_a_game_a_critical_event_cut_short(tmp_path):
    _, adapter, fake, env = stack(tmp_path, headless_clock="exact", start_date="1939-08-30")
    state = env.advance(10)                       # Germany invades Poland on 1 September
    assert any(e.kind == "war_declared" for e in state.events)
    assert fake.date < "1939-09-09" and fake.paused   # stopped by the harness, not the deadline
    assert fake.keylog.count("space") > len(fake.runs)           # the pause key, pressed


def test_observe_without_a_log_says_so_instead_of_a_traceback(tmp_path, capsys, monkeypatch):
    from hoi4_harness.cli import main

    monkeypatch.setenv("HOI4_LOG_PATH", str(tmp_path / "nope.log"))
    assert main(["observe", "--headless", "--country", "SWE", "--run-dir", str(tmp_path)]) == 1
    assert "No game.log found" in capsys.readouterr().err
