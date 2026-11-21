"""Issue #130: the telemetry cadence is configurable, faster and slower than a day.

The mod side is generated (modgen.py) and cannot run here, so the chain is checked by running
the generated script text in a small interpreter: it knows the handful of effects and triggers
the cadence file uses and the clock of the game's own scheduling (an event with ``hours = N``
fires N hours later; ``on_daily`` fires every 24th hour). The reader and the fake game are the
real ones; no test sends a key.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hoi4_harness import cadence, modgen
from hoi4_harness.adapters.logtail import EVENT_CODES, SCHEMA_VERSION, LogTailAdapter, engine_hour
from hoi4_harness.cli import build_parser, main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.fakegame import FakeGame, ScriptError, demo_index, parse_script
from hoi4_harness.fakegame import _compare as compare

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"


# --- a tiny interpreter for the generated cadence script ------------------------------------


def _strip(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _generated(path: str) -> str:
    return next(item.text for item in modgen.generate() if item.path == path)


class Mod:
    """The generated effects and events, and the game's clock as far as they depend on it."""

    def __init__(self):
        effects = parse_script(_strip(_generated("common/scripted_effects/llm_bridge_cadence.txt")))
        self.effects = {e.key: e.value for e in effects}
        events = parse_script(_strip(_generated("events/llm_bridge_cadence_events.txt")))
        self.events = {}
        for entry in events:
            if entry.key != "country_event":
                continue
            body = {e.key: e.value for e in entry.value}
            self.events[body["id"]] = body["immediate"]
        self.vars: dict[str, float] = {}
        self.now = 0
        self.queue: list[tuple[int, str]] = []        # (hour it fires at, event id)
        self.ticks: list[int] = []                    # the hours llm_bridge_emit_tick ran at
        self.evt: list[tuple[float, float]] = []      # (kind, detail) of each emitted event line
        self.max_waiting = 0

    # -- values and triggers
    def _value(self, raw):
        try:
            return float(raw)
        except ValueError:
            return self.vars.get(raw, 0.0)

    def _check(self, entries) -> bool:
        return all(self._trigger(e) for e in entries)

    def _trigger(self, entry) -> bool:
        if entry.key == "check_variable":
            (inner,) = entry.value
            return compare(self.vars.get(inner.key, 0.0), inner.op, self._value(inner.value))
        if entry.key == "OR":
            return any(self._trigger(e) for e in entry.value)
        if entry.key == "NOT":
            return not self._check(entry.value)
        if entry.key == "llm_bridge_is_harness":
            return True
        raise AssertionError(f"the interpreter does not know the trigger {entry.key}")

    # -- effects
    def run(self, entries) -> None:
        for entry in entries:
            key = entry.key
            if key == "if":
                body = entry.value
                limit = next((e for e in body if e.key == "limit"), None)
                if limit is None or self._check(limit.value):
                    self.run([e for e in body if e.key != "limit"])
            elif key in ("set_variable", "add_to_variable", "subtract_from_variable"):
                (inner,) = entry.value
                value = self._value(inner.value)
                old = self.vars.get(inner.key, 0.0)
                self.vars[inner.key] = {"set_variable": value, "add_to_variable": old + value,
                                        "subtract_from_variable": old - value}[key]
            elif key == "country_event":
                body = {e.key: e.value for e in entry.value}
                assert body["id"] in self.events, f"{body['id']} has no event"
                self.queue.append((self.now + int(body["hours"]), body["id"]))
                self.max_waiting = max(self.max_waiting, len(self.queue))
            elif key == "llm_bridge_emit_tick":
                self.ticks.append(self.now)
            elif key == "llm_bridge_emit_event":
                self.evt.append((self.vars["llmb_event_kind"], self.vars["llmb_event_detail"]))
            elif key.startswith("llm_bridge_"):
                self.run(self.effects[key])
            else:
                raise AssertionError(f"the interpreter does not know the effect {key}")

    # -- the game
    def console(self, hours: int) -> None:
        """``effect <TAG> llm_bridge_telemetry_every_<H>h``."""
        self.run(self.effects[f"llm_bridge_telemetry_every_{hours}h"])

    def advance(self, hours: int) -> None:
        for _ in range(hours):
            self.now += 1
            due = [item for item in self.queue if item[0] == self.now]
            self.queue = [item for item in self.queue if item[0] != self.now]
            for _, event_id in sorted(due):
                self.run(self.events[event_id])
            if self.now % 24 == 0:
                self.run(self.effects["llm_bridge_daily_tick"])

    def waiting(self, hours: int) -> int:
        return sum(1 for _, event_id in self.queue if event_id == f"llmb_tick.{hours}")


