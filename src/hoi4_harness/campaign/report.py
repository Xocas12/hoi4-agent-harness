"""The report of a campaign run: what happened, what it cost, and who did it.

``hoi4-harness campaign-report --run-dir runs/x`` reads the files a campaign leaves
(``transcript.jsonl``, ``campaign_progress.jsonl``, ``campaign_events.jsonl``,
``campaign.log``) and writes ``campaign-report.md``. It reads only what is on disk, so it
works on a run that crashed or is still going, and says so when the run has no end record.

It is honest about attribution, which is the point of the experiment: the game's AI
fights the wars and the model only points it, so the report separates what the model
asked for (a directive, a posture, a decision, with the model's own stated reason) from
what happened in the game, and says plainly which wars no model directive preceded.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .progress import EVENTS_FILE, PROGRESS_FILE
from .runner import CAMPAIGN_LOG, load_state, resume_command
from .saves import load_harness_saves

REPORT_FILE = "campaign-report.md"
#: Actions that are bookkeeping, not decisions about the war.
QUIET_ACTIONS = ("advance_time",)
RAMP = "_.-:=+*#"
MAX_DECISIONS = 40
REASON_CHARS = 420


def _read_jsonl(path: Path) -> tuple[list[dict], bool]:
    """Records of a JSONL file, and whether its last line was cut off mid-write."""
    records: list[dict] = []
    if not path.exists():
        return records, False
    truncated = False
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                truncated = True
                break
    return records, truncated


@dataclass
class Decision:
    date: str
    wake: str
    calls: list[dict]
    reason: str
    results: dict[str, dict] = field(default_factory=dict)


@dataclass
class RunData:
    run_dir: Path
    transcript: list[dict]
    progress: list[dict]
    events: list[dict]
    log_lines: list[str]
    transcript_truncated: bool = False

    @classmethod
    def load(cls, run_dir: Path) -> RunData:
        run_dir = Path(run_dir)
        transcript, cut = _read_jsonl(run_dir / "transcript.jsonl")
        progress, _ = _read_jsonl(run_dir / PROGRESS_FILE)
        events, _ = _read_jsonl(run_dir / EVENTS_FILE)
        log_path = run_dir / CAMPAIGN_LOG
        lines = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
        if not (transcript or progress or events or lines):
            raise FileNotFoundError(
                f"{run_dir} holds no campaign files (transcript.jsonl, {PROGRESS_FILE}, "
                f"{EVENTS_FILE}, {CAMPAIGN_LOG})")
        return cls(run_dir, transcript, progress, events, lines, cut)

    # --- the transcript ---------------------------------------------------------

    def kind(self, name: str) -> list[dict]:
        return [r for r in self.transcript if r.get("kind") == name]

    @property
    def player(self) -> str:
        for row in self.progress:
            if row.get("country") and row["country"] != "___":
                return row["country"]
        return "the player"

    def decisions(self) -> list[Decision]:
        """The model's calls, each with the date it was woken, its reason and the result."""
        out: list[Decision] = []
        date, wake = "", ""
        open_: Decision | None = None
        for rec in self.transcript:
            kind = rec.get("kind")
            if kind == "observe":
                date, wake = rec.get("date", ""), rec.get("wake_reason", "")
            elif kind == "llm":
                calls = [c for c in rec.get("tool_calls", []) if c.get("name") not in QUIET_ACTIONS]
                open_ = Decision(date, wake, calls, (rec.get("text") or "").strip())
                if calls or open_.reason:
                    out.append(open_)
            elif kind == "actions" and out:
                for result in rec.get("results", []):
                    out[-1].results[str(result.get("call_id"))] = result
        return out

    def advance_requests(self) -> list[int]:
        days = []
        for rec in self.kind("llm"):
            for call in rec.get("tool_calls", []):
                if call.get("name") == "advance_time":
                    try:
                        days.append(int(call["arguments"]["days"]))
                    except (KeyError, TypeError, ValueError):
                        pass
        return days

    def usage_totals(self) -> dict:
        """Tokens and calls from the ``llm`` records: right even for a run with no end record."""
        total = Counter()
        for rec in self.kind("llm"):
            for key, value in (rec.get("usage") or {}).items():
                total[key] += value or 0
            total["calls"] += 1
        return dict(total)

    def run_end(self) -> dict | None:
        ends = self.kind("run_end")
        return ends[-1] if ends else None


# --- rendering ---------------------------------------------------------------


def sparkline(values: list[float | None], width: int = 60) -> str:
    """An ASCII sparkline: ``_`` the lowest value in view, ``#`` the highest."""
    clean = [v for v in values if v is not None]
    if not clean:
        return ""
    if len(values) > width:
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width)]
    lo, hi = min(clean), max(clean)
    out = []
    for v in values:
        if v is None:
            out.append(" ")
        elif hi == lo:
            out.append(RAMP[0])
        else:
            out.append(RAMP[min(len(RAMP) - 1, int((v - lo) / (hi - lo) * len(RAMP)))])
    return "".join(out)


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c).replace("|", "/") for c in row) + " |" for row in rows]
    return lines


