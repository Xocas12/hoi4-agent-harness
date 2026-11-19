"""Rival mode against a fake game with a person in it (fakerival.py).

The real adapters, loop, queue, key guard and supervisor run against ``FakeRivalGame``: SWE is an
AI country the harness steers, GER is the person's, and the person pauses, unpauses, types,
looks away and declares war on a schedule of wall-clock seconds. What this proves is the harness
around the rules (it marks, reads only its own country, queues, waits for a window, stops when the
person comes back, unmarks); whether the real game accepts a command, and whether a pause is
detectable the way the real log shows it, is docs/rival.md's live plan.
"""

from __future__ import annotations

import json
from pathlib import Path

from hoi4_harness.fakerival import HUMAN_ENEMY, HUMAN_PP, human_cycle
from hoi4_harness.handover import GRANT_FILENAME
from hoi4_harness.rival.runner import run_rival
from hoi4_harness.types import ActionCall
from rival_helpers import LazyClient, acts, build_env, rival_config, say

FIX = Path(__file__).parent / "fixtures" / "modctx"
INVADE = ("set_ai_directive", {"directive": "invade", "target": "POL"})
THEATER = ("set_ai_posture", {"posture": "offensive", "theater": "POL"})


def nap(days: int = 30):
    return ("advance_time", {"days": days, "reason": "let it run"})


def play(tmp_path, script=None, wakes=(), *, until="1936-12-01", fake_setup=None, nap_days=30,
         config=None, **overrides):
    """One rival run; ``wakes`` are the model's replies to its first wakes (then it naps)."""
    if config is None:
        config = rival_config(tmp_path, script=script, **overrides)
    else:
        for key, value in overrides.items():
            setattr(config, key, value)
    env = build_env(config)
    fake = env.adapter.fake_game
    if fake_setup is not None:
        fake_setup(fake, env, config)
    client = LazyClient(script=[say(text, *calls) for text, calls in wakes], nap=nap_days)
    report, summary = run_rival(config, planner=client, env=env, until=until)
    return env, fake, summary, client, tmp_path


def records(tmp_path, kind):
    path = tmp_path / "run" / "transcript.jsonl"
    return [r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())
            if r["kind"] == kind]


def tool_messages(tmp_path, name):
    """What the model was told when it called ``name``, from the transcript."""
    return [json.loads(r["content"])["message"] for a in records(tmp_path, "actions")
            for r in a["results"] if r["name"] == name]


def rival_log(tmp_path):
    return (tmp_path / "run" / "rival.log").read_text(encoding="utf-8")


def game_log(fake):
    return fake.log_path.read_text(encoding="utf-8")


def run_with(fake, needle):
    """The index of the first console run holding ``needle``."""
    return next(i for i, run in enumerate(fake.runs) if any(needle in line for line in run))


# --- marking and reading --------------------------------------------------------------------------


def test_the_start_marks_the_rival_in_a_safe_window_and_proves_it_from_the_log(tmp_path):
    env, fake, summary, *_ = play(tmp_path, acts((0, "resume"), (20, "pause"), (500, "resume")),
                                  until="1936-02-15")
    assert fake.runs[0][0] == "effect SWE llm_bridge_mark_harness"
    assert fake.run_walls[0] >= 20 + 10              # the date had stood still for 10 s
    lines = game_log(fake).splitlines()
    mine = [ln for ln in lines if "|tag=SWE|" in ln or "tag=SWE|" in ln]
    assert "|evt|tag=SWE" in mine[0] and "kind=16" in mine[0]       # the mark event, first
    assert any("|state|" in ln for ln in mine[1:])                   # then its state lines
    assert "marked: the mark event and a state line for SWE are in the log" in rival_log(tmp_path)
    assert summary["reached_end_date"]


def test_a_rival_that_already_reports_is_not_marked_again(tmp_path):
    def premark(fake, env, config):
        fake.flags.add("llmb_harness")
        fake.emit_state()
    env, fake, *_ = play(tmp_path, human_cycle(run=30, pause=60), until="1936-02-20",
                         fake_setup=premark)
    assert not any("llm_bridge_mark_harness" in line for run in fake.runs for line in run)
    assert "already reports to the log: marking skipped" in rival_log(tmp_path)


