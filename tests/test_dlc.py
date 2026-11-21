"""Which DLCs are on, and what the harness then offers (docs/dlc.md).

Everything here runs on an invented playset written to tmp_path: ids are made up, but the
shapes (``country = { factor = 0 modifier = { add = 10 tag = X has_dlc = ".." } }``, ``allow_branch``,
``has_dlc`` in ``allowed`` / ``available``) are the ones the game's own files use.
"""

from __future__ import annotations

import json
from pathlib import Path

from hoi4_harness import dlc
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig as Config
from hoi4_harness.identifiers import IdentifierIndex, Playset
from hoi4_harness.modcontext import script as S
from hoi4_harness.types import ActionCall

AAT, NSB, MTG, BBA = "Arms Against Tyranny", "No Step Back", "Man the Guns", "By Blood Alone"

FILES = {
    "common/country_tags/00_countries.txt": 'SWE = "countries/Sweden.txt"\nGER = "countries/Germany.txt"\n'
                                            'POL = "countries/Poland.txt"\n',
    "common/national_focus/generic.txt": """
focus_tree = { id = generic_focus country = { factor = 1 } default = yes
  focus = { id = GEN_work x = 0 y = 0 cost = 10 }
}
""",
    "common/national_focus/swe.txt": """
focus_tree = {
  id = sweden_focus
  country = { factor = 0 modifier = { add = 10 tag = SWE has_dlc = "Arms Against Tyranny" } }
  focus = { id = SWE_root x = 0 y = 0 cost = 10 }
  focus = { id = SWE_nsb_branch x = 1 y = 1 cost = 10 prerequisite = { focus = SWE_root }
            allow_branch = { has_dlc = "No Step Back" } }
  focus = { id = SWE_nsb_child x = 1 y = 2 cost = 10 prerequisite = { focus = SWE_nsb_branch } }
  focus = { id = SWE_nsb_gated x = 2 y = 1 cost = 10 prerequisite = { focus = SWE_root }
            available = { has_dlc = "No Step Back" } }
  focus = { id = SWE_plain x = 3 y = 1 cost = 10 prerequisite = { focus = SWE_root } }
}
""",
    "common/national_focus/ger.txt": """
focus_tree = { id = german_focus country = { factor = 0 modifier = { add = 10 tag = GER } }
  focus = { id = GER_root x = 0 y = 0 cost = 10 }
}
""",
    "common/national_focus/pol.txt": """
focus_tree = { id = polish_focus
  country = { factor = 0 modifier = { add = 10 tag = POL
              OR = { has_dlc = "Poland: United and Ready" has_dlc = "No Step Back" } } }
  focus = { id = POL_root x = 0 y = 0 cost = 10 }
}
""",
    "common/decisions/categories/cats.txt": """
swe_cat = { allowed = { tag = SWE } }
swe_aat_cat = { allowed = { tag = SWE has_dlc = "Arms Against Tyranny" } }
""",
    "common/decisions/SWE.txt": """
swe_cat = {
  swe_always = { complete_effect = { add_stability = 0.01 } }
  swe_nsb_only = { allowed = { has_dlc = "No Step Back" } complete_effect = { add_stability = 0.01 } }
  swe_not_nsb = { allowed = { NOT = { has_dlc = "No Step Back" } }
                  complete_effect = { add_stability = 0.01 } }
  swe_nsb_available = { available = { has_dlc = "No Step Back" } complete_effect = { add_stability = 0.01 } }
}
swe_aat_cat = {
  swe_in_aat_category = { complete_effect = { add_stability = 0.01 } }
}
""",
    "common/ideas/SWE.txt": """
ideas = {
  country = {
    SWE_spirit = { allowed = { original_tag = SWE } }
    SWE_nsb_spirit = { allowed = { original_tag = SWE has_dlc = "No Step Back" } }
  }
  economy = {
    law = yes
    civilian_economy = { cost = 100 default = yes }
    war_economy = { cost = 150 available = { has_dlc = "No Step Back" } }
    total_economy = { cost = 200 }
  }
}
""",
    "common/scripted_guis/swe.txt": """
scripted_gui = {
  SWE_plain_window = { context_type = player_context window_name = "w1" visible = { tag = SWE }
    effects = { swe_a_click = { add_political_power = 1 } } }
  SWE_nsb_window = { context_type = player_context window_name = "w2"
    visible = { tag = SWE has_dlc = "No Step Back" }
    effects = { swe_b_click = { add_political_power = 1 } } }
}
""",
    "common/special_projects/projects/air.txt": """
sp_base = { specialization = specialization_air allowed = { always = yes } }
sp_bba = { specialization = specialization_air allowed = { has_dlc = "By Blood Alone" } }
""",
    "common/bop/SWE.txt": """
SWE_riksdag_political_balance = { initial_value = 0 left_side = a right_side = b }
ITA_power_balance = { initial_value = 0 left_side = a right_side = b }
""",
    "events/swe_events.txt": """
add_namespace = swe
country_event = {
  id = swe.1 title = swe.1.t desc = swe.1.d
  option = { name = swe.1.a }
  option = { name = swe.1.b trigger = { has_dlc = "No Step Back" } }
  option = { name = swe.1.c trigger = { NOT = { has_dlc = "No Step Back" } } }
}
country_event = {
  id = swe.2 title = swe.2.t desc = swe.2.d trigger = { has_dlc = "By Blood Alone" }
  option = { name = swe.2.a }
}
""",
    "localisation/english/mod_l_english.yml": 'l_english:\n swe.1.t:0 "Alpha"\n swe.1.a:0 "One"\n'
                                              ' swe.1.b:0 "Two"\n swe.1.c:0 "Three"\n',
}