def _num(value, fmt: str = "{}") -> str:
    return "?" if value is None else fmt.format(value)


def _share(row: dict | None) -> str:
    return "?" if not row or row.get("state_share") is None else f"{row['state_share'] * 100:.1f}%"


def _first_with(rows: list[dict], key: str) -> dict | None:
    return next((r for r in rows if r.get(key) is not None), None)


def _last_with(rows: list[dict], key: str) -> dict | None:
    return next((r for r in reversed(rows) if r.get(key) is not None), None)


def _how_it_ended(data: RunData) -> list[str]:
    end = data.run_end()
    resumes = data.kind("resumed")
    lines: list[str] = []
    if end is None:
        last = data.progress[-1]["date"] if data.progress else "?"
        lines.append(f"**No end record.** The run was interrupted: the last turn the progress file "
                     f"has is at {last}. Resume it with `play --campaign --resume` (see docs/campaign.md).")
    else:
        reason = end.get("stopped_reason")
        if reason:
            lines.append(f"**Stopped early** at {end.get('end_date')}: {reason}.")
        elif end.get("reached_end_date"):
            lines.append(f"**Reached the end date**, {end.get('end_date')}.")
        else:
            lines.append(f"**Ran out of turns** at {end.get('end_date')} (the end date was not reached).")
    for rec in data.kind("game_lost"):
        lines.append(f"- The game was lost at {rec.get('date') or '?'}: {rec.get('reason')}.")
    for rec in data.kind("budget_stop"):
        lines.append(f"- The budget stopped the run at {rec.get('date')}: {rec.get('reason')}.")
    for rec in data.kind("crash"):
        lines.append(f"- The harness crashed: {rec.get('error')}.")
    if resumes:
        lines.append(f"- Resumed {len(resumes)} time(s) after an interruption.")
    if data.transcript_truncated:
        lines.append("- The transcript's last line was cut off mid-write (a hard kill).")
    return lines


def _where_to_resume(data: RunData) -> list[str]:
    """Where the run stopped and the save and command to pick it up from; empty when it
    reached the end date."""
    end = data.run_end()
    if end is not None and end.get("reached_end_date") and not end.get("stopped_reason"):
        return []
    state = load_state(data.run_dir)
    last = data.progress[-1]["date"] if data.progress else None
    stopped_at = (end or {}).get("end_date") or state.get("stopped_at") or last or "?"
    reason = (end or {}).get("stopped_reason") or state.get("stopped_reason")
    out = [f"The run stopped at **{stopped_at}**"
           + (f": {reason}." if reason else " with no recorded reason (it was interrupted).")]
    save_dir = Path(state["save_dir"]) if state.get("save_dir") else None
    saves = load_harness_saves(data.run_dir)["saves"]
    on_disk = [s for s in saves if save_dir is not None and (save_dir / s["name"]).exists()]
    if on_disk:
        newest = on_disk[-1]
        out.append(f"Newest recovery save the harness took: `{newest['name']}` "
                   f"(game date {newest.get('date') or '?'}) in `{save_dir}`.")
        out.append("Load it in the game (Load game), pause, bring the game window to the front, "
                   "then continue the run with:")
    else:
        out.append("No recovery save of this run is on disk. If the game is still open, pause it and "
                   "bring it to the front; otherwise start from the newest save you have. Then:")
    out += ["", "```", resume_command(data.run_dir, state.get("profile")), "```"]
    return out


def _rule_label(data: RunData) -> str:
    """The Historical AI Focuses rule the run's first recovery save read."""
    state = load_state(data.run_dir)
    if "historical_ai" not in state:
        return "not recorded (no recovery save was read)"
    value = state["historical_ai"]
    return "unknown (the save does not say)" if value is None else "on" if value else "off"


