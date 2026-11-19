"""Owned civilian factories versus ones on loan through trade.

Seen live on 1.19.5: the mod's civ field read 20 while the top bar showed 15
factories in all. The game's tooltip: civilian 9 owned + 11 from trade. A brief
that says "20 civ" overstates what the country builds with once trade ends.
"""

from hoi4_harness.adapters.logtail import LogTailAdapter
from hoi4_harness.observation import render_full

LINE = ("[12:00:00][1937.04.12.21]: LLMB|v3|state|date=1937.4.12|tag=SWE|pp=881|stab=82|ws=0"
        "|civ={civ}|mil=3|doc=3{owned}|mp=32980\n")


def read(tmp_path, civ, owned=None):
    log = tmp_path / "game.log"
    log.write_text(LINE.format(civ=civ, owned="" if owned is None else f"|owned={owned}"),
                   encoding="utf-8")
    return LogTailAdapter(log).read_state()


def test_trade_factories_are_split_from_owned_ones(tmp_path):
    state = read(tmp_path, civ=20, owned=15)
    assert (state.civilian_factories, state.trade_civilian_factories) == (9, 11)
    assert "Factories: 9 civ (+11 from trade) / 3 mil / 3 dockyards" in render_full(state)


def test_no_trade_shows_no_trade(tmp_path):
    state = read(tmp_path, civ=9, owned=15)
    assert state.trade_civilian_factories == 0
    assert "Factories: 9 civ / 3 mil" in render_full(state)


def test_a_split_that_does_not_add_up_is_not_used(tmp_path):
    state = read(tmp_path, civ=5, owned=15)       # would mean 9 owned of 5 in all
    assert state.civilian_factories == 5 and state.trade_civilian_factories is None


def test_an_older_line_without_owned_reports_the_games_own_count(tmp_path):
    state = read(tmp_path, civ=20)
    assert state.civilian_factories == 20 and state.trade_civilian_factories is None
