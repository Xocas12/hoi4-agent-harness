"""Politics: advisors and decisions, from a rule table.

There is no sound default. Which advisors and decisions exist is per country and per mod, the
state does not list them, and the catalog has no action for laws, so a script that guessed an id
would be refused every turn. This controller therefore runs a table of rules you give it
(``auto_options.politics_rules``, else ``data/politics.json``, which ships empty):

    {"id": "economy-advisor",
     "when": {"min_pp": 150, "at_war": false, "from": "1936-06-01"},
     "do": {"action": "hire_advisor", "arguments": {"slot": "economy_minister", "advisor_id": "..."}},
     "once": true}

``when`` takes ``min_pp`` (political power), ``at_war`` (true / false), ``from`` / ``until``
(ISO dates) and ``max_stability``; every key given must hold. ``once`` (default true) stops the
rule after one accepted call. A refused rule waits 60 days.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..types import ActionCall, ActionResult, GameState
from .base import Script, call

DATA = Path(__file__).parent / "data" / "politics.json"
ACTIONS = ("hire_advisor", "take_decision")


def holds(when: dict, state: GameState) -> bool:
    if "min_pp" in when and state.political_power < float(when["min_pp"]):
        return False
    if "at_war" in when and bool(state.wars) != bool(when["at_war"]):
        return False
    if "from" in when and state.date < str(when["from"]):
        return False
    if "until" in when and state.date >= str(when["until"]):
        return False
    return not ("max_stability" in when and state.stability > float(when["max_stability"]))


class PoliticsScript(Script):
    domain = "politics"
    name = "politics rule table"

    def __init__(self, ctx=None):
        super().__init__(ctx)
        own = self.ctx.option("politics_rules")
        self.rules = list(own if own is not None
                          else json.loads(DATA.read_text(encoding="utf-8"))["rules"])
        self.done: set[str] = set()

    def decide(self, state: GameState, objective: str, memory) -> list[ActionCall]:
        out = []
        for index, rule in enumerate(self.rules):
            rid = str(rule.get("id", index))
            action = rule["do"]["action"]
            if action not in ACTIONS:
                continue
            if rid in self.done or self.blocked(rid, state.date):
                continue
            if not holds(rule.get("when", {}), state):
                continue
            out.append(call(action, f"rule {rid}", **dict(rule["do"].get("arguments", {}))))
            out[-1].call_id = rid
        return out

    def feedback(self, call_: ActionCall, result: ActionResult, state: GameState) -> None:
        rid = call_.call_id
        if rid is None:
            return
        if not result.ok:
            self._blocked[rid] = state.date
        elif next((r for i, r in enumerate(self.rules)
                   if str(r.get("id", i)) == rid), {}).get("once", True):
            self.done.add(rid)

    def idle_note(self, state: GameState) -> str:
        return "no rule applies" if self.rules else "the rule table is empty"
