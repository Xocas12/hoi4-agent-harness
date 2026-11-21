"""The inbox transport (inbox.py): orders through a mod file the game reloads, no input at all.

What the fake game proves is the harness's half: the files are what the mod's events expect,
every batch runs once and is acknowledged, acknowledged batches leave the file, and a rival run
on the inbox sends not one key. Whether HOI4 1.20 reloads the file is the live probe's question
(docs/inbox.md, ``hoi4-harness inbox probe``).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hoi4_harness import modgen, native
from hoi4_harness.agent.llm.base import Msg
from hoi4_harness.agent.llm.claude_cli import ClaudeCliClient, extract_json
from hoi4_harness.fakerival import human_cycle
from hoi4_harness.inbox import (
    EFFECTS_FILE,
    EVENTS_FILE,
    Inbox,
    InboxError,
    parse_batches,
    render,
    to_script,
)
from hoi4_harness.rival.runner import RivalStartError, run_rival
from rival_helpers import LazyClient, build_env, rival_config, say

REPO_MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"
INVADE = ("set_ai_directive", {"directive": "invade", "target": "POL"})
THEATER = ("set_ai_posture", {"posture": "offensive", "theater": "POL"})


def nap(days=30):
    return ("advance_time", {"days": days, "reason": "let it run"})


# --- the file format -----------------------------------------------------------------------------


def test_console_lines_become_effects_in_the_countrys_own_scope():
    assert to_script("effect POL llm_bridge_raise_invade") == ("POL", "llm_bridge_raise_invade = yes")
    assert to_script("eval_effect SWE = { add_political_power = 5 }") == ("SWE", "add_political_power = 5")
    for line in ("savegame", "gamespeed 3", "run llmb_turn.txt", "human_ai"):
        with pytest.raises(InboxError):
            to_script(line)


def test_each_batch_runs_once_in_its_country_and_logs_its_acknowledgement(tmp_path):
    box = Inbox(tmp_path, clock=lambda: 1_700_000_000.0)
    (batch,) = box.post(["effect POL llm_bridge_select_GER", "effect POL llm_bridge_raise_invade"])
    events = (tmp_path / EVENTS_FILE).read_text(encoding="utf-8")
    effects = (tmp_path / EFFECTS_FILE).read_text(encoding="utf-8")
    flag = f"llmb_inbox_{batch.id}"
    # the dispatcher fires the order event in POL's scope, so ROOT is POL as in the console
    assert "POL = { country_event = { id = llmb_inbox.2 } }" in events
    for text, via in ((events, "event"), (effects, "effect")):
        assert f"limit = {{ tag = POL NOT = {{ has_global_flag = {flag} }} }}" in text
        assert f"set_global_flag = {{ flag = {flag} value = 1 days = 365 }}" in text
        assert "llm_bridge_raise_invade = yes" in text
        assert f'|id={batch.id}|via={via}"' in text
    # both copies are reachable whichever database the game reloads
    assert "llm_bridge_inbox_dispatch = yes" in events and "llm_bridge_inbox_run = yes" in events
    assert parse_batches(events)[0].script == ["llm_bridge_select_GER = yes", "llm_bridge_raise_invade = yes"]


def test_an_unacknowledged_batch_survives_a_harness_restart_and_an_acknowledged_one_leaves(tmp_path):
    log = tmp_path / "game.log"
    log.write_text("", encoding="utf-8")
    first = Inbox(tmp_path / "mod", log_path=log)
    a, = first.post(["effect POL llm_bridge_raise_protect"])
    b, = first.post(["effect SWE llm_bridge_raise_invade"])
    again = Inbox(tmp_path / "mod", log_path=log)          # a new harness process
    assert [x.id for x in again.pending] == [a.id, b.id]
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"[12:00:00][1936.01.02.12]: LLMB|v5|inbox|tag=POL|id={a.id}|via=event\n")
    assert [ack.id for ack in again.poll()] == [a.id]
    assert [x.id for x in again.pending] == [b.id]
    assert a.id not in (tmp_path / "mod" / EVENTS_FILE).read_text(encoding="utf-8")
    # an acknowledgement already in the log at start means the batch is done
    third = Inbox(tmp_path / "mod", log_path=log)
    assert a.id in third.acked and [x.id for x in third.pending] == [b.id]


def test_two_posts_in_one_second_get_different_ids(tmp_path):
    box = Inbox(tmp_path, clock=lambda: 5.0)
    ids = {box.post(["effect POL x"])[0].id, box.post(["effect POL x"])[0].id}
    assert len(ids) == 2


def test_the_mod_ships_the_empty_inbox_and_fires_it_daily_for_the_human():
    assert modgen.drift(REPO_MOD) == []
    for rel, text in render([]).items():
        assert (REPO_MOD / rel).read_text(encoding="utf-8").replace("\r\n", "\n") == text
    hook = (REPO_MOD / "common" / "on_actions" / "llm_bridge_on_actions.txt").read_text(encoding="utf-8")
    block = hook[hook.index("on_daily"):hook.index("on_weekly")]
    assert ("limit = { llm_bridge_is_harness = yes }\n\t\t\t\tcountry_event = { id = llmb_inbox.1 }"
            in block)
    assert native.installed_mod_problems(REPO_MOD) == []


# --- the rival on the inbox: no key, no window ------------------------------------------------------


def _inbox_run(tmp_path, wakes, *, reloads=True, until="1936-08-01", script=None):
    config = rival_config(tmp_path, script=script or human_cycle(run=60, pause=5, cycles=60),
                          console_transport="inbox")
    env = build_env(config)
    fake = env.adapter.fake_game
    fake.reloads_inbox = reloads
    client = LazyClient(script=[say(text, *calls) for text, calls in wakes], nap=30)
    run_rival(config, planner=client, env=env, until=until)
    return config, env, fake


def test_a_rival_on_the_inbox_lands_its_orders_while_the_person_plays_and_sends_no_key(tmp_path):
    # The person never pauses for long and never stops touching the keyboard: the console path
    # would never get a window. The inbox needs none.
    config, env, fake = _inbox_run(tmp_path, [("go", [INVADE, THEATER, nap(30)])])
    assert config.rival_act_when == "inbox"
    assert "invade POL" in fake.directives
    assert fake.keylog == [] and fake.runs == [] and fake.stray_keys == []
    assert fake.errors == []
    log = fake.log_path.read_text(encoding="utf-8")
    assert "|inbox|tag=SWE|" in log            # the mark and the orders ran in SWE's scope
    assert "|inbox|tag=GER|" not in log
    # everything acknowledged has left the inbox; the exit's unmark was posted after the
    # game's last day and waits for the next one
    box = Inbox(fake.user_dir / "mod" / "llm_bridge", log_path=fake.log_path)
    assert [b.script for b in box.pending] == [["llm_bridge_unmark_harness = yes"]]


def test_a_game_that_does_not_reload_the_inbox_gets_no_orders_and_the_start_says_so(tmp_path):
    with pytest.raises(RivalStartError):
        _inbox_run(tmp_path, [("go", [INVADE, nap(30)])], reloads=False, until="1936-03-01")


def test_the_inbox_and_a_window_policy_cannot_be_mixed(tmp_path):
    config = rival_config(tmp_path, console_transport="inbox", rival_act_when="handover")
    with pytest.raises(RivalStartError, match="both halves"):
        run_rival(config, planner=LazyClient(script=[], nap=30), env=build_env(config))


# --- the claude CLI provider ---------------------------------------------------------------------------


class _Proc:
    def __init__(self, stdout, code=0):
        self.stdout, self.stderr, self.returncode = stdout, "", code


def test_the_cli_provider_runs_claude_with_no_tools_no_settings_and_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-pass")
    seen = {}

    def run(cmd, **kwargs):
        seen["cmd"], seen["kwargs"] = cmd, kwargs
        body = {"text": "hold", "calls": [{"name": "advance_time", "arguments": {"days": 30}}]}
        return _Proc(json.dumps({"result": json.dumps(body), "is_error": False,
                                 "usage": {"input_tokens": 10, "output_tokens": 5},
                                 "total_cost_usd": 0.001}))

    client = ClaudeCliClient(cli="claude", workdir=tmp_path, run=run)
    out = client.complete("system text", [Msg(role="user", text="brief")])
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "haiku"
    assert cmd[cmd.index("--tools") + 1] == "" and cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd
    assert "ANTHROPIC_API_KEY" not in seen["kwargs"]["env"]
    assert "brief" in seen["kwargs"]["input"] and "ONLY one JSON object" in seen["kwargs"]["input"]
    assert (tmp_path / "system.md").read_text(encoding="utf-8") == "system text"
    assert [(c.name, c.arguments) for c in out.tool_calls] == [("advance_time", {"days": 30})]
    assert out.usage.input_tokens == 10 and client.total_cost_usd == pytest.approx(0.001)


def test_the_cli_provider_asks_again_once_and_then_does_nothing_rather_than_crash(tmp_path):
    replies = iter(["I think we should wait.", "still prose"])
    client = ClaudeCliClient(cli="claude", workdir=tmp_path,
                             run=lambda cmd, **kw: _Proc(json.dumps({"result": next(replies)})))
    out = client.complete("s", [Msg(role="user", text="b")])
    assert out.tool_calls == [] and out.text == "still prose"


def test_the_cli_provider_reports_a_failed_cli(tmp_path):
    from hoi4_harness.agent.llm.claude_cli import ClaudeCliError

    client = ClaudeCliClient(cli="claude", workdir=tmp_path, run=lambda cmd, **kw: _Proc("", 1))
    with pytest.raises(ClaudeCliError):
        client.complete("s", [Msg(role="user", text="b")])

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    with pytest.raises(ClaudeCliError, match="longer than"):
        ClaudeCliClient(cli="claude", workdir=tmp_path, run=slow).complete("s", [Msg(role="user", text="b")])


def test_extract_json_takes_a_fenced_or_embedded_object():
    assert extract_json('```json\n{"text": "a", "calls": []}\n```')["text"] == "a"
    assert extract_json('Sure: {"text": "b", "calls": []} done')["text"] == "b"
    with pytest.raises(ValueError):
        extract_json("no braces here")


# --- native ---------------------------------------------------------------------------------------------


def test_native_knows_a_game_started_without_debug_mode_cannot_take_orders():
    assert native.check_debug_mode('"C:/x/hoi4.exe" -gdpr-compliant -debug_mode').ok is True
    assert native.check_debug_mode('"C:/x/hoi4.exe" -gdpr-compliant').ok is False
    assert native.check_debug_mode(None).ok is None


def test_native_names_what_an_old_installed_mod_lacks(tmp_path):
    problems = native.installed_mod_problems(tmp_path)
    assert len(problems) == 3 and any("llmb_inbox.1" in p or "on_actions" in p for p in problems)


def test_native_waits_for_the_game_and_gives_up_when_told():
    said, ticks = [], iter([False, False, True])
    assert native.wait_for_game(lambda: next(ticks), lambda s: None, said.append, poll=0)
    assert len(said) == 1
    clock = iter(range(100))
    assert not native.wait_for_game(lambda: False, lambda s: None, said.append, poll=0,
                                    give_up=3, clock=lambda: next(clock))