def _summary(data: RunData) -> list[str]:
    rows = data.progress
    first, last = _first_with(rows, "owned_states"), _last_with(rows, "owned_states")
    run_end = data.run_end() or {}
    start = rows[0]["date"] if rows else "?"
    end = run_end.get("end_date") or (rows[-1]["date"] if rows else "?")
    declared = [e for e in data.events if e["kind"] == "war_declared"]
    caps = [e for e in data.events if e["kind"] == "capitulation"]
    usage = data.usage_totals()
    usd = (run_end.get("spend") or {}).get("usd")
    if usd is None and rows:
        usd = rows[-1].get("usd")
    rows_out = [
        ["Country", data.player],
        ["Period", f"{start} to {end}"],
        ["States held", f"{_num(first and first['owned_states'])} to {_num(last and last['owned_states'])}"
                        f" ({_share(first)} to {_share(last)} of the world)"],
        ["Independent countries", f"{_num(first and first.get('countries_independent'))} to "
                                  f"{_num(last and last.get('countries_independent'))}"],
        ["Historical AI focuses", _rule_label(data)],
        ["Wars declared", str(len(declared))],
        ["Capitulations", str(len(caps))],
        *_own_capitulation_row(data),
        ["Model wakes (calls)", _num(usage.get("calls"))],
        ["Tokens", f"{usage.get('input_tokens', 0):,} in "
                   f"({usage.get('cached_input_tokens', 0):,} cached) / "
                   f"{usage.get('output_tokens', 0):,} out"],
        ["Spend", _num(usd, "${:.2f}")],
    ]
    return _table(["", ""], rows_out)


def _own_capitulation_row(data: RunData) -> list[list[str]]:
    """A summary row when the player's own country capitulated and the run played on."""
    cap = load_state(data.run_dir).get("player_capitulation")
    if not isinstance(cap, dict) or not cap.get("date"):
        return []
    held = _last_with(data.progress, "owned_states")
    text = f"{cap.get('tag') or data.player} capitulated on {cap['date']}" + (
        f" to {cap['to']}" if cap.get("to") else "")
    if held is not None:
        text += f"; {_num(held['owned_states'])} states held at {held['date']}"
    return [["Own capitulation", text]]


def _war_timeline(data: RunData) -> list[str]:
    if not data.events:
        return ["No war events were recorded."]
    rows = [[e["date"], e["kind"].replace("_", " "), e.get("text", "")] for e in data.events
            if e["kind"] != "harness_marked"]
    return _table(["Date", "Event", "What"], rows) or ["No war events were recorded."]


def _territory(data: RunData, rows_wanted: int = 16) -> list[str]:
    rows = [r for r in data.progress if r.get("owned_states") is not None]
    if not rows:
        return ["No territory numbers were recorded (the game's weekly `camp` line never arrived)."]
    pick = rows if len(rows) <= rows_wanted else [rows[int(i * (len(rows) - 1) / (rows_wanted - 1))]
                                                  for i in range(rows_wanted)]
    out = _table(
        ["Date", "States", "Share", "Independent left", "At war", "Divisions", "Spend"],
        [[r["date"], _num(r.get("owned_states")), _share(r), _num(r.get("countries_independent")),
          "/".join(r.get("wars") or []) or "-", _num(r.get("divisions")), _num(r.get("usd"), "${:.2f}")]
         for r in pick])
    states = [r.get("owned_states") for r in data.progress]
    indep = [r.get("countries_independent") for r in data.progress]
    out += ["", "States held over the run (`_` low, `#` high):", "", "    " + sparkline(states),
            "", "Independent countries left (same scale, its own range):", "",
            "    " + sparkline(indep)]
    return out


def _call_text(call: dict) -> str:
    args = call.get("arguments") or {}
    inner = ", ".join(f"{k}={v}" for k, v in args.items())
    return f"{call.get('name')}({inner})"


def _why(result: dict) -> str:
    """The game's or the harness's reason, from a tool result's JSON content."""
    try:
        message = json.loads(result.get("content", "{}")).get("message", "")
    except (TypeError, ValueError):
        message = ""
    message = " ".join(str(message).split())
    return f" ({message[:160]})" if message else ""


def _decisions(data: RunData) -> list[str]:
    decisions = [d for d in data.decisions() if d.calls]
    if not decisions:
        return ["The model made no calls other than advance_time."]
    out = []
    shown = decisions if len(decisions) <= MAX_DECISIONS else decisions[:MAX_DECISIONS]
    for d in shown:
        out.append(f"**{d.date}** (woken: {d.wake or '?'})")
        for call in d.calls:
            result = d.results.get(str(call.get("id")))
            verdict = "" if result is None else (
                " - REFUSED" + _why(result) if result.get("is_error") else " - ok")
            out.append(f"- `{_call_text(call)}`{verdict}")
        if d.reason:
            text = " ".join(d.reason.split())
            out.append(f"  - Reason: \"{text[:REASON_CHARS]}{'...' if len(text) > REASON_CHARS else ''}\"")
        out.append("")
    if len(decisions) > MAX_DECISIONS:
        out.append(f"... and {len(decisions) - MAX_DECISIONS} more wakes with calls "
                   f"(the whole record is `transcript.jsonl`).")
    return out


