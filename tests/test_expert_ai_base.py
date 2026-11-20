"""Expert AI as the base AI (docs/expert-ai.md): detection, the preflight check, and the strong
directive weights the campaign turns on so its own plans (500-5000) do not drown the model's
directives (150). Fake game and temporary folders only; nothing touches the real game."""

from __future__ import annotations

import json
import re
from pathlib import Path

from campaign_helpers import LazyClient, build_env, campaign_config
from hoi4_harness import baseai, modgen
from hoi4_harness.adapters.logtail import EVENT_CODES
from hoi4_harness.campaign.preflight import FAIL, INFO, PASS, WARN, check_base_ai
from hoi4_harness.campaign.runner import load_state, run_campaign
from hoi4_harness.config import HarnessConfig

MOD = Path(__file__).resolve().parents[1] / "mod" / "llm_bridge"
EAI_ENTRY = "mod/ugc_741805475.mod"


def user_dir(tmp_path: Path, *entries: str, eai_version: str = "1.19.2.0") -> Path:
    """A game user folder with a dlc_load.json and the .mod descriptors it names."""
    root = tmp_path / "user"
    (root / "mod").mkdir(parents=True, exist_ok=True)
    (root / "save games").mkdir(exist_ok=True)
    (root / "dlc_load.json").write_text(json.dumps(
        {"enabled_mods": [*entries, "mod/llm_bridge.mod"], "disabled_dlcs": []}), encoding="utf-8")
    names = {"741805475": ("Expert AI 5.0", eai_version), "3369706283": ("Better AI | 1.15",
                                                                         "1.19.2.0")}
    for entry in entries:
        wid = re.search(r"ugc_(\d+)", entry).group(1)
        name, version = names.get(wid, (f"mod {wid}", "1.20.*"))
        (root / entry).write_text(f'version="1"\nname="{name}"\nsupported_version="{version}"\n'
                                  f'remote_file_id="{wid}"\n', encoding="utf-8")
    return root


def game_dir(tmp_path: Path, version: str = "1.20.0.0") -> Path:
    root = tmp_path / "game"
    root.mkdir(exist_ok=True)
    (root / "launcher-settings.json").write_text(json.dumps({"rawVersion": version}),
                                                 encoding="utf-8")
    return root


# --- the mod: a strong twin for every directive -------------------------------------------


def test_every_directive_and_theater_posture_has_a_strong_twin_on_both_flags():
    text = (MOD / "common/ai_strategy/llm_bridge_targets.txt").read_text(encoding="utf-8")
    blocks = dict(re.findall(r"^(llmb_\w+) = \{\n(.*?)^\}", text, re.M | re.S))
    plain = [name for name in blocks if not name.endswith("_strong")]
    assert plain and all(f"{name}_strong" in blocks for name in plain)
    strong = blocks["llmb_invade_POL_strong"]
    assert "has_country_flag = llmb_invade_POL has_global_flag = llmb_strong_directives" in strong
    # NOT = { a b } is a NOR in the game's script, so the abort must be an OR of two NOTs.
    assert ("OR = { NOT = { has_country_flag = llmb_invade_POL } "
            "NOT = { has_global_flag = llmb_strong_directives } }") in strong
    assert f'type = conquer id = "POL" value = {modgen.STRONG_BOOST}' in strong
    assert f'type = invade id = "POL" value = {modgen.STRONG_BOOST}' in strong
    posture = blocks["llmb_posture_offensive_POL_strong"]
    assert f"value = {modgen.STRONG_POSTURE_BOOST}" in posture


def test_the_strong_weight_beats_the_biggest_expert_ai_country_plan_weight():
    # Expert AI's own GER plan weighs antagonize POL at 5000; a directive that cannot outweigh
    # that is not a directive (docs/expert-ai.md).
    assert modgen.STRONG_BOOST + 150 > 5000


def test_the_global_postures_have_strong_twins_and_the_flag_effects_log_events():
    text = (MOD / "common/ai_strategy/llm_bridge_directives.txt").read_text(encoding="utf-8")
    for name in ("llmb_defensive_posture_strong", "llmb_offensive_posture_strong"):
        assert f"{name} = {{" in text
    effects = (MOD / "common/scripted_effects/llm_bridge_directives.txt").read_text(
        encoding="utf-8")
    assert re.search(r"llm_bridge_strong_directives = \{\s*set_global_flag = "
                     r"llmb_strong_directives\s*set_variable = \{ llmb_event_kind = 18 \}", effects)
    assert re.search(r"llm_bridge_normal_directives = \{\s*clr_global_flag = "
                     r"llmb_strong_directives\s*set_variable = \{ llmb_event_kind = 19 \}", effects)
    assert EVENT_CODES["18"][0] == "strong_directives_on"
    assert EVENT_CODES["19"][0] == "strong_directives_off"


