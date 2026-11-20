"""The campaign's numbers over time: a time series on disk and a block in the brief.

One sample per turn goes to ``campaign_progress.jsonl`` (the report's territory table
and sparkline read it), every war event to ``campaign_events.jsonl``, and the model's
brief carries the same figures in a few lines, so it can judge whether the conquest
is moving. Numbers the reader could not see are None in the file and absent from the
brief, never zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..types import GameEvent, GameState

PROGRESS_FILE = "campaign_progress.jsonl"
EVENTS_FILE = "campaign_events.jsonl"
#: Events worth a line in the timeline and in "since your last review".
NEWS_KINDS = ("war_declared", "war_ended", "capitulation", "harness_marked")
#: What a person reading the brief wants of them; the marker is bookkeeping.
BRIEF_NEWS = ("war_declared", "war_ended", "capitulation")


def sample(state: GameState, spend: dict | None = None, turn: int = 0) -> dict:
    """One row of the time series, from what the reader sees now."""
    camp = state.campaign
    row: dict = {
        "date": state.date, "turn": turn, "country": state.country,
        "wars": sorted(w.against for w in state.wars) if state.known("wars") else None,
        "civ": state.civilian_factories if state.known("civilian_factories") else None,
        "mil": state.military_factories if state.known("military_factories") else None,
        "manpower": state.manpower if state.known("manpower") else None,
        "ai": state.ai_controlled,
    }
    if camp is not None:
        row.update(
            camp_date=camp.date, owned_states=camp.owned_states,
            controlled_states=camp.controlled_states, divisions=camp.divisions,
            enemies=camp.enemies, capitulated=camp.capitulated, in_faction=camp.in_faction,
            faction_leader=camp.faction_leader, faction_members=camp.faction_members,
            countries_existing=camp.countries_existing,
            countries_independent=camp.countries_independent, world_states=camp.world_states,
            state_share=None if camp.state_share is None else round(camp.state_share, 5))
    if spend:
        row.update(usd=spend.get("usd"), llm_calls=spend.get("llm_calls"),
                   input_tokens=spend.get("input_tokens"), output_tokens=spend.get("output_tokens"))
    return row


@dataclass
class ProgressTracker:
    """Writes the series and keeps what the brief says about it."""

    run_dir: Path | None = None
    objective: str = "conquer the world"
    #: The brief block's heading: CAMPAIGN, or RIVAL when the model steers one AI country.
    label: str = "CAMPAIGN"
    until: str | None = None
    max_usd: float | None = None
    #: The first campaign numbers seen, for "since the start".
    start: dict | None = None
    #: The numbers at the model's last review, for "since your last review".
    last_review: dict | None = None
    #: War events since the model last looked, as (date, text).
    news: list[tuple[str, str]] = field(default_factory=list)
    samples: int = 0
    latest: dict | None = None
    spend: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.run_dir is not None:
            self.run_dir = Path(self.run_dir)

    # --- writing ----------------------------------------------------------------

    def _append(self, name: str, record: dict) -> None:
        if self.run_dir is None:
            return
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with (self.run_dir / name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    def record(self, state: GameState, spend: dict | None = None, turn: int = 0) -> dict:
        row = sample(state, spend, turn)
        if spend:
            self.spend = spend
        if self.start is None and row.get("owned_states") is not None:
            self.start = row
        self.latest = row
        self.samples += 1
        self._append(PROGRESS_FILE, row)
        return row

    def events(self, events: list[GameEvent], date: str) -> None:
        """Note this turn's war events (each is seen once: the loop acknowledges them)."""
        for event in events:
            if event.kind not in NEWS_KINDS:
                continue
            when = event.date or date
            self._append(EVENTS_FILE, {"date": when, "kind": event.kind, "text": event.text})
            if event.kind in BRIEF_NEWS:
                self.news.append((when, event.text))

    # --- the brief ----------------------------------------------------------------

    def brief_lines(self, state: GameState) -> list[str]:
        """The campaign block for a planner brief; empty before any numbers exist."""
        camp = state.campaign
        lines: list[str] = []
        head = f"{self.label}: objective {self.objective}"
        if self.until:
            head += f", until {self.until}"
        lines.append(head + f" (now {state.date}).")
        if camp is not None and camp.owned_states is not None:
            held = f"You hold {camp.owned_states} states"
            if camp.state_share is not None:
                held += f" ({camp.state_share * 100:.1f}% of the world's {camp.world_states})"
            held += _since(camp.owned_states, "owned_states", self.start, " since the start")
            held += _since(camp.owned_states, "owned_states", self.last_review, " since your last review")
            lines.append(held + ".")
            bits = []
            if camp.countries_independent is not None:
                bits.append(f"independent countries left {camp.countries_independent}"
                            + (f" of {camp.countries_existing} existing"
                               if camp.countries_existing is not None else ""))
            # The war lines are daily; the weekly report can be a week old.
            # An empty list is not "no wars" when the weekly count says otherwise:
            # a mod without the daily war lines leaves it empty all game.
            enemies = len(state.wars) if state.known("wars") and state.wars else camp.enemies
            if enemies is not None:
                bits.append(f"at war with {enemies}"
                            + (f" ({camp.capitulated} capitulated as of {camp.date})"
                               if camp.capitulated else ""))
            if camp.in_faction is not None:
                bits.append("in a faction" + (" (leader)" if camp.faction_leader else "")
                            + (f", {camp.faction_members} members" if camp.faction_members else "")
                            if camp.in_faction else "in no faction")
            if camp.divisions is not None:
                bits.append(f"{camp.divisions} divisions")
            if bits:
                lines.append("  " + "; ".join(bits).capitalize() + ".")
        else:
            lines.append("  (no campaign numbers from the game yet: the weekly report has not arrived)")
        if self.spend.get("usd") is not None:
            cap = f" of ${self.max_usd:.2f}" if self.max_usd else ""
            lines.append(f"  Spend so far ${self.spend['usd']:.2f}{cap}, "
                         f"{self.spend.get('llm_calls', 0)} model calls.")
        if self.news:
            lines.append("  Since your last review: "
                         + "; ".join(f"{d} {text}" for d, text in self.news[-8:])
                         + (f" (+{len(self.news) - 8} earlier)" if len(self.news) > 8 else ""))
        return lines

    def reviewed(self) -> None:
        """The model has seen the brief: what it reads next is news again."""
        self.news = []
        if self.latest is not None:
            self.last_review = self.latest


def _since(now: int, key: str, base: dict | None, label: str) -> str:
    if not base or base.get(key) is None:
        return ""
    delta = now - base[key]
    return f", {delta:+d}{label}" if delta else ""
