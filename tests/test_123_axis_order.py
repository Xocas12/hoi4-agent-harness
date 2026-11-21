"""Planner(axis_order=...): the order the drawn axes are handed on in (#123)."""

import pytest

from hoi4_harness.tactics.controllers import CONTROLLERS
from hoi4_harness.tactics.env import TacticalEnv
from hoi4_harness.tactics.picture import build_picture
from hoi4_harness.tactics.planner import AXIS_ORDERS, Planner
from hoi4_harness.tactics.scenario import SCENARIO_DIR

FRANCE = str(SCENARIO_DIR.parent / "realmap" / "germany_france_1940.json")
POLAND = str(SCENARIO_DIR.parent / "realmap" / "germany_poland_1939.json")


def _pic(path, seed=4001):
    env = TacticalEnv(opponent=CONTROLLERS["vanilla"]())
    env.reset(path, seed=seed)
    view = env.view()
    return view, build_picture(view, "aggregated")


def test_123_best_first_is_the_default_and_unknown_orders_are_refused():
    assert Planner().axis_order == "best"
    assert "best" in AXIS_ORDERS and "sorted" in AXIS_ORDERS and len(AXIS_ORDERS) >= 4
    with pytest.raises(ValueError):
        Planner(axis_order="loudest")


def test_123_sort_axes_is_the_sorted_order():
    assert Planner(sort_axes=True).axis_order == "sorted"


@pytest.mark.parametrize("path", [FRANCE, POLAND])
def test_123_every_order_is_a_permutation_of_the_best_first_axes(path):
    _, pic = _pic(path)
    axes = Planner(patience=0)._choose_axes(pic)
    for order in AXIS_ORDERS:
        got = Planner(patience=0, axis_order=order)._order_axes(pic, list(axes))
        assert sorted(got) == sorted(axes)
        again = Planner(patience=0, axis_order=order)._order_axes(pic, list(reversed(axes)))
        assert got == again or order == "best"


def test_123_orders_follow_their_key():
    _, pic = _pic(FRANCE)
    axes = sorted(pic.regions)[:6]   # any set of regions will do for a pure ordering
    assert Planner(axis_order="best")._order_axes(pic, axes) == axes
    assert Planner(axis_order="sorted")._order_axes(pic, axes[::-1]) == axes
    weak = Planner(axis_order="weakest")._order_axes(pic, axes)
    powers = [pic.regions[a].foe_power for a in weak]
    assert powers == sorted(powers)
    p = Planner(axis_order="nearest")
    marches = [p._march(pic, a) for a in p._order_axes(pic, axes)]
    assert marches == sorted(marches)
    net = pic.hops(sorted(pic.supply_net(pic.side)))
    sup = Planner(axis_order="supply")._order_axes(pic, axes)
    assert [net.get(a, 99) for a in sup] == sorted(net.get(a, 99) for a in axes)


def test_123_flanks_puts_the_middle_axis_last():
    _, pic = _pic(FRANCE)
    ids = sorted(pic.regions)
    # three regions in a row along the hop graph: the middle one is the one closest to both others
    start = ids[0]
    d = pic.hops([start])
    far = max(ids, key=lambda r: d.get(r, 0))
    mid = min((r for r in ids if r not in (start, far)),
              key=lambda r: abs(d.get(r, 99) - d[far] / 2) + pic.hops([far]).get(r, 99) - d.get(r, 99))
    out = Planner(axis_order="flanks")._order_axes(pic, [start, far, mid])
    assert sorted(out) == sorted([start, far, mid])
    assert out[-1] != out[0]


def test_123_the_order_changes_the_plan_names_not_the_plan_set():
    view, pic = _pic(FRANCE)
    base = Planner(patience=0).plan(view, pic)
    for order in AXIS_ORDERS:
        plans = Planner(patience=0, axis_order=order).plan(view, build_picture(view, "aggregated"))
        assert sorted(p.name for p in plans) == sorted(p.name for p in base)
