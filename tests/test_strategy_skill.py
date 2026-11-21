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
    assert both.index(guidance.load("rival")[:40]) < both.index("Hearts of Iron IV 1.20")
    assert "Hearts of Iron IV 1.20" in build_system(guidance="rival+strategy")


def test_the_committed_skill_is_the_sheet_behind_its_frontmatter():
    committed = (ROOT / guidance.SKILL_PATH).read_text(encoding="utf-8")
    assert committed == guidance.skill_text()
    assert committed.startswith("---\nname: hoi4-strategy\ndescription: ")


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
