"""The campaign's numbers: the series on disk and the block in the brief."""

from __future__ import annotations

import json

from hoi4_harness.campaign.progress import ProgressTracker
from hoi4_harness.types import CampaignStats, GameEvent, GameState, War

# --- the brief and the series -----------------------------------------------------------------------


def _state(owned, **kw):
    return GameState(date="1938-05-01", country="GER", wars=kw.pop("wars", []),
                     campaign=CampaignStats(date="1938-04-27", owned_states=owned, divisions=96,
                                            enemies=2, capitulated=1, in_faction=True,
                                            faction_leader=True, faction_members=4,
                                            countries_existing=71, countries_independent=58,
                                            world_states=1081))


def test_the_brief_block_reports_the_conquest_and_what_changed_since_the_last_look(tmp_path):
    tracker = ProgressTracker(run_dir=tmp_path, until="1948-12-31", max_usd=150.0)
    tracker.record(_state(30), {"usd": 0.0, "llm_calls": 0})
    tracker.reviewed()
    tracker.events([GameEvent("war_declared", "war declared: GER on POL", "critical", "1938-04-02")],
                   "1938-04-02")
    tracker.record(_state(41), {"usd": 12.31, "llm_calls": 94})
    text = "\n".join(tracker.brief_lines(_state(41, wars=[War(against="POL")])))
    lowered = text.lower()
    assert "You hold 41 states (3.8% of the world's 1081), +11 since the start" in text
    assert "+11 since your last review" in text
    assert "independent countries left 58 of 71" in lowered
    assert "at war with 1 (1 capitulated as of 1938-04-27)" in lowered
    assert "in a faction (leader), 4 members" in lowered
    assert "$12.31 of $150.00" in text and "94 model calls" in text
    assert "1938-04-02 war declared: GER on POL" in text
    tracker.reviewed()
    assert "Since your last review" not in "\n".join(tracker.brief_lines(_state(41)))


def test_the_series_is_one_row_a_turn_with_what_is_known(tmp_path):
    tracker = ProgressTracker(run_dir=tmp_path)
    tracker.record(GameState(date="1936-02-01", country="GER"), {"usd": 1.5, "llm_calls": 3})
    (row,) = [json.loads(ln) for ln in
              (tmp_path / "campaign_progress.jsonl").read_text().splitlines()]
    assert "owned_states" not in row          # no camp line yet: absent, not zero
    assert row["usd"] == 1.5 and row["country"] == "GER"


