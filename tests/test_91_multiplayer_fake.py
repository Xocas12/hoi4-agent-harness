"""The harness as a multiplayer client (#91), end to end on the fake multiplayer game.

``FakeMultiplayerGame`` (fakemultiplayer.py) is a harness's country and a person's on one
log, with the host's clock, a console that can be shut and an in-game chat box. The loop,
adapters, key layer and chat are the real ones; a scripted provider stands in for the model.

What this proves is the harness's side of the contract. What it cannot: whether a real
non-host client may use the console, whether the real chat box takes these keys, what the
host's latency does to a queued action (docs/multiplayer.md, "Live setup" and the table).
"""

from __future__ import annotations

import json

from campaign_helpers import LazyClient, build_env, say
from hoi4_harness.adapters.mp_chat import attach as attach_mp_chat
from hoi4_harness.adapters.mp_chat import typable
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.config import HarnessConfig
from hoi4_harness.coop import CoopGuidance, validate
from hoi4_harness.fakerival import HUMAN_PP, Act

FOCUS = ("set_national_focus", {"focus_id": "industrial_effort"})
BUILD = ("queue_construction", {"building": "civilian_factory", "state": "Svealand", "count": 1})
DECLARE = ("diplomacy", {"action": "declare_war", "target": "GER"})
HELLO = ("send_message", {"to": "GER", "text": "Peace on the Baltic? [SWE]"})


class Game:
    """SWE is the harness, GER the person on the host; a scripted model."""

    def __init__(self, tmp_path, console="open", script=None, **overrides):
        options = dict(adapter="headless", fake_game=True, fake_game_dir=tmp_path / "fake",
                       country="SWE", multiplayer=True, mp_human_tags=["GER"],
                       clock_owner="player", headless_reader="logtail", run_dir=tmp_path / "run",
                       dry_run=False, chat_path=tmp_path / "chat.jsonl")
        options.update(overrides)
        self.config = HarnessConfig(**options)
        self.config.budget.max_usd = None
        self.run_dir = tmp_path / "run"
        if script is not None:
            path = tmp_path / "human.json"
            path.write_text(json.dumps([[a.at, a.kind, a.arg] if a.arg else [a.at, a.kind]
                                        for a in script]), encoding="utf-8")
            self.config.fake_human_script = path
        assert validate(self.config) == []
        self.env = build_env(self.config)
        self.fake = self.env.adapter.fake_game
        self.fake.console_mode = console
        self.env.reset()
        attach_mp_chat(self.env, self.config)
        self.planner = LazyClient(nap=10)
        self.loop = AgentLoop(self.env, self.planner, self.config,
                              transcript_path=self.run_dir / "t.jsonl")
        self.loop.supervisor = CoopGuidance(self.run_dir, self.loop, incoming=self.fake.read_chat)

    def wake(self, text: str = "", *calls):
        self.planner.script.append(say(text, *calls))
        before = len(self.planner.calls)
        for _ in range(12):
            self.loop.run(1)
            if len(self.planner.calls) > before:
                return
        raise AssertionError("the model was never woken")

    def results(self) -> list[dict]:
        lines = (self.run_dir / "t.jsonl").read_text(encoding="utf-8").splitlines()
        out = []
        for r in map(json.loads, lines):
            if r["kind"] == "actions":
                out += [json.loads(x["content"]) for x in r["results"]]
        return out

    def briefs(self) -> list[str]:
        lines = (self.run_dir / "t.jsonl").read_text(encoding="utf-8").splitlines()
        return [r["brief"] for r in map(json.loads, lines) if r["kind"] == "observe"]


def host_plays(*extra: Act, pause_at: float = 5000.0) -> list[Act]:
    """The host resumes the game at once and pauses it for five seconds at ``pause_at`` (late by
    default: a pause in the middle of an order stalls the log its verification reads, which is
    what a real host does to a client too, and is its own test)."""
    return [Act(0.0, "resume"), Act(pause_at, "pause"), Act(pause_at + 5, "resume"), *extra]


def test_91_the_harness_plays_its_country_and_the_game_is_a_two_human_game(tmp_path):
    g = Game(tmp_path, script=host_plays())
    state = g.env.observe().state
    assert state.country == "SWE"
    log = g.fake.log_path.read_text(encoding="utf-8")
    assert "tag=SWE" in log and "tag=GER" in log and "|ai=0|" in log.replace("ai=0", "|ai=0|")
    g.wake("start", FOCUS)
    assert g.fake.world.state.national_focus == "industrial_effort"


def test_91_per_country_telemetry_never_shows_the_other_players_numbers(tmp_path):
    g = Game(tmp_path, script=host_plays())
    g.wake("", FOCUS)
    g.loop.run(3)
    assert HUMAN_PP in g.fake.log_path.read_text(encoding="utf-8")       # GER is on the log
    assert not any(HUMAN_PP in b for b in g.briefs())                    # and not in any brief
    assert all("GER:" not in b and "tag=GER" not in b for b in g.briefs())


