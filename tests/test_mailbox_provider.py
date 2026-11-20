"""The mailbox provider: requests go out as files, answers come back as files."""

from __future__ import annotations

import json
import threading
import time

from hoi4_harness.actions.registry import ToolSpec
from hoi4_harness.agent.llm import build_llm
from hoi4_harness.agent.llm.base import Msg, ToolCall, ToolResult
from hoi4_harness.agent.llm.mailbox import (
    CLOSED,
    REQUEST_JSON,
    REQUEST_MD,
    RESPONSE_JSON,
    SYSTEM_MD,
    MailboxClient,
    MailboxTimeout,
    check_answer,
    pending,
    wait_for_request,
    write_answer,
)
from hoi4_harness.cli import main
from hoi4_harness.config import LLMConfig

TOOLS = [
    ToolSpec("advance_time", "Let time pass.", {
        "type": "object", "required": ["days"],
        "properties": {"days": {"type": "integer"}, "reason": {"type": "string"}}}),
    ToolSpec("set_ai_posture", "Posture.", {
        "type": "object", "required": ["posture"],
        "properties": {"posture": {"type": "string", "enum": ["defensive", "balanced", "aggressive"]}}}),
]


def answer_in_background(directory, answers, delay=0.05):
    """Answer each request in turn, the way an outside agent would."""
    def run():
        for answer in answers:
            state, _ = wait_for_request(directory, timeout_seconds=10, poll_seconds=0.01)
            assert state == "request"
            time.sleep(delay)
            seq, problems = write_answer(directory, answer)
            assert not problems, problems
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_a_request_goes_out_as_files_and_the_answer_comes_back(tmp_path):
    client = MailboxClient(tmp_path, poll_seconds=0.01)
    answer_in_background(tmp_path, [{"text": "Hold.", "calls": [
        {"name": "set_ai_posture", "arguments": {"posture": "defensive"}},
        {"name": "advance_time", "arguments": {"days": 14}}]}])
    resp = client.complete("You are GER.", [Msg("user", "BRIEF 1936-01-01 GER")], TOOLS)
    assert [c.name for c in resp.tool_calls] == ["set_ai_posture", "advance_time"]
    assert resp.tool_calls[0].arguments == {"posture": "defensive"}
    assert resp.stop_reason == "tool_use" and resp.usage.input_tokens == 0
    assert (tmp_path / SYSTEM_MD).read_text(encoding="utf-8") == "You are GER."
    md = (tmp_path / REQUEST_MD).read_text(encoding="utf-8")
    assert "BRIEF 1936-01-01 GER" in md and "set_ai_posture(posture: defensive|balanced|aggressive)" in md


def test_tool_rounds_show_the_calls_and_results_in_order(tmp_path):
    client = MailboxClient(tmp_path, poll_seconds=0.01)
    answer_in_background(tmp_path, [{"text": "", "calls": []}])
    messages = [Msg("user", "brief"),
                Msg("assistant", "", tool_calls=[ToolCall("a", "set_ai_posture", {"posture": "aggressive"})]),
                Msg("user", tool_results=[ToolResult("a", "posture set", name="set_ai_posture")])]
    resp = client.complete("sys", messages, TOOLS)
    assert resp.stop_reason == "end_turn" and not resp.tool_calls
    req = json.loads((tmp_path / REQUEST_JSON).read_text(encoding="utf-8"))
    assert req["messages"][2]["results"][0]["content"] == "posture set"
    assert "result `set_ai_posture`: posture set" in (tmp_path / REQUEST_MD).read_text(encoding="utf-8")


def test_a_stale_answer_from_an_earlier_request_is_never_taken(tmp_path):
    (tmp_path / RESPONSE_JSON).write_text(json.dumps({"seq": 1, "calls": [
        {"name": "advance_time", "arguments": {"days": 90}}]}), encoding="utf-8")
    (tmp_path / REQUEST_JSON).write_text(json.dumps({"seq": 1}), encoding="utf-8")
    client = MailboxClient(tmp_path, poll_seconds=0.01)        # resumes numbering at 1
    answer_in_background(tmp_path, [{"text": "fresh", "calls": []}])
    resp = client.complete("sys", [Msg("user", "brief")], TOOLS)
    assert resp.text == "fresh" and client.seq == 2


