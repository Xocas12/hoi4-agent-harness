"""Single-player chat (#133): the person and the LLM countries, and LLM countries among themselves."""

from __future__ import annotations

import json

import pytest

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


# --- multiplayer: the game's own chat (adapters/mp_chat.py) -----------------------------------


class FakeDriver:
    def __init__(self):
        self.calls = []

    def press(self, key):
        self.calls.append(("press", key))

    def type_text(self, text):
        self.calls.append(("type", text))


def _mp_env(tmp_path, driver=None, file_chat=False):
    from hoi4_harness.adapters.mp_chat import MultiplayerChat

    config = HarnessConfig(multiplayer=True, country="FRA", mp_human_tags=["GER"],
                           clock_owner="player")
    env = HOI4Env(MockAdapter(), config)
    driver = driver or FakeDriver()
    env.attach_mp_chat(MultiplayerChat(driver, "FRA", ["GER"], sleep=lambda _s: None))
    if file_chat:
        env.attach_chat(ChatChannel(ChatLog(tmp_path / "chat.jsonl"), "FRA"))
    return env, driver


def test_multiplayer_send_message_types_into_the_games_chat(tmp_path):
    env, driver = _mp_env(tmp_path, file_chat=True)
    assert "send_message" in env.allowed_actions
    result = env.act(ActionCall("send_message", {"to": "GER", "text": "Let us talk"}), "1936-03-01")
    assert result.ok and "sent to GER" in result.message
    assert driver.calls == [("press", "enter"), ("type", "[FRA to GER] Let us talk"), ("press", "enter")]
    assert ChatLog(tmp_path / "chat.jsonl").messages() == []       # not the file chat


def test_multiplayer_person_and_global_mean_the_game_chat_too(tmp_path):
    env, driver = _mp_env(tmp_path)
    for target in ("person", "global"):
        assert env.act(ActionCall("send_message", {"to": target, "text": "hello"}), "").ok
    assert ("type", "[FRA to everyone] hello") in driver.calls


def test_multiplayer_talk_to_another_llm_stays_in_the_file_chat(tmp_path):
    env, driver = _mp_env(tmp_path, file_chat=True)
    assert env.act(ActionCall("send_message", {"to": "ENG", "text": "Alliance?"}), "").ok
    assert driver.calls == []
    assert [(m.frm, m.to) for m in ChatLog(tmp_path / "chat.jsonl").messages()] == [("FRA", "ENG")]


def test_multiplayer_refuses_a_target_it_cannot_reach(tmp_path):
    env, driver = _mp_env(tmp_path)
    result = env.act(ActionCall("send_message", {"to": "ENG", "text": "Alliance?"}), "")
    assert not result.ok and "not available" in result.message and driver.calls == []


def test_a_multiplayer_run_with_no_driver_says_so(tmp_path):
    env = HOI4Env(MockAdapter(), HarnessConfig(multiplayer=True, country="FRA", mp_human_tags=["GER"],
                                               clock_owner="player"))
    result = env.act(ActionCall("send_message", {"to": "GER", "text": "hi"}), "")
    assert not result.ok and "not available" in result.message


def test_single_player_still_uses_the_file_chat(tmp_path):
    env = HOI4Env(MockAdapter(), HarnessConfig())
    env.attach_chat(ChatChannel(ChatLog(tmp_path / "chat.jsonl"), "SWE"))
    driver = FakeDriver()
    from hoi4_harness.adapters.mp_chat import MultiplayerChat

    env.attach_mp_chat(MultiplayerChat(driver, "SWE", ["GER"]))      # present but multiplayer is off
    assert env.act(ActionCall("send_message", {"to": "GER", "text": "hi"}), "").ok
    assert driver.calls == [] and len(ChatLog(tmp_path / "chat.jsonl").messages()) == 1


def test_the_sender_checks_its_input():
    from hoi4_harness.adapters.mp_chat import MAX_CHARS, MultiplayerChat

    driver = FakeDriver()
    chat = MultiplayerChat(driver, "fra", ["ger"], sleep=lambda _s: None)
    assert chat.reaches("GER") and chat.reaches("Person") and not chat.reaches("ENG")
    with pytest.raises(ValueError, match="empty"):
        chat.send("GER", "  ")
    with pytest.raises(ValueError, match="not a person"):
        chat.send("ENG", "hi")
    assert driver.calls == []
    chat.send("GER", "x" * 1000)
    assert len(driver.calls[1][1]) == MAX_CHARS


def test_the_sender_goes_through_the_input_driver_and_its_guards():
    from hoi4_harness.adapters.input_driver import InputDriverAdapter
    from hoi4_harness.adapters.mp_chat import attach, find_driver

    driver = InputDriverAdapter(dry_run=True)
    config = HarnessConfig(multiplayer=True, country="FRA", mp_human_tags=["GER"], clock_owner="player")
    env = HOI4Env(driver, config)
    assert find_driver(driver) is driver
    sender = attach(env, config)
    assert sender is not None and env.mp_chat is sender
    assert env.act(ActionCall("send_message", {"to": "GER", "text": "Peace?"}), "").ok
    assert driver.log[-3:] == ["press enter", "type [FRA to GER] Peace?", "press enter"]
    assert attach(HOI4Env(MockAdapter(), config), config) is None        # nothing to type with