def write_game(root: Path, installed: dict[str, str] | None = None) -> Path:
    game = root / "game"
    for rel, text in FILES.items():
        path = game / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for folder, name in (installed or {}).items():
        d = game / "dlc" / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{folder[:6]}.dlc").write_text(
            f'name = "{name}"\npath = "dlc/{folder}"\npops_id = "hoi4_{folder}"\n', encoding="utf-8")
    return game


def build(tmp_path: Path, active) -> IdentifierIndex:
    game = write_game(tmp_path)
    picture = dlc.explicit(active) if active is not None else dlc.DlcSet()
    return IdentifierIndex.build(Playset.from_paths(game, dlc=picture))


ALL = [AAT, NSB, MTG, BBA, "Poland: United and Ready"]


# --- detection -----------------------------------------------------------------------------------------


INSTALLED = {"dlc028_la_resistance": "La Resistance", "dlc034_no_step_back": "No Step Back",
             "dlc036_by_blood_alone": "By Blood Alone", "dlc038_arms_against_tyranny": AAT,
             "dlc023_man_the_guns": "unused"}


def test_installed_dlcs_are_read_from_the_dlc_folder(tmp_path):
    game = write_game(tmp_path, INSTALLED)
    found = {i.name: i for i in dlc.read_installed(game)}
    assert set(found) == {"La Resistance", NSB, BBA, AAT, "unused"}
    assert found[NSB].folder == "dlc034_no_step_back" and found[NSB].pops_id == "hoi4_dlc034_no_step_back"


def test_a_folder_without_a_dlc_file_is_not_installed(tmp_path):
    game = write_game(tmp_path, INSTALLED)
    (game / "dlc" / "dlc014_wallpaper").mkdir()                 # a wallpaper pack: a folder of pngs
    (game / "dlc" / "dlc014_wallpaper" / "a.png").write_bytes(b"x")
    assert "dlc014_wallpaper" not in {i.folder for i in dlc.read_installed(game)}


def test_disabled_dlcs_are_taken_off_in_every_spelling_the_launcher_may_use(tmp_path):
    game = write_game(tmp_path, INSTALLED)
    user = tmp_path / "user"
    user.mkdir()
    for entry in ("dlc/dlc034_no_step_back/dlc034.dlc", "dlc034_no_step_back", "hoi4_dlc034_no_step_back",
                  {"path": "dlc\\dlc034_no_step_back\\dlc034.dlc"}):
        (user / "dlc_load.json").write_text(json.dumps({"enabled_mods": [], "disabled_dlcs": [entry]}))
        found = dlc.detect(game, user)
        assert found.source == "installed"
        assert found.disabled == (NSB,)
        assert NSB not in found.active and AAT in found.active and "La Resistance" in found.active


