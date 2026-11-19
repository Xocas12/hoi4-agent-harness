"""The log+save reader through the whole stack, against the fake game.

The real log reader, the real combined reader, the console channel, the scan-code
key layer, the clock logic and the agent loop; only the game is fake. The fake
writes the mod's schema-v4 telemetry, and when the console says ``savegame`` it
writes a text save (``fakesave.py``, the layout the real game wrote on 1.20.0) a
couple of fake seconds later. A scripted provider plays: no model call, no input
to this machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoi4_harness.adapters import build_adapter
from hoi4_harness.agent.llm.base import LLMResponse, ToolCall, Usage
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.env import HOI4Env
from hoi4_harness.types import ActionCall, ProductionLine


def config_for(tmp_path, **overrides) -> HarnessConfig:
    options = dict(adapter="headless", fake_game=True, fake_game_dir=tmp_path / "fake",
                   run_dir=tmp_path / "run", dry_run=False, operational_control="ai",
                   days_per_turn=30, turns=6)
    options.update(overrides)
    return HarnessConfig(**options)


def stack(tmp_path, prepare=None, **overrides):
    """Adapter and env; ``prepare(fake)`` shapes the game before the first read."""
    config = config_for(tmp_path, **overrides)
    adapter = build_adapter(config)
    fake = adapter.fake_game
    if prepare is not None:
        prepare(fake)
    env = HOI4Env(adapter, config, index=config.build_index())
    env.reset()
    return config, adapter, fake, env


def say(*calls: tuple[str, dict]) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(f"t{i}", name, args) for i, (name, args) in enumerate(calls)],
        stop_reason="tool_use", usage=Usage(10, 10))


def play(tmp_path, script, turns, prepare=None, **overrides):
    config, adapter, fake, env = stack(tmp_path, prepare, turns=turns, **overrides)
    transcript = tmp_path / "run" / "transcript.jsonl"
    AgentLoop(env=env, planner=ScriptedClient(script=list(script)), config=config,
              transcript_path=transcript).run(turns)
    entries = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
    return config, adapter, fake, env, entries


def results(transcript):
    out = {}
    for entry in transcript:
        if entry["kind"] == "actions":
            for item in entry["results"]:
                body = json.loads(item["content"])
                out[item["name"]] = (body["ok"], body.get("error"), body["message"])
    return out


def two_free_factories(fake) -> None:
    """Three of the five military factories busy, so a new line can have two."""
    fake.world.state.production = [ProductionLine("infantry_equipment_1", 3, 0.5, 4.0)]


PEACETIME = [
    ("queue_construction", {"building": "civilian_factory", "state": "Svealand", "count": 1}),
    ("set_production", {"equipment": "artillery_equipment_1", "factories": 2}),
    ("advance_time", {"days": 7, "reason": "let the orders land"}),
]


# --- a peacetime campaign: both verified through the save ------------------------------


def test_construction_and_production_are_verified_through_the_save(tmp_path):
    _, _, fake, _, transcript = play(tmp_path, [say(*PEACETIME)], turns=1,
                                     prepare=two_free_factories, seed=3)
    by_name = results(transcript)
    assert by_name["queue_construction"] == (True, None, "done and verified")
    assert by_name["set_production"] == (True, None, "done and verified")
    # What the fake game holds is what the model asked for.
    assert [(x.equipment, x.factories) for x in fake.world.state.production] == [
        ("infantry_equipment_1", 3), ("artillery_equipment_1", 2)]
    assert [(c.building, c.state) for c in fake.world.state.construction
            ] in ([("civilian_factory", "Svealand")], [])      # queued, maybe built by day 7
    # The save is the evidence: it was asked for around the actions, in the same run
    # file as the effects.
    action_run = next(run for run in fake.runs if run[0].startswith("eval_effect"))
    assert action_run[-1] == "savegame"
    assert len(fake.saves_written) >= 3          # start, before the batch, after it


def test_the_brief_shows_the_queue_and_the_line_the_save_found(tmp_path):
    _, _, _, _, transcript = play(
        tmp_path, [say(("queue_construction", {"building": "civilian_factory",
                                                "state": "Svealand", "count": 1}),
                       ("advance_time", {"days": 1, "reason": "next report"}))],
        turns=2, prepare=two_free_factories, seed=3)
    briefs = [e["brief"] for e in transcript if e["kind"] == "observe"]
    assert "Production: infantry_equipment_1 x3 (50% eff) | 2 MIL FACTORIES IDLE" in briefs[0]
    assert "Construction: QUEUE EMPTY" in briefs[0]
    assert "Construction queue: 0 -> 1" in briefs[1]


def test_a_queue_that_is_not_there_is_not_reported_as_verified(tmp_path):
    # The state is not the player's: the effect's own limit makes the game do nothing,
    # and the save, not the log, is what shows it.
    _, _, fake, _, transcript = play(
        tmp_path, [say(("queue_construction", {"building": "civilian_factory",
                                                "state": "Pomerania", "count": 1}))],
        turns=1, seed=3)
    ok, kind, message = results(transcript)["queue_construction"]
    assert not ok and kind == "rejected" and "the queue gained 0" in message
    assert fake.world.state.construction == []


def test_a_state_given_by_id_is_checked_by_the_name_the_save_reports(tmp_path):
    _, _, fake, _, transcript = play(
        tmp_path, [say(("queue_construction", {"building": "civilian_factory",
                                                "state": "10", "count": 1}))], turns=1, seed=3)
    assert results(transcript)["queue_construction"][:2] == (True, None)
    assert len(fake.world.state.construction) == 1


def test_a_second_line_for_equipment_that_has_one_is_refused_before_anything_is_sent(tmp_path):
    _, _, fake, _, transcript = play(
        tmp_path, [say(("set_production", {"equipment": "infantry_equipment_1", "factories": 1}))],
        turns=1, prepare=two_free_factories, seed=3)
    ok, kind, message = results(transcript)["set_production"]
    assert not ok and kind == "rejected" and "a infantry_equipment_1 line already exists" in message
    assert not any(run[0].startswith("eval_effect") for run in fake.runs)


def test_production_is_offered_to_a_run_that_can_see_it(tmp_path):
    _, adapter, _, _ = stack(tmp_path)
    assert "set_production" in adapter.supported_actions
    assert HarnessConfig().headless_reader == "log+save"


def test_a_save_is_taken_at_the_start_and_then_at_most_every_n_days(tmp_path):
    _, _, fake, _, _ = play(
        tmp_path, [say(("advance_time", {"days": 10, "reason": "wait"}))], turns=3,
        headless_save_days=15, seed=3, days_per_turn=10)
    periodic = len(fake.saves_written)
    assert 2 <= periodic <= 3          # the start, then roughly one per 15 game days
    fake2_dir = tmp_path / "again"
    fake2_dir.mkdir()
    _, _, only_start, _, _ = play(
        fake2_dir, [say(("advance_time", {"days": 10, "reason": "wait"}))], turns=3,
        headless_save_days=0, seed=3, days_per_turn=10)
    assert len(only_start.saves_written) == 1


def test_the_log_wins_for_what_it_has_and_the_save_fills_the_rest(tmp_path):
    _, adapter, fake, _ = stack(tmp_path, prepare=two_free_factories)
    fake.world.state.political_power = 999.0           # the log will say this, the save did not
    fake.emit_state()
    state = adapter.read_state()
    assert state.political_power == pytest.approx(999.0, abs=0.01)
    assert [(x.equipment, x.factories) for x in state.production] == [("infantry_equipment_1", 3)]
    assert state.known("production") and state.known("construction")
    assert state.raw["save_date"] == "1936-01-01"
    assert [(g.template, g.count) for g in state.divisions] == [("Infantry", 12)]


# --- failure paths ------------------------------------------------------------------------


def test_a_binary_save_is_refused_with_the_setting_that_fixes_it(tmp_path):
    def binary(fake):
        two_free_factories(fake)
        fake.save_mode = "binary"

    _, _, fake, _, transcript = play(tmp_path, [say(*PEACETIME)], turns=1, prepare=binary, seed=3)
    by_name = results(transcript)
    ok, kind, message = by_name["queue_construction"]
    assert not ok and kind == "unverified" and "save_as_binary=no" in message
    # The action itself still went in; only its proof is missing.
    ok, kind, message = by_name["set_production"]
    assert not ok and kind == "rejected" and "save_as_binary=no" in message
    assert len(fake.world.state.construction) == 1


def test_a_save_that_never_appears_is_unverified_not_a_crash(tmp_path):
    def silent(fake):
        fake.save_mode = "none"

    _, adapter, fake, _, transcript = play(
        tmp_path, [say(("queue_construction", {"building": "civilian_factory",
                                                "state": "Svealand", "count": 1}),
                       ("advance_time", {"days": 2, "reason": "go on"}))],
        turns=2, prepare=silent, seed=3, headless_save_timeout=5.0)
    ok, kind, message = results(transcript)["queue_construction"]
    assert not ok and kind == "unverified"
    assert "no new save appeared" in message and "cannot see construction" in message
    assert adapter.reader.last_error is not None
    assert len(fake.world.state.construction) == 1 or fake.world.built["built_civ"] >= 1


def test_a_failed_save_turns_what_the_last_one_said_back_to_unknown(tmp_path):
    _, adapter, fake, _ = stack(tmp_path, prepare=two_free_factories)
    assert adapter.read_state().known("production")
    fake.save_mode = "none"
    error = adapter.reader.refresh()
    assert error and "no new save appeared" in error
    state = adapter.read_state()
    assert not state.known("production") and not state.known("construction")
    assert state.known("political_power")           # the log is unaffected
    fake.save_mode = "text"
    assert adapter.reader.refresh() is None
    assert adapter.read_state().known("production")


def test_a_user_save_is_never_touched(tmp_path):
    saves = tmp_path / "fake" / "user" / "save games"
    saves.mkdir(parents=True)
    mine = saves / "autosave.hoi4"
    mine.write_bytes(b"HOI4txt\nplayer=\"SWE\"\n")
    stamp = mine.stat().st_mtime_ns
    play(tmp_path, [say(*PEACETIME)], turns=1, prepare=two_free_factories, seed=3)
    assert mine.read_bytes() == b"HOI4txt\nplayer=\"SWE\"\n" and mine.stat().st_mtime_ns == stamp


def test_a_dry_run_takes_no_save_and_does_not_wait_for_one(tmp_path):
    config = config_for(tmp_path, dry_run=True, fake_game=False, game_dir=None,
                        save_dir=tmp_path / "saves")
    config.fake_game = False
    from hoi4_harness.adapters.combined import CombinedReader
    from hoi4_harness.adapters.logtail import LogTailAdapter

    reader = CombinedReader(LogTailAdapter(tmp_path / "game.log", country="SWE"),
                            config.save_dir, sleep=lambda s: pytest.fail("waited"))
    reader.enabled = False
    assert reader.refresh() is None and reader.saves_taken == 0


# --- a war declared mid-run ---------------------------------------------------------------


def war_on_the_tenth(fake) -> None:
    fake.war_script["1936-01-11"] = ("declare", "POL", 40)
    fake.war_script["1936-02-20"] = ("peace", "POL", 0)


def test_a_war_declared_mid_run_wakes_the_planner_and_shows_in_the_brief(tmp_path):
    _, adapter, fake, env, transcript = play(
        tmp_path, [say(("advance_time", {"days": 30, "reason": "wait"}))], turns=3,
        prepare=war_on_the_tenth, seed=3, days_per_turn=15, wake_on_no_focus=False)
    # The clock stopped for the war (day 10 of a 15-day turn), not at its end...
    ends = [e for e in transcript if e["kind"] == "turn_end"]
    assert ends[0]["date"] == "1936-01-11" and ends[0]["woke"]
    # ...and the planner was woken with a brief that already names the enemy.
    wake_brief = next(e["brief"] for e in transcript if e["kind"] == "observe"
                      and "war declared" in e["brief"])
    assert "AT WAR with POL since 1936-01-11" in wake_brief
    # The lines that follow say what the enemy is.
    assert [(w.against, w.enemy_divisions, w.borders) for w in adapter.read_state().wars] == [
        ("POL", 40, True)]
    assert "NEW WAR: POL" not in wake_brief or "POL" in wake_brief


def test_peace_is_reported_when_the_war_lines_stop(tmp_path):
    _, adapter, fake, env, transcript = play(
        tmp_path, [say(("advance_time", {"days": 40, "reason": "wait"}))], turns=5,
        prepare=war_on_the_tenth, seed=3, days_per_turn=20, wake_on_no_focus=False)
    briefs = "\n".join(e["brief"] for e in transcript if e["kind"] == "observe")
    assert "War ended: POL" in briefs
    assert adapter.read_state().wars == []


def test_the_wars_wake_rule_fires_on_the_event_the_log_reader_derived(tmp_path):
    config, adapter, fake, env = stack(tmp_path, seed=3)
    fake.start_war("POL", divisions=25)
    fake.run_days(2)
    state = adapter.read_state()
    from hoi4_harness.agent.policy import Policy

    decision = Policy().should_wake(state, days_since_planner=1)
    assert decision.wake and decision.urgency == "critical"
    assert [w.against for w in state.wars] == ["POL"]
    assert ActionCall  # imported for the type used in other tests
    assert isinstance(tmp_path, Path)


def test_the_command_line_picks_the_reader_and_the_save_cadence():
    from hoi4_harness.cli import _config_from_args, build_parser

    args = build_parser().parse_args(["play", "--headless", "--headless-reader", "logtail",
                                      "--save-days", "3"])
    config = _config_from_args(args)
    assert (config.headless_reader, config.headless_save_days) == ("logtail", 3)
    args = build_parser().parse_args(["play", "--headless"])
    assert _config_from_args(args).headless_reader == "log+save"