def test_the_rival_is_unmarked_on_exit_in_a_window(tmp_path):
    env, fake, *_ = play(tmp_path, human_cycle(run=30, pause=60), until="1936-03-15",
                         rival_exit_wait_seconds=500)
    assert fake.runs[-1] == ["effect SWE llm_bridge_unmark_harness"]
    assert "llmb_harness" not in fake.flags
    assert "|evt|tag=SWE" in game_log(fake) and "kind=17" in game_log(fake)
    assert "SWE unmarked" in rival_log(tmp_path)


def test_without_a_window_at_exit_it_says_what_to_type_and_leaves_the_mark(tmp_path):
    env, fake, summary, *_ = play(tmp_path, acts((0, "resume"), (20, "pause"), (45, "resume")),
                                  until="1936-04-01", rival_exit_wait_seconds=20)
    assert "llmb_harness" in fake.flags
    assert "To undo it type in the console: effect SWE llm_bridge_unmark_harness" in rival_log(tmp_path)
    assert summary["reached_end_date"]


def test_the_brief_is_the_rivals_own_and_nothing_of_the_persons_leaks_into_it(tmp_path):
    env, fake, *_ = play(tmp_path, human_cycle(run=30, pause=60), until="1936-06-01")
    # the person's country really is in the shared log, with numbers that are theirs...
    assert f"pp={HUMAN_PP}" in game_log(fake) and f"enemy={HUMAN_ENEMY}" in game_log(fake)
    # ...and the reader keeps to the rival: its brief never shows them.
    briefs = [r["brief"] for r in records(tmp_path, "observe")]
    assert briefs and all(b.startswith(("## SWE", "## SWE")) or "## SWE" in b for b in briefs)
    for brief in briefs:
        assert "777" not in brief and HUMAN_ENEMY not in brief and "## GER" not in brief
    assert env.read_state().country == "SWE"


def test_a_war_the_person_declares_on_the_rival_wakes_it_and_shows_in_its_brief(tmp_path):
    script = acts((0, "resume"), (20, "pause"), (40, "resume"), (60, "war", "40"), (100, "pause"),
                  (160, "resume"))
    env, fake, *_ = play(tmp_path, script, wakes=[("go", [nap(90)])], nap_days=90,
                         until="1936-09-01")
    woke = [r for r in records(tmp_path, "observe") if "war declared: GER on SWE" in r["brief"]]
    assert woke and "AT WAR with GER" in woke[0]["brief"]
    assert HUMAN_ENEMY not in woke[0]["brief"]       # their other war is none of the rival's business
    assert "wake: critical event: war declared: GER on SWE" in rival_log(tmp_path)


# --- acting: queued while it runs, typed in the next pause, in the rival's scope ------------------


def test_actions_queue_while_the_game_runs_and_go_out_in_the_next_pause_as_one_file(tmp_path):
    script = human_cycle(run=40, pause=40, cycles=30)
    env, fake, summary, *_ = play(
        tmp_path, script, wakes=[("quiet", [nap(30)]), ("now", [INVADE, THEATER, nap(60)])],
        until="1936-10-01")
    queued = tool_messages(tmp_path, "set_ai_directive")
    assert queued and "Queued as #1" in queued[0] and "NOT done yet" in queued[0]
    # one run file, holding both actions, typed inside a pause (never while the game ran)
    index = run_with(fake, "llm_bridge_raise_invade")
    assert any("llm_bridge_raise_theater_offensive" in ln for ln in fake.runs[index])
    wall = fake.run_walls[index]
    pauses = [(t.at, n.at) for t, n in zip(
        [a for a in script if a.kind == "pause"], [a for a in script if a.kind == "resume"][1:],
        strict=False)]
    assert any(start + 10 <= wall < end for start, end in pauses)
    assert fake.directives == ["invade POL"]
    window = records(tmp_path, "handover")[0]
    assert len(window["ran"]) == 2 and window["ran"][0]["queued_on"] < window["ran"][0]["ran_on"]


