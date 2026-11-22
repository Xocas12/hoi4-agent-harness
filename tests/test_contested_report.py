"""Issue #161 step 1: the measurement script reads successive saves and reports, per production
line the model added, whether it still exists, its factories, and what the AI changed.

``fixtures/contested/`` is four small text saves in the real layout (a stable id per
``military_lines`` entry, the ``equipments`` block that names each variant): the model's
infantry line is kept but loses factories and is then switched to a newer model by the AI; its
Tank_A line is deleted once the AI has put the design on a line of its own; a third line it asked
for never shows up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hoi4_harness import contested
from hoi4_harness.adapters.console import CONTESTED_LINES_FILE

FIXTURES = Path(__file__).parent / "fixtures"
SAVES = FIXTURES / "contested"

RECORDS = [
    {"call_id": "a", "country": "GER", "date": "1936-01-15", "equipment": "infantry_equipment_1",
     "variant": None, "factories": 5, "wall_time": 1},
    {"call_id": "b", "country": "GER", "date": "1936-01-20", "equipment": "medium_tank_chassis_1",
     "variant": "Tank_A", "factories": 2, "wall_time": 2},
    {"call_id": "c", "country": "GER", "date": "1936-01-25", "equipment": "artillery_equipment_1",
     "variant": None, "factories": 1, "wall_time": 3},
]


@pytest.fixture(scope="module")
def result():
    return contested.report(RECORDS, contested.read_saves([SAVES]))


def test_161_the_saves_are_read_in_date_order_with_their_lines(result):
    assert [s["date"] for s in result["saves"]] == [
        "1936-01-01", "1936-02-01", "1936-03-01", "1936-04-01"]
    assert [s["lines"] for s in result["saves"]] == [2, 4, 5, 4]


def test_161_a_line_the_ai_cut_and_then_switched_is_reported_with_both_changes(result):
    line = result["lines"][0]
    assert line["found"] and line["line_id"] == 20 and line["baselined"]
    assert line["first_seen"] == "1936-02-01" and line["still_exists"]
    assert (line["factories_first"], line["factories_last"]) == (5, 3)
    assert (line["factories_max"], line["factories_min"]) == (5, 3)
    assert line["changes"] == ["1936-03-01: factories 5 -> 3",
                               "1936-04-01: switched infantry_equipment_1 -> infantry_equipment_2"]
    assert line["outcome"] == "switched"


def test_161_a_deleted_line_is_reported_gone_and_the_ais_use_of_the_design_is_counted(result):
    line = result["lines"][1]
    assert line["found"] and line["line_id"] == 21 and line["variant"] == "Tank_A"
    assert not line["still_exists"] and line["factories_last"] == 0
    assert line["changes"] == ["1936-04-01: line removed"] and line["outcome"] == "deleted"
    design = result["designs"]["Tank_A"]
    assert design["ai_builds_it"]
    assert [p["lines"] for p in design["ai_lines_with_it"]] == [0, 0, 1, 1]


def test_161_a_line_that_never_appears_is_not_found_and_does_not_steal_another(result):
    line = result["lines"][2]
    assert not line["found"] and line["outcome"] == "not_found"
    assert result["outcomes"] == {"switched": 1, "deleted": 1, "not_found": 1}


def test_161_a_line_nobody_touched_is_kept_unchanged():
    records = [{"call_id": "x", "date": "1936-01-15", "equipment": "infantry_equipment_1",
                "variant": None, "factories": 5, "wall_time": 1}]
    saves = [s for s in contested.read_saves([SAVES]) if s.date < "1936-03-01"]
    (line,) = contested.report(records, saves)["lines"]
    assert line["outcome"] == "kept_unchanged" and line["still_exists"] and line["changes"] == []


def test_161_without_a_save_before_the_action_nothing_is_guessed():
    records = [{"call_id": "x", "date": "1935-12-01", "equipment": "infantry_equipment_1",
                "variant": None, "factories": 5, "wall_time": 1}]
    (line,) = contested.report(records, contested.read_saves([SAVES]))["lines"]
    assert not line["found"] and "no save before the action" in line["note"]


def test_161_a_real_save_parses_to_its_production_lines():
    saved = contested.parse_save((FIXTURES / "swe_1938_02_04.hoi4").read_text(encoding="utf-8"))
    assert saved.date == "1938-02-04" and saved.tag == "SWE"
    assert saved.lines[25].equipment == "infantry_equipment_1" and saved.lines[25].factories == 1
    assert saved.lines[500].equipment == "infantry_equipment_1"
    assert len(saved.lines) >= 4


def test_161_the_script_prints_the_report_from_a_run_dir(tmp_path, capsys):
    (tmp_path / CONTESTED_LINES_FILE).write_text(
        "\n".join(json.dumps(r) for r in RECORDS) + "\n", encoding="utf-8")
    assert contested.main([str(tmp_path), "--saves", str(SAVES)]) == 0
    text = capsys.readouterr().out
    assert "Lines the model added (confirmed by their marker): 3" in text
    assert "AI: 1936-03-01: factories 5 -> 3" in text and "AI: 1936-04-01: line removed" in text
    assert "Design Tank_A: the AI builds it on lines of its own: yes" in text
    assert contested.main([str(tmp_path), "--saves", str(SAVES), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["outcomes"]["deleted"] == 1


def test_161_the_records_the_console_writes_are_the_ones_the_report_reads(tmp_path):
    from test_contested_production import _add, _rig

    _game, channel, _ = _rig(tmp_path)
    assert _add(channel, equipment="infantry_equipment_1", factories=4).ok
    (record,) = contested.load_records(channel.config.archive_dir)
    assert record["equipment"] == "infantry_equipment_1" and record["factories"] == 4
    assert {"date", "country", "variant", "call_id", "wall_time"} <= set(record)
