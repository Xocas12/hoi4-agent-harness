"""LogTailAdapter only resets its offset when the log shrinks. A new game.log
(game restarted) that has already grown past the old offset is read from the
middle: its first lines -- here the new session's tag and date -- are skipped."""
from hoi4_harness.adapters.logtail import LogTailAdapter


def line(date, tag, pp):
    return (f"[10:00:00][{date}.1][effect.cpp:1] LLMB|v1|state|tag={tag}|pp={pp}|"
            "stab=50|ws=10|civ=10|mil=5|doc=2|mp=1000\n")


def test_rotated_log_larger_than_offset_is_read_from_the_start(tmp_path):
    log = tmp_path / "game.log"
    log.write_text(line("1936.1.1", "SWE", 10), encoding="utf-8")
    reader = LogTailAdapter(log_path=log)
    assert reader.read_state().date == "1936-01-01"

    # Game restarted: game.log is recreated and the first tick of the new
    # session is already longer than the old file before the next poll.
    new = line("1940.6.1", "GER", 99) + "[10:00:01] some engine line, padding padding padding\n"
    log.unlink()
    log.write_text(new, encoding="utf-8")
    state = reader.read_state()
    assert state.date == "1940-06-01" and state.political_power == 99.0, (
        state.date, state.political_power, state.country)