def test_nothing_disabled_means_everything_installed_is_active(tmp_path):
    game = write_game(tmp_path, INSTALLED)
    user = tmp_path / "user"
    user.mkdir()
    (user / "dlc_load.json").write_text('{"enabled_mods": [], "disabled_dlcs": []}')
    found = dlc.detect(game, user)
    assert found.active == frozenset({"La Resistance", NSB, BBA, AAT, "unused"})
    assert dlc.detect(game, None).active == found.active           # no dlc_load.json at all


def test_a_broken_dlc_load_json_is_reported_and_disables_nothing(tmp_path):
    game = write_game(tmp_path, INSTALLED)
    user = tmp_path / "user"
    user.mkdir()
    (user / "dlc_load.json").write_text("{not json")
    found = dlc.detect(game, user)
    assert len(found.active) == 5 and "could not be read" in found.notes[0]


def test_no_dlc_folder_means_unknown_so_nothing_is_filtered(tmp_path):
    game = write_game(tmp_path)
    found = dlc.detect(game, None)
    assert found.active is None and found.source == "unknown" and not found.known


def test_an_explicit_list_wins_over_the_install(tmp_path):
    game = write_game(tmp_path, INSTALLED)
    assert dlc.detect(game, None, [AAT]).active == frozenset({AAT})
    assert dlc.detect(game, None, []).active == frozenset()          # --no-dlc
    odd = dlc.explicit(["by blood alone", "My Own Pack"])
    assert odd.active == frozenset({BBA, "My Own Pack"}) and "My Own Pack" in odd.notes[0]


def test_config_reads_the_dlc_set_and_hands_it_to_the_index(tmp_path):
    game = write_game(tmp_path, INSTALLED)
    user = tmp_path / "user"
    user.mkdir()
    (user / "dlc_load.json").write_text(json.dumps({"disabled_dlcs": ["dlc/dlc038_arms_against_tyranny/x.dlc",
                                                                       "dlc038_arms_against_tyranny"]}))
    config = Config(game_dir=game, user_dir=user)
    index = config.build_index()
    assert AAT not in index.playset.dlcs and NSB in index.playset.dlcs
    config.dlcs = []
    assert config.build_index().playset.dlcs == frozenset()
    config.dlcs = None
    assert [t.id for t in config.build_index().trees_for("SWE")] == ["generic_focus"]


def test_the_cli_flags_set_the_list(tmp_path):
    from hoi4_harness.cli import _config_from_args, build_parser

    parser = build_parser()
    args = parser.parse_args(["dlc", "--dlc", AAT, "--dlc", NSB])
    assert _config_from_args(args).dlcs == [AAT, NSB]
    assert _config_from_args(parser.parse_args(["dlc", "--no-dlc"])).dlcs == []
    assert _config_from_args(parser.parse_args(["dlc"])).dlcs is None


def test_the_dlc_command_prints_installed_disabled_active_and_features(tmp_path, capsys):
    game = write_game(tmp_path, INSTALLED)
    user = tmp_path / "user"
    user.mkdir()
    (user / "dlc_load.json").write_text(json.dumps({"disabled_dlcs": ["dlc034_no_step_back"]}))
    assert main(["dlc", "--game-dir", str(game), "--user-dir", str(user)]) == 0
    out = capsys.readouterr().out
    assert "installed        5" in out and "disabled         1: No Step Back" in out
    assert "active           4" in out
    assert "off      Tank designer" in out and "ON       Plane designer" in out
    assert main(["dlc", "--game-dir", str(game), "--dlc", MTG]) == 0
    out = capsys.readouterr().out
    assert "source           explicit" in out and "ON       Ship designer" in out


# --- the evaluator ---------------------------------------------------------------------------------------


