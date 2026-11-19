"""A fake single-player game with a person in it, for rival mode.

``FakeGame`` models one country. A rival run needs two: the **rival** (an AI country, the
harness's, whose telemetry only exists once the harness marks it) and the **person's**
(``is_ai = no``, so the mod always logs it, and which the harness must never see). This
subclass adds the person:

* the person's country writes its own ``state`` / ``army`` / ``war`` / ``camp`` lines every day the
  game runs, under its own tag, with numbers the rival's brief must never show;
* the person **acts on a schedule** in wall-clock seconds, the one clock the fake has: they pause,
  unpause, touch the keyboard or mouse, type a key, alt-tab away, and declare war on the rival
  (``schedule`` / ``human_cycle``);
* the system's **last-input stamp** (``GetLastInputInfo``) is modelled, and every key the
  harness sends moves it, as SendInput does: ``input_watch()`` is what the harness's own guard
  reads, so what it proves here is the logic and not a flattering clock;
* a person's key lands wherever the keys of the harness would: in the console if it is open.

The rival is an AI country from the start (``ai=1``) and the game's crude AI (fakecampaign.py)
runs it: the harness steers it with directives and nothing else. The person's country is never a
target of that AI, because a country the person plays does not capitulate to a script.

Nothing here is HOI4. It says what the fake does so a green test is read for what it is.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .fakegame import MONTHS, TICK_HOUR, FakeGame
from .rival.safety import InputWatch
from .types import War

#: What the person's lines carry. Distinctive on purpose: a test that sees any of these in the
#: rival's brief has found a leak.
HUMAN_PP = "777.0000"
HUMAN_ENEMY = "SOV"
HUMAN_DIVISIONS = 64

#: In a several-rival game each country's ``techs`` counter is a number of its own (the first
#: rival's is this, the next ones' a multiple of it): a leak test looks for another country's.
SIGNATURE_TECHS = 1000

KINDS = ("pause", "resume", "input", "type", "war", "focus_away", "call")


@dataclass
class Act:
    at: float
    kind: str
    arg: str = ""
    #: For ``call``: something the test wants done at that second (grant a handover, ...).
    fn: Callable[[], None] | None = None


class _RivalSide:
    """What makes a fake country an AI rival: reported only once flagged ``llmb_harness``."""

    @property
    def visible(self) -> bool:
        """The rival is an AI country: only the ``llmb_harness`` flag makes the mod report it."""
        return "llmb_harness" in self.flags

    def _harness(self, tag: str) -> bool:
        return tag == self.human or (tag == self.country and "llmb_harness" in self.flags)

    def start_war(self, enemy: str, divisions: int = 30, declared_by_player: bool = False) -> None:
        """The rival is at war with ``enemy`` from now. The mod logs a declaration when either
        side is a harness country (``on_declare_war``): the person always is, a rival only
        once marked. ``declared_by_player`` is "the rival declared it", as in the base class."""
        state = self.world.state
        if not any(w.against == enemy for w in state.wars):
            state.wars.append(War(against=enemy, since=self.date, enemy_divisions=divisions,
                                  borders=True, surrender_progress=0.0))
        declarer, target = (self.country, enemy) if declared_by_player else (enemy, self.country)
        if self._harness(declarer) or self._harness(target):
            self._write(self._llmb("evt", tag=declarer, other=target, kind=1, detail=0))


class FakeRivalCountry(_RivalSide, FakeGame):
    """One more AI country in a several-rival game: its own world, flags, directives and
    telemetry, sharing the log, the clock and the console with the game it belongs to
    (``FakeRivalGame.extras``). It has no keys of its own: effects aimed at its tag reach it
    through the game's console."""

    def __init__(self, user_dir: Path, country: str, human: str, techs: int | None = None,
                 **kwargs):
        self.human = human
        super().__init__(user_dir, country=country, **kwargs)
        self.human_ai = True            # an AI country from the start (the state line says ai=1)
        self.ai.countries.pop(human, None)
        if techs is not None:
            self.world.techs = techs    # a number that is this country's alone