def test_the_harness_acts_only_in_the_rivals_scope_and_never_touches_the_clock(tmp_path):
    env, fake, *_ = play(tmp_path, human_cycle(run=30, pause=60),
                         wakes=[("go", [INVADE, THEATER,
                                        ("hire_advisor", {"advisor_id": "SWE_demo_minister"}),
                                        nap(30)])], until="1936-08-01")
    lines = [line for run in fake.runs for line in run]
    assert lines and all(line.startswith(("effect SWE ", "eval_effect SWE = ")) for line in lines)
    assert not any("GER" in line for line in lines)
    assert fake.errors == []
    assert fake.speed_log == [] and fake.pause_in is None            # no gamespeed, no pause_in_hours
    assert not any("human_ai" in line or "gamespeed" in line for line in lines)
    assert fake.stray_keys == []                                      # no key but inside a console
    assert "escape" not in fake.keylog
    assert fake.advisors.get("political_advisor") == ["SWE_demo_minister"]   # SWE's own PP and slot


def test_take_decision_goes_out_under_the_rivals_scope_with_the_decisions_own_conditions(tmp_path):
    config = rival_config(tmp_path, script=human_cycle(run=30, pause=60), game_dir=FIX / "game")
    env, fake, *_ = play(tmp_path, config=config, until="1936-06-01",
                         wakes=[("go", [("take_decision", {"decision_id": "swe_cheap"}), nap(30)])])
    line = next(ln for run in fake.runs for ln in run if "activate_decision = swe_cheap" in ln)
    assert line.startswith("eval_effect SWE = { if = { limit = { ")
    assert "add_political_power = -25" in line


# --- stale: re-checked when the window opens ------------------------------------------------------


def test_an_action_the_world_overtook_while_it_waited_is_reported_stale_not_typed(tmp_path):
    # GER declares war on SWE before the first window: "protect GER" no longer makes sense.
    script = acts((0, "resume"), (20, "pause"), (21, "war", "40"), (60, "resume"), (100, "pause"),
                  (400, "resume"))
    env, fake, *_ = play(tmp_path, script, until="1936-09-01",
                         wakes=[("protect", [("set_ai_directive",
                                              {"directive": "protect", "target": "GER"}), nap(30)])])
    window = records(tmp_path, "handover")[0]
    assert window["ran"] == [] and window["stale"][0]["reason"].startswith("at war with GER now")
    assert fake.directives == []                      # nothing was typed for it
    assert not any("llm_bridge_raise_protect" in ln for run in fake.runs for ln in run)
    assert "1 stale: set_ai_directive" in rival_log(tmp_path)


def test_an_action_older_than_the_stale_days_is_not_typed_when_the_person_finally_pauses(tmp_path):
    # the person plays on for a long stretch after the model asked, and the plan is stale
    script = acts((0, "resume"), (20, "pause"), (40, "resume"), (100, "pause"), (400, "resume"))
    env, fake, *_ = play(tmp_path, script, until="1936-12-01", rival_stale_days=20,
                         wakes=[("quiet", [nap(30)]),
                                ("go", [("set_ai_posture", {"posture": "defensive"}), nap(30)])])
    reports = records(tmp_path, "handover")
    stale = [s for r in reports for s in r["stale"]]
    assert stale and "queued" in stale[0]["reason"] and "days ago" in stale[0]["reason"]
    assert not any("set_posture_defensive" in ln for run in fake.runs for ln in run)


# --- the person comes back ------------------------------------------------------------------------


def window_script(*extra):
    """The person pauses at 40 and stays away until 300, so the windows fall in 50..300."""
    return acts((0, "resume"), (40, "pause"), (300, "resume"), *extra)


def first_directive_run_wall(tmp_path):
    """Learn when the window that types the directive runs, from a run with a quiet person."""
    _, fake, *_ = play(tmp_path / "learn", window_script(), until="1936-06-01",
                       wakes=[("go", [INVADE, nap(30)])])
    return fake.run_walls[run_with(fake, "llm_bridge_raise_invade")]


def test_a_person_who_types_mid_sequence_stops_it_before_enter_and_nothing_runs(tmp_path):
    wall = first_directive_run_wall(tmp_path)
    (tmp_path / "go").mkdir()
    # the same game, but the person presses a key one second before the file would have run
    script = window_script((wall - 1.0, "type", "x"))
    env, fake, *_ = play(tmp_path / "go", script, until="1936-06-01",
                         wakes=[("go", [INVADE, nap(30)])])
    log = rival_log(tmp_path / "go")
    assert "stopped before it finished (the person started using the keyboard or mouse)" in log
    window = records(tmp_path / "go", "handover")[0]
    assert window["aborted"] and window["left_queued"] == 1 and window["ran"] == []
    # nothing ran in the stopped window; the action went out in the next one, once
    runs = [i for i, run in enumerate(fake.runs) if any("raise_invade" in ln for ln in run)]
    assert len(runs) == 1 and fake.run_walls[runs[0]] > wall + 4
    assert fake.directives == ["invade POL"]
    assert not fake.console_open and fake.line == ""      # the console we opened is closed again
    assert fake.errors == [] and "escape" not in fake.keylog


