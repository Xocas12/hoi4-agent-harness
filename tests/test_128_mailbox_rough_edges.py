"""#128: reads sent with advance_time, stale answer files, owned vs controlled states."""

from __future__ import annotations

import json
import os
from pathlib import Path

from campaign_helpers import build_env, campaign_config, say
from hoi4_harness.agent.llm.base import Usage  # noqa: F401
from hoi4_harness.agent.llm.mailbox import REQUEST_JSON, RESPONSE_JSON, write_answer
from hoi4_harness.campaign.runner import run_campaign
from hoi4_harness.cli import main
from hoi4_harness.identifiers import IdentifierIndex, Playset
from test_campaign_run import LazyClient, records
from test_mailbox_provider import TOOLS

FIX = Path(__file__).parent / "fixtures" / "modctx"


def _run(tmp_path, script, *, occupied=0):
    config = campaign_config(tmp_path, campaign_until="1936-03-01", mod_context=True)
    env = build_env(config)
    env.index = IdentifierIndex.build(Playset.from_paths(FIX / "game"))
    env.adapter.fake_game.ai.occupied_states = occupied
    client = LazyClient(script=list(script), nap=60)
    run_campaign(config, planner=client, env=env)
    return client


# --- 1: a read sent with advance_time ---------------------------------------------------------


def test_128_a_read_sent_with_advance_time_reaches_the_next_request(tmp_path):
    client = _run(tmp_path, [say("look", ("list_decisions", {}),
                                 ("advance_time", {"days": 30}))])
    first = records(tmp_path, "actions")[0]["results"]
    assert [r["name"] for r in first] == ["list_decisions", "advance_time"]
    assert first[0]["is_error"] is False
    assert first[1]["is_error"] is True and "not run" in first[1]["content"]
    # The same wake carried on: its next request holds the read's result.
    _system, messages = client.calls[1]
    assert messages[-1].tool_results[0].name == "list_decisions"
    assert messages[-1].tool_results[0].content


def test_the_skipped_advance_time_names_the_reads_it_was_sent_with(tmp_path):
    # inspect_world counts as a read too; the old message named only list_decisions and
    # describe_action, and Haiku kept pairing inspect_world with advance_time (2026-11-22).
    _run(tmp_path, [say("look", ("inspect_world", {"view": "divisions"}),
                        ("list_decisions", {}), ("advance_time", {"days": 30}))])
    skipped = records(tmp_path, "actions")[0]["results"][-1]
    assert skipped["is_error"] is True
    assert "(inspect_world, list_decisions)" in skipped["content"]


def test_128_advance_time_alone_still_ends_the_turn(tmp_path):
    client = _run(tmp_path, [say("quiet", ("advance_time", {"days": 30}))])
    first = records(tmp_path, "actions")[0]["results"]
    assert [r["is_error"] for r in first] == [False]
    assert len(client.calls[1][1]) == 1   # a new wake: a fresh conversation


# --- 2: a stale answer file -------------------------------------------------------------------


def _mailbox(tmp_path, seq):
    (tmp_path / REQUEST_JSON).write_text(json.dumps(
        {"seq": seq, "tools": [{"name": t.name, "parameters": t.parameters} for t in TOOLS],
         "messages": []}), encoding="utf-8")


def _answer_file(tmp_path, days, name="a.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"text": "x", "calls": [
        {"name": "advance_time", "arguments": {"days": days}}]}), encoding="utf-8")
    return path


def test_128_an_answer_file_older_than_the_request_is_refused_unless_forced(tmp_path, capsys):
    mb = tmp_path / "mb"
    mb.mkdir()
    old = _answer_file(tmp_path, 7)
    _mailbox(mb, 48)
    os.utime(old, (1, 1))
    assert main(["mailbox", "answer", "--dir", str(mb), "--file", str(old)]) == 1
    out = capsys.readouterr().out
    assert "REFUSED" in out and "before request 48" in out and "--force" in out
    assert not (mb / RESPONSE_JSON).exists()
    assert main(["mailbox", "answer", "--dir", str(mb), "--file", str(old), "--force"]) == 0
    assert json.loads((mb / RESPONSE_JSON).read_text())["seq"] == 48


def test_128_a_file_identical_to_the_last_answer_is_refused(tmp_path, capsys):
    mb = tmp_path / "mb"
    mb.mkdir()
    _mailbox(mb, 47)
    path = _answer_file(tmp_path, 7)
    assert write_answer(mb, json.loads(path.read_text()), source=path)[1] == []
    _mailbox(mb, 48)
    path.write_text(path.read_text(), encoding="utf-8")       # touched, same bytes
    assert main(["mailbox", "answer", "--dir", str(mb), "--file", str(path)]) == 1
    assert "identical to the last answer" in capsys.readouterr().out
    fresh = _answer_file(tmp_path, 14, "b.json")
    assert main(["mailbox", "answer", "--dir", str(mb), "--file", str(fresh)]) == 0


# --- 3: owned and controlled ------------------------------------------------------------------


def test_128_the_line_and_the_brief_show_controlled_next_to_owned(tmp_path):
    _run(tmp_path, [], occupied=47)
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "states 30 owned / 77 controlled" in log
    briefs = [r["brief"] for r in records(tmp_path, "observe") if "CAMPAIGN" in r["brief"]]
    assert any("You hold 30 states owned / 77 controlled" in b for b in briefs)


# --- 4: "own 0" in the front picture ----------------------------------------------------------


def test_128_own_0_in_the_front_picture_says_it_is_not_your_whole_army():
    from hoi4_harness.observation.forces import forces_lines
    from hoi4_harness.types import GameState, StateForces

    def picture(own):
        return GameState(date="1940-09-07", country="GER", state_forces=[
            StateForces(state=428, controller="RAJ", own=own, own_weak=0, foe=12, foe_weak=0)])

    assert "no division of yours is in or beside" in forces_lines(picture(0))[0]
    assert forces_lines(picture(3))[0].endswith("own 3 vs foe 12 in 1 state(s)")
