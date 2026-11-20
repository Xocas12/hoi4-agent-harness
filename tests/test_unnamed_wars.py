"""When no daily war line names an enemy, the weekly count still says there is a war.

Seen live (Germany, 1936 start): the installed mod never printed a war line, so every
brief and turn line said "wars: none" from 1939 to 1940 while the weekly camp line
counted 9 enemies and Germany lost control of 11 of its 34 states. The strategist
spent a year waiting for a war it was already losing.
"""

from __future__ import annotations

from types import SimpleNamespace

from hoi4_harness.campaign.progress import ProgressTracker
from hoi4_harness.campaign.runner import CampaignSupervisor
from hoi4_harness.observation.render import render_full, unnamed_enemies
from hoi4_harness.types import CampaignStats, GameState, War


def _germany_1940(wars=()):
    return GameState(date="1940-05-16", country="GER", wars=list(wars),
                     campaign=CampaignStats(date="1940-05-13", owned_states=34,
                                            controlled_states=23, divisions=68, enemies=9))


def test_the_brief_says_at_war_when_only_the_weekly_count_knows():
    text = render_full(_germany_1940())
    assert "AT WAR with 9 countries" in text and "1940-05-13" in text
    assert unnamed_enemies(_germany_1940()) == 9


def test_named_wars_replace_the_count():
    state = _germany_1940([War(against="FRA"), War(against="ENG")])
    assert unnamed_enemies(state) == 0
    assert "AT WAR with 9 countries" not in render_full(state)


def test_the_turn_line_does_not_say_none_during_a_war():
    runner = SimpleNamespace(loop=None, config=SimpleNamespace(budget=SimpleNamespace(max_usd=1.0)))
    line = CampaignSupervisor.turn_line(runner, _germany_1940(), True, "scheduled review", [], 0)
    assert "wars: 9 unnamed" in line
    peace = GameState(date="1937-01-01", country="GER",
                      campaign=CampaignStats(date="1937-01-01", owned_states=24, enemies=0))
    assert "wars: none" in CampaignSupervisor.turn_line(runner, peace, True, "x", [], 0)


def test_the_campaign_block_counts_the_enemies_from_the_weekly_line(tmp_path):
    tracker = ProgressTracker(run_dir=tmp_path, until="1948-12-31", max_usd=1.0)
    text = "\n".join(tracker.brief_lines(_germany_1940())).lower()
    assert "at war with 9" in text
