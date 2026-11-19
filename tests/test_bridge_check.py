"""Checking the bridge mod against an install and a log (#1, #2)."""

from pathlib import Path

from hoi4_harness.bridge_check import check_log, check_tokens
from hoi4_harness.cli import main

GAME = Path(__file__).parent / "fixtures" / "playset" / "game"


def test_on_actions_the_mod_hooks_are_found_in_the_install():
    report = check_tokens(GAME)
    assert report.on_actions_found == ["on_capitulation", "on_daily", "on_declare_war", "on_startup"]
    assert report.on_actions_missing == []
    assert report.ok


def test_a_hook_the_install_does_not_define_is_missing(tmp_path):
    mod = tmp_path / "mod"
    (mod / "common" / "on_actions").mkdir(parents=True)
    (mod / "common" / "on_actions" / "x.txt").write_text(
        "on_actions = {\n\ton_research_complete = { effect = { x = { y = 1 } } }\n}\n")
    report = check_tokens(GAME, mod)
    assert report.on_actions_missing == ["on_research_complete"] and not report.ok
    assert "never fire" in report.render()


def test_completion_candidates_are_listed_for_issue_2():
    assert check_tokens(GAME).completion_candidates == ["on_fixture_focus_done"]


def test_strategy_types_the_game_never_uses_are_suspect():
    report = check_tokens(GAME)
    # The fixture uses conquer, protect and front_unit_request; the mod also
    # raises garrison, invade, contain, befriend, antagonize and ignore.
    assert {"conquer", "protect", "front_unit_request"} <= set(report.strategy_types_seen)
    assert "garrison" in report.strategy_types_unseen and "invade" in report.strategy_types_unseen


def test_triggers_are_checked_against_the_installs_documentation():
    report = check_tokens(GAME)
    assert report.documentation_found and report.triggers_unseen == ["num_divisions"]


def test_the_log_check_names_fields_that_did_not_expand(tmp_path):
    log = tmp_path / "game.log"
    log.write_text(
        "[17:34:20][1936.01.02.01][x.cpp:1]: LLMB|v3|state|date=1936.1.2|tag=SWE|pp=42"
        "|stab=[?llmb_stab]|ws=18\n"
        "[17:34:21][1936.01.03.01][x.cpp:1]: LLMB|v3|evt|tag=SWE|kind=1|detail=war\n"
        "noise\n"
    )
    report = check_log(log)
    assert report.lines == 2 and report.engine_dated == 2
    assert report.expanded == {"pp": "42", "ws": "18"}
    assert report.literal == {"stab": "[?llmb_stab]"} and not report.ok
    text = report.render()
    assert "LITERAL" in text and "war_declared x1" in text


def test_a_field_that_expanded_once_is_real(tmp_path):
    log = tmp_path / "game.log"
    log.write_text("[x]: LLMB|v3|state|tag=SWE|pp=[?llmb_pp]\n[x]: LLMB|v3|state|tag=SWE|pp=5\n")
    report = check_log(log)
    assert report.literal == {} and report.ok


def test_a_log_with_no_telemetry_says_so(tmp_path):
    log = tmp_path / "game.log"
    log.write_text("nothing here\n")
    assert "no LLMB telemetry" in check_log(log).render()


def test_the_command_runs_both_checks(tmp_path, capsys):
    log = tmp_path / "game.log"
    log.write_text("[x]: LLMB|v3|state|tag=SWE|pp=5|stab=60|ws=10|civ=1|mil=1|doc=1|mp=5\n")
    assert main(["verify-bridge", "--game-dir", str(GAME), "--log", str(log)]) == 0
    out = capsys.readouterr().out
    assert "tokens, against the install" in out and "log: 1 telemetry line" in out


def test_the_command_needs_something_to_check(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("HOI4_GAME_DIR", raising=False)
    assert main(["verify-bridge", "--log", str(tmp_path / "missing.log")]) == 2


STATE_LINE = ("[11:04:29][1936.01.{d:02d}.21]: LLMB|v3|state|date= 21:00, {d} January, 1936|tag={tag}"
              "|pp=1.9|stab=83.2|ws=0|civ=9|mil=3|doc=3|mp={mp}\n")


def _log(tmp_path, *lines):
    path = tmp_path / "game.log"
    path.write_text("".join(lines), encoding="utf-8")
    return path


def test_the_game_date_text_is_reported_parsed(tmp_path):
    log = _log(tmp_path, "[10:52:52][1936.01.01.12]: LLMB|v3|boot|tag=SWE|start= 12:00, 1 January, 1936\n")
    report = check_log(log)
    assert report.mod_date_shapes == {"parsed"}


def test_a_session_labelled_with_the_player_throughout_is_fine(tmp_path):
    report = check_log(_log(tmp_path, *[STATE_LINE.format(d=d, tag="SWE", mp=31153) for d in (2, 3, 4)]))
    assert report.tags == ["SWE"] and not report.tag_problems and report.ok


def test_a_tag_that_varies_is_the_on_daily_scope_bug(tmp_path):
    # What 1.19.5.0 printed before the fix: the player's numbers once per
    # country, tagged LIT / BRA / POL, with mp=0 for the others.
    report = check_log(_log(tmp_path, STATE_LINE.format(d=2, tag="LIT", mp=0),
                            STATE_LINE.format(d=2, tag="BRA", mp=0),
                            STATE_LINE.format(d=2, tag="POL", mp=0)))
    assert not report.ok
    assert "tag varies across state lines: LIT, BRA, POL" in report.render()
    assert "on_daily scope bug?" in report.render()


def test_a_blank_tag_is_flagged(tmp_path):
    report = check_log(_log(tmp_path, STATE_LINE.format(d=2, tag="", mp=5),
                            STATE_LINE.format(d=3, tag="SWE", mp=5)))
    assert not report.ok and "blank tag" in report.render()


def test_with_a_known_player_any_other_tag_is_wrong(tmp_path):
    log = _log(tmp_path, STATE_LINE.format(d=2, tag="POL", mp=5))
    assert check_log(log).ok                          # one tag is consistent...
    report = check_log(log, player="swe")             # ...but not the player's
    assert not report.ok and "expected the player SWE" in report.render()


def test_army_lines_are_held_to_the_same_tag(tmp_path):
    report = check_log(_log(tmp_path, STATE_LINE.format(d=2, tag="SWE", mp=5),
                            "[x]: LLMB|v3|army|tag=POL|divisions=12|delegated=0\n"))
    assert report.tags == ["SWE", "POL"] and not report.ok


def test_the_new_counters_are_checked_for_expansion(tmp_path):
    line = STATE_LINE.format(d=2, tag="SWE", mp=5).rstrip("\n") + "|techs=[?llmb_techs]|built_civ=3\n"
    report = check_log(_log(tmp_path, line))
    assert report.literal == {"techs": "[?llmb_techs]"} and report.expanded["built_civ"] == "3"
