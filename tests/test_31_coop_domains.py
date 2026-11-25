"""Co-op on one country (#31): the person keeps some domains, the model the rest, and they
change hands mid-game.

Everything runs on the fake game (``fakegame.py``) with the real loop, adapters, queue and
chat; a scripted provider stands in for the model. Nothing here reaches a model or a key.
What it cannot say: whether a real model keeps to its side (docs/live-session.md section 15).
"""

from __future__ import annotations

import json

import pytest

from campaign_helpers import LazyClient, build_env, say
from hoi4_harness.agent.loop import AgentLoop
from hoi4_harness.chat import ChatChannel, ChatLog
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.control import DOMAINS
from hoi4_harness.coop import CoopGuidance, DomainBoard, parse_domain_message
from hoi4_harness.types import ActionCall

FOCUS = ("set_national_focus", {"focus_id": "industrial_effort"})
BUILD = ("queue_construction", {"building": "civilian_factory", "state": "any"})


class Table:
    """A co-op run on the fake game: SWE, the person at the chat file, a scripted model."""

    def __init__(self, tmp_path, **overrides):
        options = dict(adapter="headless", fake_game=True, fake_game_dir=tmp_path / "fake",
                       country="SWE", headless_reader="logtail", run_dir=tmp_path / "run",
                       dry_run=False, clock_owner="player", chat_path=tmp_path / "chat.jsonl")
        options.update(overrides)
        self.config = HarnessConfig(**options)
        self.config.budget.max_usd = None
        self.run_dir = tmp_path / "run"
        self.env = build_env(self.config)
        self.fake = self.env.adapter.fake_game
        self.fake.paused = False
        self.env.reset()
        self.chat = ChatChannel(ChatLog(self.config.chat_path), "SWE", self.run_dir / "seen.json")
        self.env.attach_chat(self.chat)
        self.planner = LazyClient(nap=10)
        self.loop = AgentLoop(self.env, self.planner, self.config,
                              transcript_path=self.run_dir / "t.jsonl")
        self.loop.supervisor = CoopGuidance(self.run_dir, self.loop)
        self.person = ChatLog(self.config.chat_path)

    def wake(self, text: str = "", *calls) -> list[dict]:
        """Queue one model answer and play turns until the model has been asked once."""
        self.planner.script.append(say(text, *calls))
        before = len(self.planner.calls)
        for _ in range(12):
            self.loop.run(1)
            if len(self.planner.calls) > before:
                return self.records("actions")[-1:]
        raise AssertionError("the model was never woken")

    def records(self, kind: str) -> list[dict]:
        lines = (self.run_dir / "t.jsonl").read_text(encoding="utf-8").splitlines()
        return [r for r in map(json.loads, lines) if r["kind"] == kind]

    def said(self) -> list[str]:
        return [m.text for m in self.person.messages() if m.frm == "SWE"]

    def tools(self) -> set[str]:
        return self.env.allowed_actions


@pytest.fixture
def table(tmp_path):
    return Table(tmp_path, human_domains=["construction"])


# --- the split ---------------------------------------------------------------------------


def test_31_the_model_acts_in_its_domains_and_never_in_the_persons(table):
    assert "queue_construction" not in table.tools() and "set_national_focus" in table.tools()
    refused = table.env.act(ActionCall(*BUILD), "1936-01-01")
    assert not refused.ok and "player holds" in refused.message
    results = table.wake("focus", FOCUS)[0]["results"]
    assert not results[0]["is_error"]
    held = "Held by the player, not yours (do not act there): construction"
    assert held in table.env.observe().brief


def test_31_the_model_explains_what_it_did_in_chat(table):
    table.wake("", FOCUS)
    lines = table.said()
    assert len(lines) == 1 and lines[0].startswith("This turn I did: set_national_focus")
    assert table.records("coop_explained")[0]["ok"] is True


def test_31_a_model_that_spoke_for_itself_is_not_echoed(table):
    table.wake("", FOCUS, ("send_message", {"to": "person", "text": "Industry first, then rifles"}))
    assert table.said() == ["Industry first, then rifles"]


# --- the person moves a domain ---------------------------------------------------------------


def test_31_taking_a_domain_back_by_command_removes_the_tools_at_once(table):
    assert "set_national_focus" in table.tools()
    DomainBoard(table.run_dir).post("focus", "human")
    table.loop.run(1)
    assert "set_national_focus" not in table.tools()
    assert table.config.control_map().controller("focus") == "human"
    assert [r["to"] for r in table.records("domain_change")] == ["human"]
    refused = table.env.act(ActionCall(*FOCUS), "1936-01-02")
    assert not refused.ok and "player holds" in refused.message
    held = "Held by the player, not yours (do not act there): focus, construction"
    assert held in table.env.observe().brief
    table.wake("")
    briefs = [r["brief"] for r in table.records("observe")]
    assert sum("the player took focus back" in b for b in briefs) == 1      # said once
    assert any("Focus is yours again" in line for line in table.said())


