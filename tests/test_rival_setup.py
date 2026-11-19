"""Rival mode's configuration, prompt and command line: what it implies, what it refuses."""

from __future__ import annotations

import json

import pytest

from hoi4_harness.agent import hybrid
from hoi4_harness.agent.prompts import CAMPAIGN, RIVAL, RIVAL_WINDOWS, build_system, system_for
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.rival.runner import RivalStartError, run_rival
from hoi4_harness.rival.setup import BACKSTOP_TURNS, RivalConfigError, apply_defaults
from rival_helpers import acts, rival_config

# --- what the mode implies --------------------------------------------------------------------


def test_defaults_give_the_person_the_clock_and_the_game_ai_the_country():
    config = apply_defaults(HarnessConfig(rival="swe"))
    assert (config.rival, config.country) == ("SWE", "SWE")
    assert (config.adapter, config.operational_control, config.clock_owner) == (
        "headless", "game_ai", "player")
    assert config.headless_reader == "logtail" and config.guidance == "rival"
    assert not config.reflex_enabled and not config.require_confirmation
    assert config.turns >= BACKSTOP_TURNS and config.paced and not config.campaign
    assert "SWE" in config.objective


def test_the_model_is_offered_the_directive_verbs_and_never_the_clock_or_the_ais_jobs():
    config = apply_defaults(HarnessConfig(rival="SWE"))
    offered = config.allowed_actions({"set_game_speed", "set_ai_directive", "set_national_focus",
                                      "take_decision", "hire_advisor", "note", "advance_time"})
    assert offered == {"set_ai_directive", "take_decision", "hire_advisor", "note", "advance_time"}
    # structurally, not by asking: even an explicit whitelist cannot give it the clock back
    config.enabled_actions = ["set_game_speed", "set_ai_directive"]
    assert config.allowed_actions({"set_game_speed", "set_ai_directive"}) == {"set_ai_directive"}
    assert "set_game_speed" in hybrid.GAME_AI_OWNED


@pytest.mark.parametrize("fields, message", [
    ({"rival": "SW"}, "country tag"),
    ({"rival": "SWE", "rival_act_when": "sometimes"}, "known: paused, handover, never"),
    ({"rival": "SWE", "campaign": True}, "different modes"),
    ({"rival": "SWE", "advisor": True}, "do not combine"),
    ({"rival": "SWE", "handover": True}, "do not combine"),
])
def test_a_configuration_that_cannot_be_a_rival_run_is_refused(fields, message):
    with pytest.raises(RivalConfigError, match=message):
        apply_defaults(HarnessConfig(**fields))


def test_the_run_refuses_to_start_unless_it_may_type(tmp_path):
    config = rival_config(tmp_path)
    config.dry_run = True
    with pytest.raises(RivalStartError, match="pass --live"):
        run_rival(config)
    config = rival_config(tmp_path, fake_game=False, enforce_window_focus=False)
    with pytest.raises(RivalStartError, match="never types without the game in front"):
        run_rival(config)


def test_a_priced_model_needs_a_cap(tmp_path):
    config = rival_config(tmp_path)
    config.planner.provider = "anthropic"
    with pytest.raises(RivalStartError, match="--max-usd"):
        run_rival(config)


# --- the prompt: fairness and honesty ------------------------------------------------------------


def test_the_prompt_says_what_the_model_is_and_is_not_and_when_its_actions_run():
    config = apply_defaults(HarnessConfig(rival="SWE"))
    system = " ".join(system_for(config).split())          # the prompt wraps its lines
    assert " ".join(RIVAL.split("{")[0].split()) in system and CAMPAIGN not in system
    assert "grand strategist of SWE" in system and "run by the game's own AI" in system
    assert "QUEUED" in system and "do not repeat a call that is already queued" in system
    assert "You do not own the clock. The person does" in system
    assert "You cannot see the person's plans, economy or orders" in system
    assert RIVAL_WINDOWS["paused"] in system and "are not run at all" not in system


@pytest.mark.parametrize("when", ["paused", "handover", "never"])
def test_each_policy_has_its_own_account_of_when_actions_run(when):
    system = " ".join(build_system("rival", operational_control="game_ai", headless=True,
                                   rival="SWE", rival_when=when).split())
    assert RIVAL_WINDOWS[when] in system
    assert all(RIVAL_WINDOWS[other] not in system for other in RIVAL_WINDOWS if other != when)


def test_the_rival_guidance_asks_for_slow_standing_orders():
    system = build_system("rival", operational_control="game_ai", rival="SWE")
    assert "Slow on purpose" in system and "set_ai_directive" in system
    assert "It says nothing about what the person is building or planning" in system


# --- the command line ----------------------------------------------------------------------------------


def test_play_rival_runs_the_whole_stack_on_the_fake_game_and_prints_a_summary(tmp_path, capsys):
    human = tmp_path / "human.json"
    human.write_text(json.dumps([[0, "resume"], [20, "pause"], [60, "resume"], [100, "pause"],
                                 [140, "resume"]]), encoding="utf-8")
    code = main(["play", "--rival", "swe", "--fake-game", "--provider", "scripted",
                 "--run-dir", str(tmp_path / "run"), "--until", "1936-05-01",
                 "--fake-human-script", str(human), "--rival-act-when", "paused",
                 "--rival-paused-seconds", "8", "--rival-idle-seconds", "3"])
    out, err = capsys.readouterr()
    assert code == 0, err
    summary = json.loads(out)
    assert summary["rival"] == "SWE" and summary["reached_end_date"]
    assert "marked: the mark event" in err and "wake:" in err
    assert (tmp_path / "run" / "rival.log").exists()


def test_play_rival_refuses_a_bad_tag_and_a_dry_run_with_exit_code_2(tmp_path, capsys):
    with pytest.raises(SystemExit, match="country tag"):
        main(["play", "--rival", "SW", "--fake-game", "--run-dir", str(tmp_path / "run")])
    code = main(["play", "--rival", "SWE", "--provider", "scripted", "--run-dir",
                 str(tmp_path / "run")])
    assert code == 2 and "pass --live" in capsys.readouterr().err


def test_the_fake_person_defaults_to_playing_in_pauses_so_the_demo_command_works(tmp_path):
    from hoi4_harness.fakerival import human_cycle

    script = human_cycle()
    assert script[0].kind == "resume" and {"pause", "resume"} <= {a.kind for a in script}
    assert acts((1, "pause"))[0].at == 1.0
