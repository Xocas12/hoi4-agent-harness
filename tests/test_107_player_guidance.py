"""#107: guidance from the person while a campaign runs (`hoi4-harness guide`).

Fake game only. Guidance is advice: it wakes the model once, shows in every brief under its
own heading (new items marked) until cleared, and the harness never executes it."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

from campaign_helpers import LazyClient, build_env, campaign_config
from hoi4_harness.agent.llm.mailbox import CLOSED, wait_for_request, write_answer
from hoi4_harness.campaign import inbox
from hoi4_harness.campaign.runner import run_campaign
from hoi4_harness.cli import main

TEXT = "Take Poland before France; do not attack the USSR"


# --- the inbox file ---------------------------------------------------------------


def test_107_add_list_clear_remove_and_expire(tmp_path):
    now = datetime(2026, 11, 20, 12, 0)
    first = inbox.add(tmp_path, "  Hold the line   in the west ", now=now - timedelta(days=3))
    second = inbox.add(tmp_path, "Puppet Austria", now=now)
    assert (first.id, second.id) == (1, 2) and first.text == "Hold the line in the west"
    assert [g.id for g in inbox.active(tmp_path)] == [1, 2]
    assert inbox.expire(tmp_path, 2, now=now) == [1]
    assert [g.id for g in inbox.active(tmp_path)] == [2]
    assert inbox.add(tmp_path, "third").id == 3            # ids are never reused
    assert inbox.clear(tmp_path, [3]) == [3]
    assert inbox.clear(tmp_path) == [2] and inbox.active(tmp_path) == []


def test_107_a_half_written_line_is_skipped_not_fatal(tmp_path):
    inbox.add(tmp_path, "one")
    with inbox.inbox_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"id": 2, "ts": "2026-11-20T12:00:00", "te')
    assert [g.text for g in inbox.active(tmp_path)] == ["one"]


def test_107_the_guide_command(tmp_path, capsys):
    run = str(tmp_path)
    assert main(["guide", "--run-dir", run, TEXT]) == 0
    assert main(["guide", "--run-dir", run, "--list"]) == 0
    assert TEXT in capsys.readouterr().out
    assert main(["guide", "--run-dir", run, "--clear"]) == 0
    assert inbox.active(tmp_path) == []
    assert main(["guide", "--run-dir", str(tmp_path / "missing"), "x"]) == 1


# --- end to end on the fake campaign ----------------------------------------------------


class Reader(LazyClient):
    """Sleeps long (so a scheduled review is far off), keeps every brief, and runs a hook
    at the start of the Nth call: that is where the 'person' acts."""

    def __init__(self, hooks, nap=60):
        super().__init__(nap=nap)
        self.hooks, self.briefs, self.n = hooks, [], 0

    def _policy(self, messages, tools):
        brief = next((m.text for m in reversed(messages) if m.role == "user" and m.text), "")
        if not messages[-1].tool_results:                  # a new wake, not a tool round
            self.n += 1
            self.briefs.append(brief)
            hook = self.hooks.get(self.n)
            if hook:
                hook()
        return super()._policy(messages, tools)


def play(tmp_path, hooks, **options):
    # Speed 1 is two fake seconds a day, so the wait has many polls for guidance to land in.
    options.setdefault("headless_game_speed", 1)
    config = campaign_config(tmp_path, campaign_until="1936-09-01", **options)
    env = build_env(config)
    client = Reader(hooks(config))
    _, summary = run_campaign(config, planner=client, env=env)
    return summary, client


def from_another_thread(fn):
    thread = threading.Thread(target=fn)
    thread.start()
    thread.join()


def scenario(tmp_path, **options):
    """Wake 1 plain; guidance arrives from another thread while the model sleeps; wake 2 is
    the guidance; wake 3 is the scheduled review with it still standing; it is cleared there;
    wake 4 no longer has it."""
    def hooks(config):
        run = config.run_dir
        return {
            1: lambda: from_another_thread(lambda: inbox.add(run, TEXT)),
            3: lambda: from_another_thread(lambda: main(["guide", "--run-dir", str(run), "--clear"])),
        }
    return play(tmp_path, hooks, **options)


def test_107_new_guidance_wakes_the_model_with_it_in_the_brief(tmp_path):
    summary, client = scenario(tmp_path)
    assert summary["reached_end_date"]
    assert "PLAYER GUIDANCE" not in client.briefs[0]
    second = client.briefs[1]
    assert "PLAYER GUIDANCE" in second and TEXT in second and "NEW #1" in second
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "guidance #1 from the player: " + TEXT in log
    assert "wake: guidance from the player" in log
    # heard now, not at the review two months on: the model slept 60 days after wake 1
    assert "## GER -- 1936-01-0" in second, second[:300]


def test_107_it_persists_in_later_briefs_and_is_no_longer_new(tmp_path):
    _, client = scenario(tmp_path)
    third = client.briefs[2]
    assert "PLAYER GUIDANCE" in third and TEXT in third
    assert "NEW" not in third.split("PLAYER GUIDANCE")[1].split("\n\n")[0]
    assert third.count(TEXT) == 1                          # one standing copy, not a pile


def test_107_clearing_removes_it_from_the_brief_and_does_not_wake(tmp_path):
    _, client = scenario(tmp_path)
    assert len(client.briefs) >= 4
    assert "PLAYER GUIDANCE" not in client.briefs[3] and TEXT not in client.briefs[3]
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert log.count("wake: guidance from the player") == 1


def test_107_each_item_is_logged_in_the_transcript_once(tmp_path):
    scenario(tmp_path)
    rows = [json.loads(x) for x in
            (tmp_path / "run" / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    seen = [r for r in rows if r["kind"] == "guidance"]
    assert len(seen) == 1 and seen[0]["text"] == TEXT and seen[0]["id"] == 1


def test_107_it_works_in_the_independent_mode_too(tmp_path):
    _, client = scenario(tmp_path, campaign_control="llm")
    assert TEXT in client.briefs[1] and "NEW #1" in client.briefs[1]
    assert TEXT in client.briefs[2] and TEXT not in client.briefs[3]


def test_107_a_second_guidance_marks_only_the_new_one(tmp_path):
    def hooks(config):
        run = config.run_dir
        return {1: lambda: from_another_thread(lambda: inbox.add(run, TEXT)),
                2: lambda: from_another_thread(lambda: inbox.add(run, "Join the Axis faction"))}
    _, client = play(tmp_path, hooks)
    third = client.briefs[2].split("PLAYER GUIDANCE")[1]
    assert "NEW #2" in third and "NEW #1" not in third and TEXT in third


def test_107_the_harness_does_not_execute_it(tmp_path):
    """Advice only: a run with guidance and a model that ignores it plays exactly the actions
    the model chose, nothing added."""
    _, client = scenario(tmp_path)
    rows = [json.loads(x) for x in
            (tmp_path / "run" / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    names = {res["name"] for r in rows for res in r.get("results", [])}
    assert names == {"advance_time"}


# --- through the mailbox, which is what a Claude Code player reads --------------------------


def test_107_a_mailbox_player_sees_the_guidance_in_its_request(tmp_path):
    run, mb = tmp_path / "run", tmp_path / "run" / "mailbox"
    stop, requests, guided = threading.Event(), [], []

    def answerer():
        while not stop.is_set():
            state, req = wait_for_request(mb, timeout_seconds=0.5, poll_seconds=0.01)
            if state == "closed":
                return
            if state != "request":
                continue
            last = req["messages"][-1]
            if last.get("results"):
                answer = {"text": "", "calls": [{"name": "advance_time", "arguments": {"days": 60}}]}
            else:
                requests.append(json.dumps(req))
                if len(requests) == 1:
                    try:                                                       # mid-run
                        guided.append(main(["guide", "--run-dir", str(run), TEXT]))
                    except BaseException as exc:      # noqa: BLE001 - never leave the run waiting
                        guided.append(repr(exc))
                answer = {"text": "ok", "calls": [{"name": "advance_time", "arguments": {"days": 60}}]}
            _, problems = write_answer(mb, answer)
            assert not problems, problems

    mb.mkdir(parents=True)
    thread = threading.Thread(target=answerer, daemon=True)
    thread.start()
    try:
        code = main(["play", "--campaign", "--profile", "profiles/campaign-fake.json",
                     "--mailbox", "--until", "1936-06-01", "--run-dir", str(run)])
    finally:
        stop.set()
        thread.join(timeout=5)
    assert code == 0 and (mb / CLOSED).exists()
    assert "PLAYER GUIDANCE" not in requests[0]
    assert len(requests) >= 2
    assert "PLAYER GUIDANCE" in requests[1] and TEXT in requests[1] and "NEW #1" in requests[1]
    assert "guidance from the player" in requests[1]       # the wake reason line of the brief
