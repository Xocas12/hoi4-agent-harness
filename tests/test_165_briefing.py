"""#165: the briefing agent orients the playing model; it does not decide for it.

Acceptance:
1. ``hoi4-harness brief-world --country TAG`` builds the dossier from the playset and a save and
   the briefing from it with the configured model (scripted model, fixtures); the dossier's parts
   and the saved briefing are checked.
2. ``campaign_briefing`` (off by default) runs the briefing before the first turn and puts it in
   the player's context; the first model call's context is checked.
3. Re-briefing at set dates or events, configured (a fake campaign).
4. Web search is opt-in, uses the Workshop brief first, and is off for scripted and offline
   providers (injected fetcher).
5. Docs: docs/briefing.md.

Maintainer's direction: the briefing orients, the playing model decides, the person's own
instruction comes first.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from campaign_helpers import LazyClient, build_env, campaign_config, say
from hoi4_harness import briefing as B
from hoi4_harness import pathchoice
from hoi4_harness.agent.llm.anthropic_provider import WEB_SEARCH_TOOL, AnthropicClient
from hoi4_harness.agent.llm.claude_cli import ClaudeCliClient
from hoi4_harness.agent.llm.scripted import ScriptedClient
from hoi4_harness.agent.memory import Memory
from hoi4_harness.campaign.runner import load_state, run_campaign
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.identifiers import IdentifierIndex, Playset

HERE = Path(__file__).parent
GAME = HERE / "fixtures" / "paths" / "game"
SAVE = HERE / "fixtures" / "swe_1937_04_23.hoi4"
WORKSHOP = (HERE / "fixtures" / "workshop" / "tno_2438003901.html").read_text(encoding="utf-8")

INSTRUCTION = "PERSON-ORDER: play Sweden as a neutral trader and never join a faction."

BRIEFING = "\n".join([
    "## Strengths", "A small, stable economy.",
    "## Weaknesses", "Few divisions.",
    "## Opportunities", "Norway is weak.",
    "## Threats", "Germany to the south.",
    "## Viable paths", "### Armed neutrality", "Asks for factories. Gains safety. Risks isolation.",
    "### Join a bloc", "Asks for a faction.",
    "## Decision points and what to watch", "1938: look at German divisions.",
    "## What would change the picture", "An Anschluss.",
])


def briefing_reply(text: str = BRIEFING):
    return say(text)


def index():
    return IdentifierIndex.build(Playset.from_paths(GAME))


def config_for(tmp_path, **overrides):
    saves = tmp_path / "saves"
    saves.mkdir(exist_ok=True)
    shutil.copy(SAVE, saves / "SWE_1937_04_23_05.hoi4")
    options = dict(country="SWE", game_dir=GAME, save_dir=saves, campaign_until="1936-05-01",
                   campaign_briefing=True, objective=INSTRUCTION)
    options.update(overrides)
    return campaign_config(tmp_path, **options)


class Briefer(ScriptedClient):
    """A briefing model: one reply, every call recorded with whether web search was on."""

    web_search_support = True

    def __init__(self, text: str = BRIEFING):
        super().__init__(script=[])
        self.text, self.sent, self.web_flags = text, [], []
        self._web = False

    def prose(self, web: bool = False):
        from contextlib import contextmanager

        @contextmanager
        def block():
            self._web = web
            try:
                yield
            finally:
                self._web = False

        return block()

    def complete(self, system, messages, tools=None, max_tokens=None):
        self.sent.append((system, messages[0].text))
        self.web_flags.append(self._web)
        return briefing_reply(self.text)


# --- 1. brief-world and the dossier --------------------------------------------


def test_165_the_dossier_has_the_world_the_country_the_paths_and_the_guide():
    cfg = HarnessConfig(country="SWE", objective=INSTRUCTION)
    dossier = B.build_dossier(cfg, index(), "SWE", save=SAVE)
    titles = dossier.titles()
    assert titles[0] == "Objective and instruction for this country"
    assert INSTRUCTION in dossier.section(titles[0])
    assert any(t.startswith("The world on 1937-04-23") for t in titles)
    assert any(t.startswith("Your country: SWE") for t in titles)
    for part in ("Factions", "Balance of threats around SWE", "Resources",
                 "Paths the country's files open (#160)", "Strategy guide"):
        assert part in titles, part
    text = dossier.render()
    assert "SWE_ai_behavior" in text and "Join the Axis" in text
    assert "factories civ" in text and "divisions" in text
    assert "no map layer" in text                      # no layer was given: said, not hidden


def test_165_without_a_save_the_dossier_says_the_world_picture_is_missing():
    cfg = HarnessConfig(country="SWE", objective="")
    dossier = B.build_dossier(cfg, index(), "SWE", save=None)
    assert not any(t.startswith("The world on") for t in dossier.titles())
    assert any("no save" in g for g in dossier.gaps)
    assert "None given. The player chooses its own course" in dossier.section(dossier.titles()[0])


def test_165_the_dossier_names_the_previous_briefing_on_a_rebriefing():
    dossier = B.build_dossier(HarnessConfig(country="SWE"), index(), "SWE", save=None,
                              previous=("1937.1.1", "OLD BRIEFING TEXT"))
    assert "Your last briefing (written 1937.1.1)" in dossier.titles()
    assert "OLD BRIEFING TEXT" in dossier.render()


def test_165_brief_world_writes_the_briefing_and_the_dossier(tmp_path, monkeypatch, capsys):
    client = Briefer()
    monkeypatch.setattr("hoi4_harness.agent.llm.build_llm", lambda spec: client)
    out = tmp_path / "out"
    code = main(["brief-world", "--country", "SWE", "--game-dir", str(GAME), "--save", str(SAVE),
                 "--out", str(out), "--objective", INSTRUCTION])
    assert code == 0
    shown = capsys.readouterr().out
    assert "## Viable paths" in shown                  # shown to the person
    saved = (out / "briefing.md").read_text(encoding="utf-8")
    assert saved.startswith("# Briefing for SWE, 1937-04-23") and "Armed neutrality" in saved
    dated = sorted(p.name for p in (out / "briefings").iterdir())
    assert dated == ["1937-04-23-SWE-dossier.md", "1937-04-23-SWE.md"]
    system, prompt = client.sent[0]
    assert system == B.BRIEF_SYSTEM
    assert INSTRUCTION in prompt and "## Strategy guide" in prompt
    assert "Balance of threats around SWE" in prompt


def test_165_brief_world_dossier_only_calls_no_model(monkeypatch, capsys):
    def refuse(spec):
        raise AssertionError("no model may be built")

    monkeypatch.setattr("hoi4_harness.agent.llm.build_llm", refuse)
    assert main(["brief-world", "--country", "SWE", "--game-dir", str(GAME), "--save", str(SAVE),
                 "--dossier-only"]) == 0
    assert "# Dossier: SWE" in capsys.readouterr().out


def test_165_brief_world_needs_a_country(capsys):
    assert main(["brief-world", "--game-dir", str(GAME)]) == 2


def test_165_a_reply_missing_sections_is_kept_and_says_which(tmp_path):
    cut = BRIEFING.replace("## What would change the picture", "## Other").replace(
        "## Threats", "## Risks")
    made = B.brief(Briefer(cut), B.build_dossier(HarnessConfig(country="SWE"), index(), "SWE"))
    assert made.missing == ["Threats", "What would change the picture"]
    assert made.text.startswith("## Strengths")


# --- the briefing orients, it does not decide ------------------------------------


def test_165_the_briefing_model_is_asked_for_options_not_a_recommendation():
    system = B.BRIEF_SYSTEM
    assert "an orientation for the player, not a plan for it" in system
    assert "Do NOT recommend one grand strategy" in system
    assert "the player decides" in system
    assert "Viable paths" in system and "timing window" in system
    assert "that instruction stands" in system and "never argue against it" in system
    assert "## Recommended" not in system


def test_165_the_injected_context_frames_the_briefing_as_orientation():
    block = B.context_block(BRIEFING, "1936.1.1")
    assert block.startswith("Briefing (orientation, not orders)")
    assert "names no plan for you to follow" in block
    assert "The choice is yours" in block
    assert "that comes first and this is read in its light" in block
    assert block.endswith(BRIEFING.strip())


def test_165_the_persons_instruction_sits_above_the_briefing_in_the_playing_models_context(tmp_path):
    player = LazyClient(nap=20)
    config = config_for(tmp_path)
    run_campaign(config, planner=player, env=build_env(config), briefer=Briefer())
    first_turn = player.calls[0][1][0].text
    assert "Standing objective: " + INSTRUCTION in first_turn
    assert first_turn.index(INSTRUCTION) < first_turn.index("Briefing (orientation, not orders)")
    assert first_turn.index("Briefing (orientation, not orders)") < first_turn.index("Armed neutrality")
    assert "The choice is yours" in first_turn


def test_165_the_dossier_the_briefer_reads_leads_with_the_persons_instruction(tmp_path):
    briefer = Briefer()
    config = config_for(tmp_path)
    run_campaign(config, planner=LazyClient(nap=20), env=build_env(config), briefer=briefer)
    prompt = briefer.sent[0][1]
    assert prompt.index("## Objective and instruction for this country") < prompt.index("## Playset")
    assert INSTRUCTION in prompt


# --- 2. campaign_briefing --------------------------------------------------------


def test_165_a_campaign_briefs_before_the_first_turn_and_the_player_gets_it(tmp_path):
    briefer, player = Briefer(), LazyClient(nap=20)
    config = config_for(tmp_path)
    run_campaign(config, planner=player, env=build_env(config), briefer=briefer)
    assert len(briefer.sent) == 1                      # asked once
    assert briefer.sent[0][0] == B.BRIEF_SYSTEM
    first = player.calls[0][1][0].text                 # the player's first context
    assert "Armed neutrality" in first and "Norway is weak." in first
    run = tmp_path / "run"
    assert (run / "briefing.md").read_text(encoding="utf-8").startswith("# Briefing for SWE")
    assert (run / "briefings").is_dir()
    state = load_state(run)["briefing"]
    assert state["history"][0]["reason"] == "the campaign start"
    assert json.loads((run / "memory.json").read_text(encoding="utf-8"))["briefing"].startswith(
        "Briefing (orientation, not orders)")
    log = (run / "campaign.log").read_text(encoding="utf-8")
    assert "briefing written for SWE (the campaign start)" in log and "outside budget.max_usd" in log


def test_165_the_briefing_is_off_by_default(tmp_path):
    player = LazyClient(nap=20)
    config = campaign_config(tmp_path, country="SWE", game_dir=GAME, campaign_until="1936-05-01")
    assert config.campaign_briefing is False and config.campaign_rebrief == []
    run_campaign(config, planner=player, env=build_env(config))
    assert "Briefing (orientation" not in player.calls[0][1][0].text
    assert not (tmp_path / "run" / "briefing.md").exists()


def test_165_without_its_own_model_the_players_client_briefs(tmp_path):
    player = LazyClient(script=[briefing_reply()], nap=20)
    config = config_for(tmp_path)
    run_campaign(config, planner=player, env=build_env(config))
    assert player.calls[0][0] == B.BRIEF_SYSTEM
    assert "Armed neutrality" in player.calls[1][1][0].text


def test_165_a_failing_briefing_model_does_not_end_the_campaign(tmp_path):
    class Broken(Briefer):
        def complete(self, *args, **kwargs):
            raise RuntimeError("the api is down")

    player = LazyClient(nap=20)
    config = config_for(tmp_path)
    _, summary = run_campaign(config, planner=player, env=build_env(config), briefer=Broken())
    assert summary["reached_end_date"] is True
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "WARNING: the briefing (the campaign start) could not be written: RuntimeError" in log
    assert "Briefing (orientation" not in player.calls[0][1][0].text


def test_165_a_resumed_run_keeps_its_briefing_and_does_not_ask_again(tmp_path):
    config = config_for(tmp_path)
    run_campaign(config, planner=LazyClient(nap=20), env=build_env(config), briefer=Briefer())
    again, player = Briefer("## Strengths\nNEW"), LazyClient(nap=20)
    config = config_for(tmp_path, campaign_until="1936-06-01")
    run_campaign(config, planner=player, env=build_env(config), resume=True, briefer=again)
    assert again.sent == []
    assert "Armed neutrality" in player.calls[0][1][0].text


def test_165_the_choose_path_prompt_carries_the_briefing_and_the_choice_stays_the_models(tmp_path):
    player = LazyClient(script=[say(json.dumps({"choice": "DEFAULT", "reason": "Neutral."}))],
                        nap=20)
    config = config_for(tmp_path, campaign_choose_path=True)
    run_campaign(config, planner=player, env=build_env(config), briefer=Briefer())
    ask = next(c for c in player.calls if c[0] == pathchoice.CHOOSE_SYSTEM)[1][0].text
    assert "Armed neutrality" in ask
    assert "The briefing informs your choice; it does not make it." in ask
    assert load_state(tmp_path / "run")["path_choice"]["option"] == "DEFAULT"


def test_165_memory_pins_the_briefing_across_a_save_and_load(tmp_path):
    memory = Memory()
    memory.briefing = B.context_block("## Strengths\nx", "1936.1.1")
    for n in range(30):
        memory.note("1936.1.1", n, f"note {n}")
    memory.save(tmp_path / "m.json")
    loaded = Memory.load(tmp_path / "m.json")
    assert loaded.briefing.startswith("Briefing (orientation, not orders)")
    assert loaded.context_block().startswith("Briefing (orientation, not orders)")


# --- 3. re-briefing --------------------------------------------------------------


def test_165_the_schedule_parses_dates_years_and_events_and_refuses_the_rest():
    assert B.parse_schedule("Yearly, 1938-06-01, war,capitulation") == [
        "yearly", "1938-06-01", "war", "capitulation"]
    assert B.parse_schedule(["faction"]) == ["faction"]
    assert B.parse_schedule(None) == []
    with pytest.raises(ValueError, match="not yearly"):
        B.parse_schedule("monthly")


def due(schedule, **kw):
    base = dict(date="1937.1.2", last="1936.1.1", seen_wars=set(), wars=set(),
                faction_before=None, faction_now=None, capitulated=False, done_dates=set())
    base.update(kw)
    return B.due(schedule, **base)


def test_165_due_fires_on_the_year_the_date_and_the_events():
    assert due(["yearly"]) == "12 months since the last briefing"
    assert due(["yearly"], date="1936.11.1") is None
    assert due(["halfyearly"], date="1936.7.1").startswith("6 months")
    assert due(["1936-09-01"], date="1936.9.2") == "the date 1936-09-01 was reached"
    assert due(["1936-09-01"], date="1936.8.30") is None
    assert due(["1936-09-01"], date="1936.9.2", done_dates={"1936-09-01"}) is None
    assert due(["war"], date="1936.3.1", wars={"POL"}) == "war with POL"
    assert due(["war"], date="1936.3.1", wars={"POL"}, seen_wars={"POL"}) is None
    assert due(["capitulation"], date="1936.3.1", capitulated=True) == "the country capitulated"
    assert due(["faction"], date="1936.3.1", faction_before=None, faction_now="Axis").startswith(
        "faction changed")
    assert due([], date="1940.1.1") is None


def test_165_a_fake_campaign_briefs_again_at_the_configured_date(tmp_path):
    briefer, player = Briefer(), LazyClient(nap=30)
    config = config_for(tmp_path, campaign_until="1936-09-01", campaign_rebrief=["1936-05-01"])
    run_campaign(config, planner=player, env=build_env(config), briefer=briefer)
    assert len(briefer.sent) == 2
    history = load_state(tmp_path / "run")["briefing"]["history"]
    assert [h["reason"] for h in history] == [
        "the campaign start", "the date 1936-05-01 was reached"]
    assert "## Your last briefing" in briefer.sent[1][1]      # the second reads the first
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "briefing written for SWE (the date 1936-05-01 was reached)" in log
    later = [c[1][0].text for c in player.calls[-2:]]
    assert all("Armed neutrality" in t for t in later)
    assert len(list((tmp_path / "run" / "briefings").glob("*-SWE.md"))) >= 1


def test_165_the_yearly_schedule_briefs_once_a_year_and_not_before(tmp_path):
    briefer = Briefer()
    config = config_for(tmp_path, campaign_until="1937-04-01", campaign_rebrief="yearly")
    run_campaign(config, planner=LazyClient(nap=30), env=build_env(config), briefer=briefer)
    reasons = [h["reason"] for h in load_state(tmp_path / "run")["briefing"]["history"]]
    assert reasons[0] == "the campaign start"
    assert len(reasons) == 2 and reasons[1].endswith("months since the last briefing")


def test_165_without_a_schedule_the_campaign_briefs_once(tmp_path):
    briefer = Briefer()
    config = config_for(tmp_path, campaign_until="1936-12-01")
    run_campaign(config, planner=LazyClient(nap=30), env=build_env(config), briefer=briefer)
    assert len(briefer.sent) == 1


def test_165_a_bad_schedule_fails_at_the_start_not_a_year_in(tmp_path):
    config = config_for(tmp_path, campaign_rebrief=["monthly"])
    with pytest.raises(ValueError, match="campaign_rebrief"):
        run_campaign(config, planner=LazyClient(nap=20), env=build_env(config), briefer=Briefer())


# --- 4. web search ---------------------------------------------------------------


class Opener:
    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return WORKSHOP


def mod_playset(tmp_path):
    mod = tmp_path / "mods" / "tno"
    mod.mkdir(parents=True)
    (mod / "descriptor.mod").write_text(
        'name="Fixture Mod"\nversion="1"\nremote_file_id="2438003901"\n', encoding="utf-8")
    cfg = HarnessConfig(country="SWE")
    return cfg, IdentifierIndex.build(Playset.from_paths(GAME, [mod]))


def test_165_the_workshop_brief_is_read_first_and_only_when_web_is_on(tmp_path):
    cfg, idx = mod_playset(tmp_path)
    opener = Opener()
    off = B.build_dossier(cfg, idx, "SWE", offline=True, opener=opener, cache_dir=tmp_path / "c")
    assert opener.urls == []                           # offline: no fetch
    assert "Fixture Mod [descriptor]" in off.section("Mods")
    assert off.lookup == ["Fixture Mod"]
    on = B.build_dossier(cfg, idx, "SWE", offline=False, opener=opener, cache_dir=tmp_path / "c")
    assert opener.urls == ["https://steamcommunity.com/sharedfiles/filedetails/?id=2438003901"]
    assert "Fixture Mod [steam]" in on.section("Mods")
    assert on.lookup == []                             # the page settled it: nothing to search


def test_165_a_thin_mod_goes_on_the_lookup_list_the_model_may_search(tmp_path):
    cfg, idx = mod_playset(tmp_path)
    page_less = lambda url: "<html>no description</html>"  # noqa: E731
    dossier = B.build_dossier(cfg, idx, "SWE", offline=False, opener=page_less,
                              cache_dir=tmp_path / "c")
    assert dossier.lookup == ["Fixture Mod"]
    assert "## Mods to look up" in dossier.render()
    client = Briefer()
    B.brief(client, dossier, web=True)
    assert B.WEB_NOTE in client.sent[0][0] and client.web_flags == [True]
    B.brief(client, dossier, web=False)
    assert B.WEB_NOTE not in client.sent[1][0] and client.web_flags[1] is False


def test_165_web_search_is_off_unless_asked_for():
    on, why = B.enable_web(Briefer(), False)
    assert (on, why) == (False, "web search is off")
    assert B.enable_web(Briefer(), True) == (True, "web search is on")


def test_165_web_search_is_off_for_scripted_and_mailbox_providers():
    from hoi4_harness.agent.llm.mailbox import MailboxClient

    on, why = B.enable_web(ScriptedClient(script=[]), True)
    assert on is False and "not available for the" in why
    assert B.enable_web(MailboxClient("x"), True)[0] is False


def test_165_a_campaign_with_web_on_and_a_scripted_model_fetches_nothing(tmp_path):
    opener = Opener()
    player = LazyClient(script=[briefing_reply()], nap=20)
    config = config_for(tmp_path, briefing_web_search=True)
    run_campaign(config, planner=player, env=build_env(config), briefing_opener=opener)
    assert opener.urls == []
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    assert "briefing: web search is not available for the scripted provider" in log


def test_165_a_campaign_with_web_on_and_a_capable_model_reads_the_workshop_first(tmp_path):
    mod = tmp_path / "mods" / "tno"
    mod.mkdir(parents=True)
    (mod / "descriptor.mod").write_text(
        'name="Fixture Mod"\nversion="1"\nremote_file_id="2438003901"\n', encoding="utf-8")
    opener, briefer = Opener(), Briefer()
    config = config_for(tmp_path, briefing_web_search=True, mod_dirs=[mod],
                        mod_context_cache_dir=tmp_path / "cache")
    run_campaign(config, planner=LazyClient(nap=20), env=build_env(config), briefer=briefer,
                 briefing_opener=opener)
    assert len(opener.urls) == 1 and briefer.web_flags == [True]
    assert "Fixture Mod [steam]" in briefer.sent[0][1]


def test_165_the_api_web_search_tool_is_added_only_while_asked():
    client = AnthropicClient.__new__(AnthropicClient)
    client.cache_prefix, client.web_search_uses = False, 0
    assert client._tools(None) == []
    with client.prose(web=True):
        assert client._tools(None) == [{"type": WEB_SEARCH_TOOL, "name": "web_search",
                                        "max_uses": 5}]
    assert client._tools(None) == []                   # the player's own calls are untouched
    with client.prose(web=False):
        assert client._tools(None) == []


def test_165_claude_cli_gets_web_tools_only_inside_a_web_briefing(tmp_path):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        envelope = json.dumps({"result": "## Strengths\nfine", "usage": {"input_tokens": 3,
                                                                          "output_tokens": 4}})
        return subprocess.CompletedProcess(command, 0, stdout=envelope, stderr="")

    client = ClaudeCliClient(cli="claude", workdir=tmp_path, run=fake_run)
    from hoi4_harness.agent.llm.base import Msg

    with client.prose(web=True):
        reply = client.complete("sys", [Msg(role="user", text="dossier")])
    assert reply.text == "## Strengths\nfine"          # prose back, not a JSON envelope
    tools = commands[0][commands[0].index("--tools") + 1]
    assert tools == "WebSearch,WebFetch"
    with client.prose(web=False):
        client.complete("sys", [Msg(role="user", text="dossier")])
    assert commands[1][commands[1].index("--tools") + 1] == ""
    assert client.plain is False and client.web is False     # restored for the player's turns


def test_165_a_save_later_than_the_game_date_is_never_briefed(tmp_path):
    # Live 2026-11-27: a fresh 1936 campaign was briefed from a 1941 save of another game.
    from hoi4_harness.campaign.briefer import _newest_not_after

    (tmp_path / "GER_1941.hoi4").write_text('HOI4txt\ndate="1941.8.31.2"\nplayer="GER"\n', encoding="utf-8")
    assert _newest_not_after(tmp_path, "1936-01-01", "GER") is None
    old = tmp_path / "GER_1936.hoi4"
    old.write_text('HOI4txt\ndate="1936.1.1.12"\nplayer="GER"\n', encoding="utf-8")
    assert _newest_not_after(tmp_path, "1936-01-01", "GER") == old
