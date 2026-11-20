"""#105: the campaign plays on after the player's own capitulation (fake game only).

The model is woken the turn it is seen, every later brief says it, and the run ends only
when the country holds no states. Nothing here touches the real game or sends input."""

from __future__ import annotations

import json

from campaign_helpers import LazyClient, build_env, campaign_config
from hoi4_harness.agent.policy import CampaignPacing, Policy
from hoi4_harness.campaign.report import write_report
from hoi4_harness.campaign.runner import load_state, run_campaign
from hoi4_harness.config import HarnessConfig
from hoi4_harness.types import CampaignStats, GameEvent, GameState

DAY = "1936-04-10"


class Recorder(LazyClient):
    """A lazy model that keeps every brief it was shown, in order."""

    def __init__(self, nap=20):
        super().__init__(nap=nap)
        self.briefs: list[str] = []

    def _policy(self, messages, tools):
        self.briefs.append(next((m.text for m in reversed(messages) if m.role == "user" and m.text), ""))
        return super()._policy(messages, tools)


def hook_ticks(env, day, then, to="SOV"):
    """Run ``then(fake)`` the day the clock reaches ``day``, after the day's own tick."""
    fake = env.adapter.fake_game
    tick = fake.tick_day

    def tick_then():
        tick()
        if fake.date == day:
            then(fake)

    fake.tick_day = tick_then
    return fake


def play(tmp_path, *, then=None, client=None, until="1936-09-01", **options):
    config = campaign_config(tmp_path, campaign_until=until, **options)
    env = build_env(config)
    hook_ticks(env, DAY, then or (lambda fake: fake.player_capitulates(to="SOV")))
    client = client or Recorder()
    _, summary = run_campaign(config, planner=client, env=env)
    return summary, client


def transcript(tmp_path):
    return [json.loads(x) for x in
            (tmp_path / "run" / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]


def test_105_the_default_is_to_play_on(tmp_path):
    assert HarnessConfig().campaign_stop_on_capitulation is False
    summary, _ = play(tmp_path)
    assert summary["stopped_reason"] is None
    assert summary["reached_end_date"] and summary["end_date"] >= "1936-09-01"


def test_105_the_switch_still_stops_with_the_old_reason(tmp_path):
    summary, _ = play(tmp_path, campaign_stop_on_capitulation=True)
    assert summary["stopped_reason"].startswith("GER capitulated (")
    assert not summary["reached_end_date"]


def test_105_the_model_is_woken_the_turn_it_is_seen_inside_the_wake_gap():
    """A critical event normally waits out campaign_wake_gap_days; the player's own
    capitulation does not."""
    policy = Policy(campaign=CampaignPacing(wake_gap_days=7))
    state = GameState(country="GER", date="1941-09-15", events=[
        GameEvent("capitulation", "capitulation: GER (to SOV)", "critical", date="1941-09-15")])
    decision = policy.should_wake(state, days_since_planner=1)
    assert decision.wake and decision.urgency == "critical"
    assert "capitulated" in decision.reason and "SOV" in decision.reason
    enemy = GameState(country="GER", date="1941-09-15", events=[
        GameEvent("capitulation", "capitulation: POL (to GER)", "critical")])
    assert not policy.should_wake(enemy, days_since_planner=1).wake


def test_105_the_wake_reaches_the_model_with_the_capitulation_in_its_brief(tmp_path):
    summary, client = play(tmp_path)
    assert summary["reached_end_date"]
    woke = [r for r in transcript(tmp_path)
            if r["kind"] == "wake" or "capitulated on" in json.dumps(r)]
    assert woke, "the transcript never mentions the capitulation"
    first = next(i for i, b in enumerate(client.briefs) if "CAPITULATED" in b)
    brief = client.briefs[first]
    assert "GER CAPITULATED on 1936-04-" in brief and "to SOV" in brief
    assert "government in exile" in brief
    # the wake came right after it happened, not a scheduled review weeks later
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "wake: your own country capitulated" in log
    assert "GER CAPITULATED on" in log


def test_105_later_briefs_keep_saying_it(tmp_path):
    _, client = play(tmp_path)
    flagged = [b for b in client.briefs if "CAPITULATED" in b]
    assert len(flagged) >= 3, len(client.briefs)
    first = next(i for i, b in enumerate(client.briefs) if "CAPITULATED" in b)
    assert all("CAPITULATED" in b for b in client.briefs[first:])
    assert not any("CAPITULATED" in b for b in client.briefs[:first])


def test_105_state_file_and_report_record_it(tmp_path):
    play(tmp_path)
    cap = load_state(tmp_path / "run")["player_capitulation"]
    assert cap["tag"] == "GER" and cap["to"] == "SOV" and cap["date"].startswith("1936-04")
    text, _ = write_report(tmp_path / "run")
    summary = text.split("## Summary")[1].split("\n## ")[0]
    assert "Own capitulation" in summary and "GER capitulated on 1936-04" in summary


def test_105_a_resumed_run_still_knows(tmp_path):
    play(tmp_path, until="1936-06-01")
    config = campaign_config(tmp_path, campaign_until="1936-09-01")
    client = Recorder()
    run_campaign(config, planner=client, resume=True, env=build_env(config))
    assert client.briefs and all("CAPITULATED" in b for b in client.briefs)


def zero_states(fake):
    fake.player_capitulates(to="SOV")
    fake.ai.player_states = 0


def test_105_the_run_stops_when_the_country_holds_no_states(tmp_path):
    summary, _ = play(tmp_path, then=zero_states)
    reason = summary["stopped_reason"]
    assert reason and "holds no states" in reason and "GER" in reason
    assert not summary["reached_end_date"]
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "STOP: GER holds no states" in log


def test_105_one_empty_weekly_report_is_not_the_end(tmp_path):
    def blip(fake):
        fake.player_capitulates(to="SOV")
        fake.ai.player_states = 0

    fake_days = {"restored": False}

    def then(fake):
        blip(fake)
        tick = fake.tick_day

        def restore_next_week():
            tick()
            if fake.date_ordinal % 7 == 1 and not fake_days["restored"]:
                fake.ai.player_states = 12
                fake_days["restored"] = True
        fake.tick_day = restore_next_week

    summary, _ = play(tmp_path, then=then)
    assert summary["stopped_reason"] is None, summary["stopped_reason"]


def test_105_a_stale_camp_line_is_not_two_reads():
    """The same weekly line read on two turns counts once."""
    from hoi4_harness.campaign.progress import ProgressTracker
    from hoi4_harness.campaign.runner import CampaignLog, CampaignSupervisor

    config = HarnessConfig(country="GER")
    sup = CampaignSupervisor(config, env=None, tracker=ProgressTracker(), log=CampaignLog(None),
                             run_dir=__import__("pathlib").Path("."), watchdog=None)
    sup.saves = None
    state = GameState(country="GER", date="1941-10-01",
                      campaign=CampaignStats(date="1941-09-29", owned_states=0))
    assert sup._no_states_left(state) is None
    assert sup._no_states_left(state) is None
    state.campaign = CampaignStats(date="1941-10-06", owned_states=0)
    assert "holds no states" in sup._no_states_left(state)