def test_the_generated_files_match_the_generator():
    assert modgen.drift(MOD) == []


# --- detection --------------------------------------------------------------------------


def test_auto_finds_expert_ai_in_the_playset_and_falls_back_to_vanilla(tmp_path):
    with_eai = user_dir(tmp_path / "a", EAI_ENTRY)
    assert baseai.detect(with_eai) == "expert_ai"
    assert baseai.resolve("auto", with_eai)[0] == "expert_ai"
    without = user_dir(tmp_path / "b", "mod/ugc_3369706283.mod")
    assert baseai.detect(without) == "vanilla"
    assert baseai.resolve("auto", tmp_path / "nowhere") == (
        "vanilla", "base_ai = auto and dlc_load.json could not be read: assuming vanilla")
    assert baseai.resolve("expert_ai", without)[0] == "expert_ai"      # the profile wins
    try:
        baseai.resolve("superai", without)
    except ValueError as exc:
        assert "base_ai must be one of" in str(exc)
    else:
        raise AssertionError("expected a ValueError")


def test_versions_compare_on_major_and_minor(tmp_path):
    assert not baseai.same_minor("1.19.2.0", "1.20.0.0")
    assert baseai.same_minor("1.20.1", "1.20.0.0")
    assert baseai.same_minor("1.20.*", "1.20.0.0")
    assert baseai.game_version(game_dir(tmp_path)) == "1.20.0.0"
    assert baseai.game_version(tmp_path / "missing") is None


# --- the preflight check -----------------------------------------------------------------


def levels(checks):
    return [(c.status, c.name) for c in checks]


def test_preflight_passes_expert_ai_and_warns_about_the_version_and_the_bonuses(tmp_path):
    config = HarnessConfig(game_dir=game_dir(tmp_path))
    checks = check_base_ai(config, user_dir(tmp_path, EAI_ENTRY))
    assert levels(checks) == [(PASS, "base AI"), (WARN, "base AI version"),
                              (INFO, "base AI bonuses")]
    assert "made for 1.19.2.0, the game is 1.20.0.0" in checks[1].detail


def test_preflight_fails_a_profile_that_wants_expert_ai_without_it(tmp_path):
    checks = check_base_ai(HarnessConfig(base_ai="expert_ai"), user_dir(tmp_path))
    assert levels(checks) == [(FAIL, "base AI")]


def test_preflight_warns_when_two_ai_mods_fight_and_when_vanilla_is_forced(tmp_path):
    root = user_dir(tmp_path, "mod/ugc_3369706283.mod", EAI_ENTRY)
    checks = check_base_ai(HarnessConfig(base_ai="vanilla"), root)
    assert (WARN, "base AI") == levels(checks)[0] and "more than one AI mod" in checks[0].detail
    assert any("drown" in c.detail for c in checks)


def test_preflight_with_vanilla_passes(tmp_path):
    assert levels(check_base_ai(HarnessConfig(), user_dir(tmp_path))) == [(PASS, "base AI")]


# --- the campaign start -------------------------------------------------------------------


def start(tmp_path, *entries, **options):
    root = user_dir(tmp_path, *entries)
    config = campaign_config(tmp_path, save_dir=root / "save games",
                             campaign_until="1936-01-20", **options)
    env = build_env(config)
    fake = env.adapter.fake_game
    run_campaign(config, planner=LazyClient(nap=30), env=env)
    log = (tmp_path / "run" / "campaign.log").read_text(encoding="utf-8")
    return fake, log, load_state(tmp_path / "run")


def test_under_expert_ai_the_campaign_turns_on_the_strong_weights(tmp_path):
    fake, log, state = start(tmp_path, EAI_ENTRY)
    assert "llmb_strong_directives" in fake.global_flags
    assert "base AI: expert_ai (base_ai = auto: Expert AI (Workshop 741805475)" in log
    assert "strong directive weights on" in log
    assert state["base_ai"] == "expert_ai"


def test_under_the_vanilla_ai_the_weights_stay_normal(tmp_path):
    fake, log, state = start(tmp_path)
    assert "llmb_strong_directives" not in fake.global_flags
    assert "base AI: vanilla" in log and "strong directive weights on" not in log
    assert state["base_ai"] == "vanilla"


def test_the_independent_mode_does_not_steer_the_ai_so_it_does_not_send_them(tmp_path):
    fake, log, _ = start(tmp_path, EAI_ENTRY, campaign_control="llm", campaign_input="none")
    assert "llmb_strong_directives" not in fake.global_flags
    assert "Expert AI runs only the countries where is_ai = yes" in log


def test_a_fake_game_without_a_save_folder_never_reads_the_machines_own_playset(tmp_path):
    from hoi4_harness.campaign.runner import user_dir_for

    assert user_dir_for(HarnessConfig(fake_game=True)) is None