def test_unset_cadence_prints_once_a_day_as_before():
    mod = Mod()
    mod.advance(24 * 5)
    assert mod.ticks == [24, 48, 72, 96, 120]
    assert mod.queue == []


@pytest.mark.parametrize("hours", cadence.SUBDAILY_HOURS)
def test_every_subdaily_value_ticks_on_its_own_hours_and_only_there(hours):
    mod = Mod()
    mod.console(hours)
    mod.advance(48)
    assert mod.ticks == list(range(hours, 49, hours))
    assert mod.waiting(hours) == 1
    assert mod.evt == [(modgen.EVENT_TELEMETRY_CADENCE, hours)]


def test_setting_the_same_cadence_twice_starts_one_chain():
    mod = Mod()
    mod.console(6)
    mod.console(6)
    mod.advance(2)
    mod.console(6)
    mod.advance(46)
    assert mod.ticks == list(range(6, 49, 6))
    assert mod.max_waiting == 1


def test_changing_the_cadence_ends_the_old_chain_by_itself():
    mod = Mod()
    mod.console(6)
    mod.advance(3)
    mod.console(4)
    mod.advance(45)
    assert mod.ticks == list(range(7, 49, 4))          # 3 + 4k; nothing at 6, 12, ...
    assert mod.waiting(6) == 0 and mod.waiting(4) == 1


def test_going_a_to_b_to_a_before_a_fires_never_doubles_the_chain():
    mod = Mod()
    mod.console(6)
    mod.advance(1)
    mod.console(4)
    mod.advance(1)
    mod.console(6)                  # the first 6-chain event is still waiting: it is revived
    assert mod.waiting(6) == 1
    mod.advance(46)
    assert mod.ticks == list(range(6, 49, 6))
    assert mod.waiting(6) == 1 and mod.waiting(4) == 0


def test_going_back_to_daily_leaves_no_ticks_from_the_old_chain():
    mod = Mod()
    mod.console(3)
    mod.advance(10)
    mod.console(24)
    before = len(mod.ticks)
    mod.advance(24 * 3)
    assert mod.ticks[before:] == [24, 48, 72]
    assert mod.queue == []


@pytest.mark.parametrize("hours", [48, 72, 168])
def test_a_slower_cadence_ticks_every_n_days_starting_with_the_next_day(hours):
    mod = Mod()
    mod.console(hours)
    mod.advance(24 * 30)
    days = hours // 24
    assert mod.ticks == [24 * d for d in range(1, 31, days)]


def test_a_chain_whose_event_was_lost_is_restarted_by_the_daily_tick():
    mod = Mod()
    mod.console(8)
    mod.advance(3)
    mod.queue.clear()               # the country stopped existing, the event is gone
    mod.advance(24 * 2 - 3)         # two daily ticks: the first still sees the setter's mark
    assert mod.waiting(8) == 1
    assert mod.vars["llmb_tick_inflight_8"] == 1
    mod.advance(8)
    assert mod.ticks[-1] == 56 and mod.waiting(8) == 1


def test_a_live_chain_is_never_restarted_by_the_daily_tick():
    mod = Mod()
    mod.console(12)
    mod.advance(24 * 10)
    assert mod.ticks == list(range(12, 24 * 10 + 1, 12)) and mod.max_waiting == 1


def test_the_event_waits_only_while_the_cadence_still_asks_for_it():
    mod = Mod()
    mod.console(12)
    mod.console(24)
    mod.advance(12)
    assert mod.ticks == [] and mod.queue == []


# --- the generated files ------------------------------------------------------------------


