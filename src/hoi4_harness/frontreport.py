"""The front report (#159): how each front moves over time, the battles over a period, the force
picture, and a computed reading of why a front is stuck.

A text save is one moment: the brief's "Forces and map" shows it, and a front that sat on the same
border for four years looked the same at every wake (the 2026-11-22 campaign, docs/results). What
turns moments into movement is a small history kept in the run directory
(``front_history.json``), written from each new save the observer reads:

* per enemy, the provinces of that enemy's land that ``me`` holds ("taken") and the provinces of
  ``me`` that the enemy holds ("lost"). Between two saves the difference is what the line gained and
  lost; "unchanged for N days" is the time since the last save that differed. A province that left
  both sets because the country annexed it (it is still held) is not a loss.
* the battles ``me`` is in. A save holds only the combats in flight, so each is recorded when seen
  and closed when it is gone: who holds the province then decides who won. A battle that began and
  ended between two saves is not seen (the game autosaves monthly), and the report says so.

Everything here is computed from the save and that history; nothing is read from the game's own
front or AI state, so the reading is the one a player could make from the map and the battle
windows.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

HISTORY_FILE = "front_history.json"
#: The period the report speaks about, in days.
WINDOW_DAYS = 90
#: A front that has not moved for this long is stuck; so is one that moved at most STUCK_MOVED
#: provinces over two months or more of history.
STUCK_DAYS = 30
STUCK_MOVED = 2
#: A front is losing when it gave up this many more provinces than it took over the period.
LOSING_NET = 5
#: An attack is piecemeal when the attacker had under this share of the defender's divisions.
PIECEMEAL = 0.5
#: Outnumbered: the foe in contact is at least this many times own.
OUTNUMBERED = 1.5
#: Armour: a division with this share of tank battalions counts as armoured; a front below
#: ARMOUR_LOW of armoured divisions, with at least ARMOUR_MIN divisions in contact, is thin on tanks.
ARMOUR_DIVISION = 0.2
ARMOUR_LOW = 0.15
ARMOUR_MIN = 4
#: Share of the divisions in contact that must be low on organisation / supply to be named.
WEAK_SHARE = 0.25
#: Attacks needed before a win rate is read, and the most closed battles kept.
MIN_ATTACKS = 3
KEEP_CLOSED = 300
KEEP_SNAPSHOTS = 80
MAX_FRONTS = 3

_ARMOUR_NAME = re.compile(r"panzer|armou?r|tank", re.I)


def ordinal(day: str | None) -> int | None:
    found = re.findall(r"\d+", day or "")[:3]
    if len(found) < 3:
        return None
    try:
        return date(*(int(x) for x in found)).toordinal()
    except ValueError:
        return None


def days_between(a: str | None, b: str | None) -> int | None:
    x, y = ordinal(a), ordinal(b)
    return None if x is None or y is None else y - x


class FrontHistory:
    """What the observer has seen of the fronts, kept between wakes in the run directory."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        #: Oldest first: {"date", "fronts": {tag: {gained, lost, gained_states, lost_states,
        #: baseline, taken, lost_set}}}; the province lists are kept on the newest entry only.
        self.snapshots: list[dict] = []
        #: enemy tag -> the date its line last moved (or was first seen).
        self.since: dict[str, str] = {}
        #: Battles in the last save, by key, and the ones that have ended.
        self.open: dict[str, dict] = {}
        self.closed: list[dict] = []
        self._load()

    @classmethod
    def for_run(cls, run_dir: Path | None) -> FrontHistory:
        return cls(Path(run_dir) / HISTORY_FILE if run_dir else None)

    # -- storage -------------------------------------------------------------------------------

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self.snapshots = [s for s in data.get("snapshots", []) if isinstance(s, dict)]
        self.since = dict(data.get("since", {}))
        self.open = dict(data.get("open", {}))
        self.closed = [b for b in data.get("closed", []) if isinstance(b, dict)]

    def save(self) -> None:
        if self.path is None:
            return
        data = {"version": 1, "snapshots": self.snapshots, "since": self.since,
                "open": self.open, "closed": self.closed}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    # -- recording -----------------------------------------------------------------------------

    @property
    def last_date(self) -> str | None:
        return self.snapshots[-1]["date"] if self.snapshots else None

    def record(self, world, me: str, layer) -> bool:
        """Add the save ``world`` to the history; False when it is not newer than the last one."""
        day = ordinal(world.date)
        if day is None:
            return False
        last_day = ordinal(self.last_date)
        if last_day is not None and day <= last_day:
            return False
        enemies = set(world.enemies)
        taken: dict[str, set[int]] = {}
        lost: dict[str, set[int]] = {}
        if layer is not None:
            for p, controller in world.controllers.items():
                owner = world.owner_of(p, layer)
                if owner is None:
                    continue
                if controller == me and owner in enemies:
                    taken.setdefault(owner, set()).add(p)
                elif controller in enemies and owner == me:
                    lost.setdefault(controller, set()).add(p)
        prev = self.snapshots[-1]["fronts"] if self.snapshots else {}
        fronts: dict[str, dict] = {}
        for tag in sorted(enemies | set(prev)):
            now_t, now_l = taken.get(tag, set()), lost.get(tag, set())
            old = prev.get(tag)
            if old is None or "taken" not in old:
                gained = lost_p = set()
                baseline = True
            else:
                baseline = False
                old_t, old_l = set(old["taken"]), set(old["lost_set"])
                gained = (now_t - old_t) | {p for p in old_l - now_l
                                             if world.controller_of(p, layer) == me}
                lost_p = ({p for p in old_t - now_t if world.controller_of(p, layer) == tag}
                          | (now_l - old_l))
            fronts[tag] = {
                "gained": len(gained), "lost": len(lost_p), "baseline": baseline,
                "gained_states": _states(gained, layer), "lost_states": _states(lost_p, layer),
                "gained_by_state": _by_state(gained, layer), "lost_by_state": _by_state(lost_p, layer),
                "taken": sorted(now_t), "lost_set": sorted(now_l)}
            if baseline or gained or lost_p:
                self.since[tag] = world.date
        for s in self.snapshots:
            for f in s["fronts"].values():
                f.pop("taken", None)
                f.pop("lost_set", None)
        self.snapshots.append({"date": world.date, "fronts": fronts})
        del self.snapshots[:-KEEP_SNAPSHOTS]
        self._record_battles(world, me, layer, enemies)
        self.save()
        return True

    def _record_battles(self, world, me: str, layer, enemies: set[str]) -> None:
        from . import battles as B

        now: dict[str, dict] = {}
        for b in B.resolve(world, me, layer, None):
            if b.kind != "land" or not b.own:
                continue
            foe = b.defender if b.own_side == "attacker" else b.attacker
            front = next((t for t in foe.countries if t in enemies), None) or (
                foe.countries[0] if foe.countries else "?")
            key = f"{b.ident}:{b.province}"
            old = self.open.get(key, {})
            now[key] = {
                "key": key, "front": front, "province": b.province, "state": b.state,
                "terrain": b.terrain, "own_side": b.own_side,
                "attacker": list(b.attacker.countries), "defender": list(b.defender.countries),
                "att_div": max(b.attacker.divisions, old.get("att_div", 0)),
                "def_div": max(b.defender.divisions, old.get("def_div", 0)),
                "first": old.get("first", world.date), "last": world.date}
        for key, rec in self.open.items():
            if key in now:
                continue
            rec = dict(rec)
            rec["ended"] = world.date
            rec["outcome"] = _outcome(rec, world.controller_of(rec["province"], layer), me, enemies)
            self.closed.append(rec)
        del self.closed[:-KEEP_CLOSED]
        self.open = now


