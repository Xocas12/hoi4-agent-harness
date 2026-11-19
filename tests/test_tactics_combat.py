"""The combat round, checked against numbers worked out by hand."""

import pytest

from hoi4_harness.tactics.combat import expected_hits, hit_chance, shoot, stochastic_round
from hoi4_harness.tactics.model import load_constants
from tactics_helpers import StubRng, tiny_world

C = load_constants()
INF_PAIR = [
    {"side": "A", "template": "inf_9", "province": "p0"},
    {"side": "B", "template": "inf_9", "province": "p1"},
]


def pair(att, dfn, terrain="plains"):
    w = tiny_world(
        ["plains", terrain], ["A", "B"],
        [{"side": "A", "template": att, "province": "p0"}, {"side": "B", "template": dfn, "province": "p1"}],
    )
    return w, w.divisions["A1"], w.divisions["B1"]


def test_templates_are_summed_from_the_battalion_table():
    inf = C.templates["inf_9"]
    assert (inf.width, inf.hp, inf.org) == (18, 225, 60)
    assert (inf.soft_attack, inf.defense, inf.breakthrough) == (54, 198, 27)
    tank = C.templates["armor_6"]
    assert tank.width == 12 and tank.armor == 30 and tank.kind == "armor"


def test_terrain_widths_match_the_game_files():
    assert {k: v["width"] for k, v in C.terrain.items()} == {
        "plains": 70, "forest": 60, "hills": 70, "mountain": 50,
        "urban": 80, "marsh": 50, "jungle": 60, "desert": 70,
    }


def test_hit_chance_by_hand():
    # attack 54 against defense 198 with breakthrough 27: defense falls to 171, p = 2 * 54 / (54 + 171)
    assert hit_chance(54, 198, 27, C) == pytest.approx(0.48)
    # breakthrough can only remove three quarters of the defense
    assert hit_chance(54, 198, 900, C) == 1.0  # 2 * 54 / (54 + 49.5) is above 1, so capped
    assert hit_chance(20, 198, 900, C) == pytest.approx(2 * 20 / (20 + 0.25 * 198))
    assert hit_chance(0, 100, 0, C) == 0.0
    assert hit_chance(500, 1, 0, C) == 1.0  # capped
    assert expected_hits(54, 198, 27, C) == pytest.approx(54 * C.combat("hit_rate") * 0.48)


def test_stochastic_round_is_unbiased_at_the_ends():
    class R:
        def __init__(self, v):
            self.v = v

        def random(self):
            return self.v

    assert stochastic_round(3.0, R(0.0)) == 3
    assert stochastic_round(3.2, R(0.5)) == 3
    assert stochastic_round(3.2, R(0.1)) == 4


def test_infantry_on_infantry_damage_with_top_dice():
    _, a, b = pair("inf_9", "inf_9")
    dmg = shoot([a], [b], {}, {}, True, StubRng(), C)
    attempts = int(54 * C.combat("hit_rate")) + 1  # fractional part rounds up on a zero roll
    assert dmg.hits == attempts
    assert dmg.strength["B1"] == pytest.approx(attempts * 2 * 0.060)
    assert dmg.org["B1"] == pytest.approx(attempts * 4 * 0.053)


def test_armor_outclassing_piercing_adds_dice():
    _, tank, inf = pair("armor_6", "inf_9")
    dmg = shoot([tank], [inf], {}, {}, True, StubRng(), C)
    attempts = int(48 * C.combat("hit_rate")) + 1  # soft attack 6 * 8 against unarmoured infantry
    assert dmg.hits == attempts
    # armor 30 beats the infantry's piercing of 4: dice grow from 2/4 to 4/10
    assert dmg.strength["B1"] == pytest.approx(attempts * 4 * 0.060)
    assert dmg.org["B1"] == pytest.approx(attempts * 10 * 0.053)


def test_target_armor_deflects_half_the_damage():
    _, inf, tank = pair("inf_9", "armor_6")
    dmg = shoot([inf], [tank], {}, {}, True, StubRng(), C)
    # hardness .85 blends soft 54 and hard 9 into 54 * .15 + 9 * .85 = 15.75 attack
    attempts = int(15.75 * C.combat("hit_rate")) + 1
    assert dmg.hits == attempts
    assert dmg.strength["B1"] == pytest.approx(attempts * 2 * 0.060 * 0.5)
    assert dmg.org["B1"] == pytest.approx(attempts * 4 * 0.053 * 0.5)


def test_attack_is_scaled_by_remaining_strength():
    _, a, b = pair("inf_9", "inf_9")
    full = shoot([a], [b], {}, {}, True, StubRng(), C).hits
    a.strength = a.template.hp / 2
    half = shoot([a], [b], {}, {}, True, StubRng(), C).hits
    assert half < full


def test_modifiers_terrain_river_and_directions():
    w = tiny_world(
        ["plains", "urban"], ["A", "B"],
        INF_PAIR,
        rivers={0: 1},
    )
    a, b = w.divisions["A1"], w.divisions["B1"]
    a_mod, d_mod = w._modifiers([a], [b], "p1", {"A1": "p0"}, 80, 80)
    # attacking into a town (-0.3) across a small river (-0.3)
    assert a_mod["A1"] == pytest.approx(-0.6)
    assert d_mod["A1"] == pytest.approx(0.0)
    assert a_mod["B1"] == pytest.approx(0.0)
    # a second attack direction costs the defender half its defense
    _, d_two = w._modifiers([a], [b], "p1", {"A1": "p0", "X": "p9"}, 80, 80)
    assert d_two["B1"] == pytest.approx(-0.5)


def test_out_of_supply_and_encirclement_penalties():
    w = tiny_world(
        ["plains", "plains"], ["A", "B"],
        INF_PAIR,
    )
    a, b = w.divisions["A1"], w.divisions["B1"]
    a.supply = 0.0
    a.encircled = True
    a_mod, d_mod = w._modifiers([a], [b], "p1", {"A1": "p0"}, 70, 70)
    assert a_mod["A1"] == pytest.approx(-0.25 + -0.3)  # attacker out of supply, encircled
    assert d_mod["A1"] == pytest.approx(-0.65 + -0.3)


def test_over_width_and_stacking_penalties():
    divs = [{"side": "A", "template": "inf_9", "province": "p0", "count": 7},
            {"side": "B", "template": "inf_9", "province": "p1"}]
    w = tiny_world(["plains", "plains"], ["A", "B"], divs)
    att = [w.divisions[f"A{i}"] for i in range(1, 8)]
    a_mod, _ = w._modifiers(att, [w.divisions["B1"]], "p1", {d.id: "p0" for d in att}, 70, 70)
    # 7 x 18 = 126 wide against 70 is 80% over, clamped at -0.33;
    # seven divisions is two past the stacking start (-0.04)
    assert a_mod["A1"] == pytest.approx(-0.33 - 0.04)
