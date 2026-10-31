"""A line the game has only half-written is parsed and consumed."""
from hoi4_harness.adapters.logtail import LogTailAdapter

PREFIX = "[10:00:00][1936.1.2.12][effect.cpp:1] "


def test_half_written_state_line(tmp_path):
    log = tmp_path / "game.log"
    full = PREFIX + "LLMB|v1|state|tag=SWE|pp=30|stab=50|ws=20|civ=12|mil=5|doc=4|mp=214000\n"
    cut = full.index("mp=21") + len("mp=21")
    log.write_text(PREFIX + "LLMB|v1|state|tag=SWE|pp=29\n" + full[:cut])
    adapter = LogTailAdapter(log)
    assert adapter.read_state().political_power == 29   # the unfinished line waits
    with log.open("a") as handle:
        handle.write(full[cut:])
    assert adapter.read_state().manpower == 214000   # got 21


def test_half_written_event_code_becomes_war(tmp_path):
    log = tmp_path / "game.log"
    state = PREFIX + "LLMB|v1|state|tag=SWE|pp=30\n"
    evt = PREFIX + "LLMB|v1|evt|tag=SWE|kind=12|detail=0\n"   # directives cleared
    cut = evt.index("kind=1") + len("kind=1")
    log.write_text(state + evt[:cut])
    adapter = LogTailAdapter(log)
    adapter.read_state()
    with log.open("a") as handle:
        handle.write(evt[cut:])
    kinds = [e.kind for e in adapter.read_state().events]
    assert "war_declared" not in kinds   # a stand-down read as a critical war declaration
