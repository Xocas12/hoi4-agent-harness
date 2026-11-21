"""The mod's weekly ``observe`` lines (llm_bridge_observe.txt) into :class:`~..types.ModObserved`.

Kinds: ``obsbegin`` (a new weekly batch: the per-country sets start empty), ``res``, ``tension``,
``equip``, ``garrison``, ``peace``, ``fac``, ``vol``, ``collab``. Each field is read on its own: a
token the game did not expand prints literally (``[?llmb_rp]``) and is MISSING, never 0, so a line
with one bad field still gives the others.
"""

from __future__ import annotations

from ..types import ModObserved

KINDS = frozenset({"obsbegin", "res", "tension", "equip", "garrison", "peace", "fac", "vol", "collab"})


def number(raw: str | None) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _fields(fields: dict[str, str], names: dict[str, str]) -> dict[str, float]:
    out = {}
    for key, name in names.items():
        value = number(fields.get(key))
        if value is not None:
            out[name] = value
    return out


def apply(observed: ModObserved, kind: str, fields: dict[str, str], date: str | None) -> None:
    """Fold one line into ``observed``."""
    observed.date = date or observed.date
    other = fields.get("of", "").strip().upper()
    if kind == "obsbegin":
        observed.faction_members, observed.volunteers, observed.collaboration = {}, {}, {}
    elif kind == "res":
        name = fields.get("r", "").strip()
        got = _fields(fields, {"prod": "prod", "cons": "cons", "imp": "imp", "exp": "exp", "net": "net"})
        if name and got:
            observed.resources[name] = got
    elif kind == "tension":
        value = number(fields.get("threat"))
        if value is not None:
            observed.tension = max(0.0, min(1.0, value / 100))
    elif kind == "equip":
        name = fields.get("a", "").strip()
        got = _fields(fields, {"stock": "stock", "army": "army", "need": "need"})
        if name and got:
            observed.equipment[name] = got
    elif kind == "garrison":
        got = _fields(fields, {"need": "need", "foreign": "foreign", "occupied": "occupied"})
        if got:
            observed.garrison = got
    elif kind == "peace":
        got: dict[str, float | bool] = dict(_fields(fields, {"score": "score", "total": "total",
                                                             "turn": "turn"}))
        flag = fields.get("in", "").strip()
        if flag in ("0", "1"):
            got["in"] = flag == "1"
        if got:
            observed.peace = got
    elif kind == "fac":
        got = _fields(fields, {"score": "score", "ratio": "ratio", "rank": "rank",
                               "manifest": "manifest", "power": "power"})
        for key in ("ratio", "manifest"):
            if key in got:
                got[key] /= 100
        if other:
            if got:
                observed.faction_members[other] = got
        elif got:
            observed.faction = got
    elif kind == "vol" and other:
        got = {k: int(v) for k, v in _fields(fields, {"in": "in", "out": "out"}).items()}
        if got:
            observed.volunteers[other] = got
    elif kind == "collab" and other:
        value = number(fields.get("v"))
        if value is not None:
            observed.collaboration[other] = max(0.0, min(1.0, value / 100))