def _states(provinces, layer) -> int:
    if layer is None:
        return 0
    return len({layer.state_of(p) for p in provinces} - {None})


def _by_state(provinces, layer) -> dict[str, int]:
    """Provinces moved per state (state id as a string, as it comes back from the json file): the
    per-sector outcome the micro controller's dataset joins on (micro/dataset.py)."""
    if layer is None:
        return {}
    out: dict[str, int] = {}
    for p in provinces:
        sid = layer.state_of(p)
        if sid is not None:
            out[str(sid)] = out.get(str(sid), 0) + 1
    return out


def _outcome(rec: dict, holder: str | None, me: str, enemies: set[str]) -> str:
    """Who took the fight, from who holds the province after it: attacker, defender or unknown."""
    if holder is None:
        return "unknown"
    if holder in rec["attacker"]:
        return "attacker"
    if holder in rec["defender"]:
        return "defender"
    if holder == me:
        return rec["own_side"]
    if holder in enemies:
        return "defender" if rec["own_side"] == "attacker" else "attacker"
    return "unknown"


# --- the report ---------------------------------------------------------------------------------


def _is_armoured(d) -> bool:
    if getattr(d, "armour", None) is not None:
        return d.armour >= ARMOUR_DIVISION
    return bool(_ARMOUR_NAME.search(d.template or ""))


