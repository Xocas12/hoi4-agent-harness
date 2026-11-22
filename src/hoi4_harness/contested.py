"""Measure what the game's AI did to the production lines the model added (issue #161, step 1).

With ``campaign_contested_production`` on, the model opens production lines through the console
while the game's AI keeps running the country. Each line the game confirmed (the marker logged
inside the guard) is appended to ``contested_lines.jsonl`` in the run's directory
(``adapters.console.CONTESTED_LINES_FILE``). This module reads that file and a series of text
saves and says, per line the model added: was it found in the saves, does it still exist, how many
factories it had over time, and what the AI changed (factories, equipment, removal), plus whether
the AI builds the model's design on lines of its own.

How a recorded line is found in a save: a text save gives every production line a stable id
(``military_lines = { id = { id = 25 type = 56 } ...``). A line is *new* when its id is not in the
last save made before the action (the baseline). The first later save holding a new, unclaimed
line of the recorded equipment (and of the recorded design, when there is one) is the model's line,
the one asking for the recorded number of factories first; its id is then followed through every
later save. With no save before the action there is no baseline, so the highest-numbered lines of
the equipment stand in for the new ones and the match is flagged ``baselined: false``.

Not read: the lines of any country but the player's, and general (construction) lines.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import savegame as sg
from .adapters.console import CONTESTED_LINES_FILE


@dataclass
class Line:
    id: int
    equipment: str
    variant_id: int | None
    variant: str | None
    factories: int
    active: int | None = None
    produced: float = 0.0
    efficiency: float = 0.0


@dataclass
class SaveLines:
    path: str
    date: str
    tag: str
    lines: dict[int, Line] = field(default_factory=dict)


def parse_save(text: str, path: str = "") -> SaveLines:
    """The player's military production lines in a save's text."""
    from .designers import read_variants

    head = sg._head(text)
    tag = head.get("player")
    date = sg.save_date(head.get("date", "")) if head.get("date") else None
    if not isinstance(tag, str) or not date:
        raise ValueError(f"{path or 'save'}: no date and player in its header (binary save?)")
    country = sg.country_block(text, tag) or {}
    names = sg._equipment_names_fast(text) or {}
    by_id = {v.id: v for v in read_variants(text, tag) if v.id is not None}
    production = country.get("production") if isinstance(country, dict) else None
    lines: dict[int, Line] = {}
    for raw in sg._as_list(production.get("military_lines") if isinstance(production, dict) else None):
        if not isinstance(raw, dict):
            continue
        ident = sg._ident(raw.get("id"))
        index = sg._ident(raw.get("equipment_variant_index"))
        if not ident.isdigit() or not index:
            continue
        active = sg._number(raw.get("active_factories"))
        requested = sg._number(raw.get("requested_factories"))
        if requested is None:
            requested = active if active is not None else sg._number(raw.get("queued_factories"))
        items = raw.get("factory_efficiencies")
        listed = items.get("_items") if isinstance(items, dict) else None
        values = [sg._number(v) for v in sg._as_list(listed)]
        running = [v for v in values[: int(active or 0)] if v is not None]
        variant = by_id.get(int(index)) if index.isdigit() else None
        lines[int(ident)] = Line(
            id=int(ident), equipment=names.get(index, f"variant:{index}"),
            variant_id=int(index) if index.isdigit() else None,
            variant=variant.name if variant is not None else None,
            factories=int(requested or 0), active=None if active is None else int(active),
            produced=round(sg._number(raw.get("produced")) or 0.0, 3),
            efficiency=round(sum(running) / len(running) / 100.0, 4) if running else 0.0)
    return SaveLines(path=str(path), date=date, tag=tag, lines=lines)


def read_saves(paths) -> list[SaveLines]:
    """Text saves from files and/or folders (``*.hoi4``), oldest first by their own date."""
    files: list[Path] = []
    for entry in paths:
        entry = Path(entry)
        files.extend(sorted(entry.glob("*.hoi4")) if entry.is_dir() else [entry])
    out = []
    for file in files:
        out.append(parse_save(sg.read_text(file), file.name))
    out.sort(key=lambda s: (s.date, s.path))
    return out


def load_records(run_dir) -> list[dict]:
    path = Path(run_dir) / CONTESTED_LINES_FILE
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.sort(key=lambda r: (r.get("date", ""), r.get("wall_time", 0)))
    return rows


def _matches(line: Line, record: dict) -> bool:
    if line.equipment != record["equipment"]:
        return False
    return record.get("variant") in (None, "") or line.variant == record["variant"]


def _snapshot(save: SaveLines, line: Line | None) -> dict:
    if line is None:
        return {"date": save.date, "save": save.path, "present": False}
    return {"date": save.date, "save": save.path, "present": True, "equipment": line.equipment,
            "variant": line.variant, "factories": line.factories, "active": line.active,
            "produced": line.produced, "efficiency": line.efficiency}


def _find(record: dict, saves: list[SaveLines], claimed: set[int]):
    """(baseline index or None, first save index holding the line, the Line, candidates)."""
    base = None
    for i, save in enumerate(saves):
        if save.date < record["date"]:
            base = i
    if base is None:
        return None, None, None, 0
    known = set(saves[base].lines)
    for i in range(base + 1, len(saves)):
        candidates = [ln for ln in saves[i].lines.values()
                      if ln.id not in known and ln.id not in claimed and _matches(ln, record)]
        if candidates:
            exact = [ln for ln in candidates if ln.factories == record["factories"]]
            pick = sorted(exact or candidates, key=lambda ln: ln.id)[0]
            return base, i, pick, len(candidates)
    return base, None, None, 0


def _changes(timeline: list[dict]) -> list[str]:
    out: list[str] = []
    prev = None
    for snap in timeline:
        if prev is not None:
            if prev["present"] and not snap["present"]:
                out.append(f"{snap['date']}: line removed")
            elif prev["present"] and snap["present"]:
                if snap["equipment"] != prev["equipment"] or snap["variant"] != prev["variant"]:
                    was = prev["variant"] or prev["equipment"]
                    now = snap["variant"] or snap["equipment"]
                    out.append(f"{snap['date']}: switched {was} -> {now}")
                if snap["factories"] != prev["factories"]:
                    out.append(f"{snap['date']}: factories {prev['factories']} -> {snap['factories']}")
        prev = snap
    return out


def _outcome(timeline: list[dict], changes: list[str]) -> str:
    if not timeline[-1]["present"]:
        return "deleted"
    if any("switched" in c for c in changes):
        return "switched"
    if any("factories" in c for c in changes):
        return "kept_factories_changed"
    return "kept_unchanged"


def report(records: list[dict], saves: list[SaveLines]) -> dict:
    """Per added line: found or not, the timeline over the saves, what changed, the outcome."""
    claimed: set[int] = set()
    lines = []
    for record in records:
        base, first, line, n = _find(record, saves, claimed)
        item = {"call_id": record.get("call_id"), "added": record["date"],
                "equipment": record["equipment"], "variant": record.get("variant"),
                "factories_asked": record["factories"]}
        if line is None:
            item.update({"found": False, "outcome": "not_found",
                         "note": ("no save before the action, so the model's line cannot be told "
                                  "from the AI's" if base is None else
                                  "no save after the action" if base == len(saves) - 1
                                  else "no new line of that equipment in any later save")})
            lines.append(item)
            continue
        claimed.add(line.id)
        timeline = [_snapshot(s, s.lines.get(line.id)) for s in saves[first:]]
        # Once gone, a line stays gone (an id is not reused).
        gone = False
        for snap in timeline:
            gone = gone or not snap["present"]
            if gone:
                snap.update(present=False)
        changes = _changes(timeline)
        peak = [s["factories"] for s in timeline if s["present"]]
        item.update({
            "found": True, "line_id": line.id, "baselined": True, "candidates": n,
            "first_seen": saves[first].date, "still_exists": timeline[-1]["present"],
            "factories_first": timeline[0]["factories"], "factories_last":
                timeline[-1].get("factories") if timeline[-1]["present"] else 0,
            "factories_max": max(peak), "factories_min": min(peak),
            "produced_last": next((s["produced"] for s in reversed(timeline) if s["present"]), 0.0),
            "changes": changes, "outcome": _outcome(timeline, changes), "timeline": timeline})
        lines.append(item)
    designs = {}
    for name in sorted({r["variant"] for r in records if r.get("variant")}):
        per_save = []
        for save in saves:
            theirs = [ln for ln in save.lines.values() if ln.variant == name]
            per_save.append({"date": save.date, "lines": len(theirs),
                             "factories": sum(ln.factories for ln in theirs),
                             "line_ids": [ln.id for ln in theirs]})
        own = {i["line_id"] for i in lines if i.get("found") and i["variant"] == name}
        other = [{"date": p["date"], "lines": len([i for i in p["line_ids"] if i not in own])}
                 for p in per_save]
        designs[name] = {"per_save": per_save, "ai_lines_with_it": other,
                         "ai_builds_it": any(o["lines"] for o in other)}
    counts: dict[str, int] = {}
    for item in lines:
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    return {"saves": [{"date": s.date, "save": s.path, "tag": s.tag, "lines": len(s.lines)}
                      for s in saves],
            "lines_added": len(records), "outcomes": counts, "lines": lines, "designs": designs}


def format_report(result: dict) -> str:
    out = [f"Saves read: {len(result['saves'])}"
           + (f" ({result['saves'][0]['date']} .. {result['saves'][-1]['date']})"
              if result["saves"] else ""),
           f"Lines the model added (confirmed by their marker): {result['lines_added']}",
           "Outcomes: " + (", ".join(f"{k} {v}" for k, v in sorted(result["outcomes"].items()))
                           or "none")]
    for item in result["lines"]:
        what = item["equipment"] + (f" [{item['variant']}]" if item.get("variant") else "")
        out.append("")
        out.append(f"- {what}, added {item['added']} asking {item['factories_asked']} factories: "
                   f"{item['outcome']}")
        if not item["found"]:
            out.append(f"    not found: {item['note']}")
            continue
        out.append(f"    line {item['line_id']}, first seen {item['first_seen']}"
                   + (f", {item['candidates']} candidates" if item["candidates"] > 1 else ""))
        out.append(f"    still exists: {'yes' if item['still_exists'] else 'no'}; factories "
                   f"{item['factories_first']} -> {item['factories_last']} "
                   f"(min {item['factories_min']}, max {item['factories_max']})")
        for change in item["changes"]:
            out.append(f"    AI: {change}")
    for name, info in result["designs"].items():
        out.append("")
        out.append(f"Design {name}: the AI builds it on lines of its own: "
                   f"{'yes' if info['ai_builds_it'] else 'no'}")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="What the game's AI did to the production lines the model added "
                    "(issue #161 step 1).")
    parser.add_argument("run_dir", help="the run's directory (holds contested_lines.jsonl)")
    parser.add_argument("--saves", nargs="+", required=True,
                        help="text saves, or folders of *.hoi4, in any order")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)
    records = load_records(args.run_dir)
    saves = read_saves(args.saves)
    result = report(records, saves)
    print(json.dumps(result, indent=2) if args.json else format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