def test_31_taking_a_domain_back_in_chat_works_the_same(table):
    table.person.post("person", "SWE", "take back the politics")
    table.loop.run(1)
    assert "take_decision" not in table.tools()
    assert table.records("domain_change")[0]["source"] == "chat"


def test_31_handing_a_domain_over_gives_the_tools_and_wakes_the_model(table):
    assert "queue_construction" not in table.tools()
    table.person.post("person", "global", "give construction")
    calls_before = len(table.planner.calls)
    table.planner.script.append(say("construction noted"))
    table.loop.run(2)
    assert "queue_construction" in table.tools()
    assert len(table.planner.calls) > calls_before            # woken for it, without a timer
    assert any("handed you construction" in str(r.get("wake_reason"))
               for r in table.records("observe"))
    assert any(line.startswith("Construction is mine now") for line in table.said())
    assert any("the player handed construction to you" in r["brief"]
               for r in table.records("observe"))


def test_31_a_handover_and_a_take_back_round_trip(table):
    board = DomainBoard(table.run_dir)
    board.post("construction", "llm")
    table.loop.run(1)
    assert "queue_construction" in table.tools()
    board.post("construction", "human")
    table.loop.run(1)
    assert "queue_construction" not in table.tools()
    assert [r["to"] for r in table.records("domain_change")] == ["llm", "human"]


def test_31_what_the_model_queued_in_a_domain_taken_back_does_not_run(tmp_path):
    from hoi4_harness.coop import wire_handover

    t = Table(tmp_path, handover=True, handover_mode="hotkey")
    wire_handover(t.env, t.config)
    t.wake("", FOCUS)
    assert [p.call.name for p in t.env.handover.pending] == ["set_national_focus"]
    DomainBoard(t.run_dir).post("focus", "human")
    t.loop.run(1)
    t.env.handover.grant()
    report = t.env.run_handover()
    assert report.ran == []
    assert "player holds" in report.failed[0]["reason"]
    assert t.fake.world.state.national_focus is None


def test_31_a_domain_the_script_or_the_game_runs_is_not_the_persons_to_trade(table):
    table.config.control = {"research": "off"}
    table.loop.refresh_control()
    DomainBoard(table.run_dir).post("research", "human")
    table.loop.run(1)
    assert table.config.control_map().controller("research") == "off"
    assert any("research is off" in line.lower() for line in table.said())


def test_31_commands_and_chat_say_the_same_thing(table):
    DomainBoard(table.run_dir).post("politics", "human")
    table.person.post("person", "SWE", "give politics")
    table.loop.run(1)
    assert [r["to"] for r in table.records("domain_change")] == ["human", "llm"]
    assert table.config.control_map().controller("politics") == "llm"


def test_31_a_plain_run_with_no_human_domain_can_still_have_one_taken(tmp_path):
    t = Table(tmp_path)
    assert "queue_construction" in t.tools()
    DomainBoard(t.run_dir).post("construction", "human")
    t.loop.run(1)
    assert "queue_construction" not in t.tools() and "set_national_focus" in t.tools()


# --- what counts as a command -----------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("take army", [("army", "human")]),
    ("/take back the navy and air", [("navy", "human"), ("air", "human")]),
    ("Take the army, please", [("army", "human")]),
    ("give army", [("army", "llm")]),
    ("hand over diplomacy", [("diplomacy", "llm")]),
    ("you take the navy", [("navy", "llm")]),
    ("give the economy back", [(d, "llm") for d in ("focus", "research", "construction",
                                                     "production")]),
    ("take all", [(d, "human") for d in DOMAINS]),
    ("I will take a look at the army later", []),
    ("take care of the army", []),
    ("what is the army doing", []),
    ("take", []),
    ("", []),
])
def test_31_the_chat_parser_reads_commands_and_not_talk(text, expected):
    assert parse_domain_message(text, DOMAINS) == expected


def test_31_the_domains_command_posts_and_lists(tmp_path, capsys):
    run = tmp_path / "run"
    run.mkdir()
    assert main(["domains", "--run-dir", str(run), "--take", "army", "--give", "air"]) == 0
    assert main(["domains", "--run-dir", str(run), "--take", "nonsense"]) == 1
    capsys.readouterr()
    assert [(r["domain"], r["to"]) for r in DomainBoard(run).rows()] == [("army", "human"),
                                                                       ("air", "llm")]
    assert main(["domains", "--run-dir", str(run)]) == 0
    assert "army -> you" in capsys.readouterr().out
    assert main(["domains", "--run-dir", str(tmp_path / "nowhere"), "--take", "army"]) == 1


def test_31_the_incoming_seam_carries_a_chat_the_harness_cannot_read_itself(tmp_path):
    """Multiplayer: the game's chat cannot be read back, so a reader is injected."""
    t = Table(tmp_path)
    lines = ["take construction"]
    t.loop.supervisor.incoming = lambda: [lines.pop()] if lines else []
    t.loop.run(1)
    assert "queue_construction" not in t.tools()
