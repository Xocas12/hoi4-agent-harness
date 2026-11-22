"""The strategy sheet: a guidance pack that combines with others, and the Claude Code skill made from it.

The [files] numbers in the sheet are checked against the installed game when it is here, so a
patch that changes them fails this test instead of leaving the model with stale numbers.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from hoi4_harness import guidance
from hoi4_harness.agent.prompts import build_system

ROOT = Path(__file__).resolve().parents[1]
GAME = Path(os.environ.get("HOI4_GAME_DIR")
            or "C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV")


def test_packs_combine_in_order_with_a_plus():
    both = guidance.load("rival+strategy")
    assert guidance.load("rival") in both and guidance.load("strategy") in both
    assert both.index(guidance.load("rival")[:40]) < both.index("Hearts of Iron IV: how the game works")
    assert "Hearts of Iron IV: how the game works" in build_system(guidance="rival+strategy")


def test_the_committed_skill_is_the_sheet_behind_its_frontmatter():
    committed = (ROOT / guidance.SKILL_PATH).read_text(encoding="utf-8")
    assert committed == guidance.skill_text()
    assert committed.startswith("---\nname: hoi4-strategy\ndescription: ")


def test_the_sheet_has_a_general_part_for_any_playset_and_a_vanilla_part():
    sheet = guidance.load("strategy")
    general, vanilla = sheet.split("# Part 2: vanilla 1.20")
    for idea in ("Organization and strength", "Combat width", "Supply", "Production",
                 "Research", "Judging your country and choosing a plan", "Reading a mod"):
        assert idea in general, idea
    # the general part carries no vanilla numbers; those belong to Part 2
    assert "urban 80" not in general and "urban 80" in vanilla


def test_the_sheet_names_only_doctrines_the_game_has():
    sheet = guidance.load("strategy")
    for gone in ("Individual Excellence", "Desperate Defense"):
        assert gone not in sheet
    for grand in ("Mobile Warfare", "Superior Firepower", "Grand Battleplan", "Mass Assault"):
        assert grand in sheet


def _terrain_widths(game: Path) -> dict[str, int]:
    text = (game / "common" / "terrain" / "00_terrain.txt").read_text(encoding="utf-8-sig")
    cats = text[text.find("categories"):]
    out = {}
    for m in re.finditer(r"\n\t(\w+)\s*=\s*\{(.*?)\n\t\}", cats, re.S):
        width = re.search(r"\n\s*combat_width\s*=\s*(\d+)", m.group(2))
        if width:
            out[m.group(1)] = int(width.group(1))
    return out


def test_the_sheets_file_numbers_match_the_installed_game():
    if not (GAME / "common" / "terrain" / "00_terrain.txt").exists():
        pytest.skip("HOI4 is not installed here")
    sheet = guidance.load("strategy")
    widths = _terrain_widths(GAME)
    expected = {"plains": 70, "hills": 70, "desert": 70, "forest": 60, "jungle": 60,
                "mountain": 50, "marsh": 50, "urban": 80}
    assert {k: widths[k] for k in expected} == expected
    assert "Plains, hills and desert 70; forest and jungle 60; mountain and marsh 50; urban 80" in sheet
    economic = (GAME / "common" / "ideas" / "_economic.txt").read_text(encoding="utf-8-sig")
    for law, share in (("civilian_economy", "0.35"), ("partial_economic_mobilisation", "0.25"),
                       ("war_economy", "0.2"), ("tot_economic_mobilisation", "0.15")):
        start = economic.index(f"\t\t{law} = {{")
        following = re.compile(r"^\t\t\w+ = \{", re.M).search(economic, start + 5)
        block = economic[start:following.start() if following else None]
        assert re.search(r"consumer_goods_expected_value\s*=\s*" + re.escape(share) + r"\b",
                         block), law
    doctrines = (GAME / "common" / "doctrines" / "grand_doctrines" / "land_grand_doctrines.txt"
                 ).read_text(encoding="utf-8-sig")
    for ident in ("new_mobile_warfare", "superior_firepower", "grand_battleplan", "mass_assault"):
        assert re.search(rf"^{ident}\s*=", doctrines, re.M), ident


NEW_PART_ONE_SECTIONS = (
    "Navy", "Air", "Logistics in depth", "Intelligence", "Special projects, scientists and facilities",
    "Industrial organisations (MIOs)", "Trade and the market",
    "Occupation, resistance, compliance and collaboration", "Diplomacy actions",
    "Research and doctrines", "Leaders", "Division design", "Battle plans and the front AI",
    "Elections, coups, civil wars and ideology drift", "Victory and surrender",
)


def _part_one_sections() -> dict[str, str]:
    general = guidance.load("strategy").split("# Part 2: vanilla 1.20")[0]
    out = {}
    for m in re.finditer(r"^## (.+?)\r?\n(.*?)(?=^#{1,2} |\Z)", general, re.S | re.M):
        out[m.group(1).strip()] = m.group(2)
    return out


def test_part_one_covers_navy_air_logistics_and_the_rest():
    sections = _part_one_sections()
    for name in NEW_PART_ONE_SECTIONS:
        assert name in sections, name
        assert len(sections[name].split()) > 60, name


def test_the_new_part_one_sections_carry_no_vanilla_numbers():
    sections = _part_one_sections()
    for name in NEW_PART_ONE_SECTIONS:
        body = re.sub(r"\b1\.\d\d\b", "", sections[name])  # patch versions are not game numbers
        assert not re.search(r"\d", body), (name, re.search(r".{20}\d.{20}", body, re.S))
        assert "[files]" not in body, name


def _define(game: Path, name: str, section: str | None = None) -> str:
    text = (game / "common" / "defines" / "00_defines.lua").read_text(encoding="utf-8-sig")
    if section:
        start = text.index(f"\n{section} = {{")
        end = re.search(r"\n\},", text[start:])
        text = text[start:start + end.start()]
    m = re.search(rf"^\s*{name}\s*=\s*(-?[\d.]+)", text, re.M)
    assert m, name
    return m.group(1)


def test_the_sheets_new_file_numbers_match_the_installed_game():
    if not (GAME / "common" / "defines" / "00_defines.lua").exists():
        pytest.skip("HOI4 is not installed here")
    sheet = guidance.load("strategy")
    vanilla = sheet.split("# Part 2: vanilla 1.20")[1]
    expected = {
        ("DOMINANCE_CONTROLLED_THRESHOLD_RATIO", "NNavy"): "0.60",
        ("DOMINANCE_DAILY_GAIN_FACTOR", "NNavy"): "0.02",
        ("DOMINANCE_DAILY_LOSS_FACTOR", "NNavy"): "0.04",
        ("NAVAL_INVASION_PREPARE_DAYS", "NNavy"): "60",
        ("NAVAL_INVASION_PLAN_CAP", "NNavy"): "1",
        ("AIRBASE_CAPACITY_MULT", "NBuildings"): "200",
        ("ANTI_AIR_SUPERIORITY_MULT", "NBuildings"): "5.0",
        ("SUPPLY_GRACE", "NMilitary"): "72",
        ("MAX_OUT_OF_SUPPLY_DAYS", "NMilitary"): "30",
        ("SUPPLY_ORG_MAX_CAP", "NMilitary"): "0.35",
        ("RAILWAY_BASE_FLOW", "NSupply"): "10.0",
        ("SUPPLY_HUB_FULL_MOTORIZATION_TRUCK_COST", "NSupply"): "60.0",
        ("AGENCY_CREATION_DAYS", "NOperatives"): "30",
        ("AGENCY_CREATION_FACTORIES", "NOperatives"): "5",
        ("BASE_TECH_COST", "NTechnology"): "110",
        ("RESISTANCE_TARGET_BASE", "NResistance"): "35.0",
        ("BASE_SURRENDER_LIMIT", "NCountry"): "0.8",
        ("BASE_TRUCE_PERIOD", "NDiplomacy"): "180",
        ("FUNDS_FOR_SIZE_UP", "NIndustrialOrganisation"): "700",
        ("DEFAULT_REWARD_MASTERY", "NDoctrines"): "100.0",
        ("MAX_PROJECT_COUNT", "NFactions"): "3",
    }
    for (name, section), value in expected.items():
        assert _define(GAME, name, section) == value, name
        assert name in vanilla, name
    # the numbers the prose quotes, tied to the defines above
    for quoted in ("holds 200 planes", "Troops carry 72 hours", "after 30 days", "takes 60 days to prepare",
                   "capitulates at 80% occupation", "Base 110", "base resistance target is 35%",
                   "A base truce lasts 180 days", "needs 700 funds", "and 5 factories"):
        assert quoted in vanilla, quoted
    recruit = re.search(r"RECRUIT_SCIENTIST_COST\s*=\s*\{(.*?)\}", (GAME / "common" / "defines" /
                        "00_defines.lua").read_text(encoding="utf-8-sig"), re.S)
    assert [int(n) for n in re.findall(r"^\s*(\d+)", recruit.group(1), re.M)] == [25, 50, 75, 100]
    assert "25, 50, 75, then 100" in vanilla


def test_the_unattended_packs_teach_how_to_break_a_stalemate():
    for pack in ("world-conquest", "rival"):
        text = guidance.load(pack)
        assert "**Breaking a stalemate.**" in text, pack
        paragraph = text.split("**Breaking a stalemate.**")[1].split("\n")[0]
        for tool in ("set_front_order", "order=push", "order=reserve", "steer_army", "steer_production",
                     "view=fronts"):
            assert tool in paragraph, (pack, tool)
        assert "not to wait" in paragraph
