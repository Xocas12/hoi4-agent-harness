"""A crude world and a crude game AI for the fake game, so a campaign can run on it.

The real campaign mode hands the player's country to the game's own AI with the
console's ``human_ai`` and steers it through the mod's directives. To prove the
harness around that without a game, ``FakeGame`` needs the other end: a world of
countries to conquer, and an AI that, once ``human_ai`` is on, researches, builds,
raises divisions, declares the wars the directives ask for and wins or stalls them.

Nothing here is HOI4. It is deliberately small and says what it does:

* the player's country and ~90 others each hold some states and some divisions;
* with ``human_ai`` on, an ``invade`` directive against a country that exists makes
  the AI declare war on it after ``DECLARE_DELAY`` days (at most ``MAX_WARS`` at a
  time); with no directive it stays at peace, so the model's choices are what moves
  the world;
* a war's surrender progress grows each day with the player's divisions over the
  enemy's, faster under an offensive posture, slower under a defensive one;
* at 100% the enemy capitulates: the mod's capitulation event, the war lines stop,
  its states become the player's and it leaves the world.

What it cannot show is whether the real AI acts on a directive at all
(docs/campaign.md lists that among the things only the live run can prove).
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .types import ActionCall

if TYPE_CHECKING:
    from .fakegame import FakeGame

#: Days from a directive being raised to the AI declaring the war it asks for.
DECLARE_DELAY = 14
#: Wars the AI will fight at once.
MAX_WARS = 3
#: The player's own states at the start, as Germany's in 1936 (a round number).
START_STATES = 30
RESEARCH_DAYS = 60
#: Days between two new divisions, and the share of a conquered army that is added.
DIVISION_EVERY = 10
CONQUEST_ARMY_SHARE = 0.25


@dataclass
class FakeCountry:
    tag: str
    states: int
    divisions: int
    #: Nobody's subject: false for a country the player annexed as a puppet (unused so far).
    independent: bool = True


class FakeAI:
    """The world beyond the player, and what the game's AI does with the player's country."""

    def __init__(self, game: FakeGame, seed: int = 1936):
        self.game = game
        rng = random.Random(seed)
        from . import modgen

        self.countries: dict[str, FakeCountry] = {
            tag: FakeCountry(tag, states=rng.randint(3, 16), divisions=rng.randint(6, 40))
            for tag in modgen.DEFAULT_TAGS if tag != game.country
        }
        self.player_states = START_STATES
        #: States the player controls without owning (occupied, not yet annexed).
        self.occupied_states = 0
        #: tag -> the date the AI first saw an invade directive against it.
        self._asked: dict[str, dt.date] = {}
        self._day = 0
        #: Every war ended by a capitulation, for tests: (iso date, tag).
        self.capitulated: list[tuple[str, str]] = []

    @property
    def world_states(self) -> int:
        return self.player_states + sum(c.states for c in self.countries.values())

    @property
    def player_divisions(self) -> int:
        return sum(g.count for g in self.game.world.state.divisions)

    def _add_divisions(self, n: int) -> None:
        groups = self.game.world.state.divisions
        if groups:
            groups[0].count += n

    # --- the AI's day -----------------------------------------------------------

    def tick_day(self) -> None:
        """One day of the game's AI running the player's country (``human_ai`` on)."""
        self._day += 1
        self._research()
        self._build()
        if self._day % DIVISION_EVERY == 0:
            self._add_divisions(1)
        self._declare_wars()
        self._fight()

    def _research(self) -> None:
        for slot in self.game.world.state.research:
            if slot.technology is None:
                slot.technology = f"ai_tech_{self._day}_{slot.index}"
                slot.days_remaining = RESEARCH_DAYS

    def _build(self) -> None:
        state = self.game.world.state
        if state.construction or not self.game.states:
            return
        owned = [s for s in self.game.states.values() if s.owner == self.game.country]
        if owned:
            self.game.world.apply(ActionCall("queue_construction", {
                "building": "civilian_factory", "state": owned[0].name, "count": 1}))

    def _declare_wars(self) -> None:
        game, today = self.game, self.game.world.date
        wanted = {d.split()[1] for d in game.directives if d.startswith("invade ")}
        for tag in list(self._asked):
            if tag not in wanted:
                del self._asked[tag]
        at_war = {w.against for w in game.world.state.wars}
        for tag in sorted(wanted):
            self._asked.setdefault(tag, today)
            if len(at_war) >= MAX_WARS:
                break
            country = self.countries.get(tag)
            if country is None or tag in at_war:
                continue
            if (today - self._asked[tag]).days >= DECLARE_DELAY:
                game.start_war(tag, country.divisions, declared_by_player=True)
                at_war.add(tag)

    def _rate(self, tag: str) -> float:
        game = self.game
        enemy = self.countries[tag].divisions
        ratio = min(4.0, self.player_divisions / max(1, enemy))
        posture = game.theater_postures.get(tag) or game.global_posture
        factor = {"offensive": 1.5, "defensive": 0.7}.get(posture or "", 1.0)
        return 0.004 * ratio * factor

    def _fight(self) -> None:
        for war in list(self.game.world.state.wars):
            country = self.countries.get(war.against)
            if country is None:
                continue
            war.surrender_progress = min(1.0, (war.surrender_progress or 0.0) + self._rate(war.against))
            if war.surrender_progress >= 1.0:
                self._capitulate(country)

    def _capitulate(self, country: FakeCountry) -> None:
        game = self.game
        game.capitulation(country.tag)
        self.capitulated.append((game.date, country.tag))
        self.player_states += country.states
        self._add_divisions(max(1, round(country.divisions * CONQUEST_ARMY_SHARE)))
        del self.countries[country.tag]
        game.end_war(country.tag)

    # --- the numbers the weekly camp line carries ------------------------------------

    def campaign_fields(self) -> dict:
        state = self.game.world.state
        enemies = state.wars
        return {
            "tag": self.game.country, "ostates": self.player_states,
            "cstates": self.player_states + self.occupied_states,
            "divs": self.player_divisions, "enemies": len(enemies),
            "capit": sum(1 for w in enemies if (w.surrender_progress or 0) >= 1.0),
            "infac": 0, "leader": 0, "fmem": 0,
            "exist": len(self.countries) + 1, "indep": len(self.countries) + 1,
            "wstates": self.world_states,
        }
