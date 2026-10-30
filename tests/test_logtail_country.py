"""logtail filters every line by config.country, which defaults to SWE."""
from hoi4_harness.adapters import build_adapter
from hoi4_harness.config import HarnessConfig

LOG = (
    "[10:00:00][1936.1.2.12][effect.cpp:1] LLMB|v1|boot|tag=GER|start=1936.1.1\n"
    "[10:00:00][1936.1.2.12][effect.cpp:1] LLMB|v1|state|date=1936.1.2|tag=GER|pp=30|stab=50|"
    "ws=20|civ=30|mil=20|doc=5|mp=500000\n"
)


def test_logtail_reads_a_german_game_with_default_config(tmp_path, monkeypatch):
    monkeypatch.delenv("HOI4_COUNTRY", raising=False)
    log = tmp_path / "game.log"
    log.write_text(LOG)
    config = HarnessConfig.from_env()
    config.adapter = "logtail"
    config.log_path = log
    state = build_adapter(config).read_state()   # raises: "no LLM Bridge telemetry"
    assert state.country == "GER"

from hoi4_harness.adapters.logtail import LogTailAdapter

P = "[10:00:00][1939.9.1.12][effect.cpp:1] "


def test_war_declared_on_player_is_seen(tmp_path):
    """on_declare_war runs in the declarer's scope; the target is `other`."""
    log = tmp_path / "game.log"
    log.write_text(
        P + "LLMB|v1|state|tag=POL|pp=30\n"
        + P + "LLMB|v1|evt|tag=GER|other=POL|kind=1|detail=0\n"
    )
    state = LogTailAdapter(log, country="POL").read_state()
    assert any(e.kind == "war_declared" for e in state.events)


def test_a_war_between_two_other_countries_is_not_the_players(tmp_path):
    log = tmp_path / "game.log"
    log.write_text(
        P + "LLMB|v1|state|tag=POL|pp=30\n"
        + P + "LLMB|v1|evt|tag=JAP|other=CHI|kind=1|detail=0\n"
    )
    state = LogTailAdapter(log, country="POL").read_state()
    assert not any(e.kind == "war_declared" for e in state.events)


def test_the_country_is_matched_whatever_its_case(tmp_path):
    log = tmp_path / "game.log"
    log.write_text(P + "LLMB|v1|state|tag=GER|pp=30\n")
    assert LogTailAdapter(log, country="ger").read_state().political_power == 30