def test_control_without_the_guard_the_persons_key_lands_in_our_console_line(tmp_path, monkeypatch):
    """The failable half of the test above: with the guard off the same keypress corrupts the
    command the harness was typing, and the game is asked to open a file that does not exist."""
    from hoi4_harness.rival.safety import KeyWatch

    wall = first_directive_run_wall(tmp_path)
    (tmp_path / "go").mkdir()
    monkeypatch.setattr(KeyWatch, "before", lambda self, kind, text: None)
    env, fake, *_ = play(tmp_path / "go", window_script((wall - 1.0, "type", "x")),
                         until="1936-06-01", wakes=[("go", [INVADE, nap(30)])])
    assert any("Could not open file" in error for error in fake.errors)
    assert fake.directives == []                    # the action never reached the game


def test_a_person_who_unpauses_mid_sequence_stops_it_too(tmp_path):
    # (unpausing is itself a key press, so the keyboard rule usually fires first; the date
    # rule is the backstop for an unpause the idle clock did not see, and is pinned in
    # test_rival_safety.py)
    wall = first_directive_run_wall(tmp_path)
    (tmp_path / "go").mkdir()
    script = acts((0, "resume"), (40, "pause"), (wall - 1.0, "resume"), (wall + 60, "pause"))
    env, fake, *_ = play(tmp_path / "go", script, until="1936-06-01",
                         wakes=[("go", [INVADE, nap(30)])])
    window = records(tmp_path / "go", "handover")[0]
    assert window["aborted"] and window["ran"] == []
    assert "keyboard" in window["reason"] or "unpaused" in window["reason"]
    assert not fake.console_open


def test_a_window_waits_while_the_person_keeps_touching_the_keyboard(tmp_path):
    busy = [(t, "input") for t in range(42, 100, 3)]
    env, fake, *_ = play(tmp_path, acts((0, "resume"), (40, "pause"), *busy, (400, "resume")),
                         until="1936-02-15")
    assert fake.run_walls[0] >= 99 + 5                  # five quiet seconds after the last input
    assert "waiting: there was keyboard or mouse input" in rival_log(tmp_path)


def test_a_window_waits_for_the_game_to_be_in_front(tmp_path):
    env, fake, *_ = play(tmp_path, acts((0, "resume"), (40, "pause"), (41, "focus_away", "100"),
                                        (400, "resume")), until="1936-02-15")
    assert fake.run_walls[0] >= 141
    assert "waiting: the game window is not in front" in rival_log(tmp_path)
    assert fake.stray_keys == []


# --- the handover policy --------------------------------------------------------------------------


def grant(config, fake, at):
    path = Path(config.run_dir) / GRANT_FILENAME

    def give():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    fake.schedule_call(at, give)
    return path


def test_under_handover_nothing_is_typed_until_the_person_grants_and_the_grant_is_used_up(tmp_path):
    config = rival_config(tmp_path, script=acts((0, "resume")), rival_act_when="handover")
    def setup(fake, env, config):
        grant(config, fake, 5)                           # lets the start mark
        fake.schedule_call(100.0, lambda: (Path(config.run_dir) / GRANT_FILENAME).touch())
    env, fake, *_ = play(tmp_path, config=config, until="1936-12-01", fake_setup=setup,
                         wakes=[("go", [INVADE, nap(30)])], rival_exit_wait_seconds=5, rival_stale_days=0)
    index = run_with(fake, "llm_bridge_raise_invade")
    assert fake.run_walls[index] >= 100                  # only after the second grant
    assert not (Path(config.run_dir) / GRANT_FILENAME).exists()
    assert "waiting: waiting for `hoi4-harness handover`" in rival_log(tmp_path)


