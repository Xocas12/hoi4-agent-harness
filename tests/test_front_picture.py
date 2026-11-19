"""The front picture (#85): divisions per state, from the mod's div/divend lines.

Why per state: a division scope has no location a script can read, but a state
can be asked for the divisions standing in it (llm_bridge_divisions.txt).
"""

import re
from pathlib import Path

from hoi4_harness.adapters.logtail import SCHEMA_VERSION, LogTailAdapter
from hoi4_harness.observation.delta import render_delta
from hoi4_harness.observation.render import render_full
from hoi4_harness.types import GameState, StateForces

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"


def stamp(d):
    return f"[11:04:29][1939.09.{d:02d}.21]: "


def state_line(d, tag="SWE"):
    return (f"{stamp(d)}LLMB|v{SCHEMA_VERSION}|state|date= 21:00, {d} September, 1939|tag={tag}"
            "|pp=1|stab=80|ws=0|civ=9|mil=3|doc=3|mp=5\n")


def div(d, sid, ctl, own, foe, own_weak=0, foe_weak=0, tag="SWE"):
    return (f"{stamp(d)}LLMB|v{SCHEMA_VERSION}|div|tag={tag}|state={sid}|ctl={ctl}|own={own}"
            f"|own_weak={own_weak}|foe={foe}|foe_weak={foe_weak}\n")


def divend(d, n, tag="SWE"):
    return f"{stamp(d)}LLMB|v{SCHEMA_VERSION}|divend|tag={tag}|n={n}\n"


def read(tmp_path, *lines):
    path = tmp_path / "game.log"
    path.write_text("".join(lines), encoding="utf-8")
    return LogTailAdapter(path).read_state()


def test_a_complete_run_becomes_the_picture(tmp_path):
    s = read(tmp_path, state_line(3), div(3, 64, "GER", 3, 5, own_weak=1, foe_weak=2),
             div(3, 66, "SWE", 2, 0), divend(3, 2))
    assert [(f.state, f.controller, f.own, f.own_weak, f.foe, f.foe_weak) for f in s.state_forces] == [
        (64, "GER", 3, 1, 5, 2), (66, "SWE", 2, 0, 0, 0)]
    assert s.state_forces_date == "1939-09-03"


def test_a_run_whose_end_has_not_arrived_shows_nothing_new(tmp_path):
    s = read(tmp_path, state_line(3), div(3, 64, "GER", 3, 5), divend(3, 1),
             div(4, 64, "GER", 1, 9))                      # day 4's divend not written yet
    assert s.state_forces[0].own == 3


def test_a_run_missing_a_line_is_dropped_whole(tmp_path):
    # n=2 but one line carried a token the game did not expand.
    bad = div(4, 66, "SWE", 0, 0).replace("own=0", "own=[?llmb_f_own]")
    s = read(tmp_path, state_line(3), div(3, 64, "GER", 3, 5), divend(3, 1),
             div(4, 64, "GER", 1, 9), bad, divend(4, 2))
    assert s.state_forces[0].own == 3 and s.state_forces_date == "1939-09-03"


def test_n_zero_is_an_answer_not_a_gap(tmp_path):
    s = read(tmp_path, state_line(3), div(3, 64, "GER", 3, 5), divend(3, 1), divend(4, 0))
    assert s.state_forces == []


def test_without_front_lines_the_picture_is_unobserved_not_empty(tmp_path):
    assert read(tmp_path, state_line(3)).state_forces is None


def test_lines_about_another_country_are_ignored(tmp_path):
    path = tmp_path / "game.log"
    path.write_text(state_line(3) + div(3, 64, "GER", 3, 5, tag="GER") + divend(3, 1, tag="GER"),
                    encoding="utf-8")
    assert LogTailAdapter(path, country="SWE").read_state().state_forces is None


def _state(*forces, date="1939-09-03"):
    return GameState(date=date, country="SWE", country_name="Sweden", state_forces=list(forces),
                     state_forces_date=date)


def test_the_brief_shows_contact_first_and_folds_the_rest():
    forces = [StateForces(s, "GER", 0, 0, 2) for s in range(10, 18)]
    forces.append(StateForces(64, "GER", 3, 1, 5, 2, name="Skane"))
    brief = render_full(_state(*forces))
    lines = brief.splitlines()
    head = next(i for i, ln in enumerate(lines) if ln.startswith("Front picture"))
    assert "own 3 vs foe 21 in 9 state(s)" in lines[head]
    assert lines[head + 1] == "  CONTACT Skane [GER]: own 3 (1 weak) | foe 5 (2 weak)"
    assert lines[head + 6].startswith("  ... 4 more state(s): own 0, foe 8")


def test_an_old_picture_says_how_old():
    s = _state(StateForces(64, "GER", 1, 0, 1))
    s.date = "1939-09-10"
    assert "7 days old" in render_full(s)


def test_nothing_is_printed_when_the_picture_is_unobserved_or_empty():
    assert "Front picture" not in render_full(GameState())
    assert "Front picture" not in render_full(_state())


def test_the_delta_names_the_states_that_moved():
    before = _state(StateForces(64, "GER", 3, 0, 5))
    after = _state(StateForces(64, "GER", 3, 0, 2), StateForces(66, "GER", 0, 0, 4), date="1939-09-04")
    text = render_delta(before, after)
    assert "Front state 64: own 3 -> 3, foe 5 -> 2 [GER]" in text
    assert "Front state 66: own 0 -> 0, foe 0 -> 4 [GER]" in text


def test_the_mod_prints_what_the_reader_parses():
    text = (MOD / "common" / "scripted_effects" / "llm_bridge_divisions.txt").read_text(encoding="utf-8")
    div_log = re.search(r'log = "(LLMB\|v\d+\|div\|[^"]+)"', text).group(1)
    fields = re.findall(r"\|(\w+)=\[", div_log)
    assert fields == ["tag", "state", "ctl", "own", "own_weak", "foe", "foe_weak"]
    assert f"LLMB|v{SCHEMA_VERSION}|divend|tag=" in text
    # Nothing here may rely on a location the division scope does not have.
    code = " ".join(line.split("#", 1)[0] for line in text.splitlines())
    assert "every_state_division" in code and "location" not in code


def test_the_daily_hook_is_the_players_and_lives_in_its_own_file():
    hook = (MOD / "common" / "on_actions" / "llm_bridge_front_on_actions.txt").read_text(encoding="utf-8")
    assert "llm_bridge_is_player = yes" in hook and "llm_bridge_emit_front = yes" in hook