def test_91_the_harness_never_pauses_resumes_or_sets_the_speed(tmp_path):
    g = Game(tmp_path, script=host_plays())
    g.wake("", FOCUS)
    g.loop.run(8)
    assert g.fake.clock_attempts == []
    assert not any("gamespeed" in str(run) for run in g.fake.runs)
    assert "set_game_speed" not in g.env.allowed_actions


def test_91_the_game_clock_is_the_hosts_and_the_loop_waits_for_it(tmp_path):
    g = Game(tmp_path, script=host_plays(pause_at=4.0))
    start = g.fake.date
    g.loop.run(6)
    assert g.fake.date > start and g.loop.report.turns == 6
    # The host paused at second 4 and resumed at 9: the loop waited through it.
    assert [a[1] for a in g.fake.human_log][:3] == ["resume", "pause", "resume"]
    assert g.fake.clock_attempts == []


def test_91_an_action_aimed_at_the_human_country_is_refused_and_nothing_reaches_the_game(tmp_path):
    g = Game(tmp_path, script=host_plays())
    g.wake("", FOCUS, DECLARE)
    ok, refused = g.results()[:2]
    assert refused["error"] == "rejected" and "a person plays" in refused["message"]
    assert ok["ok"]
    assert not any(w.against == "GER" for w in g.fake.world.state.wars)


def test_91_a_queued_call_aimed_at_the_human_country_is_refused_again_when_it_runs(tmp_path):
    from hoi4_harness.coop import wire_handover
    from hoi4_harness.handover import Pending
    from hoi4_harness.types import ActionCall

    g = Game(tmp_path, script=host_plays(), handover=True)
    wire_handover(g.env, g.config)
    g.env.handover.pending.append(Pending(ActionCall(*DECLARE), "1936-01-01", 0, 1))
    g.env.handover.grant()
    report = g.env.run_handover()
    assert report.ran == [] and "a person plays" in report.failed[0]["reason"]


def test_91_the_brief_says_who_plays_whom_every_turn(tmp_path):
    g = Game(tmp_path, script=host_plays())
    g.loop.run(5)
    briefs = g.briefs()
    assert briefs and all("you play SWE; people play GER" in b for b in briefs)


def test_91_the_message_channel_is_the_games_chat_and_its_text_is_typable(tmp_path):
    g = Game(tmp_path, script=host_plays())
    g.wake("", HELLO)
    assert g.results()[0]["ok"]
    assert [text for _, text in g.fake.chat_sent[:1]] == ["SWE to GER, Peace on the Baltic SWE"]
    assert g.fake.chat_open is False
    assert typable("Façade [now]: ok?") == "Facade now ok"
    # the keys went through the key layer and its guards, not around them
    assert g.fake.clock_attempts == []


def test_91_what_the_model_did_is_said_in_the_games_chat(tmp_path):
    g = Game(tmp_path, script=host_plays())
    g.wake("", FOCUS)
    assert any(text.startswith("SWE to everyone, This turn I did set_national_focus")
               for _, text in g.fake.chat_sent)


def test_91_the_host_can_move_a_domain_by_chat(tmp_path):
    """The reader is a seam: the real game's chat is not readable (adapters/mp_chat.py)."""
    g = Game(tmp_path, script=host_plays())
    assert "set_national_focus" in g.env.allowed_actions
    g.fake.host_says("take focus")
    g.loop.run(1)
    assert "set_national_focus" not in g.env.allowed_actions
    assert any("Focus is yours again" in text for _, text in g.fake.chat_sent)


def test_91_a_client_without_the_console_cannot_apply_a_console_order(tmp_path):
    g = Game(tmp_path, console="blocked", script=host_plays())
    g.wake("", BUILD)
    assert any("not available to a client" in e for e in g.fake.errors)
    assert not any(item.building == "civilian_factory" and item.state == "Svealand"
                   for item in g.fake.world.state.construction)
    # and with the console open (debug mode) the same order lands
    (tmp_path / "open").mkdir()
    g2 = Game(tmp_path / "open", console="open", script=host_plays())
    g2.wake("", BUILD)
    assert not g2.fake.errors


def test_91_the_multiplayer_run_is_not_scoreable(tmp_path):
    import pytest

    from hoi4_harness.eval import run_scenario

    with pytest.raises(Exception, match="(?i)multiplayer"):
        run_scenario("economy_ramp", HarnessConfig(multiplayer=True, country="SWE",
                                                   mp_human_tags=["GER"], clock_owner="player",
                                                   planner_enabled=False))
