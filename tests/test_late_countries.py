"""Countries that appear after the start: civil-war breakaways (dynamic tags) and late fixed tags."""

from __future__ import annotations

from pathlib import Path

from hoi4_harness import late, modgen
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.inbox import EVENTS_FILE, Inbox

PLAYSET = Path(__file__).parent / "fixtures" / "playset" / "game"
MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"
STAMP = "[11:04:29][1936.07.18.12]: "


def test_breakaway_lines_are_read_and_matched_by_origin_and_ideology():
    text = (f"{STAMP}LLMB|v5|state|tag=SPR|pp=1\n"
            f"{STAMP}LLMB|v5|dyn|of=CHI|tag=D02|ideo=Communism\n"
            f"{STAMP}LLMB|v5|dyn|of=SPR|tag=D01|ideo=Fascism\n")
    found = late.breakaways(text)
    assert [b.tag for b in found] == ["D02", "D01"]
    assert late.match(found, "SPR", None).tag == "D01"
    assert late.match(found, "spr", "fascism").tag == "D01"
    assert late.match(found, "SPR", "communism") is None
    assert late.match(found, "GER", None) is None


def test_the_wait_finds_the_breakaway_once_the_civil_war_starts(tmp_path):
    log = tmp_path / "game.log"
    log.write_text(f"{STAMP}LLMB|v5|state|tag=SPR\n", encoding="utf-8")
    said, naps = [], []

    def sleep(seconds):
        naps.append(seconds)
        if len(naps) == 2:     # two polls later the war breaks out
            with log.open("a", encoding="utf-8") as fh:
                fh.write(f"{STAMP}LLMB|v5|dyn|of=SPR|tag=D01|ideo=Fascism\n")

    found = late.wait_for_breakaway(log, "SPR", "fascism", said.append, sleep=sleep, poll=0)
    assert found.tag == "D01" and len(naps) == 2
    assert "break away from SPR" in said[0] and "playing D01" in said[-1]


def test_the_wait_gives_up_when_the_game_quits(tmp_path):
    log = tmp_path / "game.log"
    log.write_text("", encoding="utf-8")
    assert late.wait_for_breakaway(log, "SPR", None, lambda s: None, sleep=lambda s: None,
                                   alive=lambda: False) is None


def test_the_probe_names_every_dynamic_country_with_its_origin():
    index = IdentifierIndex.build(Playset.from_paths(PLAYSET))
    text = modgen.generate_probe(index).text
    assert "limit = { is_dynamic_country = yes }" in text
    some = sorted(index.tags)[0]
    assert (f'if = {{ limit = {{ original_tag = {some} }} log = "LLMB|v5|dyn|of={some}'
            '|tag=[THIS.GetTag]|ideo=[THIS.GetRulingIdeology]" }') in text
    committed = (MOD / modgen.PROBE_PATH).read_text(encoding="utf-8")
    assert "original_tag = SPR }" in committed           # Spain, vanilla


def test_orders_for_a_country_that_does_not_exist_yet_wait_in_the_inbox(tmp_path):
    box = Inbox(tmp_path, clock=lambda: 1_700_000_000.0)
    box.post(["effect D01 llm_bridge_mark_harness"])
    events = (tmp_path / EVENTS_FILE).read_text(encoding="utf-8")
    assert ("if = { limit = { country_exists = D01 } D01 = { country_event = { id = llmb_inbox.2 } } }"
            in events)