def truth(text: str, dlcs, tag: str = "SWE"):
    return S.tag_truth(S.parse(text), tag, dlcs)


def only_dlc(text: str, dlcs):
    return S.dlc_truth(S.parse(text), dlcs)


def test_has_dlc_is_true_false_or_unknown_by_the_active_set():
    assert only_dlc('has_dlc = "A"', {"A"}) is True
    assert only_dlc('has_dlc = "A"', set()) is False
    assert only_dlc('has_dlc = "A"', None) is None                    # unknown set: unknown
    assert only_dlc("has_war = yes", set()) is None


def test_has_dlc_under_not_or_and():
    assert only_dlc('NOT = { has_dlc = "A" }', {"A"}) is False
    assert only_dlc('NOT = { has_dlc = "A" }', set()) is True
    assert only_dlc('OR = { has_dlc = "A" has_dlc = "B" }', {"B"}) is True
    assert only_dlc('OR = { has_dlc = "A" has_dlc = "B" }', set()) is False
    assert only_dlc('OR = { has_dlc = "A" has_war = yes }', set()) is None     # the war could decide
    assert only_dlc('AND = { has_dlc = "A" has_dlc = "B" }', {"A"}) is False
    assert only_dlc('AND = { has_dlc = "A" has_war = yes }', set()) is False   # one false is enough
    assert only_dlc('AND = { has_dlc = "A" has_war = yes }', {"A"}) is None
    assert only_dlc('NOT = { has_dlc = "A" has_dlc = "B" }', {"B"}) is False   # NOT of several is a NOR
    assert only_dlc('NOT = { has_dlc = "A" has_dlc = "B" }', set()) is True


def test_a_tag_test_still_decides_and_a_live_dlc_says_nothing_about_the_country():
    assert truth('tag = SWE has_dlc = "A"', {"A"}) is True
    assert truth('tag = SWE has_dlc = "A"', set()) is False           # the DLC rules it out
    assert truth('tag = FIN has_dlc = "A"', {"A"}) is False
    assert truth('has_dlc = "A"', {"A"}) is None                      # no tag named: not "this country's"
    assert truth('OR = { tag = FIN has_dlc = "A" }', {"A"}) is None
    assert truth('OR = { tag = FIN has_dlc = "A" }', set()) is False
    assert truth('NOT = { has_dlc = "A" }', set()) is None
    assert truth('tag = SWE has_dlc = "A"', None) is None             # legacy: unevaluated


def test_the_condition_evaluator_counts_a_block_of_only_dlc_tests():
    assert S.holds(S.parse('has_dlc = "A"'), "SWE", {"A"}) is True
    assert S.holds(S.parse('tag = SWE has_dlc = "A"'), "SWE", set()) is False
    assert S.holds([], "SWE", set()) is True
    assert S.holds(S.parse('has_dlc = "A"'), "SWE", None) is None
    assert S.dlc_names(S.parse('OR = { has_dlc = "A" NOT = { has_dlc = "B" } } has_dlc = "A"')) == ["A", "B"]


# --- the matrix: all DLC, no DLC, mixed -------------------------------------------------------------------


def trees(index, tag):
    return [t.id for t in index.trees_for(tag)]


def test_a_country_gets_the_dlc_tree_only_with_the_dlc(tmp_path):
    every = build(tmp_path / "a", ALL)
    none = build(tmp_path / "n", [])
    mixed = build(tmp_path / "m", [NSB])
    unknown = build(tmp_path / "u", None)
    assert trees(every, "SWE") == ["sweden_focus"] and trees(none, "SWE") == ["generic_focus"]
    assert trees(mixed, "SWE") == ["generic_focus"]                  # NSB is not Arms Against Tyranny
    assert trees(build(tmp_path / "m2", [AAT]), "SWE") == ["sweden_focus"]
    assert trees(unknown, "SWE") == ["sweden_focus"]                 # unknown: as before this module
    # An untied tree is the same whatever is on.
    assert trees(every, "GER") == trees(none, "GER") == ["german_focus"]
    # An OR of two DLCs: either one will do.
    assert trees(none, "POL") == ["generic_focus"]
    assert trees(mixed, "POL") == ["polish_focus"]
    assert trees(build(tmp_path / "m3", ["Poland: United and Ready"]), "POL") == ["polish_focus"]


