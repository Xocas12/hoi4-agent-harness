"""Skill packs: SKILL.md folders found on the search paths and added to the prompt."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoi4_harness import skills as sk
from hoi4_harness.agent.prompts import build_system, system_for
from hoi4_harness.cli import main
from hoi4_harness.config import HarnessConfig
from hoi4_harness.identifiers import Mod

ROOT = Path(__file__).resolve().parents[1]


def write(folder: Path, name: str, extra: str = "", body: str = "Some advice.",
          description: str = "d") -> Path:
    path = folder / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n{extra}---\n\n{body}\n",
                    encoding="utf-8")
    return path


def mod(tmp_path: Path, name: str, remote: str | None = None, folder: str | None = None) -> Mod:
    path = tmp_path / "mods" / (folder or name.replace(" ", "_"))
    path.mkdir(parents=True, exist_ok=True)
    return Mod(path=path, name=name, remote_file_id=remote)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    monkeypatch.setenv("HOI4_HARNESS_HOME", str(h))
    return h


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    (r / ".claude" / "skills").mkdir(parents=True)
    monkeypatch.setattr(sk, "repo_root", lambda: r)
    return r


def names(skills):
    return [s.name for s in skills]


# --- parsing ------------------------------------------------------------------


def test_frontmatter_keys_lists_and_booleans(tmp_path):
    path = tmp_path / "x" / "SKILL.md"
    path.parent.mkdir()
    path.write_text(
        '---\nname: x\ndescription: first line\n  and a second\nmods: ["Mod, One", 123]\n'
        "tags:\n  - ger\n  - FRA\nalways: true\n---\n# Body\n", encoding="utf-8")
    skill = sk.parse_skill(path)
    assert skill.name == "x" and skill.description == "first line and a second"
    assert skill.mods == ["Mod, One", "123"] and skill.tags == ["GER", "FRA"]
    assert skill.always and skill.body == "# Body"


def test_no_frontmatter_is_all_body_and_name_is_the_folder(tmp_path):
    path = tmp_path / "plain" / "SKILL.md"
    path.parent.mkdir()
    path.write_text("just text\n", encoding="utf-8")
    skill = sk.parse_skill(path)
    assert skill.name == "plain" and skill.body == "just text" and not skill.has_harness_keys


# --- discovery -----------------------------------------------------------------


def test_discovery_in_every_search_path_and_first_wins(tmp_path, home, repo):
    cfg = tmp_path / "cfg"
    m = mod(tmp_path, "Some Mod")
    write(cfg, "from-config")
    write(cfg, "shared", body="config copy")
    write(home / "skills", "from-home")
    write(home / "skills", "shared", body="home copy")
    write(repo / ".claude" / "skills", "from-repo")
    write(m.path / "harness_skills", "from-mod")
    found = {s.name: s for s in sk.discover([cfg], [m], repo)}
    assert set(found) == {"from-config", "from-home", "from-repo", "from-mod", "shared"}
    assert found["from-config"].source == "config" and found["from-home"].source == "home"
    assert found["from-repo"].source == "repo" and found["from-mod"].source == "mod:Some Mod"
    assert found["shared"].body == "config copy"


def test_a_skills_dir_may_be_a_skill_itself_and_missing_dirs_are_skipped(tmp_path, home, repo):
    one = write(tmp_path / "t", "solo").parent
    assert names(sk.discover([one, tmp_path / "nope"], [], repo)) == ["solo"]


def test_home_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HOI4_HARNESS_HOME", str(tmp_path / "h"))
    assert sk.harness_home() == tmp_path / "h"


def test_the_shipped_strategy_skill_still_loads(home):
    found = {s.name: s for s in sk.discover([], [], ROOT)}
    strategy = found[sk.STRATEGY_SKILL]
    assert strategy.source == "repo" and not strategy.has_harness_keys
    assert "how the game works" in strategy.body and strategy.description.startswith("How Hearts")


# --- selection -----------------------------------------------------------------


@pytest.fixture
def pool(tmp_path, home, repo):
    d = tmp_path / "pool"
    write(d, "by-name", "mods: [Kaiserreich]\n")
    write(d, "by-id", "mods: [1521695605]\n")
    write(d, "by-tag", "tags: [ger]\n")
    write(d, "always-on", "always: true\n")
    write(d, "plain")
    return sk.discover([d], [], repo)


def picked(pool, **kw):
    return names(s for s, _ in sk.select(pool, **kw))


def test_by_mod_name_is_case_insensitive(pool, tmp_path):
    assert picked(pool, mods=[mod(tmp_path, "KAISERREICH")]) == ["always-on", "by-name"]


def test_by_workshop_id_or_the_workshop_folder_name(pool, tmp_path):
    assert "by-id" in picked(pool, mods=[mod(tmp_path, "Whatever", remote="1521695605")])
    assert "by-id" in picked(pool, mods=[mod(tmp_path, "Whatever", folder="1521695605")])
    assert "by-id" not in picked(pool, mods=[mod(tmp_path, "Whatever", remote="999")])


def test_by_tag(pool):
    assert picked(pool, tag="GER") == ["always-on", "by-tag"]
    assert picked(pool, tag="FRA") == ["always-on"]


def test_always_and_nothing_else_by_default(pool):
    assert picked(pool) == ["always-on"]


def test_named_skills_come_first_and_plain_skills_need_naming(pool):
    assert picked(pool, explicit=["plain"]) == ["plain", "always-on"]
    assert picked(pool, explicit=["PLAIN"]) == ["plain", "always-on"]


def test_an_unknown_name_is_an_error(pool):
    with pytest.raises(sk.SkillError, match="nope"):
        sk.select(pool, explicit=["nope"])


def test_order_is_deterministic(pool, tmp_path):
    kw = {"mods": [mod(tmp_path, "Kaiserreich")], "tag": "GER", "explicit": ["plain"]}
    expected = ["plain", "always-on", "by-name", "by-tag"]
    assert picked(pool, **kw) == expected
    assert picked(list(reversed(pool)), **kw) == expected


# --- budget --------------------------------------------------------------------


def test_budget_leaves_out_what_does_not_fit_and_says_so(tmp_path, home, repo):
    d = tmp_path / "pool"
    write(d, "a-small", "always: true\n", body="x" * 400)      # 100 tokens
    write(d, "b-big", "always: true\n", body="y" * 4000)       # 1000 tokens
    write(d, "c-small", "always: true\n", body="z" * 400)
    chosen = sk.select(sk.discover([d], [], repo))
    out = sk.fit(chosen, budget_tokens=300)
    assert names(out.included) == ["a-small", "c-small"] and names(out.left_out) == ["b-big"]
    text = out.prompt()
    assert "Included: a-small, c-small." in text and "Left out for space: b-big." in text
    assert "yyyy" not in text and "xxxx" in text
    assert "Left out" not in sk.fit(chosen, budget_tokens=5000).prompt()


def test_nothing_selected_adds_nothing():
    assert sk.fit([]).prompt() == ""


def test_strategy_skill_is_not_added_twice(tmp_path, home, repo):
    d = tmp_path / "pool"
    write(d, sk.STRATEGY_SKILL, "always: true\n")
    chosen = sk.select(sk.discover([d], [], repo))
    assert names(sk.fit(chosen, guidance="rival+strategy").included) == []
    assert names(sk.fit(chosen, guidance="rival").included) == [sk.STRATEGY_SKILL]


# --- scaffold ------------------------------------------------------------------


def test_scaffold_output_parses_and_has_the_sections(tmp_path):
    path = sk.scaffold("My Notes", tmp_path, mods=["Kaiserreich", "Mod: X"], tags=["ger"])
    assert path == tmp_path / "my-notes" / "SKILL.md"
    skill = sk.parse_skill(path)
    assert skill.name == "my-notes" and skill.mods == ["Kaiserreich", "Mod: X"] and skill.tags == ["GER"]
    assert skill.description
    for heading in ("changes", "Key mechanics and decisions", "Custom windows worth pressing",
                    "Balance of power bars", "What plays well", "What not to do"):
        assert heading in skill.body
    with pytest.raises(sk.SkillError):
        sk.scaffold("my notes", tmp_path)


def test_the_shipped_example_is_a_template_that_parses():
    skill = sk.parse_skill(ROOT / "examples" / "skills" / "example-mod-skill" / "SKILL.md")
    assert skill.name == "example-mod-skill" and skill.mods and skill.tags == ["XXX"]
    assert "<MOD NAME>" in skill.body and "fill-in-the-blanks" in skill.body


# --- prompt integration ----------------------------------------------------------


def test_system_for_includes_selected_skills_after_guidance(home, repo):
    write(home / "skills", "mine", "always: true\n", body="MY SPECIAL ADVICE")
    system = system_for(HarnessConfig(guidance="doctrine"))
    assert "MY SPECIAL ADVICE" in system and "Included: mine." in system
    assert system.startswith(build_system("doctrine"))


def test_system_for_by_tag_and_explicit_name(home, repo):
    write(home / "skills", "ger-notes", "tags: [GER]\n", body="GERMAN NOTES")
    write(home / "skills", "plain", body="PLAIN NOTES")
    assert "GERMAN NOTES" not in system_for(HarnessConfig(country="SWE"))
    system = system_for(HarnessConfig(country="GER", skills=["plain"]))
    assert "GERMAN NOTES" in system and "PLAIN NOTES" in system


def test_system_for_without_skills_is_unchanged(home, repo):
    assert system_for(HarnessConfig(guidance="doctrine")) == build_system("doctrine")


def test_system_prompt_replacement_skips_skills(tmp_path, home, repo):
    write(home / "skills", "mine", "always: true\n", body="MY SPECIAL ADVICE")
    own = tmp_path / "own.md"
    own.write_text("only this", encoding="utf-8")
    assert system_for(HarnessConfig(system_prompt_path=own)) == "only this"


def test_config_profile_keys(tmp_path):
    config = HarnessConfig().merge({"skills_dirs": [str(tmp_path)], "skills": ["a"], "skill_tokens": 50})
    assert config.skills_dirs == [tmp_path] and config.skills == ["a"] and config.skill_tokens == 50


# --- CLI -------------------------------------------------------------------------


def test_cli_new_list_show(tmp_path, home, repo, capsys):
    d = tmp_path / "mine"
    assert main(["skills", "new", "kr-notes", "--mod", "Kaiserreich", "--tag", "GER",
                 "--dir", str(d)]) == 0
    assert (d / "kr-notes" / "SKILL.md").is_file()
    capsys.readouterr()
    assert main(["skills", "list", "--skills-dir", str(d), "--country", "GER"]) == 0
    out = capsys.readouterr().out
    assert "kr-notes" in out and "INCLUDED (tag GER)" in out
    assert main(["skills", "list", "--skills-dir", str(d), "--country", "FRA"]) == 0
    assert "not applied" in capsys.readouterr().out
    assert main(["skills", "show", "kr-notes", "--skills-dir", str(d)]) == 0
    assert "What not to do" in capsys.readouterr().out
    assert main(["skills", "show", "missing", "--skills-dir", str(d)]) == 2


def test_cli_prompt_takes_skill_and_skills_dir(tmp_path, home, repo, capsys):
    write(tmp_path / "d", "style", body="PLAYSTYLE TEXT")
    assert main(["prompt", "--skills-dir", str(tmp_path / "d"), "--skill", "style"]) == 0
    assert "PLAYSTYLE TEXT" in capsys.readouterr().out
    assert main(["prompt", "--skill", "ghost"]) == 2


def test_cli_new_defaults_to_the_home_folder(home, capsys):
    assert main(["skills", "new", "fresh"]) == 0
    assert (home / "skills" / "fresh" / "SKILL.md").is_file()
