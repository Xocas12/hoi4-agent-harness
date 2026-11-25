"""The micro controller under the LLM (#85): the interface, the rule baseline, the config switch and
the campaign runtime. The offline loop is in test_85_micro_learning.py."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hoi4_harness.config import HarnessConfig
from hoi4_harness.micro import (
    Intent,
    MicroOrder,
    Observation,
    RuleController,
    SectorState,
    build_observation,
    orders_to_calls,
)
from hoi4_harness.micro.baseline import baseline_action, baseline_from_features
from hoi4_harness.micro.features import feature_dict
from hoi4_harness.micro.runtime import MicroRuntime, build_controller, intents_from_standing
from hoi4_harness.world import Division


def sec(state, enemy="FRA", own=0, foe=0, low=0, rear=None):
    return SectorState(state, enemy, own=own, foe=foe, own_low_org=low, rear_state=rear)


def test_orders_become_set_front_order_calls():
    calls = orders_to_calls([MicroOrder(42, "push", "x"), MicroOrder(7, "hold")])
    assert [(c.name, c.arguments) for c in calls] == [
        ("set_front_order", {"state": "42", "order": "push"}),
        ("set_front_order", {"state": "7", "order": "hold"})]


def test_an_order_outside_the_front_tool_is_refused():
    with pytest.raises(ValueError):
        MicroOrder(1, "invade")
    with pytest.raises(ValueError):
        Intent("FRA", posture="berserk")


def test_baseline_concentrates_on_the_weakest_sector_and_holds_elsewhere():
    obs = Observation("1940-01-01", [sec(1, own=10, foe=10), sec(2, own=9, foe=3), sec(3, own=4, foe=8)])
    orders = {o.state: o.order for o in RuleController().decide([], obs)}
    assert orders == {2: "push", 1: "hold", 3: "hold"}


def test_baseline_rotates_tired_divisions_to_the_rear_state():
    obs = Observation(None, [sec(5, own=8, foe=4, low=4, rear=99)])
    orders = {o.state: o.order for o in RuleController().decide([], obs)}
    assert orders[99] == "reserve"
    assert orders[5] == "reserve"        # the main effort of a tired sector is relief, not an attack


def test_baseline_leaves_orders_that_already_stand_alone():
    obs = Observation(None, [sec(1, own=9, foe=3), sec(2, own=2, foe=9)], current={1: "push", 2: "hold"})
    assert RuleController().decide([], obs) == []


def test_the_llm_intent_picks_the_sector_and_the_others_on_that_front_hold():
    obs = Observation(None, [sec(1, own=10, foe=2), sec(2, own=5, foe=5), sec(3, "ENG", own=6, foe=3)])
    intent = Intent("FRA", sector=2, posture="offensive", concentration=0.4)
    orders = {o.state: o.order for o in RuleController().decide([intent], obs)}
    assert orders[2] == "attack" and orders[1] == "hold"
    assert orders[3] == "push"                       # a front with no intent still gets the baseline


def test_a_defensive_intent_without_a_sector_stands_the_front_down():
    obs = Observation(None, [sec(1, own=10, foe=2)])
    orders = {o.state: o.order for o in
              RuleController().decide([Intent("FRA", None, "defensive", 0.0)], obs)}
    assert orders == {1: "hold"}


def test_two_llm_sectors_on_one_front_both_keep_their_orders():
    obs = Observation(None, [sec(1, own=4, foe=4), sec(2, own=4, foe=4), sec(3, own=4, foe=4)])
    intents = [Intent("FRA", 1, "offensive", 1.0), Intent("FRA", 2, "hold", 0.0)]
    orders = {o.state: o.order for o in RuleController().decide(intents, obs)}
    assert orders == {1: "push", 2: "hold", 3: "hold"}


def test_the_order_cap_keeps_the_attacks():
    obs = Observation(None, [sec(i, own=1, foe=1) for i in range(1, 10)] + [sec(20, own=9, foe=2)])
    orders = RuleController(max_orders=3).decide([], obs)
    assert len(orders) == 3 and orders[0].state == 20 and orders[0].order == "push"


def test_baseline_rule_from_the_forces():
    assert baseline_action(0, 5) == "hold"
    assert baseline_action(10, 4) == "push"
    assert baseline_action(5, 5) == "attack"
    assert baseline_action(3, 5) == "probe"
    assert baseline_action(1, 9) == "hold"
    assert baseline_from_features(feature_dict(10, 4)) == "push"


def test_intents_ignore_orders_the_controller_gave_itself():
    obs = Observation(None, [sec(1), sec(2)])
    standing = {"1": "push", "2": "hold", "99": "reserve", "region:3": "dominate"}
    intents = intents_from_standing(standing, {"1": "push"}, obs)
    assert [(i.sector, i.posture) for i in intents] == [(2, "hold")]


# -- reading the picture ---------------------------------------------------------------------------


class Layer:
    states = {10: SimpleNamespace(provinces=[100, 101]), 11: SimpleNamespace(provinces=[110])}
    near = {100: [110], 101: [], 110: [100]}

    def neighbours(self, p):
        return self.near.get(p, [])

    def state_of(self, p):
        return {100: 10, 101: 10, 110: 11}.get(p)


def division(ident, province, state, org=60.0, country="GER"):
    return Division(ident, country, "inf", province, state, organisation=org)


def test_build_observation_counts_the_forces_in_contact_and_the_tired_ones():
    own = [division("a", 110, 11), division("b", 110, 11), division("c", 110, 11)]
    foe = [division("f1", 100, 10, country="FRA"), division("f2", 101, 10, country="FRA")]
    world = SimpleNamespace(enemies={"FRA"}, countries={})
    pic = SimpleNamespace(
        world=world, date="1940-05-01", own=own, contact={"FRA": foe}, borders={10: ("FRA", "hills")},
        conditions=[SimpleNamespace(id="a", problems=["low organisation"]),
                    SimpleNamespace(id="b", problems=["short of supply"]),
                    SimpleNamespace(id="c", problems=[])])
    obs = build_observation(pic, Layer(), None, {10: "hold"})
    (s,) = obs.sectors
    assert (s.state, s.enemy, s.own, s.foe, s.terrain) == (10, "FRA", 3, 2, "hills")
    assert (s.own_low_org, s.own_short_supply, s.rear_state) == (1, 1, 11)
    assert obs.current == {10: "hold"}


def test_build_observation_skips_a_capitulated_enemy():
    world = SimpleNamespace(enemies={"FRA"}, countries={"FRA": SimpleNamespace(capitulated=True)})
    pic = SimpleNamespace(world=world, date=None, own=[], contact={"FRA": [division("f", 100, 10)]},
                          borders={10: ("FRA", "")}, conditions=[])
    assert build_observation(pic, Layer()).sectors == []


# -- the switch and the campaign runtime ---------------------------------------------------------------


def test_the_controller_is_off_by_default_and_the_switch_loads_from_a_profile(tmp_path):
    assert HarnessConfig().micro_controller == "off"
    assert build_controller("off") is None
    profile = tmp_path / "p.json"
    profile.write_text(json.dumps({"micro_controller": "rule", "micro_model": "m.json"}), encoding="utf-8")
    cfg = HarnessConfig.from_file(profile)
    assert cfg.micro_controller == "rule" and cfg.micro_model.name == "m.json"
    assert build_controller("rule").name == "rule"
    with pytest.raises(ValueError):
        build_controller("learned")
    with pytest.raises(ValueError):
        build_controller("magic")


class FakeEnv:
    def __init__(self, pic):
        self.world = SimpleNamespace(picture=lambda state: pic, layer=Layer(), history=None)
        self.calls = []

    def act_many(self, calls, date="", *, auto=False):
        self.calls += [(c.name, dict(c.arguments), auto) for c in calls]
        return [SimpleNamespace(ok=True, message="") for _ in calls]


def test_runtime_sends_orders_through_set_front_order_and_logs_the_decisions(tmp_path):
    own = [division(str(i), 110, 11) for i in range(6)]
    foe = [division("f1", 100, 10, country="FRA")]
    pic = SimpleNamespace(world=SimpleNamespace(enemies={"FRA"}, countries={}), date="1940-05-01", own=own,
                          contact={"FRA": foe}, borders={10: ("FRA", "")}, conditions=[])
    env = FakeEnv(pic)
    rt = MicroRuntime(RuleController(), env, tmp_path)
    lines = rt.step(SimpleNamespace(date="1940-05-01"))
    assert env.calls == [("set_front_order", {"state": "10", "order": "push"}, True)]
    assert lines == ["micro push 10"]
    logged = [json.loads(x) for x in (tmp_path / "micro_decisions.jsonl").read_text().splitlines()]
    assert logged[0]["order"] == "push" and logged[0]["features"]["log_ratio"] > 0
    assert json.loads((tmp_path / "micro_orders.json").read_text()) == {"10": "push"}


def test_runtime_does_nothing_without_a_world(tmp_path):
    env = SimpleNamespace(world=None)
    assert MicroRuntime(RuleController(), env, tmp_path).step(SimpleNamespace(date="1940-01-01")) == []


def test_the_supervisor_hook_is_inert_when_off_and_builds_the_runtime_when_on(tmp_path):
    from hoi4_harness.campaign.runner import CampaignSupervisor

    pic = SimpleNamespace(world=SimpleNamespace(enemies={"FRA"}, countries={}), date="1940-05-01",
                          own=[division(str(i), 110, 11) for i in range(4)],
                          contact={"FRA": [division("f", 100, 10, country="FRA")]},
                          borders={10: ("FRA", "")}, conditions=[])
    env = FakeEnv(pic)
    lines = []
    me = SimpleNamespace(config=SimpleNamespace(micro_controller="off", micro_model=None, dry_run=False),
                         env=env, run_dir=tmp_path, log=SimpleNamespace(line=lines.append), micro=None)
    CampaignSupervisor._micro(me, SimpleNamespace(date="1940-05-01"))
    assert env.calls == [] and me.micro is None
    me.config.micro_controller = "rule"
    CampaignSupervisor._micro(me, SimpleNamespace(date="1940-05-01"))
    assert env.calls and lines == ["micro push 10"]
    me.config.micro_controller = "learned"           # no model: the run logs it and carries on
    me.micro = None
    CampaignSupervisor._micro(me, SimpleNamespace(date="1940-05-01"))
    assert "micro controller: ValueError" in lines[-1]