def test_focuses_a_dlc_hides_are_not_offered_and_their_children_follow(tmp_path):
    every = build(tmp_path / "a", ALL)
    aat_only = build(tmp_path / "b", [AAT])
    assert set(every.focuses_for("SWE")) == {"SWE_root", "SWE_nsb_branch", "SWE_nsb_child",
                                             "SWE_nsb_gated", "SWE_plain"}
    assert set(aat_only.focuses_for("SWE")) == {"SWE_root", "SWE_nsb_gated", "SWE_plain"}
    assert aat_only.hidden_focuses() == {"SWE_nsb_branch": [NSB], "SWE_nsb_child": [NSB]}
    # allow_branch hides; an `available` test only keeps it from being started now.
    assert sorted(aat_only.available_focuses("SWE", ["SWE_root"])) == ["SWE_plain"]
    assert sorted(every.available_focuses("SWE", ["SWE_root"])) == ["SWE_nsb_branch", "SWE_nsb_gated",
                                                                    "SWE_plain"]


def test_a_hidden_focus_is_refused_in_the_dlcs_name(tmp_path):
    aat_only = build(tmp_path / "b", [AAT])
    for focus in ("SWE_nsb_branch", "SWE_nsb_child", "SWE_nsb_gated"):
        result = aat_only.check(ActionCall("set_national_focus", {"focus_id": focus}), "SWE")
        assert result is not None and not result.ok
        assert result.message == f"Focus '{focus}' needs the {NSB} DLC, which is not active."
    assert aat_only.check(ActionCall("set_national_focus", {"focus_id": "SWE_plain"}), "SWE") is None
    every = build(tmp_path / "a", ALL)
    assert every.check(ActionCall("set_national_focus", {"focus_id": "SWE_nsb_branch"}), "SWE") is None


def test_decisions_follow_the_dlc_in_allowed_available_and_the_category(tmp_path):
    def offered(active):
        ctx = build(tmp_path / ("d" + "".join(sorted(a[:2] for a in active))), active).mod_context()
        return {d.id for d in ctx.decision_for("SWE")}

    assert offered(ALL) == {"swe_always", "swe_nsb_only", "swe_nsb_available", "swe_in_aat_category"}
    assert offered([AAT]) == {"swe_always", "swe_not_nsb", "swe_in_aat_category"}
    assert offered([]) == {"swe_always", "swe_not_nsb"}
    assert offered([NSB]) == {"swe_always", "swe_nsb_only", "swe_nsb_available"}


def test_a_decision_the_dlc_rules_out_is_refused_by_name(tmp_path):
    ctx = build(tmp_path / "x", [AAT]).mod_context()
    result = ctx.check_take(ActionCall("take_decision", {"decision_id": "swe_nsb_only"}), "SWE")
    assert result.message == f"Decision 'swe_nsb_only' needs the {NSB} DLC, which is not active."
    assert ctx.check_take(ActionCall("take_decision", {"decision_id": "swe_always"}), "SWE") is None
    listed = {d["id"] for d in ctx.list_decisions("SWE")["decisions"]}
    assert "swe_nsb_only" not in listed and "swe_always" in listed


def test_laws_and_spirits_follow_the_dlc(tmp_path):
    def surface(active):
        return build(tmp_path / ("s" + "".join(sorted(a[:2] for a in active))), active).mod_context() \
            .surface("SWE")

    full, bare = surface(ALL), surface([])
    assert {s["id"] for s in full.spirits} == {"SWE_spirit", "SWE_nsb_spirit"}
    assert {s["id"] for s in bare.spirits} == {"SWE_spirit"}
    assert {o["id"] for o in full.laws[0]["options"]} == {"civilian_economy", "war_economy", "total_economy"}
    assert {o["id"] for o in bare.laws[0]["options"]} == {"civilian_economy", "total_economy"}


