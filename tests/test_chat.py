"""Single-player chat (#133): the person and the LLM countries, and LLM countries among themselves."""

from __future__ import annotations

import json

from hoi4_harness.adapters.mock import MockAdapter
from hoi4_harness.chat import ChatChannel, ChatLog
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.control import ALWAYS
from hoi4_harness.env import HOI4Env
from hoi4_harness.rival.runner import run_rival
from hoi4_harness.types import ActionCall
from rival_helpers import LazyClient, build_env, rival_config, say


def test_each_country_reads_global_and_its_own_lines_only(tmp_path):
    log = ChatLog(tmp_path / "chat.jsonl")
    pol = ChatChannel(log, "POL")
    ger = ChatChannel(log, "GER")
    log.post("person", "POL", "Join me against the Soviets")
    log.post("person", "global", "Hands off Romania")
    log.post("person", "GER", "Stay out of Poland")
    assert [m.text for m in pol.new()] == ["Join me against the Soviets", "Hands off Romania"]
    assert [m.text for m in ger.new()] == ["Hands off Romania", "Stay out of Poland"]
    assert pol.wake_reason() == "a message from person"


def test_a_direct_message_wakes_once_and_the_brief_marks_what_is_new(tmp_path):
    log = ChatLog(tmp_path / "chat.jsonl")
    pol = ChatChannel(log, "POL", tmp_path / "seen.json")
    log.post("person", "global", "Hello all")
    assert pol.wake_reason() is None                        # global talk waits for a turn
    log.post("person", "POL", "Poland?")
    lines = pol.brief_lines()
    assert lines[0].startswith("Messages") and any("NEW [person to POL] Poland?" in x for x in lines)
    pol.mark_seen()
    assert pol.wake_reason() is None and not any("NEW" in x for x in pol.brief_lines())
    # the seen mark survives a restart of the harness
    assert ChatChannel(log, "POL", tmp_path / "seen.json").new() == []


def test_two_llm_countries_talk_and_each_sees_the_other(tmp_path):
    log = ChatLog(tmp_path / "chat.jsonl")
    pol, ger = ChatChannel(log, "POL"), ChatChannel(log, "GER")
    pol.send("GER", "Non-aggression for ten years?")
    assert ger.wake_reason() == "a message from POL"
    assert any("NEW [POL to GER] Non-aggression" in x for x in ger.brief_lines())
    ger.send("POL", "Agreed, for now.")
    assert [m.text for m in pol.new()] == ["Agreed, for now."]


def test_bad_messages_are_refused(tmp_path):
    log = ChatLog(tmp_path / "chat.jsonl")
    for to, text in (("POL", "   "), ("Atlantis", "hi")):
        try:
            log.post("person", to, text)
        except ValueError:
            continue
        raise AssertionError(f"{to!r} {text!r} was accepted")


def test_send_message_is_a_free_tool_when_the_chat_is_on(tmp_path):
    env = HOI4Env(MockAdapter(), HarnessConfig())
    assert "send_message" not in env.allowed_actions
    env.attach_chat(ChatChannel(ChatLog(tmp_path / "chat.jsonl"), "SWE"))
    assert "send_message" in env.allowed_actions and "send_message" in ALWAYS
    result = env.act(ActionCall("send_message", {"to": "person", "text": "We stand with you"}),
                     "1936-03-01")
    assert result.ok and "sent to person" in result.message
    (message,) = ChatLog(tmp_path / "chat.jsonl").messages()
    assert (message.frm, message.to, message.date) == ("SWE", "person", "1936-03-01")


def test_the_chat_command_sends_and_shows(tmp_path, capsys):
    path = tmp_path / "chat.jsonl"
    assert main(["chat", "--chat", str(path), "--to", "POL", "Join", "me"]) == 0
    ChatLog(path).post("POL", "person", "Never.")
    assert main(["chat", "--chat", str(path)]) == 0
    out = capsys.readouterr().out
    assert "#1 to POL: Join me" in out and "[POL to person] Never." in out


def test_a_rival_is_woken_by_a_message_and_answers_it(tmp_path):
    config = rival_config(tmp_path, console_transport="inbox")
    config.chat_path = tmp_path / "chat.jsonl"
    run_dir = config.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "chat_seen.json").write_text(json.dumps({config.rival: 0}), encoding="utf-8")
    ChatLog(config.chat_path).post("person", config.rival, "Will you guarantee Norway?")
    env = build_env(config)
    reply = ("send_message", {"to": "person", "text": "No. We keep to ourselves."})
    client = LazyClient(script=[say("answer", reply, ("advance_time", {"days": 30}))], nap=30)
    run_rival(config, planner=client, env=env, until="1936-03-01")
    assert config.chat is True
    log = (run_dir / "rival.log").read_text(encoding="utf-8")
    assert "chat: a message from person" in log
    texts = [(m.frm, m.to, m.text) for m in ChatLog(config.chat_path).messages()]
    assert (config.rival, "person", "No. We keep to ourselves.") in texts
