"""The chat window's logic (chat_gui.ChatModel) and the players registry (players.py), headless."""

from __future__ import annotations

import json
import os

import pytest

from hoi4_harness.chat import ChatChannel, ChatLog
from hoi4_harness.chat_gui import GLOBAL, ChatModel
from hoi4_harness.cli import main
from hoi4_harness.home import home_dir
from hoi4_harness.players import PlayersRegistry, RunMark, pid_alive


def model(tmp_path, human=("GER",), alive=lambda pid: True, names=None):
    log = ChatLog(tmp_path / "chat.jsonl")
    registry = PlayersRegistry(tmp_path / "players.json", alive=alive)
    return ChatModel(log, registry, human, names), log, registry


# --- the registry ---------------------------------------------------------------------------


def test_the_home_folder_is_overridable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOI4_HARNESS_HOME", str(tmp_path / "h"))
    assert home_dir() == tmp_path / "h"
    assert PlayersRegistry().path == tmp_path / "h" / "players.json"
    assert ChatLog().path == tmp_path / "h" / "chat.jsonl"
    monkeypatch.setenv("HOI4_CHAT", str(tmp_path / "other.jsonl"))
    assert ChatLog().path == tmp_path / "other.jsonl"
    monkeypatch.delenv("HOI4_HARNESS_HOME")
    monkeypatch.delenv("HOI4_CHAT")
    assert home_dir().name == ".hoi4-harness"


def test_register_mark_and_list(tmp_path):
    clock = iter(range(100, 200))
    registry = PlayersRegistry(tmp_path / "players.json", clock=lambda: next(clock),
                               alive=lambda pid: True)
    registry.register("pol", "claude-cli", "haiku", str(tmp_path / "run"), pid=11)
    registry.register("FRA", "anthropic", "claude-opus-5", pid=11)
    (fra, pol) = registry.players()
    assert (pol.tag, pol.provider, pol.model, pol.status, pol.pid) == (
        "POL", "claude-cli", "haiku", "running", 11)
    assert pol.started == 100 and fra.started == 101 and pol.alive
    registry.mark("POL", "finished")
    again = {p.tag: p for p in registry.players()}["POL"]
    assert again.status == "finished" and not again.alive and again.updated > pol.updated
    registry.mark("XXX", "failed")                     # unknown: ignored
    assert [p.tag for p in registry.players()] == ["FRA", "POL"]


def test_a_dead_process_is_read_as_stopped(tmp_path):
    registry = PlayersRegistry(tmp_path / "players.json", alive=lambda pid: pid == 1)
    registry.register("POL", pid=1)
    registry.register("FRA", pid=2)
    registry.register("ITA", pid=2, status="finished")
    status = {p.tag: p.status for p in registry.players()}
    assert status == {"POL": "running", "FRA": "stopped", "ITA": "finished"}


def test_a_damaged_registry_reads_as_empty(tmp_path):
    path = tmp_path / "players.json"
    path.write_text("{oops", encoding="utf-8")
    assert PlayersRegistry(path).players() == []
    path.write_text(json.dumps({"players": [{"nope": 1}, {"tag": "POL", "bogus": 2}]}),
                    encoding="utf-8")
    assert [p.tag for p in PlayersRegistry(path).players()] == ["POL"]


def test_this_process_is_alive_and_pid_zero_is_not():
    assert pid_alive(os.getpid()) and not pid_alive(0) and not pid_alive(None)


def test_the_registry_holds_no_secret(tmp_path):
    registry = PlayersRegistry(tmp_path / "players.json")
    registry.register("FRA", "anthropic", "claude-opus-5")
    assert "key" not in (tmp_path / "players.json").read_text(encoding="utf-8").lower()


def test_runmark_registers_and_marks_the_way_out(tmp_path):
    registry = PlayersRegistry(tmp_path / "players.json", alive=lambda pid: True)

    def describe(tag):
        return "claude-cli", "haiku"

    def statuses():
        return {p.tag: p.status for p in registry.players()}

    with RunMark(["POL", "FRA"], describe, "run", registry) as mark:
        assert set(statuses().values()) == {"running"}
        mark.set("waiting")
        assert set(statuses().values()) == {"waiting"}
    assert set(statuses().values()) == {"finished"}
    with pytest.raises(RuntimeError), RunMark(["POL"], describe, "run", registry):
        raise RuntimeError("boom")
    assert statuses()["POL"] == "failed"
    with pytest.raises(KeyboardInterrupt), RunMark(["POL"], describe, "run", registry):
        raise KeyboardInterrupt
    assert statuses()["POL"] == "stopped"


def test_a_native_run_writes_the_registry_and_marks_its_end(tmp_path, monkeypatch):
    """cmd_native registers every country first (waiting) and marks them when it ends."""
    monkeypatch.setattr("hoi4_harness.native.installed_mod_problems", lambda _dir: ["no mod here"])
    assert main(["native", "--rival", "POL,FRA", "--run-dir", str(tmp_path / "run")]) == 2
    registry = PlayersRegistry()
    assert [(p.tag, p.provider, p.status) for p in registry.players()] == [
        ("FRA", "claude-cli", "stopped"), ("POL", "claude-cli", "stopped")]
    assert registry.path.parent == home_dir()