def _ratio_words(own: int, foe: int) -> str:
    if own and foe:
        return f"1 to {foe / own:.1f}" if foe >= own else f"{own / foe:.1f} to 1"
    return "no divisions of yours" if not own else "no foe"


def _own_in_contact(pic, foe_divs, layer) -> list:
    if layer is None:
        return list(pic.own)
    near = {d.province for d in foe_divs}
    for p in list(near):
        near.update(layer.neighbours(p))
    return [d for d in pic.own if d.province in near]


def _movement(history: FrontHistory, tag: str, now: str | None) -> dict:
    snaps = history.snapshots
    out: dict = {"known": False}
    if not snaps:
        return out
    newest = snaps[-1]
    entry = newest["fronts"].get(tag)
    if entry is None:
        return out
    out["known"] = len(snaps) > 1 and not entry["baseline"]
    out["history_since"] = snaps[0]["date"]
    last_day = newest["date"]
    inside = [i for i in range(1, len(snaps)) if tag in snaps[i]["fronts"]
              and (days_between(snaps[i]["date"], last_day) or 0) < WINDOW_DAYS]
    out["last_save"] = snaps[-2]["date"] if len(snaps) > 1 else None
    out["since_last"] = {"gained": entry["gained"], "lost": entry["lost"],
                         "gained_states": entry["gained_states"], "lost_states": entry["lost_states"]}
    out["window"] = {k: sum(snaps[i]["fronts"][tag][k] for i in inside)
                     for k in ("gained", "lost", "gained_states", "lost_states")}
    # the saves in the window are compared with the one before the first of them
    out["window"]["days"] = (days_between(snaps[inside[0] - 1]["date"], last_day) or 0) if inside else 0
    since = history.since.get(tag)
    out["unchanged_days"] = days_between(since, newest["date"]) if since else None
    return out


def _battle_stats(history: FrontHistory, tag: str, now: str | None) -> dict:
    ended = [b for b in history.closed if b.get("front") == tag
             and (days_between(b.get("ended"), now) or 0) < WINDOW_DAYS]
    ongoing = [b for b in history.open.values() if b.get("front") == tag]
    rows = [(b, True) for b in ended] + [(b, False) for b in ongoing]

    def side(own_side: str) -> dict:
        mine = [(b, done) for b, done in rows if b["own_side"] == own_side]
        decided = [b for b, done in mine if done and b["outcome"] in ("attacker", "defender")]
        attacker_won = sum(1 for b in decided if b["outcome"] == "attacker")
        piecemeal = [b for b, _ in mine if b["def_div"] >= 2
                     and b["att_div"] < PIECEMEAL * b["def_div"]]
        return {"total": len(mine), "ongoing": sum(1 for _, d in mine if not d),
                "attacker_won": attacker_won, "attacker_lost": len(decided) - attacker_won,
                "piecemeal": len(piecemeal),
                "piecemeal_examples": [f"{b['att_div']} attacking {b['def_div']}"
                                       for b in piecemeal[-3:]]}

    own, foe = side("attacker"), side("defender")
    wins = own["attacker_won"] + foe["attacker_won"]
    decided = wins + own["attacker_lost"] + foe["attacker_lost"]
    return {"period_days": WINDOW_DAYS, "started": len(rows), "ended": len(ended),
            "ongoing": len(ongoing), "your_attacks": own, "their_attacks": foe,
            "attacker_success": {"won": wins, "decided": decided,
                                 "share": round(wins / decided, 2) if decided else None},
            "note": "seen from the saves (monthly): a battle that began and ended between two "
                    "saves is not counted; the winner is who held the province after it"}