def _attribution(data: RunData) -> list[str]:
    """Who did what: the model's directives against the wars that happened."""
    directed: dict[str, str] = {}
    counts: Counter[str] = Counter()
    for d in data.decisions():
        for call in d.calls:
            counts[call["name"]] += 1
            args = call.get("arguments") or {}
            if call["name"] == "set_ai_directive" and args.get("directive") == "invade":
                directed.setdefault(str(args.get("target")), d.date)
    player = data.player
    lines: list[str] = []
    wars = [e for e in data.events if e["kind"] == "war_declared"]
    with_model, without = [], []
    for event in wars:
        enemy = _enemy_of(event.get("text", ""), player)
        asked = directed.get(enemy)
        if asked and asked <= event["date"]:
            with_model.append(f"{enemy} (invade raised {asked}, war {event['date']})")
        else:
            by = _declarer(event.get("text", ""))
            note = "declared on us" if by and by != player else "the AI's own choice"
            without.append(f"{enemy} ({event['date']}, {note})")
    lines.append(f"- Wars declared with a prior model `invade` directive against the enemy: "
                 f"{len(with_model)}" + (": " + "; ".join(with_model) if with_model else "") + ".")
    lines.append(f"- Wars with no such directive first: {len(without)}"
                 + (": " + "; ".join(without) if without else "") + ".")
    if counts:
        lines.append("- What the model did, by action: "
                     + ", ".join(f"{name} x{n}" for name, n in counts.most_common()) + ".")
    asked = data.advance_requests()
    if asked:
        lines.append(f"- It chose its own review interval {len(asked)} times, "
                     f"{min(asked)} to {max(asked)} days, mean {sum(asked) / len(asked):.0f}.")
    return lines


def _enemy_of(text: str, player: str) -> str:
    """``war declared: POL on GER`` -> POL (whoever is not the player)."""
    body = text.split(":", 1)[-1]
    tags = [w.strip(",.()") for w in body.replace(" on ", " ").split() if w.strip(",.()").isupper()]
    others = [t for t in tags if t != player]
    return others[0] if others else (tags[0] if tags else "?")


def _declarer(text: str) -> str | None:
    body = text.split(":", 1)[-1].strip()
    first = body.split(" ")[0] if body else ""
    return first if first.isupper() and len(first) == 3 else None


def _spend(data: RunData) -> list[str]:
    usage = data.usage_totals()
    rows = [r for r in data.progress if r.get("usd") is not None]
    out = []
    calls = usage.get("calls", 0)
    if rows:
        total = rows[-1]["usd"]
        out.append(f"{calls} model calls, ${total:.2f} priced by the harness's own budget "
                   f"({total / calls:.3f} per call)." if calls else f"${total:.2f}.")
        by_year: dict[str, float] = {}
        prev_year, base = None, 0.0
        for r in rows:
            year = r["date"][:4]
            if prev_year is not None and year != prev_year:
                by_year[prev_year] = r["usd"] - base
                base = r["usd"]
            prev_year = year
        if prev_year is not None:
            by_year[prev_year] = rows[-1]["usd"] - base
        out += ["", *_table(["Year", "Spend"], [[y, f"${v:.2f}"] for y, v in by_year.items()])]
    else:
        out.append("No spend samples were recorded.")
    return out


def render_report(data: RunData) -> str:
    """The markdown report of a run."""
    out = [f"# Campaign report: {data.run_dir.name}", ""]
    out += ["## Summary", "", *_summary(data), ""]
    out += ["## How the run ended", "", *_how_it_ended(data), ""]
    resume = _where_to_resume(data)
    if resume:
        out += ["## Where to resume", "", *resume, ""]
    out += ["## Wars", "", *_war_timeline(data), ""]
    out += ["## Territory over time", "", *_territory(data), ""]
    out += ["## What the model decided, and why", "",
            "The reasons are the model's own words from its transcript, not the harness's.", "",
            *_decisions(data), ""]
    out += ["## Spend and tokens", "", *_spend(data), ""]
    out += ["## What the model did and what the game AI did", "", *_attribution(data), "",
            "The division of labour is fixed by the mode: the game's AI chose the focus and the "
            "research, built and produced, designed and deployed divisions, declared the wars and "
            "fought them, and the model could only point it (directives, postures, decisions, "
            "advisors) and set its own review interval. So a conquered state is the AI's work "
            "after the model's choice of target, and a war with no model directive before it is "
            "the AI acting on its own. This report cannot say that a directive *caused* a war, "
            "only that one stood first; the game's AI may act on a directive late, or not at all.",
            "",
            "Territory and country counts are the mod's weekly `camp` line, as of that week. "
            "Wars come from the daily `war` lines. Spend is the harness's own pricing of the "
            "tokens the provider reported, not an invoice.", ""]
    return "\n".join(out)


def write_report(run_dir: Path, out: Path | None = None) -> tuple[str, Path]:
    data = RunData.load(run_dir)
    text = render_report(data)
    path = out or (Path(run_dir) / REPORT_FILE)
    path.write_text(text, encoding="utf-8", newline="\n")
    return text, path
