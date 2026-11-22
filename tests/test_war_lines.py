"""The brief's war lines: one per war, and the far ones folded in a world war.

Overnight campaign, 2026-11-22: Germany in 1940 was at war with 17 countries and the brief gave
each a line, eleven of them a dominion or mandate with no border and no divisions.
"""

from hoi4_harness.observation.render import war_lines
from hoi4_harness.types import War


def _w(tag, divs, borders, surrender=0.0):
    return War(against=tag, since="1939-08-31", enemy_divisions=divs, borders=borders,
               surrender_progress=surrender)


def test_a_few_wars_keep_a_line_each():
    lines = war_lines([_w("POL", 40, True), _w("ENG", 131, False)])
    assert len(lines) == 2 and lines[1].startswith("AT WAR with ENG since 1939-08-31")


def test_in_a_world_war_the_far_ones_share_a_line():
    wars = [_w("FRA", 135, True), _w("NOR", 17, True, 0.22), _w("POL", 0, False, 1.0),
            _w("ENG", 131, False), _w("RAJ", 25, False), _w("CAN", 14, False),
            _w("JOR", 0, False), _w("KUW", 0, False)]
    lines = war_lines(wars)
    assert [line.split(" since")[0] for line in lines[:3]] == [
        "AT WAR with FRA", "AT WAR with NOR", "AT WAR with POL"]
    assert lines[3] == ("AT WAR also with 5 more, no shared border, none surrendering: "
                        "divisions ENG 131, RAJ 25, CAN 14; JOR, KUW with none")
    assert len(lines) == 4