def _force(pic, tag: str, layer) -> dict:
    foe = pic.contact.get(tag, [])
    own = _own_in_contact(pic, foe, layer)
    by_id = {c.id: c for c in pic.conditions}
    conds = [by_id[d.id] for d in own if d.id in by_id]
    armoured = sum(1 for d in own if _is_armoured(d))
    out = {"own_in_contact": len(own), "foe_in_contact": len(foe),
           "ratio": _ratio_words(len(own), len(foe)),
           "armour_share": round(armoured / len(own), 2) if own else None,
           "armoured_divisions": armoured}
    orgs = [d.organisation for d in own if d.organisation is not None]
    if orgs:
        out["org_average"] = round(sum(orgs) / len(orgs), 1)
    if conds:
        out["low_organisation"] = sum(1 for c in conds if "low organisation" in c.problems)
        out["short_of_supply"] = sum(1 for c in conds if any("supply" in p for p in c.problems))
        out["encircled"] = sum(1 for c in conds if c.encircled)
    return out


def _causes(battles: dict, force: dict) -> tuple[list[str], list[str]]:
    """The causes the numbers show on a front, and the tool kinds that answer each."""
    reasons: list[str] = []
    tools: list[str] = []
    own, foe = force["own_in_contact"], force["foe_in_contact"]
    if foe and own and foe >= OUTNUMBERED * own:
        reasons.append(f"outnumbered {force['ratio']} in contact ({own} against {foe})")
        tools.append("concentration")
    elif foe and not own:
        reasons.append(f"none of your divisions in contact against {foe}")
        tools.append("concentration")
    mine = battles["your_attacks"]
    decided = mine["attacker_won"] + mine["attacker_lost"]
    if decided >= MIN_ATTACKS and mine["attacker_lost"] * 2 > decided:
        reasons.append(f"{mine['attacker_lost']} of {decided} attacks lost")
        tools.append("concentration")
    if mine["total"] >= MIN_ATTACKS and mine["piecemeal"] * 2 >= mine["total"]:
        reasons.append(f"{mine['piecemeal']} of {mine['total']} attacks went in at under "
                       f"{PIECEMEAL:.0%} of the defender's divisions")
        tools.append("concentration")
    share = force["armour_share"]
    if share is not None and own >= ARMOUR_MIN and share < ARMOUR_LOW:
        reasons.append(f"{share:.0%} armour")
        tools.append("template")
    if own:
        for key, label, tool in (("low_organisation", "low on organisation", "reserve"),
                                 ("short_of_supply", "short of supply", "supply")):
            n = force.get(key, 0)
            if n and n / own >= WEAK_SHARE:
                reasons.append(f"{n} of {own} divisions {label}")
                tools.append(tool)
    if force.get("encircled"):
        reasons.append(f"{force['encircled']} divisions encircled")
        tools.append("reserve")
    return reasons, tools