def test_a_law_that_needs_a_dlc_is_refused_in_its_name(tmp_path):
    ctx = build(tmp_path / "x", [AAT]).mod_context()
    plan = ctx.plan_law("war_economy", "SWE", console=False)
    assert not plan.executable and plan.reason == f"law war_economy needs the {NSB} DLC, which is not active"
    ok = build(tmp_path / "y", ALL).mod_context().plan_law("war_economy", "SWE", console=False)
    assert ok.executable
    assert ctx.plan_law("total_economy", "SWE", console=False).executable


def test_custom_windows_follow_the_dlc_and_their_buttons_are_refused(tmp_path):
    full = build(tmp_path / "a", ALL).mod_context()
    bare = build(tmp_path / "b", []).mod_context()
    assert {g.id for g in full.guis_for("SWE")} == {"SWE_plain_window", "SWE_nsb_window"}
    assert {g.id for g in bare.guis_for("SWE")} == {"SWE_plain_window"}
    click = ActionCall("click_gui_button", {"gui": "SWE_nsb_window", "button": "swe_b"})
    result = bare.check_click(click, "SWE")
    assert result.message == f"Window 'SWE_nsb_window' needs the {NSB} DLC, which is not active."
    assert full.check_click(click, "SWE") is None


def test_events_and_their_options_follow_the_dlc(tmp_path):
    from hoi4_harness.modcontext.events import EventCatalog

    def catalog(active, sub):
        return EventCatalog(build(tmp_path / sub, active).mod_context())

    every = catalog(ALL, "a").for_country("SWE")
    bare = catalog([], "b").for_country("SWE")
    assert [o.shown for o in every.get("swe.1").options] == [None, None, False]
    assert [o.shown for o in bare.get("swe.1").options] == [None, False, None]
    assert every.get("swe.2").needs == () and bare.get("swe.2").needs == (BBA,)
    said = bare.get("swe.2").describe()["unavailable"]
    assert said == f"this event needs the {BBA} DLC, which is not active"
    # With no country the DLC alone can still rule an option out.
    assert [o.shown for o in catalog([], "c").get("swe.1").options] == [None, False, None]
    # An event that cannot fire is never what a window is matched to.
    assert "swe.2" not in {i for ids in bare._inverted().values() for i in ids}


def test_balance_of_power_bars_follow_their_dlc(tmp_path):
    from hoi4_harness.types import PowerBalance

    readings = [PowerBalance(id="SWE_riksdag_political_balance", value=10, weekly=0),
                PowerBalance(id="ITA_power_balance", value=20, weekly=0)]
    full = build(tmp_path / "a", ALL).mod_context()
    aat = build(tmp_path / "b", [AAT]).mod_context()
    assert len(full.balance_lines(readings)) == 2
    assert len(aat.balance_lines(readings)) == 1                      # Italy's bar is By Blood Alone's
    assert aat.balance_needs("ITA_power_balance") == BBA
    assert aat.balance_needs("SWE_riksdag_political_balance") is None
    assert build(tmp_path / "u", None).mod_context().balance_needs("ITA_power_balance") is None


# --- feature gating -----------------------------------------------------------------------------------------


def test_whole_features_are_refused_in_the_dlcs_name():
    assert dlc.feature_refusal("tank_designer", frozenset({AAT})) == \
        f"Tank designer (modules, variants) needs the {NSB} DLC, which is not active"
    assert dlc.feature_refusal("tank_designer", frozenset({NSB})) == ""
    assert dlc.feature_refusal("ship_designer", None) == ""            # unknown: not refused
    rows = {r["id"]: r["on"] for r in dlc.feature_table(frozenset({NSB, BBA}))}
    assert rows == {"tank_designer": True, "ship_designer": False, "plane_designer": True}
    assert all(f.evidence for f in dlc.FEATURES)


