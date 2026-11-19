"""Several rivals in one game (rival/multi.py) against a fake game with a person in it.

FRA, ENG and ITA are AI countries the harness steers; GER is the person's. Each rival has its own
adapter, loop, queue, budget share and folder; the window, the key guard and the clock wait are
shared. Everything here is the real stack over ``FakeRivalGame`` (with ``extras``) and a
scripted provider: no model is called and no key reaches this machine. What it cannot say is in
docs/rival.md ("What is proved and what is not").
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hoi4_harness.agent.llm.base import Usage
from hoi4_harness.fakerival import HUMAN_PP, human_cycle
from hoi4_harness.handover import Pending
from hoi4_harness.rival.multi import MultiRivalSession, run_rivals, split_budget
from hoi4_harness.rival.setup import RivalConfigError, apply_defaults, parse_rivals
from hoi4_harness.types import ActionCall
from rival_helpers import LazyClient, acts, build_env, rival_config, say

TAGS = ("FRA", "ENG", "ITA")
INVADE = ("set_ai_directive", {"directive": "invade", "target": "POL"})
THEATER = ("set_ai_posture", {"posture": "offensive", "theater": "POL"})


def nap(days: int = 30):
    return ("advance_time", {"days": days, "reason": "let it run"})


def play_many(tmp_path, tags=TAGS, script=None, wakes=None, *, until="1936-08-01", naps=None,
              usage=None, setup=None, config=None, **overrides):
    """One several-rival run. ``wakes[tag]`` are that country's replies to its first wakes (then
    it naps ``naps[tag]`` days, default 30); ``usage[tag]`` what each of its calls costs."""
    wakes, naps, usage = wakes or {}, naps or {}, usage or {}
    if config is None:
        cap = overrides.pop("max_usd", None)
        config = rival_config(tmp_path, script=script, rival=",".join(tags), **overrides)
        config.budget.max_usd = cap
    env = build_env(config)
    fake = env.adapter.fake_game
    for k, tag in enumerate(tags):
        # a number that is this country's alone, in the brief: its civilian factories
        fake.country_game(tag).world.state.civilian_factories = 61 + k
    if setup is not None:
        setup(fake)
    planners = {tag: LazyClient(script=[say(text, *calls) for text, calls in wakes.get(tag, [])],
                                nap=naps.get(tag, 30), usage=usage.get(tag)) for tag in tags}
    report, summary = run_rivals(config, planners=planners, env=env, until=until)
    return SimpleNamespace(fake=fake, summary=summary, report=report, planners=planners,
                           run=Path(config.run_dir), config=config)


def records(res, tag, kind):
    path = res.run / tag / "transcript.jsonl"
    return [r for r in map(json.loads, path.read_text(encoding="utf-8").splitlines())
            if r["kind"] == kind]


def top_log(res):
    return (res.run / "rival.log").read_text(encoding="utf-8")


def game_log(fake):
    return fake.log_path.read_text(encoding="utf-8")


def run_with(fake, needle):
    return next(i for i, run in enumerate(fake.runs) if any(needle in line for line in run))


# --- the list of countries is checked ---------------------------------------------------------


def test_a_comma_list_is_split_checked_and_kept_in_order():
    assert parse_rivals("fra, eng,ITA") == ["FRA", "ENG", "ITA"]


@pytest.mark.parametrize("spec, message", [
    ("FRA,ENG,FRA", "FRA more than once"),
    ("FRA,EN", "three letters"),
    ("FRA,ENGL", "three letters"),
    ("FRA,GER", "GER is the country you play"),
    ("", "wants a country tag"),
])
def test_a_bad_list_is_refused_with_the_reason(spec, message):
    with pytest.raises(RivalConfigError, match=message):
        parse_rivals(spec, human=["GER"])


def test_majors_are_the_seven_great_powers_minus_the_persons_country():
    assert parse_rivals("majors", human=["GER"]) == ["ENG", "FRA", "ITA", "SOV", "USA", "JAP"]
    assert parse_rivals("majors", human=["FRA"]) == ["GER", "ENG", "ITA", "SOV", "USA", "JAP"]
    with pytest.raises(RivalConfigError, match="needs to know which country you play"):
        parse_rivals("majors")


def test_the_person_cannot_be_a_rival_when_the_log_says_who_they_are(tmp_path):
    with pytest.raises(RivalConfigError, match="GER is the country you play"):
        rival_config(tmp_path, rival="FRA,GER")


def test_the_person_is_found_in_the_log_by_their_ai_equals_zero_state_line(tmp_path):
    from hoi4_harness.rival.setup import human_tags_in_log

    log = tmp_path / "game.log"
    log.write_text("[a][1936.01.01.12]: LLMB|v5|state|tag=POL|pp=1|ai=0|techs=1\n"
                   "[a][1936.01.01.12]: LLMB|v5|state|tag=FRA|pp=1|ai=1|techs=1\n", encoding="utf-8")
    assert human_tags_in_log(log) == ["POL"]
    assert human_tags_in_log(tmp_path / "none.log") == []


def test_a_single_tag_is_still_the_one_country_form(tmp_path):
    config = rival_config(tmp_path, rival="SWE")
    assert config.rivals == [] and config.rival == "SWE"


def test_a_window_cannot_be_smaller_than_the_number_of_countries(tmp_path):
    with pytest.raises(RivalConfigError, match="rival-window-cap"):
        rival_config(tmp_path, rival="FRA,ENG,ITA", rival_window_cap=2)


def test_the_budget_splits_equally_unless_a_share_says_otherwise(tmp_path):
    config = rival_config(tmp_path, rival="FRA,ENG,ITA")
    config.budget.max_usd = 9.0
    assert split_budget(config, list(TAGS)) == {"FRA": 3.0, "ENG": 3.0, "ITA": 3.0}
    config.rival_shares = {"FRA": 2.0}
    assert split_budget(config, list(TAGS)) == {"FRA": 4.5, "ENG": 2.25, "ITA": 2.25}
    config.rival_shares = {"POL": 1.0}
    with pytest.raises(RivalConfigError, match="POL, which is not a rival"):
        apply_defaults(config)


# --- marking --------------------------------------------------------------------------------------


def test_every_rival_is_marked_in_one_window_and_unmarked_in_one(tmp_path):
    res = play_many(tmp_path, script=human_cycle(run=30, pause=60), until="1936-05-01",
                    rival_exit_wait_seconds=500)
    fake = res.fake
    assert fake.runs[0][:3] == [f"effect {t} llm_bridge_mark_harness" for t in TAGS]
    assert fake.runs[-1] == [f"effect {t} llm_bridge_unmark_harness" for t in TAGS]
    assert all("llmb_harness" not in fake.country_game(t).flags for t in TAGS)
    log = game_log(fake)
    for tag in TAGS:
        assert f"|evt|tag={tag}|kind=16" in log and f"|evt|tag={tag}|kind=17" in log
    assert "FRA, ENG, ITA unmarked" in top_log(res)


def test_a_rival_that_already_reports_is_left_alone_and_the_others_are_marked(tmp_path):
    def premark(fake):
        fake.flags.add("llmb_harness")
        fake.emit_state()
    res = play_many(tmp_path, script=human_cycle(run=30, pause=60), until="1936-04-20",
                    setup=premark)
    first = [ln for ln in res.fake.runs[0] if "mark_harness" in ln]
    assert first == ["effect ENG llm_bridge_mark_harness", "effect ITA llm_bridge_mark_harness"]
    assert "FRA already reports to the log: marking skipped" in top_log(res)


def test_without_a_window_at_exit_it_says_what_to_type_for_every_country(tmp_path):
    res = play_many(tmp_path, script=acts((0, "resume"), (20, "pause"), (45, "resume")),
                    until="1936-05-01", rival_exit_wait_seconds=20)
    assert all("llmb_harness" in res.fake.country_game(t).flags for t in TAGS)
    assert ("To undo it type in the console: effect FRA llm_bridge_unmark_harness; "
            "effect ENG llm_bridge_unmark_harness; effect ITA llm_bridge_unmark_harness"
            ) in top_log(res)


def test_a_crash_after_the_start_still_unmarks_every_rival(tmp_path, monkeypatch):
    from hoi4_harness.agent.loop import AgentLoop

    def boom(self, *args, **kwargs):
        raise RuntimeError("the loop fell over")
    monkeypatch.setattr(AgentLoop, "decide", boom)
    with pytest.raises(RuntimeError, match="fell over"):
        play_many(tmp_path, script=human_cycle(run=30, pause=60), rival_exit_wait_seconds=500)
    # (play_many raised before returning the fake: read it back from the log of the run)
    log = (tmp_path / "run" / "rival.log").read_text(encoding="utf-8")
    assert "CRASH: RuntimeError: the loop fell over" in log
    assert "FRA, ENG, ITA unmarked" in log


# --- each country reads only its own lines ------------------------------------------------------


def test_each_country_reads_only_its_own_lines_never_another_rivals_or_the_persons(tmp_path):
    res = play_many(tmp_path, script=human_cycle(run=30, pause=60), until="1936-06-01")
    log = game_log(res.fake)
    # the shared log really holds all of them, with numbers that are each one's alone...
    for tag in TAGS:
        assert "|state|" in log and f"tag={tag}|" in log
    assert f"pp={HUMAN_PP}" in log
    for k in range(3):
        assert f"civ={61 + k}|" in log
    # ...and each brief shows only its own
    for k, tag in enumerate(TAGS):
        briefs = [r["brief"] for r in records(res, tag, "observe")]
        assert briefs and all(b.startswith(f"## {tag}") or f"## {tag}" in b for b in briefs)
        assert any(f"{61 + k} civ" in b for b in briefs)         # its own, at least once
        for brief in briefs:
            for other in range(3):
                if other != k:
                    assert f"{61 + other} civ" not in brief
            assert "777" not in brief and "## GER" not in brief
            for other in TAGS:
                if other != tag:
                    assert f"## {other}" not in brief


def test_a_war_between_two_rivals_is_seen_by_those_two_and_not_by_the_third(tmp_path):
    def declare(fake):
        fake.war_script["1936-05-10"] = ("declare", "ENG", 30)     # ENG declares on FRA
    res = play_many(tmp_path, script=human_cycle(run=30, pause=60), until="1936-07-01",
                    setup=declare, naps={"FRA": 60, "ENG": 60, "ITA": 60})
    fra = [r["brief"] for r in records(res, "FRA", "observe")]
    assert any("war declared: ENG on FRA" in b for b in fra)
    ita = [r["brief"] for r in records(res, "ITA", "observe")]
    assert ita and not any("ENG" in b or "FRA" in b or "war declared" in b for b in ita)


def test_each_country_has_its_own_folder_transcript_notes_and_log(tmp_path):
    res = play_many(tmp_path, script=human_cycle(run=30, pause=60), until="1936-05-15")
    for tag in TAGS:
        folder = res.run / tag
        assert {"transcript.jsonl", "rival.log", "memory.json", "campaign_progress.jsonl",
                "handover-queue.json"} <= {p.name for p in folder.iterdir()}
        series = [json.loads(ln) for ln in (folder / "campaign_progress.jsonl").read_text(
            encoding="utf-8").splitlines()]
        assert series and all(row.get("tag", tag) == tag for row in series)
    assert (res.run / "rival-summary.json").exists()


# --- one window, one file, every country's actions, each in its own scope ---------------------


def test_one_window_types_several_countries_actions_in_one_file_each_in_its_own_scope(tmp_path):
    res = play_many(
        tmp_path, script=human_cycle(run=40, pause=60, cycles=30), until="1936-07-01",
        wakes={"FRA": [("go", [INVADE, nap(60)])], "ENG": [("go", [THEATER, nap(60)])],
               "ITA": [("go", [INVADE, nap(60)])]}, naps={t: 60 for t in TAGS},
        rival_stagger_days=0)         # all three decide before the first pause: one window
    fake = res.fake
    run = fake.runs[run_with(fake, "effect FRA llm_bridge_raise_invade")]
    assert any(ln == "effect ITA llm_bridge_raise_invade" for ln in run)
    assert any(ln == "effect ENG llm_bridge_raise_theater_offensive" for ln in run)
    # the directive and the posture landed on the right countries, and only those
    assert fake.country_game("FRA").directives == ["invade POL"]
    assert fake.country_game("ITA").directives == ["invade POL"]
    assert fake.country_game("ENG").directives == []
    assert fake.country_game("ENG").theater_postures == {"POL": "offensive"}
    assert fake.errors == []
    # every line of every run is in one of the rivals' scopes, never the person's
    lines = [ln for r in fake.runs for ln in r]
    assert all(ln.startswith(tuple(f"effect {t} " for t in TAGS)
                             + tuple(f"eval_effect {t} = " for t in TAGS)) for ln in lines)
    assert not any("GER" in ln for ln in lines)
    # one window: each country's own report says what ITS part did
    for tag in TAGS:
        window = records(res, tag, "handover")[0]
        assert len(window["ran"]) == 1 and window["ran"][0]["action"] in (
            "set_ai_directive", "set_ai_posture")
    assert fake.speed_log == [] and fake.pause_in is None and fake.stray_keys == []


def test_the_person_coming_back_mid_window_stops_every_countrys_actions(tmp_path):
    kw = dict(until="1936-06-01", wakes={t: [("go", [INVADE, nap(60)])] for t in TAGS},
              naps={t: 60 for t in TAGS}, rival_stagger_days=0)
    learn = play_many(tmp_path / "learn", script=human_cycle(run=40, pause=60, cycles=30), **kw)
    wall = learn.fake.run_walls[run_with(learn.fake, "llm_bridge_raise_invade")]
    script = acts((0, "resume"), (40, "pause"), (wall - 1.0, "type", "x"), (300, "resume"))
    # same game, the person presses a key one second before the shared file would have run
    first = play_many(tmp_path / "stopped", script=human_cycle(run=40, pause=60, cycles=30), **kw)
    assert first.fake.errors == []
    stopped = play_many(tmp_path / "go", script=script, **kw)
    log = top_log(stopped)
    assert "stopped (the person started using the keyboard or mouse)" in log
    for tag in TAGS:
        window = records(stopped, tag, "handover")[0]
        assert window["aborted"] and window["ran"] == []
    assert not stopped.fake.console_open and stopped.fake.line == ""
    assert stopped.fake.errors == [] and "escape" not in stopped.fake.keylog


def premark_all(fake):
    """The person typed the marks themselves (policy never does not): every rival reports."""
    for tag in TAGS:
        game = fake.country_game(tag)
        game.flags.add("llmb_harness")
        game.emit_state()


def test_under_never_every_countrys_actions_are_shown_and_nothing_is_typed(tmp_path):
    res = play_many(
        tmp_path, script=human_cycle(run=40, pause=60, cycles=30), until="1936-06-01",
        wakes={"FRA": [("go", [INVADE, nap(60)])], "ENG": [("go", [THEATER, nap(60)])]},
        rival_act_when="never", rival_stagger_days=0, setup=premark_all)
    assert res.fake.runs == [] and res.fake.keylog == []
    assert "shown, not run" in top_log(res)


# --- fair ordering and the cap ------------------------------------------------------------------


def entries(tag, n):
    return [Pending(ActionCall("set_ai_directive", {"directive": "invade", "target": f"T{i}"}),
                    "1936-04-01", 1, i + 1) for i in range(n)]


def chooser(cap, rotation=0):
    session = MultiRivalSession.__new__(MultiRivalSession)
    session.config = SimpleNamespace(rival_window_cap=cap)
    session.members = [SimpleNamespace(tag=t) for t in TAGS]
    session._rotation = rotation
    return session


def test_a_window_takes_one_action_per_country_round_and_round_up_to_the_cap():
    session = chooser(cap=5)
    runnable = {"FRA": entries("FRA", 9), "ENG": entries("ENG", 1), "ITA": entries("ITA", 3)}
    chosen = session._choose(session._order(), runnable)
    assert {t: len(v) for t, v in chosen.items()} == {"FRA": 2, "ENG": 1, "ITA": 2}
    assert [e.position for e in chosen["FRA"]] == [1, 2]            # oldest first, in order


def test_the_country_that_went_first_goes_last_in_the_next_window():
    first = chooser(cap=4, rotation=0)
    second = chooser(cap=4, rotation=1)
    runnable = {t: entries(t, 4) for t in TAGS}
    a = first._choose(first._order(), runnable)
    b = second._choose(second._order(), runnable)
    assert len(a["FRA"]) == 2 and len(b["FRA"]) == 1 and len(b["ENG"]) == 2


def test_the_cap_leaves_the_rest_queued_for_the_next_window(tmp_path):
    many = [("set_ai_directive", {"directive": "invade", "target": t})
            for t in ("POL", "FIN", "SOV")]
    res = play_many(
        tmp_path, script=human_cycle(run=40, pause=60, cycles=30), until="1936-08-01",
        wakes={"FRA": [("go", many + [nap(60)])], "ENG": [("go", [THEATER, nap(60)])]},
        naps={t: 60 for t in TAGS}, rival_window_cap=3, rival_stagger_days=0)
    fra, eng = records(res, "FRA", "handover"), records(res, "ENG", "handover")
    # three FRA actions and one ENG action, a window of three: FRA takes two, ENG its one...
    assert len(fra[0]["ran"]) == 2 and fra[0]["left_queued"] == 1
    assert len(eng[0]["ran"]) == 1 and eng[0]["granted_on"] == fra[0]["granted_on"]
    # ...and FRA's third goes out in a later window, nothing is lost
    assert sum(len(r["ran"]) for r in fra) == 3


# --- decisions are staggered, budgets are per country ------------------------------------------


def test_the_countries_do_not_all_call_the_model_on_the_same_day(tmp_path):
    res = play_many(tmp_path, script=human_cycle(run=30, pause=60), until="1936-06-15",
                    rival_stagger_days=4)
    dates = {}
    for tag in TAGS:
        woke = [r["from_date"] for r in records(res, tag, "turn_end") if r["woke"]]
        dates[tag] = woke
        assert woke
    firsts = [d[0] for d in dates.values()]
    assert len(set(firsts)) == 3                          # three different days to start with
    for day in set().union(*map(set, dates.values())):
        assert sum(day in d for d in dates.values()) < 3  # never all three on one day


def test_one_chatty_country_runs_out_of_its_share_and_the_others_carry_on(tmp_path):
    # $2 for the three: $0.667 each. FRA's calls cost $0.50 and it naps 7 days; the others cost
    # a cent and nap 45.
    res = play_many(
        tmp_path, script=human_cycle(run=30, pause=60), until="1936-09-01", max_usd=2.0,
        naps={"FRA": 7, "ENG": 45, "ITA": 45},
        usage={"FRA": Usage(100_000, 5_000), "ENG": Usage(100, 10), "ITA": Usage(100, 10)})
    per = res.summary["rivals"]
    assert per["FRA"]["budget_stop"] and "spend ceiling" in per["FRA"]["budget_stop"]
    assert per["FRA"]["usd"] <= per["FRA"]["share_usd"] + 0.5      # at most one call over
    assert per["ENG"]["budget_stop"] is None and per["ITA"]["budget_stop"] is None
    assert per["ENG"]["planner_calls"] > per["FRA"]["planner_calls"] >= 1
    assert res.summary["reached_end_date"] and not res.summary["stopped_reason"]
    # FRA made no model call after it stopped; the transcript says it was downgraded
    assert records(res, "FRA", "budget_downgrade")
    assert len(records(res, "FRA", "llm")) == per["FRA"]["planner_calls"]
    assert res.summary["spend"]["usd"] == pytest.approx(sum(c["usd"] for c in per.values()), abs=1e-3)
    assert res.summary["spend"]["usd"] <= 2.0 + 0.5
    assert "budget: spend ceiling" in top_log(res)


def test_when_every_share_is_spent_the_run_stops_and_says_so(tmp_path):
    res = play_many(
        tmp_path, script=human_cycle(run=30, pause=60), until="1936-12-01", max_usd=1.5,
        naps={t: 7 for t in TAGS}, usage={t: Usage(100_000, 5_000) for t in TAGS})
    assert "every country's budget is spent" in res.summary["stopped_reason"]
    assert not res.summary["reached_end_date"]


def test_spend_is_reported_per_country_in_the_summary_and_the_log(tmp_path):
    res = play_many(tmp_path, script=human_cycle(run=30, pause=60), until="1936-05-15",
                    max_usd=30.0, usage={t: Usage(10_000, 500) for t in TAGS})
    per = res.summary["rivals"]
    assert set(per) == set(TAGS)
    assert all(per[t]["usd"] > 0 and per[t]["share_usd"] == 10.0 for t in TAGS)
    log = top_log(res)
    assert "SUMMARY per country" in log and all(f"  {t}: " in log for t in TAGS)
    saved = json.loads((res.run / "rival-summary.json").read_text(encoding="utf-8"))
    assert saved["rivals"]["FRA"]["ran"] == per["FRA"]["ran"]


def test_a_priced_model_still_needs_a_total_cap(tmp_path):
    from hoi4_harness.rival.runner import RivalStartError, run_rival

    config = rival_config(tmp_path, rival="FRA,ENG,ITA")
    config.planner.provider = "anthropic"
    with pytest.raises(RivalStartError, match="max-usd"):
        run_rival(config, until="1936-06-01")