def test_no_answer_times_out_when_a_timeout_is_set(tmp_path):
    ticks = iter(range(0, 10_000, 30))
    client = MailboxClient(tmp_path, timeout_seconds=60, sleep=lambda s: None,
                           clock=lambda: next(ticks))
    try:
        client.complete("sys", [Msg("user", "brief")], TOOLS)
    except MailboxTimeout as exc:
        assert "no answer" in str(exc)
    else:
        raise AssertionError("expected a timeout")


def test_an_answer_with_a_bad_tool_or_argument_is_refused_before_it_is_handed_over(tmp_path):
    client = MailboxClient(tmp_path)
    client.seq = 0
    req = {"seq": 1, "tools": [{"name": t.name, "parameters": t.parameters} for t in TOOLS]}
    (tmp_path / REQUEST_JSON).write_text(json.dumps(req), encoding="utf-8")
    assert check_answer(req, {"calls": [{"name": "nuke", "arguments": {}}]})[0].startswith("call 1: no tool")
    assert "missing required argument 'days'" in check_answer(
        req, {"calls": [{"name": "advance_time", "arguments": {}}]})[0]
    assert "not one of" in check_answer(
        req, {"calls": [{"name": "set_ai_posture", "arguments": {"posture": "berserk"}}]})[0]
    seq, problems = write_answer(tmp_path, {"calls": [{"name": "nuke"}]})
    assert problems and not (tmp_path / RESPONSE_JSON).exists()
    assert pending(tmp_path)["seq"] == 1


def test_close_tells_a_waiting_answerer_to_stop(tmp_path):
    client = MailboxClient(tmp_path)
    client.close()
    assert (tmp_path / CLOSED).exists()
    assert wait_for_request(tmp_path, timeout_seconds=1, poll_seconds=0.01)[0] == "closed"
    MailboxClient(tmp_path)                                    # a new run clears it
    assert not (tmp_path / CLOSED).exists()


def test_build_llm_makes_a_mailbox_client(tmp_path):
    cfg = LLMConfig(provider="mailbox", extra={"mailbox_dir": str(tmp_path / "mb")})
    client = build_llm(cfg)
    assert isinstance(client, MailboxClient) and (tmp_path / "mb").is_dir()


def test_a_fake_campaign_runs_on_mailbox_answers_and_spends_nothing(tmp_path):
    """End to end: the campaign asks through files, an outside answerer replies."""
    run, mb = tmp_path / "run", tmp_path / "run" / "mailbox"
    stop = threading.Event()

    def answerer():
        while not stop.is_set():
            state, req = wait_for_request(mb, timeout_seconds=0.5, poll_seconds=0.01)
            if state == "closed":
                return
            if state != "request":
                continue
            last = req["messages"][-1]
            if last.get("results"):
                answer = {"text": "", "calls": [{"name": "advance_time", "arguments": {"days": 30}}]}
            else:
                answer = {"text": "Press west.", "calls": [
                    {"name": "set_ai_posture", "arguments": {"posture": "offensive"}},
                    {"name": "advance_time", "arguments": {"days": 30}}]}
            _, problems = write_answer(mb, answer)
            assert not problems, problems

    thread = threading.Thread(target=answerer, daemon=True)
    mb.mkdir(parents=True)
    thread.start()
    try:
        code = main(["play", "--campaign", "--profile", "profiles/campaign-fake.json",
                     "--mailbox", "--until", "1936-06-01", "--run-dir", str(run)])
    finally:
        stop.set()
        thread.join(timeout=5)
    assert code == 0
    assert (mb / CLOSED).exists()
    transcript = (run / "transcript.jsonl").read_text(encoding="utf-8")
    assert "set_ai_posture" in transcript
    requests = json.loads((mb / REQUEST_JSON).read_text(encoding="utf-8"))["seq"]
    assert requests >= 2