def test_special_projects_exist_for_a_country_only_with_a_project_it_may_have(tmp_path):
    ctx = build(tmp_path / "a", []).mod_context()
    assert ctx.item_feature("special_projects", "SWE")["available"] == 1       # sp_base
    assert ctx.feature_gap("special_projects", "SWE") == ""
    (tmp_path / "g" / "game").mkdir(parents=True)
    # A playset whose only project needs By Blood Alone has no special projects without it.
    only = tmp_path / "only"
    game = write_game(only)
    (game / "common/special_projects/projects/air.txt").write_text(
        'sp_bba = { specialization = specialization_air allowed = { has_dlc = "By Blood Alone" } }')
    ctx = IdentifierIndex.build(Playset.from_paths(game, dlc=dlc.explicit([AAT]))).mod_context()
    assert ctx.feature_gap("special_projects", "SWE") == \
        f"Special projects needs the {BBA} DLC, which is not active"
    on = IdentifierIndex.build(Playset.from_paths(game, dlc=dlc.explicit([BBA]))).mod_context()
    assert on.feature_gap("special_projects", "SWE") == ""
    unknown = IdentifierIndex.build(Playset.from_paths(game)).mod_context()
    assert unknown.feature_gap("special_projects", "SWE") == ""


def test_the_projects_view_says_the_feature_does_not_exist(tmp_path):
    from hoi4_harness import world

    game = write_game(tmp_path)
    (game / "common/special_projects/projects/air.txt").write_text(
        'sp_bba = { specialization = specialization_air allowed = { has_dlc = "By Blood Alone" } }')
    off = IdentifierIndex.build(Playset.from_paths(game, dlc=dlc.explicit([AAT])))

    class Pic:
        me = "SWE"

    data = world.query(Pic(), "projects", None, off)
    assert data["exists"] is False
    assert data["note"].startswith(f"Special projects needs the {BBA} DLC, which is not active")


# --- what the model is told ---------------------------------------------------------------------


def test_the_brief_counts_the_active_dlcs_and_says_they_filter(tmp_path):
    from hoi4_harness.types import GameState

    line = dlc.brief_line(frozenset({AAT, NSB, "Anniversary Pack"}))
    assert line.startswith("DLC: 3 active (2 of the 17 that add content)")
    assert "filtered out" in line and "\n" not in line
    assert dlc.brief_line(None) == ""
    index = build(tmp_path, [AAT, NSB])
    assert index.brief_lines(GameState(country="SWE"))[0].startswith("DLC: 2 active")
    assert not any(x.startswith("DLC:") for x in
                   build(tmp_path / "u", None).brief_lines(GameState(country="SWE")))


def test_the_prompt_section_names_the_active_dlcs_and_the_features_that_are_off(tmp_path):
    from hoi4_harness.modcontext import playset_section

    text = playset_section(build(tmp_path, [AAT, NSB]), "SWE")
    assert f"Active DLC that add content: {NSB}, {AAT}." in text.replace(
        "Arms Against Tyranny, No Step Back", "No Step Back, Arms Against Tyranny")
    assert "Not in this game: Ship designer (modules, variants) (needs Man the Guns)" in text
    assert "Tank designer" not in text
    assert "Active DLC" not in playset_section(build(tmp_path / "u", None), "SWE")


def test_the_games_own_active_list_beats_the_folder_scan(tmp_path):
    """A DLC can be owned with an empty folder, or have a folder and not be active: the game's
    system.log names what it actually activated at its last start."""
    from hoi4_harness import dlc as D

    user = tmp_path / "user"
    (user / "logs").mkdir(parents=True)
    (user / "logs" / "system.log").write_text(
        "[14:53:04][no_game_date]: Active DLC Count: 1\n"
        "[14:53:04][no_game_date]: Active DLC: Old One\n"
        "[18:00:00][no_game_date]: Active DLC Count: 2\n"
        "[18:00:00][no_game_date]: Active DLC: Man the Guns\n"
        "[18:00:00][no_game_date]: Active DLC: La Resistance\n"
        "[18:00:01][no_game_date]: something else\n", encoding="utf-8")
    found = D.detect(None, user)
    assert found.source == "game log" and found.active == {"Man the Guns", "La Resistance"}
    assert D.detect(None, user, override=[]).active == frozenset()     # explicit still wins
