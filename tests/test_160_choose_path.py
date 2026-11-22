"""#160: the model chooses its country's path before the campaign starts.

Acceptance:
1. ``hoi4-harness paths --country X`` lists the AI behaviour options and the focus tree
   branch summary from the playset files (fixture playset).
2. A campaign with ``campaign_choose_path`` asks the model once before the start and records
   the choice and reason in the run state (scripted model).
3. The lobby step is a click path with the window-in-front check before each click; the rule
   in the first save is read back and logged (the live half is verified by hand).
4. Without a rule for the country the run says so and continues, the choice guidance only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from campaign_helpers import LazyClient, build_env, campaign_config, say
from hoi4_harness import pathchoice
from hoi4_harness.adapters.savegame import game_rule, read_game_rule
from hoi4_harness.campaign import lobby
from hoi4_harness.campaign.runner import (
    CampaignStartError,
    load_state,
    path_rule_lines,
    run_campaign,
)
from hoi4_harness.cli import main
from hoi4_harness.identifiers import IdentifierIndex, Playset

GAME = Path(__file__).parent / "fixtures" / "paths" / "game"


def menu_for(tag):
    return pathchoice.build_menu(IdentifierIndex.build(Playset.from_paths(GAME)), tag)


def reply(option, reason="Because."):
    return say(json.dumps({"choice": option, "reason": reason}))


def play(tmp_path, client, tag="SWE", clicker=None, **overrides):
    config = campaign_config(tmp_path, country=tag, game_dir=GAME, campaign_until="1936-05-01",
                             campaign_choose_path=True, **overrides)
    env = build_env(config)
    result = run_campaign(config, planner=client, env=env, lobby_clicker=clicker)
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    return config, log, result


# --- 1. the paths command ------------------------------------------------------


def test_160_paths_lists_the_ai_behaviour_options_with_names_and_descriptions(capsys):
    assert main(["paths", "--country", "SWE", "--game-dir", str(GAME)]) == 0
    out = capsys.readouterr().out
    assert "SWE_ai_behavior (Sweden)" in out
    assert "DEFAULT: Default [default]" in out
    assert "Sweden turns fascist and joins the Axis." in out
    assert "needs DLC: Fixture DLC" in out
    assert "unrelated_rule" not in out


def test_160_paths_summarises_branches_and_the_focuses_that_commit(capsys):
    assert main(["paths", "--country", "SWE", "--game-dir", str(GAME)]) == 0
    out = capsys.readouterr().out
    assert "Branch Expand Industry [SWE_expand_industry]: 4 focuses" in out
    assert "Branch Northern Mines [SWE_north_root]: 2 focuses" in out
    assert "gets: add_ideas x1, add_research_slot x1" in out
    assert "Join the Axis [SWE_axis_pact]: ideology fascism" in out
    assert "ideology fascism; faction Axis" in out
    assert "The Red Turn [SWE_red_turn]: ideology communism" in out
    assert "Claim Norway [SWE_take_norway]: war goal NOR" in out
    assert "Finnish Protectorate [SWE_puppet_finland]: puppet" in out
    assert "excludes: SWE_red_turn" in out


def test_160_paths_json_is_machine_readable(capsys):
    assert main(["paths", "--country", "SWE", "--game-dir", str(GAME), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [o["name"] for o in data["rules"][0]["options"]] == ["DEFAULT", "FASCIST", "SWE_SOCIALIST"]
    assert {b["root"] for b in data["branches"]} == {"SWE_expand_industry", "SWE_north_root"}


def test_160_paths_for_a_country_without_a_rule_says_so(capsys):
    assert main(["paths", "--country", "DEN", "--game-dir", str(GAME)]) == 0
    assert "none in this playset for DEN" in capsys.readouterr().out


def test_160_paths_needs_a_country(capsys):
    assert main(["paths", "--game-dir", str(GAME)]) == 2


# --- 2. the model chooses once, and it is recorded -----------------------------


def test_160_a_campaign_asks_the_model_once_before_the_start_and_records_the_choice(tmp_path):
    client = LazyClient(script=[reply("FASCIST", "Axis gives the quickest war.")], nap=20)
    config, log, _ = play(tmp_path, client)
    asks = [c for c in client.calls if c[0] == pathchoice.CHOOSE_SYSTEM]
    assert len(asks) == 1
    assert "SWE_ai_behavior" in asks[0][1][0].text and "Join the Axis" in asks[0][1][0].text
    assert client.calls[0][0] == pathchoice.CHOOSE_SYSTEM      # before the first turn
    choice = load_state(tmp_path / "run")["path_choice"]
    assert choice == {"country": "SWE", "rule": "SWE_ai_behavior", "option": "FASCIST",
                      "reason": "Axis gives the quickest war.", "source": "model"}
    assert "path chosen for SWE: FASCIST (SWE_ai_behavior); reason: Axis gives" in log


def test_160_the_choice_shapes_the_guidance_the_model_plays_under(tmp_path):
    client = LazyClient(script=[reply("FASCIST", "Axis gives the quickest war.")], nap=20)
    config, _, _ = play(tmp_path, client)
    assert "the FASCIST path (SWE_ai_behavior)" in config.objective
    assert "Axis gives the quickest war." in config.objective
    turns = [c[1][0].text for c in client.calls if c[0] != pathchoice.CHOOSE_SYSTEM]
    assert turns and all("the FASCIST path" in t for t in turns)


def test_160_a_resumed_run_keeps_the_choice_and_does_not_ask_again(tmp_path):
    play(tmp_path, LazyClient(script=[reply("FASCIST")], nap=20))
    client = LazyClient(script=[reply("DEFAULT")], nap=20)
    config = campaign_config(tmp_path, country="SWE", game_dir=GAME, campaign_until="1936-06-01",
                             campaign_choose_path=True)
    run_campaign(config, planner=client, env=build_env(config), resume=True)
    assert not [c for c in client.calls if c[0] == pathchoice.CHOOSE_SYSTEM]
    assert load_state(tmp_path / "run")["path_choice"]["option"] == "FASCIST"
    assert "the FASCIST path" in config.objective


def test_160_an_unreadable_reply_falls_back_to_the_default_and_says_so(tmp_path):
    client = LazyClient(script=[say("I think the first one.")], nap=20)
    _, log, _ = play(tmp_path, client)
    choice = load_state(tmp_path / "run")["path_choice"]
    assert (choice["option"], choice["source"]) == ("DEFAULT", "default")
    assert "WARNING: the model's reply did not name a valid choice" in log


def test_160_an_option_the_rule_does_not_have_is_not_accepted():
    assert pathchoice.parse_choice('{"choice": "KAISER", "reason": "x"}', menu_for("SWE")) is None
    assert pathchoice.parse_choice('Sure: {"choice": "fascist", "reason": "x"}',
                                   menu_for("SWE")) == ("FASCIST", "x")


def test_160_off_by_default_nothing_is_asked(tmp_path):
    client = LazyClient(nap=20)
    config = campaign_config(tmp_path, country="SWE", game_dir=GAME, campaign_until="1936-05-01")
    run_campaign(config, planner=client, env=build_env(config))
    assert not [c for c in client.calls if c[0] == pathchoice.CHOOSE_SYSTEM]
    assert "path_choice" not in load_state(tmp_path / "run")


# --- 4. no rule for the country -------------------------------------------------


def test_160_without_a_rule_the_run_says_so_and_continues_with_guidance_only(tmp_path):
    client = LazyClient(script=[reply("north", "Hold the north.")], nap=20)
    config, log, (report, summary) = play(tmp_path, client, tag="DEN")
    assert "no AI behaviour rule for DEN in this playset: the choice steers the guidance only" in log
    choice = load_state(tmp_path / "run")["path_choice"]
    assert choice["rule"] is None and choice["option"] == "north"
    assert 'the path "north"' in config.objective
    assert "no lobby step" in log and "lobby: click" not in log
    assert summary["reached_end_date"] is True


# --- 3. the lobby click path ---------------------------------------------------


class Recorder:
    def __init__(self, front=lambda n: True):
        self.clicks, self.checks, self.front = [], 0, front

    def in_front(self):
        self.checks += 1
        return self.front(len(self.clicks))

    def click(self, point, label):
        self.clicks.append((point, label))


POINTS = {"rule_row": [1000, 400], "option:FASCIST": [1200, 450], "rules_close": [900, 900]}


def test_160_the_lobby_path_clicks_rule_option_historical_box_and_start_in_order():
    steps = lobby.steps_for("SWE_ai_behavior", "FASCIST", POINTS)
    rec = Recorder()
    lines = []
    assert lobby.run_steps(steps, rec, lines.append, pause=lambda s: None) == 6
    assert [p for p, _ in rec.clicks] == [
        (1640, 764), (1000, 400), (1200, 450), (900, 900), (1862, 929), (1742, 1028)]
    assert rec.checks == 6                 # the window check ran before every click


def test_160_the_lobby_path_stops_when_the_window_is_not_in_front():
    steps = lobby.steps_for("SWE_ai_behavior", "FASCIST", POINTS)
    rec = Recorder(front=lambda clicked: clicked < 2)
    with pytest.raises(lobby.LobbyError, match="not in front before"):
        lobby.run_steps(steps, rec, lambda t: None, pause=lambda s: None)
    assert len(rec.clicks) == 2


def test_160_an_uncalibrated_step_is_refused_before_the_first_click():
    steps = lobby.steps_for("SWE_ai_behavior", "FASCIST", {})
    rec = Recorder()
    with pytest.raises(lobby.NeedsCalibration, match="rule_row"):
        lobby.run_steps(steps, rec, lambda t: None, pause=lambda s: None)
    assert rec.clicks == [] and rec.checks == 0


def test_160_the_live_clicker_refuses_without_pyautogui():
    with pytest.raises(lobby.LobbyError, match="pyautogui"):
        lobby.LiveClicker("Hearts of Iron IV")


def test_160_a_campaign_with_the_lobby_rule_on_runs_the_click_path(tmp_path):
    rec = Recorder()
    client = LazyClient(script=[reply("FASCIST")], nap=20)
    _, log, _ = play(tmp_path, client, clicker=rec, campaign_lobby_rule=True,
                     campaign_lobby_points=POINTS)
    assert len(rec.clicks) == 6
    assert "lobby: SWE_ai_behavior=FASCIST and Historical AI Focuses off clicked" in log


def test_160_a_failed_lobby_step_ends_the_start_with_a_reason(tmp_path):
    rec = Recorder(front=lambda n: False)
    client = LazyClient(script=[reply("FASCIST")], nap=20)
    with pytest.raises(CampaignStartError, match="lobby step failed"):
        play(tmp_path, client, clicker=rec, campaign_lobby_rule=True, campaign_lobby_points=POINTS)
    assert rec.clicks == []
    assert "cannot start: the lobby step failed" in (tmp_path / "run" / "campaign.log").read_text(
        encoding="utf-8")


def test_160_with_the_lobby_off_the_log_says_which_rule_to_set_by_hand(tmp_path):
    _, log, _ = play(tmp_path, LazyClient(script=[reply("FASCIST")], nap=20))
    assert "NOT set in the lobby" in log and "SWE_ai_behavior=FASCIST" in log


# --- 3. the read-back from the first save --------------------------------------


def test_160_the_rule_is_read_back_from_a_save_header(tmp_path):
    text = 'gameplaysettings={\n\thistorical=0\n}\ngame_rules={\n\tSWE_ai_behavior="FASCIST"\n}\n'
    assert game_rule(text, "SWE_ai_behavior") == "FASCIST"
    assert game_rule(text, "GER_ai_behavior") is None
    save = tmp_path / "s.hoi4"
    save.write_text("HOI4txt\n" + text, encoding="utf-8")
    assert read_game_rule(save, "SWE_ai_behavior") == "FASCIST"


def test_160_the_first_save_is_read_back_and_logged_as_matching(tmp_path):
    config = campaign_config(tmp_path, country="SWE", game_dir=GAME, campaign_until="1936-05-01",
                             campaign_save_months=1, campaign_choose_path=True)
    env = build_env(config)
    env.adapter.fake_game.game_rules = {"SWE_ai_behavior": "FASCIST"}
    run_campaign(config, planner=LazyClient(script=[reply("FASCIST")], nap=20), env=env)
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "path rule SWE_ai_behavior = FASCIST in the first save: matches the choice" in log
    assert load_state(tmp_path / "run")["path_rule_in_save"] == "FASCIST"


def test_160_a_save_with_another_option_logs_a_warning(tmp_path):
    config = campaign_config(tmp_path, country="SWE", game_dir=GAME, campaign_until="1936-05-01",
                             campaign_save_months=1, campaign_choose_path=True)
    env = build_env(config)
    env.adapter.fake_game.game_rules = {"SWE_ai_behavior": "DEFAULT"}
    run_campaign(config, planner=LazyClient(script=[reply("FASCIST")], nap=20), env=env)
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "WARNING: path rule SWE_ai_behavior is DEFAULT in the first save but FASCIST" in log


def test_160_a_save_without_the_rule_is_logged_as_unconfirmed():
    line, = path_rule_lines({"rule": "SWE_ai_behavior", "option": "FASCIST"}, None)
    assert "does not record it" in line