class FakeRivalGame(_RivalSide, FakeGame):
    """The fake game, with a rival AI country and a person playing another.

    ``extra_rivals`` adds more AI countries to the same game (``extras``, by tag): each is a
    full fake country of its own, logged under its own tag, steerable only through effects in
    its own scope, and ticked with the game's clock."""

    def __init__(self, user_dir: Path, rival: str = "SWE", human: str = "GER",
                 human_script: list[Act] | None = None, extra_rivals: tuple[str, ...] = (),
                 **kwargs):
        if rival == human or human in extra_rivals:
            raise ValueError("the person and the rival cannot be the same country")
        if len({rival, *extra_rivals}) != 1 + len(extra_rivals):
            raise ValueError("a rival is listed twice")
        self.human = human
        #: The other rivals by tag (empty for the one-rival game).
        self.extras: dict[str, FakeRivalCountry] = {}
        #: Wall-clock second of the person's last input (the harness's own keys included).
        self.last_input_wall = -1000.0
        self._acts: list[Act] = sorted(human_script or [], key=lambda a: a.at)
        #: What the person did and when, for a test to read back.
        self.human_log: list[tuple[float, str, str]] = []
        super().__init__(user_dir, country=rival, **kwargs)
        # An AI country: the game's AI runs it from day one, and the state line says ai=1.
        self.human_ai = True
        # The person's country is theirs, not a target the crude AI can conquer.
        self.ai.countries.pop(human, None)
        shared = {key: kwargs[key] for key in ("seed", "start", "index") if key in kwargs}
        for k, tag in enumerate(extra_rivals, start=1):
            self.extras[tag] = FakeRivalCountry(
                self.user_dir, tag, human, techs=SIGNATURE_TECHS * (k + 1) + k, **shared)
        if extra_rivals:
            self.world.techs = SIGNATURE_TECHS
        self._emit_human()

    # --- several rivals ---------------------------------------------------------------

    @property
    def rivals(self) -> list[str]:
        """Every AI country the game runs for the harness to steer, the first one first."""
        return [self.country, *self.extras]

    def country_game(self, tag: str) -> FakeGame:
        """The fake country that holds ``tag``'s world (this game for its own country)."""
        return self if tag == self.country else self.extras[tag]

    def _scope_of(self, rest: str) -> FakeRivalCountry | None:
        """The extra rival a console effect is aimed at (``effect TAG ...``, ``eval_effect
        TAG = {...}``), None when it is this game's own country or nobody's."""
        match = re.match(r"\s*([A-Za-z0-9]{3})\b", rest)
        return self.extras.get(match.group(1).upper()) if match else None

    def _scripted_effect(self, rest: str) -> None:
        sub = self._scope_of(rest)
        return sub._scripted_effect(rest) if sub is not None else super()._scripted_effect(rest)

    def _eval_effect(self, rest: str) -> None:
        sub = self._scope_of(rest)
        return sub._eval_effect(rest) if sub is not None else super()._eval_effect(rest)

    # --- the person's country -------------------------------------------------------------

    def _emit_human(self) -> None:
        d, tag = self.world.date, self.human
        self._write(
            self._llmb("state", date=f" {TICK_HOUR}:00, {d.day} {MONTHS[d.month - 1]}, {d.year}",
                       tag=tag, pp=HUMAN_PP, stab="61.000", ws="55", civ=41, mil=33, doc=4,
                       owned=78, mp=99999, ai=0, techs=44, built_civ=0, built_mil=0, built_dock=0,
                       built_infra=0),
            self._llmb("army", tag=tag, divisions=HUMAN_DIVISIONS, delegated=0),
            self._llmb("war", tag=tag, enemy=HUMAN_ENEMY, divs=70, border=1, surr=12),
        )
        if self.date_ordinal % 7 == 0:
            self._write(self._llmb("camp", tag=tag, ostates=99, cstates=99, divs=HUMAN_DIVISIONS,
                                   enemies=1, capit=0, infac=0, leader=0, fmem=0, exist=90,
                                   indep=90, wstates=1000))

    def tick_day(self) -> None:
        super().tick_day()
        for sub in self.extras.values():
            sub.tick_day()
        self._emit_human()

    # --- the person's behaviour ------------------------------------------------------------

    def schedule(self, at: float, kind: str, arg: str = "") -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown act {kind!r}; known: {', '.join(KINDS)}")
        self._acts.append(Act(at, kind, arg))
        self._acts.sort(key=lambda a: a.at)

    def schedule_call(self, at: float, fn: Callable[[], None]) -> None:
        """Run ``fn`` at wall-clock second ``at`` (a test's hand: grant a window, take it back)."""
        self._acts.append(Act(at, "call", fn=fn))
        self._acts.sort(key=lambda a: a.at)

    def touch(self) -> None:
        """Someone used the keyboard or the mouse this second."""
        self.last_input_wall = self._wall

    def send(self, scan: int, up: bool) -> None:
        self.touch()                      # SendInput is input: GetLastInputInfo moves for it
        super().send(scan, up)

    def input_watch(self) -> InputWatch:
        """The system's last-input clock, as the harness's guard reads it."""
        return InputWatch(lambda: int(self.last_input_wall * 1000), lambda: int(self._wall * 1000))

    def _act(self, act: Act) -> None:
        self.human_log.append((act.at, act.kind, act.arg))
        if act.kind == "pause":
            self.touch()
            self.paused, self._acc = True, 0.0
        elif act.kind == "resume":
            self.touch()
            self.paused = False
        elif act.kind == "input":
            self.touch()
        elif act.kind == "type":
            self.touch()
            self._key(act.arg or "x", frozenset())
        elif act.kind == "war":
            # "40" (divisions) or "40:ENG": the person declares on one of several rivals.
            divisions, _, tag = act.arg.partition(":")
            target = self.country_game(tag.upper()) if tag else self
            target.start_war(self.human, divisions=int(divisions or 40), declared_by_player=False)
        elif act.kind == "focus_away":
            self.lose_focus(float(act.arg or 30))
        elif act.kind == "call" and act.fn is not None:
            act.fn()

    def _fire_due(self) -> None:
        while self._acts and self._acts[0].at <= self._wall:
            self._act(self._acts.pop(0))

    def sleep(self, seconds: float) -> None:
        """Wall-clock time passes in slices, so the person acts at their second, between two
        keys of a window if that is when it falls."""
        end = self._wall + max(0.0, seconds)
        while True:
            self._fire_due()
            nxt = next((a.at for a in self._acts if a.at <= end), None)
            if nxt is None:
                break
            step = nxt - self._wall
            if step > 0:
                super().sleep(step)
            self._wall = max(self._wall, nxt)
        remaining = end - self._wall
        if remaining > 0:
            super().sleep(remaining)
        self._fire_due()


def human_cycle(run: float = 45.0, pause: float = 90.0, cycles: int = 400, start: float = 0.0,
                declare_war_at: float | None = None) -> list[Act]:
    """A person who plays ``run`` seconds, pauses for ``pause`` seconds (hands off, thinking),
    and does it again. The game starts paused, so the first act is to unpause."""
    acts, t = [Act(start, "resume")], start
    for _ in range(cycles):
        acts.append(Act(t + run - 1, "input"))
        acts.append(Act(t + run, "pause"))
        t += run + pause
        acts.append(Act(t, "resume"))
    if declare_war_at is not None:
        acts.append(Act(declare_war_at, "war", "40"))
    return acts


def load_script(path: Path) -> list[Act]:
    """A person's script from JSON: ``[[seconds, "pause"], [seconds, "type", "x"], ...]``."""
    out = []
    for row in json.loads(Path(path).read_text(encoding="utf-8")):
        out.append(Act(float(row[0]), str(row[1]), str(row[2]) if len(row) > 2 else ""))
    return out
