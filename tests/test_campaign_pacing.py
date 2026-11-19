"""The small parts of campaign mode: control split, wake pacing, guards, the brief."""

from __future__ import annotations

from hoi4_harness.agent import hybrid
from hoi4_harness.agent.budget import BudgetGuard
from hoi4_harness.agent.llm.base import Usage
from hoi4_harness.agent.policy import CampaignPacing, Policy
from hoi4_harness.agent.prompts import CAMPAIGN, HEADLESS, build_system
from hoi4_harness.campaign.setup import BACKSTOP_TURNS, OBJECTIVE, apply_defaults
from hoi4_harness.config import BudgetConfig, HarnessConfig
from hoi4_harness.types import GameEvent, GameState, War

# --- the control split ---------------------------------------------------------------


def test_game_ai_keeps_the_strategic_verbs_and_drops_what_the_ai_would_undo():
    everything = {"set_national_focus", "start_research", "queue_construction", "set_production",
                  "delegate_army_to_ai", "design_division_template", "set_game_speed", "diplomacy",
                  "set_ai_directive", "set_ai_posture", "clear_ai_directives", "take_decision",
                  "hire_advisor", "advance_time", "note", "move_division"}
    kept = hybrid.actions_for_mode(everything, "game_ai")
    assert kept == {"set_ai_directive", "set_ai_posture", "clear_ai_directives", "take_decision",
                    "hire_advisor", "advance_time", "note"}


def test_the_prompt_says_who_does_what_and_drops_the_headless_note():
    system = build_system("world-conquest", operational_control="game_ai", headless=True,
                          campaign=True)
    assert CAMPAIGN in system and HEADLESS.split("{")[0] not in system
    assert "The game's own AI is running your country" in system
    assert "conquer the world" in system.lower() and "set_ai_directive" in system


def test_defaults_turn_off_what_the_ai_now_does_and_keep_what_the_operator_set():
    config = HarnessConfig(campaign_review_peace_days=45)
    apply_defaults(config)
    assert (config.adapter, config.operational_control, config.guidance) == (
        "headless", "game_ai", "world-conquest")
    assert not config.reflex_enabled and not config.wake_on_no_focus
    assert not config.wake_on_free_research_slot and not config.wake_on_front_quiet
    assert config.objective == OBJECTIVE and config.turns >= BACKSTOP_TURNS
    assert config.campaign_review_peace_days == 45
    assert config.headless_save_days == 0


def test_an_operators_own_objective_and_guidance_survive():
    config = HarnessConfig(objective="Take only Poland.", guidance="minimal")
    apply_defaults(config)
    assert config.objective == "Take only Poland." and config.guidance == "minimal"


# --- the wake pacing ------------------------------------------------------------------


def test_peace_reviews_monthly_war_fortnightly_and_the_model_can_move_the_next_one():
    policy = Policy(campaign=CampaignPacing(peace_days=30, war_days=14, min_days=7, max_days=90))
    peace, war = GameState(), GameState(wars=[War(against="POL")])
    assert policy.should_wake(peace, 29).wake is False and policy.should_wake(peace, 30).wake
    assert policy.should_wake(war, 13).wake is False and policy.should_wake(war, 14).wake
    policy.review_override = 60
    assert policy.should_wake(war, 59).wake is False and policy.should_wake(war, 60).wake
    assert policy.campaign.clamp(1) == 7 and policy.campaign.clamp(500) == 90


def test_the_ais_own_business_never_wakes_the_model_in_a_campaign():
    policy = Policy(campaign=CampaignPacing(), wake_on_no_focus=True,
                    wake_on_free_research_slot=True)
    state = GameState(national_focus=None)           # no focus is running: the AI will pick one
    assert policy.should_wake(state, 1).wake is False


def test_a_war_or_a_surrender_wakes_it_but_not_twice_inside_the_gap():
    policy = Policy(campaign=CampaignPacing(wake_gap_days=7))
    event = GameEvent("war_declared", "war declared: GER on POL", "critical")
    assert policy.should_wake(GameState(events=[event]), 6).wake is False
    decision = policy.should_wake(GameState(events=[event]), 7)
    assert decision.wake and decision.urgency == "critical"


# --- the budget ---------------------------------------------------------------------------


def test_a_reserve_stops_the_run_before_the_cap_not_after_it():
    guard = BudgetGuard(BudgetConfig(max_usd=10.0, reserve_usd=1.0, usd_per_m_input=4.0))
    guard.record(Usage(2_000_000, 0))                       # $8
    assert guard.why_blocked() is None
    guard.record(Usage(250_000, 0))                         # $9: 9 + 1 reserve reaches the cap
    assert "spend ceiling" in guard.why_blocked()


