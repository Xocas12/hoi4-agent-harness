"""Movement, retreat, supply, encirclement and control on hand-made maps."""

import math

from hoi4_harness.tactics import Order
from tactics_helpers import tiny_world


def inf(side, province, **kw):
    return {"side": side, "template": "inf_9", "province": province, **kw}


def test_movement_hours_follow_speed_terrain_and_river():
    w = tiny_world(["plains", "forest", "mountain", "plains"], ["A"] * 4,
                   [inf("A", "p0"), {"side": "A", "template": "armor_6", "province": "p0"}], rivers={2: 1})
    a, t = w.divisions["A1"], w.divisions["A2"]
    assert w.hop_hours(a, "p0", "p1") == math.ceil(60 * 1.5 / 4)   # forest, speed 4
    assert w.hop_hours(t, "p1", "p2") == math.ceil(60 * 2.0 / 5)   # mountain, speed 5
    assert w.hop_hours(a, "p2", "p3") == math.ceil(60 * 1.0 / (4 * 0.75))  # small river
    assert w.hop_hours(a, "p1", "p0", retreat=True) == math.ceil(60 / (4 * 1.25))


def test_a_move_takes_that_long_and_flips_empty_enemy_ground():
    w = tiny_world(["plains", "plains", "plains"], ["A", "A", "B"], [inf("A", "p1")],
                   hubs={"p0": "A", "p2": "B"})
    w.apply_orders([Order("A1", "move", "p2")])
    for _ in range(14):
        w.tick()
    assert w.divisions["A1"].province == "p1" and w.provinces["p2"].controller == "B"
    w.tick()
    assert w.divisions["A1"].province == "p2" and w.provinces["p2"].controller == "A"


def test_move_into_enemy_is_refused_and_attack_needs_an_enemy():
    w = tiny_world(["plains"] * 3, ["A", "A", "B"], [inf("A", "p1"), inf("B", "p2")])
    w.apply_orders([Order("A1", "move", "p2"), Order("A1", "attack", "p0"), Order("ghost", "hold")])
    assert w.illegal_orders == 3
    w.apply_orders([Order("A1", "attack", "p2")])
    assert w.illegal_orders == 3


def test_reissuing_a_move_keeps_progress():
    w = tiny_world(["plains"] * 3, ["A"] * 3, [inf("A", "p0")])
    w.apply_orders([Order("A1", "move", "p2")])
    for _ in range(6):
        w.tick()
    left = w.divisions["A1"].move_left
    w.apply_orders([Order("A1", "move", "p2")])
    assert w.divisions["A1"].move_left == left


def test_broken_defender_retreats_after_the_minimum_fight():
    w = tiny_world(["plains"] * 4, ["A", "A", "B", "B"], [inf("A", "p1"), inf("B", "p2")],
                   hubs={"p0": "A", "p3": "B"})
    w.apply_orders([Order("A1", "attack", "p2")])
    d = w.divisions["B1"]
    d.org = 0.0
    for _ in range(3):
        w.tick()
    assert d.province == "p2"           # not before four hours of combat
    w.tick()
    assert d.province == "p3" and d.alive


def test_defender_with_nowhere_to_go_is_destroyed():
    w = tiny_world(["plains"] * 3, ["A", "A", "B"], [inf("A", "p1"), inf("B", "p2")],
                   hubs={"p0": "A", "p2": "B"})
    w.apply_orders([Order("A1", "attack", "p2")])
    w.divisions["B1"].org = 0.0
    for _ in range(4):
        w.tick()
    assert not w.divisions["B1"].alive and w.destroyed["B"] == 1


def test_attacker_walks_in_once_the_province_is_empty():
    w = tiny_world(["plains"] * 4, ["A", "A", "B", "B"], [inf("A", "p1"), inf("B", "p2")],
                   hubs={"p0": "A", "p3": "B"})
    w.apply_orders([Order("A1", "attack", "p2")])
    w.divisions["B1"].org = 0.0
    for _ in range(4 + 15):
        w.tick()
    assert w.divisions["A1"].province == "p2" and w.provinces["p2"].controller == "A"


def test_supply_network_and_encirclement_detection():
    w = tiny_world(["plains"] * 5, ["A", "A", "A", "A", "B"],
                   [inf("A", "p2"), inf("A", "p3")], hubs={"p0": "A", "p4": "B"})
    assert w.supplied["A"] == {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
    assert not any(d.encircled for d in w.alive())
    # cutting the chain at p1 strands both divisions
    assert {d.id for d in w.would_be_cut_off("A", "p1", "B")} == {"A1", "A2"}
    w.provinces["p1"].controller = "B"
    w._update_supply()
    assert all(d.encircled for d in w.alive()) and w.pockets_closed["B"] == 1
    # ...and taking it back frees them
    assert {d.id for d in w.would_be_freed("A", "p1")} == {"A1", "A2"}


def test_encircled_divisions_get_no_recovery_and_then_attrition():
    w = tiny_world(["plains"] * 4, ["A", "B", "A", "A"], [inf("A", "p2", org=0.5, strength=0.9)],
                   hubs={"p0": "A", "p3": "B"})
    d = w.divisions["A1"]
    assert d.encircled
    org, hp = d.org, d.strength
    for _ in range(72):
        w.tick()
    assert d.org == org and d.strength == hp   # the grace period: nothing regained, nothing lost
    for _ in range(10):
        w.tick()
    assert d.strength < hp


def test_supplied_divisions_regain_organisation():
    w = tiny_world(["plains"] * 3, ["A", "A", "B"], [inf("A", "p0", org=0.5)], hubs={"p0": "A", "p2": "B"})
    w.tick()
    assert w.divisions["A1"].org > 30


def test_unopposed_arrival_costs_organisation():
    w = tiny_world(["plains"] * 3, ["A", "A", "B"], [inf("A", "p1")], hubs={"p0": "A", "p2": "B"})
    w.apply_orders([Order("A1", "move", "p2")])
    for _ in range(15):
        w.tick()
    assert w.divisions["A1"].org < 60 * 0.9


def test_combat_is_deterministic_per_seed():
    def run(seed):
        w = tiny_world(["plains"] * 4, ["A", "A", "B", "B"], [inf("A", "p1"), inf("B", "p2")], seed=seed,
                       hubs={"p0": "A", "p3": "B"})
        w.apply_orders([Order("A1", "attack", "p2")])
        for _ in range(30):
            w.tick()
        return [(round(d.strength, 6), round(d.org, 6)) for d in w.alive()]

    assert run(3) == run(3)
    assert run(3) != run(4)


def test_reserves_join_a_battle_up_to_the_width():
    # plains are 70 wide: three 18-wide divisions fit (54), the fourth overshoots and a fifth stays out
    w = tiny_world(["plains"] * 4, ["A", "A", "B", "B"],
                   [inf("A", "p1", count=5), inf("B", "p2")], hubs={"p0": "A", "p3": "B"})
    w.apply_orders([Order(f"A{i}", "attack", "p2") for i in range(1, 6)])
    w.tick()
    assert sum(d.engaged for d in w.alive("A")) == 4