def test_a_rival_run_through_play_is_running_then_finished(tmp_path, monkeypatch):
    seen = {}

    def fake_run_rival(config, **_kw):
        seen["during"] = [(p.tag, p.status) for p in PlayersRegistry().players()]
        return None, {"reached_end_date": True, "stopped_reason": None}

    monkeypatch.setattr("hoi4_harness.rival.runner.run_rival", fake_run_rival)
    assert main(["play", "--rival", "POL", "--run-dir", str(tmp_path / "run"), "--quiet"]) == 0
    assert seen["during"] == [("POL", "running")]
    assert [(p.tag, p.status) for p in PlayersRegistry().players()] == [("POL", "finished")]


# --- channels -------------------------------------------------------------------------------


def test_channels_list_global_the_llm_countries_and_the_human(tmp_path):
    m, _log, registry = model(tmp_path, names={"POL": "Poland", "GER": "Germany"})
    registry.register("POL", "claude-cli", "haiku")
    registry.register("FRA", "anthropic", "claude-opus-5", status="waiting")
    got = m.channels()
    assert [(c.id, c.kind) for c in got] == [(GLOBAL, "global"), ("FRA", "llm"), ("POL", "llm"),
                                             ("GER", "human")]
    assert got[2].label == "POL Poland" and got[2].status == "running"
    assert got[1].status == "waiting"
    assert got[3].can_send is False and got[1].can_send is True
    assert "(you)" in got[3].title() and "(running)" in got[2].title()


def test_a_country_seen_only_in_the_chat_still_has_a_thread(tmp_path):
    m, log, _ = model(tmp_path)
    log.post("SWE", "person", "Hello")
    log.post("person", "NOR", "Hi Norway")
    log.post("ITA", "global", "To all")
    log.post("ITA", "SWE", "private")            # never shown to the person, but ITA is an LLM
    assert [c.id for c in m.channels()] == [GLOBAL, "ITA", "NOR", "SWE", "GER"]


def test_the_human_country_is_not_listed_as_an_llm(tmp_path):
    m, _log, registry = model(tmp_path)
    registry.register("GER", "claude-cli", "haiku")      # a stale row for the person's country
    assert [c.kind for c in m.channels() if c.id == "GER"] == ["human"]


# --- conversations and sending --------------------------------------------------------------


def test_conversations_are_global_or_one_country(tmp_path):
    m, log, registry = model(tmp_path)
    registry.register("POL")
    registry.register("FRA")
    log.post("person", "POL", "Join me")
    log.post("POL", "person", "Never", date="1936-02-01")
    log.post("FRA", "person", "Bonjour")
    log.post("POL", "FRA", "secret between us")
    log.post("FRA", "global", "Everyone listen")
    log.post("person", "global", "I hear you")
    assert [x.text for x in m.conversation("POL")] == ["Join me", "Never"]
    assert [x.text for x in m.conversation("FRA")] == ["Bonjour"]
    assert [x.text for x in m.conversation(GLOBAL)] == ["Everyone listen", "I hear you"]
    assert m.lines("POL") == ["You: Join me", "1936-02-01  POL: Never"]
    assert m.lines(GLOBAL) == ["FRA: Everyone listen", "You (to everyone): I hear you"]


def test_sending_to_a_country_global_and_not_to_the_human(tmp_path):
    m, _log, registry = model(tmp_path)
    registry.register("POL")
    sent = m.send("POL", "  Join   me ")
    assert (sent.frm, sent.to, sent.text) == ("person", "POL", "Join me")
    assert m.send(GLOBAL, "Hello all").to == "global"
    assert [(x.frm, x.to) for x in ChatLog(tmp_path / "chat.jsonl").messages()] == [
        ("person", "POL"), ("person", "global")]
    with pytest.raises(ValueError, match="country you play"):
        m.send("GER", "talking to myself")
    with pytest.raises(ValueError, match="empty"):
        m.send("POL", "   ")


def test_a_message_the_window_sends_is_what_the_country_reads(tmp_path):
    m, log, registry = model(tmp_path)
    registry.register("POL")
    channel = ChatChannel(log, "POL", tmp_path / "seen.json")
    m.send("POL", "Will you guarantee Norway?")
    assert channel.wake_reason() == "a message from person"
    channel.send("person", "Yes.")
    assert m.lines("POL")[-1] == "POL: Yes."


def test_unread_counts_the_other_side_until_read(tmp_path):
    m, log, registry = model(tmp_path)
    registry.register("POL")
    log.post("POL", "person", "one")
    log.post("POL", "person", "two")
    log.post("person", "POL", "mine")
    log.post("POL", "global", "all")
    by_id = {c.id: c.unread for c in m.channels()}
    assert by_id["POL"] == 2 and by_id[GLOBAL] == 1
    assert "[2 new]" in next(c for c in m.channels() if c.id == "POL").title()
    m.mark_read("POL")
    assert {c.id: c.unread for c in m.channels()}["POL"] == 0
    log.post("POL", "person", "three")
    assert {c.id: c.unread for c in m.channels()}["POL"] == 1


def test_it_shows_what_a_country_writes_while_it_is_open(tmp_path):
    m, log, registry = model(tmp_path)
    registry.register("POL")
    assert m.lines("POL") == []
    log.post("POL", "person", "Late news")        # written by another process
    assert m.lines("POL") == ["POL: Late news"]


def test_the_chat_gui_command_without_a_display_says_so(tmp_path, capsys, monkeypatch):
    import tkinter

    def no_display(*_a, **_k):
        raise tkinter.TclError("no display name")

    monkeypatch.setattr("tkinter.Tk", no_display)
    assert main(["chat-gui", "--chat", str(tmp_path / "chat.jsonl")]) == 1
    assert "no display" in capsys.readouterr().out