def _reading(tag: str, move: dict, battles: dict, force: dict) -> dict | None:
    """``kind`` "losing" when the line is going backwards, "stuck" when it is not moving or the
    attacks are mostly failing; None when the front is moving the right way or too new to tell."""
    if not move.get("known"):
        return None
    unchanged = move.get("unchanged_days") or 0
    window = move.get("window", {})
    gained, lost = window.get("gained", 0), window.get("lost", 0)
    days = window.get("days", 0)
    theirs, mine = battles["their_attacks"], battles["your_attacks"]
    their_decided = theirs["attacker_won"] + theirs["attacker_lost"]
    my_decided = mine["attacker_won"] + mine["attacker_lost"]
    kind = None
    headline = ""
    if lost - gained >= LOSING_NET or (lost > gained and their_decided >= MIN_ATTACKS
                                       and theirs["attacker_won"] * 2 > their_decided):
        kind = "losing"
        headline = f"lost {lost} provinces and took {gained} in {days} days"
        if their_decided >= MIN_ATTACKS:
            headline += f"; their attacks {theirs['attacker_won']} won of {their_decided}"
    elif unchanged >= STUCK_DAYS:
        kind, headline = "stuck", f"line unchanged for {unchanged} days"
    elif days >= 2 * STUCK_DAYS and gained + lost <= STUCK_MOVED:
        kind, headline = "stuck", f"{gained + lost} provinces moved in {days} days"
    elif (my_decided >= MIN_ATTACKS and mine["attacker_lost"] * 2 > my_decided
          and gained <= LOSING_NET):
        kind = "stuck"
        headline = f"line barely moved ({gained} taken in {days} days)"
    if kind is None:
        return None
    reasons, tools = _causes(battles, force)
    if kind == "losing":
        tools = ["defend", *tools]
    numbers = headline + "; " + ("; ".join(reasons) if reasons
                                 else "no single cause stands out in the numbers")
    return {"kind": kind, "numbers": numbers, "reasons": reasons, "tools": _tools(tools, tag),
            "brief": headline + (("; " + "; ".join(reasons[:3])) if reasons else ""),
            "brief_tools": _brief_tools(tools, tag)}


_TOOLS = {
    "defend": "set_ai_posture defensive theater={tag} and set_front_order order=hold on the lines "
              "being lost, with order=reserve behind them, so the line shortens and the "
              "divisions rest",
    "concentration": "set_front_order order=push on one narrow sector where you can gather the "
                     "most divisions (it also aims the AI's concentrated offensives there), "
                     "order=hold on the rest of the line",
    "reserve": "set_front_order order=reserve behind the sector, hold on the weak stretches, so "
               "tired divisions are relieved",
    "template": "steer_army (templates and roles: tank-heavy divisions) with steer_production "
                "(tanks, units)",
    "supply": "steer_army (supply, fuel) and steer_production (infrastructure, building)",
}
#: The short forms for the brief, one line each.
_SHORT = {
    "defend": "set_ai_posture defensive {tag} + set_front_order hold/reserve",
    "concentration": "set_front_order push one sector, hold elsewhere",
    "reserve": "set_front_order reserve",
    "template": "steer_army/steer_production for tanks",
    "supply": "steer_army supply",
}
_ORDER = ["defend", "concentration", "reserve", "template", "supply"]


def _tools(keys: list[str], tag: str = "") -> list[str]:
    out = [_TOOLS[k].format(tag=tag) for k in _ORDER if k in keys]
    return out or [_TOOLS["concentration"], _TOOLS["template"]]


def _brief_tools(keys: list[str], tag: str = "", limit: int = 2) -> str:
    picked = [_SHORT[k].format(tag=tag) for k in _ORDER if k in keys][:limit]
    return "; ".join(picked or [_SHORT["concentration"], _SHORT["template"]])


def report(history: FrontHistory | None, pic, layer, name_of=None) -> list[dict]:
    """One dict per front (enemy at war that touches the line or fought on it), busiest first."""
    history = history or FrontHistory()
    world = pic.world
    enemies = set(getattr(world, "enemies", None) or set())
    now = pic.date
    done = {t for t, c in world.countries.items() if getattr(c, "capitulated", False)}
    enemies -= done
    tags = {t for t in pic.contact if t in enemies}
    tags |= {b.get("front") for b in list(history.open.values()) + history.closed
             if b.get("front") in enemies
             and (days_between(b.get("ended") or b.get("last"), now) or 0) < WINDOW_DAYS}
    out = []
    for tag in sorted(tags):
        move = _movement(history, tag, now)
        battles = _battle_stats(history, tag, now)
        force = _force(pic, tag, layer)
        row = {"enemy": tag, "date": now, "movement": move, "battles": battles, "force": force,
               "reading": _reading(tag, move, battles, force)}
        if name_of is not None:
            recent = sorted([*history.open.values(), *(b for b in history.closed
                             if (days_between(b.get("ended"), now) or 0) < WINDOW_DAYS)],
                            key=lambda b: b.get("ended") or b.get("last") or "")
            row["recent_battles"] = [
                {"state": name_of(b.get("state")), "terrain": b.get("terrain"),
                 "first_seen": b.get("first"), "ended": b.get("ended"),
                 "attacker": b["attacker"], "defender": b["defender"],
                 "attacker_divisions": b["att_div"], "defender_divisions": b["def_div"],
                 "yours": b["own_side"], "outcome": b.get("outcome", "ongoing")}
                for b in recent if b.get("front") == tag][-12:]
        out.append(row)
    out.sort(key=lambda r: (-r["force"]["foe_in_contact"], -r["battles"]["started"], r["enemy"]))
    return out