def test_the_generator_writes_a_setter_for_every_supported_value_and_an_event_per_chain():
    effects = _generated("common/scripted_effects/llm_bridge_cadence.txt")
    events = _generated("events/llm_bridge_cadence_events.txt")
    for hours in cadence.SUPPORTED_HOURS:
        assert f"llm_bridge_telemetry_every_{hours}h = {{" in effects
    for hours in cadence.SUBDAILY_HOURS:
        assert f"id = llmb_tick.{hours}" in events
        assert f"country_event = {{ id = llmb_tick.{hours} hours = {hours} }}" in effects
    # The delay is a literal each time: no variable form is documented.
    assert not re.search(r"hours = (?!\d+ \})\S", effects)
    assert "add_namespace = llmb_tick" in events


def test_the_installed_mod_files_are_what_the_generator_writes():
    assert modgen.drift(MOD) == []
    for item in modgen.generate():
        if "cadence" in item.path:
            assert (MOD / item.path).is_file(), item.path


def test_the_daily_hook_goes_through_the_tick_and_nothing_else_prints_from_on_daily():
    hooks = (MOD / "common" / "on_actions" / "llm_bridge_on_actions.txt").read_text(encoding="utf-8")
    daily = _strip(hooks)
    daily = daily[daily.index("on_daily"):daily.index("on_weekly")]
    assert "llm_bridge_daily_tick = yes" in daily and "llm_bridge_emit_state" not in daily
    assert "llm_bridge_emit_campaign = yes" in hooks            # the weekly camp line is unchanged


def test_the_schema_version_is_unchanged_and_the_cadence_event_is_a_known_code():
    assert SCHEMA_VERSION == 5
    assert EVENT_CODES[str(modgen.EVENT_TELEMETRY_CADENCE)][0] == "telemetry_cadence"
    for path in MOD.rglob("*.txt"):
        for found in re.findall(r"LLMB\|v(\d+)\|", path.read_text(encoding="utf-8-sig")):
            assert int(found) == 5


# --- the config key and the flag ------------------------------------------------------------


def test_only_the_supported_values_are_accepted():
    assert [cadence.validate(h) for h in (1, 12, 24, 48, 720)] == [1, 12, 24, 48, 720]
    for bad in (0, 5, 7, 25, 36, -6, 2.5, "x", True, None):
        with pytest.raises(ValueError, match="telemetry_hours"):
            cadence.validate(bad)


def test_the_default_leaves_the_game_alone():
    assert HarnessConfig().telemetry_hours is None


def test_the_profile_can_set_it():
    assert HarnessConfig().merge({"telemetry_hours": 6}).telemetry_hours == 6


def test_the_flag_is_on_play_and_refused_when_unsupported(capsys):
    args = build_parser().parse_args(["play", "--telemetry-hours", "6"])
    assert args.telemetry_hours == 6
    assert main(["play", "--telemetry-hours", "5", "--turns", "1"]) == 2
    assert "not supported" in capsys.readouterr().err


class _Console:
    def __init__(self):
        self.sent = []

    def send_lines(self, lines):
        self.sent.append(list(lines))


def test_apply_sends_the_effect_and_says_what_it_set():
    console, said = _Console(), []
    assert cadence.apply(console, "ger", 6, said.append) is True
    assert console.sent == [["effect GER llm_bridge_telemetry_every_6h"]]
    assert said and "every 6 in-game hours" in said[0]
    assert cadence.apply(console, "GER", 72, said.append) and "every 3 in-game days" in said[-1]


def test_apply_sends_nothing_when_unset_or_without_a_console():
    console = _Console()
    assert cadence.apply(console, "GER", None) is False and console.sent == []
    assert cadence.apply(None, "GER", 6) is False


# --- the fake game and the reader ------------------------------------------------------------


@pytest.fixture()
def game(tmp_path):
    return FakeGame(tmp_path / "user", country="SWE", index=demo_index(tmp_path / "playset"),
                    variance=0)


def _state_lines(game) -> list[str]:
    return [ln for ln in game.log_path.read_text(encoding="utf-8").splitlines() if "|state|" in ln]


def test_the_fake_game_ticks_daily_by_default(game):
    start = len(_state_lines(game))
    game.run_days(3)
    assert len(_state_lines(game)) - start == 3


