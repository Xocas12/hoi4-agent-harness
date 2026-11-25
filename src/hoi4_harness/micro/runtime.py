"""Running a controller under the LLM in a campaign (``micro_controller`` in the profile).

Once per turn, before the LLM looks: read the picture from the newest save, turn the LLM's standing
front orders into intents, let the controller decide, and send the result through the same
``set_front_order`` the LLM uses. The controller never overwrites a state the LLM ordered itself
(the LLM's order is the intent); it owns the states it ordered and the others on the same fronts.
Every decision is appended to ``micro_decisions.jsonl`` with its features, which is the data the
offline learner trains on (dataset.py).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..front_orders import Standing
from .baseline import RuleController
from .features import sector_features
from .interface import Intent, MicroController, Observation, build_observation, orders_to_calls

OWN_FILE = "micro_orders.json"
DECISIONS_FILE = "micro_decisions.jsonl"
KINDS = ("off", "rule", "learned")


def build_controller(kind: str, model_path: Path | None = None) -> MicroController | None:
    kind = (kind or "off").lower()
    if kind == "off":
        return None
    if kind == "rule":
        return RuleController()
    if kind == "learned":
        from .learn import LearnedController, LinearBandit

        if model_path is None:
            raise ValueError("micro_controller 'learned' needs micro_model (a file from `micro train`)")
        return LearnedController(LinearBandit.load(model_path))
    raise ValueError(f"micro_controller must be one of {KINDS}, not {kind!r}")


def intents_from_standing(standing: dict[str, str], ours: dict[str, str], obs: Observation) -> list[Intent]:
    """The LLM's orders on sector states, as intents. An order the controller itself gave (still
    standing unchanged in ``ours``) is not the LLM's."""
    out: list[Intent] = []
    for key, order in sorted(standing.items()):
        if not key.isdigit() or ours.get(key) == order:
            continue
        sec = obs.sector(int(key))
        if sec is None:
            continue
        if order in ("push", "attack", "probe"):
            conc = {"push": 1.0, "attack": 0.6, "probe": 0.3}[order]
            out.append(Intent(sec.enemy, sec.state, "offensive", conc))
        elif order == "hold":
            out.append(Intent(sec.enemy, sec.state, "hold", 0.0))
    return out


class MicroRuntime:
    def __init__(self, controller: MicroController, env, run_dir: Path, log=None):
        self.controller = controller
        self.env = env
        self.run_dir = Path(run_dir)
        self.log = log or (lambda text: None)
        self.standing = Standing(run_dir)
        path = self.run_dir / OWN_FILE
        try:
            self.ours: dict[str, str] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, ValueError):
            self.ours = {}

    def step(self, state) -> list[str]:
        """One turn. Returns the short lines of the orders given (for the turn line)."""
        world = getattr(self.env, "world", None)
        if world is None:
            return []
        pic = world.picture(state)
        if pic is None:
            return []
        self.standing = Standing(self.run_dir)       # the file is the record the adapter keeps
        current = {int(k): v for k, v in self.standing.orders.items() if k.isdigit()}
        obs = build_observation(pic, world.layer, world.history, current)
        intents = intents_from_standing(self.standing.orders, self.ours, obs)
        orders = self.controller.decide(intents, obs)
        if not orders:
            return []
        results = self.env.act_many(orders_to_calls(orders), state.date or "", auto=True)
        lines: list[str] = []
        for order, result in zip(orders, results, strict=False):
            if not result.ok:
                self.log(f"micro: {order.order} {order.state} refused: {result.message}")
                continue
            self.ours[str(order.state)] = order.order
            sec = obs.sector(order.state) or next(
                (s for s in obs.sectors if order.order == "reserve" and s.rear_state == order.state), None)
            self._record(state.date, order, sec)
            lines.append(f"micro {order.order} {order.state}")
        self._save()
        return lines

    def _record(self, date, order, sec) -> None:
        # a reserve order lands on a rear state: it is logged with the features of the sector it relieves
        rec = {"date": date, "state": order.state, "order": order.order, "reason": order.reason,
               "controller": self.controller.name,
               "features": sector_features(sec) if sec is not None else None}
        if rec["features"] is None:
            return
        try:
            with open(self.run_dir / DECISIONS_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
        except OSError:
            pass

    def _save(self) -> None:
        try:
            (self.run_dir / OWN_FILE).write_text(json.dumps(self.ours, sort_keys=True), encoding="utf-8")
        except OSError:
            pass