# --- words --------------------------------------------------------------------------------------


def _moved(m: dict) -> str:
    return f"+{m['gained']}/-{m['lost']} provinces" + (
        f" ({m['gained_states']}/{m['lost_states']} states)" if m["gained_states"] or m["lost_states"]
        else "")


def _movement_words(m: dict) -> str:
    if not m.get("known"):
        return "line: first look, no earlier save to compare with"
    last, window = m["since_last"], m["window"]
    text = f"line since the save of {m['last_save']}: {_moved(last)}; {window['days']} days: " \
           f"{_moved(window)}"
    if not (last["gained"] or last["lost"]) and m.get("unchanged_days"):
        text += f"; unchanged for {m['unchanged_days']} days"
    return text


def _battle_words(b: dict) -> str:
    mine, theirs = b["your_attacks"], b["their_attacks"]
    if not b["started"]:
        return f"battles ({b['period_days']} days): none seen"
    parts = []
    if mine["total"]:
        parts.append(f"your attacks {mine['attacker_won']} won, {mine['attacker_lost']} lost"
                     + (f", {mine['ongoing']} on now" if mine["ongoing"] else ""))
    if theirs["total"]:
        parts.append(f"their attacks {theirs['attacker_won']} won, {theirs['attacker_lost']} held"
                     + (f", {theirs['ongoing']} on now" if theirs["ongoing"] else ""))
    piece = mine["piecemeal"] + theirs["piecemeal"]
    if piece:
        parts.append(f"{piece} piecemeal (attacker under {PIECEMEAL:.0%} of the defender)")
    return f"battles ({b['period_days']} days): " + "; ".join(parts)


def _force_words(f: dict) -> str:
    text = f"in contact: yours {f['own_in_contact']} against {f['foe_in_contact']} ({f['ratio']})"
    if f["armour_share"] is not None:
        text += f", {f['armour_share']:.0%} armour"
    if "org_average" in f:
        text += f", org {f['org_average']:.0f}"
    bits = [f"{f[k]} {label}" for k, label in (("low_organisation", "low org"),
                                               ("short_of_supply", "short of supply"),
                                               ("encircled", "encircled")) if f.get(k)]
    return text + (", " + ", ".join(bits) if bits else "")


def brief_lines(rows: list[dict], limit: int = MAX_FRONTS) -> list[str]:
    if not rows:
        return []
    lines = ["  Front report (movement from the saves read so far; inspect_world view=fronts):"]
    for r in rows[:limit]:
        lines.append(f"  * Front vs {r['enemy']}: {_movement_words(r['movement'])}; "
                     f"{_battle_words(r['battles'])}")
        lines.append(f"    {_force_words(r['force'])}")
        if r["reading"]:
            lines.append(f"    {r['reading']['kind'].upper()}: {r['reading']['brief']}. "
                         f"Try: {r['reading']['brief_tools']}.")
    if len(rows) > limit:
        lines.append(f"  (+{len(rows) - limit} more fronts: inspect_world view=fronts)")
    return lines


def view(rows: list[dict], history: FrontHistory | None, limit: int = 60) -> dict:
    return {"view": "fronts", "count": len(rows),
            "history_from": history.snapshots[0]["date"] if history and history.snapshots else None,
            "saves_read": len(history.snapshots) if history else 0,
            "note": "movement is the provinces of the enemy's land you hold and of yours it holds, "
                    "compared between the saves read; battles over the period are the ones seen in "
                    "those saves (their winner is who held the province afterwards); in contact "
                    "means your divisions on or next to a province where an enemy division stands; "
                    "reading (stuck or losing) is computed from these numbers, with the tools that "
                    "address it",
            "fronts": rows[:limit]}