@pytest.mark.parametrize("hours", cadence.SUBDAILY_HOURS)
def test_the_fake_game_prints_the_states_a_day_at_the_chosen_hours(game, hours):
    game._scripted(cadence.effect_name(hours))
    start = len(_state_lines(game))
    game.run_days(2)
    lines = _state_lines(game)[start:]
    assert len(lines) == 2 * (24 // hours)
    day_hours = [engine_hour(ln) for ln in lines[: 24 // hours]]
    assert day_hours == sorted(day_hours) and len(set(day_hours)) == 24 // hours
    assert all(b - a == hours for a, b in zip(day_hours, day_hours[1:], strict=False))


def test_the_fake_game_prints_every_n_days_when_slower(game):
    game._scripted(cadence.effect_name(72))
    start = len(_state_lines(game))
    game.run_days(9)
    assert len(_state_lines(game)) - start == 3


def test_the_fake_game_refuses_a_value_the_mod_has_no_effect_for(game):
    with pytest.raises(ScriptError, match="Unknown scripted effect"):
        game._scripted("llm_bridge_telemetry_every_5h")


def test_the_console_line_the_harness_sends_runs_in_the_fake_game(game):
    (line,) = cadence.lines("SWE", 6)
    game._console(line)
    assert game.errors == [] and game.telemetry_hours == 6
    assert any("|evt|" in ln and "kind=20" in ln and "detail=6" in ln
               for ln in game.log_path.read_text(encoding="utf-8").splitlines())


def test_the_reader_keeps_one_tick_per_state_line_and_the_hour(game, tmp_path):
    game._scripted(cadence.effect_name(6))
    game.run_days(3)
    reader = LogTailAdapter(game.log_path, country="SWE")
    state = reader.read_state()
    assert state.date == game.date and state.hour == 21        # the last tick of the day
    daily = FakeGame(tmp_path / "other", country="SWE", index=demo_index(tmp_path / "p2"),
                     variance=0)
    daily.run_days(3)
    base = LogTailAdapter(daily.log_path, country="SWE").read_state()
    # A closed tick per state line but the newest: 4 a day against 1 a day, over the same days.
    assert state.telemetry_ticks - base.telemetry_ticks == 3 * (4 - 1)
    assert base.hour == 21


def test_several_ticks_a_day_report_a_completed_focus_and_a_war_once(tmp_path):
    log = tmp_path / "game.log"
    lines = []

    def tick(day, hour, focus=True, war=True):
        stamp = f"[10:00:00][1939.09.{day:02d}.{hour:02d}]"
        lines.append(f"{stamp}: LLMB|v5|state|date=x|tag=SWE|pp=42|stab=63|ws=18|civ=12|mil=5"
                     "|doc=4|mp=1|ai=0|techs=3\n")
        if focus:
            lines.append(f"{stamp}: LLMB|v5|focus|tag=SWE|id=SWE_rearm\n")
        if war:
            lines.append(f"{stamp}: LLMB|v5|war|tag=SWE|enemy=GER|divs=84|border=1|surr=3\n")

    for hour in (3, 9, 15, 21):
        tick(1, hour)
    for hour in (3, 9, 15, 21):
        tick(2, hour, focus=False)         # the focus ran out between the two days
    tick(3, 3, focus=False)
    log.write_text("".join(lines), encoding="utf-8")
    reader = LogTailAdapter(log, country="SWE")
    state = reader.read_state()
    assert state.date == "1939-09-03" and state.hour == 3
    assert [w.against for w in state.wars] == ["GER"]
    assert state.national_focus is None
    done = [e for e in state.events if e.kind == "focus_complete"]
    assert len(done) == 1 and "SWE_rearm" in done[0].text
    assert [e for e in state.events if e.kind == "war_ended"] == []
    assert state.telemetry_ticks == 8


def test_the_cadence_event_reads_as_an_info_event(tmp_path):
    log = tmp_path / "game.log"
    log.write_text("[10:00:00][1939.09.01.21]: LLMB|v5|evt|tag=SWE|kind=20|detail=6\n"
                   "[10:00:00][1939.09.01.21]: LLMB|v5|state|date=x|tag=SWE|pp=1\n",
                   encoding="utf-8")
    state = LogTailAdapter(log, country="SWE").read_state()
    (event,) = [e for e in state.events if e.kind == "telemetry_cadence"]
    assert event.severity == "info" and "every 6 in-game hours" in event.text