def test_taking_the_grant_back_mid_window_stops_it_and_keeps_the_queue(tmp_path):
    def run_once(root, withdraw_at=None):
        config = rival_config(root, script=acts((0, "resume")), rival_act_when="handover")

        def setup(fake, env, config):
            grant(config, fake, 5)
            grant(config, fake, 100)
            if withdraw_at is not None:
                fake.schedule_call(withdraw_at, lambda: (Path(config.run_dir) / GRANT_FILENAME).unlink())
            # a second grant later, for the window that finishes the job
            fake.schedule_call(160, lambda: (Path(config.run_dir) / GRANT_FILENAME).touch())
        return play(root, config=config, until="1936-12-01", fake_setup=setup,
                    wakes=[("go", [INVADE, nap(30)])], rival_exit_wait_seconds=5, rival_stale_days=0)

    learn = tmp_path / "learn"
    learn.mkdir()
    _, fake, *_ = run_once(learn)
    wall = fake.run_walls[run_with(fake, "llm_bridge_raise_invade")]
    go = tmp_path / "go"
    go.mkdir()
    env, fake, *_ = run_once(go, withdraw_at=wall - 1.0)
    window = records(go, "handover")[0]
    assert window["aborted"] and "took the keyboard back" in window["reason"] and window["ran"] == []
    runs = [i for i, run in enumerate(fake.runs) if any("raise_invade" in ln for ln in run)]
    assert len(runs) == 1 and fake.run_walls[runs[0]] >= 160      # the second grant finished it
    assert not fake.console_open


# --- the never policy -----------------------------------------------------------------------------


def test_under_never_nothing_is_ever_typed_and_the_model_is_told_it_is_advice(tmp_path):
    def premark(fake, env, config):           # the person typed the mark line themselves
        fake.flags.add("llmb_harness")
        fake.emit_state()
    env, fake, *_ = play(tmp_path, human_cycle(run=30, pause=60), until="1936-06-01",
                         fake_setup=premark, rival_act_when="never",
                         wakes=[("go", [INVADE, nap(30)])])
    assert fake.runs == [] and fake.keylog == [] and fake.directives == []
    (message,) = tool_messages(tmp_path, "set_ai_directive")
    assert "will NOT be run" in message
    window = records(tmp_path, "handover")[0]
    assert window["advised"][0]["action"] == "set_ai_directive" and window["ran"] == []
    assert "policy never" in rival_log(tmp_path) or "shown to the person" in rival_log(tmp_path)


def test_under_never_with_an_unmarked_rival_it_prints_what_to_type_and_waits_for_the_person(tmp_path):
    def type_it(fake, env, config):
        fake.schedule_call(30.0, lambda: fake._scripted("llm_bridge_mark_harness"))
        fake.schedule_call(30.0, fake.emit_state)
    env, fake, *_ = play(tmp_path, human_cycle(run=30, pause=60), until="1936-04-01",
                         fake_setup=type_it, rival_act_when="never")
    assert "type these in the game's console to start (SWE must be marked)" in rival_log(tmp_path)
    assert fake.runs == [] and fake.keylog == []


# --- what it will not start with ------------------------------------------------------------------


def test_a_game_that_never_gives_a_window_fails_the_start_with_the_line_to_type(tmp_path):
    import pytest

    from hoi4_harness.rival.runner import RivalStartError
    config = rival_config(tmp_path, script=acts((0, "resume")), rival_start_wait_minutes=1)
    env = build_env(config)
    client = LazyClient(nap=30)
    with pytest.raises(RivalStartError, match="no safe window.*effect SWE llm_bridge_mark_harness"):
        run_rival(config, planner=client, env=env, until="1936-06-01")
    assert env.adapter.fake_game.runs == [] and env.adapter.fake_game.keylog == []


def test_the_person_cannot_be_the_rival():
    import pytest

    from hoi4_harness.fakerival import FakeRivalGame
    with pytest.raises(ValueError):
        FakeRivalGame(Path("."), rival="SWE", human="SWE")


def test_actions_the_game_ai_owns_are_not_offered_and_the_model_cannot_take_the_clock(tmp_path):
    env = build_env(rival_config(tmp_path))
    offered = env.allowed_actions
    assert {"set_ai_directive", "set_ai_posture", "clear_ai_directives", "take_decision",
            "hire_advisor"} <= offered
    assert not offered & {"set_game_speed", "set_national_focus", "start_research",
                          "queue_construction", "set_production", "move_division"}
    result = env.act(ActionCall("set_game_speed", {"speed": 0}))
    assert not result.ok
