"""Reconcile the harness's own token log against a provider's usage export (#23).

``hoi4-harness reconcile TRANSCRIPT EXPORT`` reads two things and compares them
per model:

* the harness side: the ``llm`` records of a run's ``transcript.jsonl``, each of
  which carries the model and the neutral ``Usage`` (``input_tokens`` includes
  cache reads and writes; see ``agent/llm/base.py``);
* the provider side: a usage export, in one of three shapes (below).

Both sides are folded into the same four numbers per model (input, cache read,
cache write, output) so a difference means a real difference, not two meanings of
"input". Cost is the harness's own pricing (``BudgetGuard.price``) applied to each
side's tokens with the rates you pass, and, when the export carries a billed
amount, that amount too.

Export formats
--------------
``anthropic`` (JSON): the response of ``GET /v1/organizations/usage_report/messages``,
one page or a list of pages. Per result: ``model`` (group by model, or the row has
none), ``uncached_input_tokens``, ``cache_read_input_tokens``,
``cache_creation.ephemeral_5m_input_tokens`` + ``ephemeral_1h_input_tokens``,
``output_tokens``. Fields checked against
https://platform.claude.com/docs/en/api/beta/organization/usage_report/retrieve_messages
(Admin API key needed; the Console's Usage page shows the same numbers).

``openai`` (JSON): the response of ``GET /v1/organization/usage/completions``,
``data[].results[]`` with ``model``, ``input_tokens`` (including cached),
``input_cached_tokens``, ``output_tokens``, ``num_model_requests``. NOT verified
against the docs: the reference page refused automated fetches when this was
written. Treat the field names as a best recollection until one real export has
been run through it.

``csv``: the format to use when you copy numbers out of a console by hand. One
header row, then one row per model; extra columns are ignored::

    model,uncached_input_tokens,cache_read_tokens,cache_write_tokens,output_tokens,cost_usd

``cache_read_tokens``, ``cache_write_tokens`` and ``cost_usd`` may be left out or
blank. Optional ``date`` (``YYYY-MM-DD``, UTC) and ``provider`` columns let ``--by day``
split the export; the JSON formats carry their own days. ``uncached_input_tokens`` excludes cache reads and writes (the Anthropic
meaning); add them to ``input`` yourself if your console reports one total.
Optional ``requests`` is the call count.

I do not know of a documented CSV the Anthropic or OpenAI consoles export, so the
CSV above is this tool's own, not theirs.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..agent.budget import BudgetGuard
from ..agent.llm.base import Usage
from ..config import BudgetConfig

DEFAULT_TOLERANCE = 0.03


@dataclass
class Totals:
    calls: int = 0
    usage: Usage = field(default_factory=Usage)
    billed_usd: float | None = None     # only an export can carry this


class ReconcileError(ValueError):
    pass


# --- reading -----------------------------------------------------------------------------

Key = tuple[str, str, str]      # (provider, model, UTC day "YYYY-MM-DD", "" when unknown)
Keyed = dict[Key, Totals]


def _day_of_epoch(value) -> str:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _day_of_text(value) -> str:
    """``2026-11-16T01:00:00Z`` or ``2026-11-16`` -> ``2026-11-16``; anything else -> ``""``."""
    text = str(value or "").strip()
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else ""


def _add_k(out: Keyed, key: Key, usage: Usage, calls: int = 0, billed: float | None = None) -> None:
    total = out.setdefault(key, Totals())
    total.calls += calls
    total.usage = total.usage + usage
    if billed is not None:
        total.billed_usd = (total.billed_usd or 0.0) + billed


def fold(keyed: Keyed) -> dict[str, Totals]:
    """The per-model totals: provider and day summed away."""
    out: dict[str, Totals] = {}
    for (_, model, _), total in keyed.items():
        _add(out, model, total.usage, total.calls, total.billed_usd)
    return out


def read_transcript_keyed(path: str | Path) -> Keyed:
    """Totals per (provider, model, UTC day) from the ``llm`` records of a transcript.

    The day is the record's own wall-clock ``t``; ``provider`` is missing from transcripts
    written before it was recorded and reads as ``""``."""
    out: Keyed = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue                     # a run that died mid-write leaves a torn last line
        if record.get("kind") != "llm":
            continue
        usage = record.get("usage") or {}
        _add_k(out, (str(record.get("provider") or ""), str(record.get("model", "")),
                     _day_of_epoch(record.get("t"))),
               Usage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
                     int(usage.get("cached_input_tokens", 0)),
                     int(usage.get("cache_write_input_tokens", 0))), calls=1)
    return out


def read_transcript(path: str | Path) -> dict[str, Totals]:
    """Per-model totals from the ``llm`` records of a transcript."""
    return fold(read_transcript_keyed(path))


def _add(out: dict[str, Totals], model: str, usage: Usage, calls: int = 0,
         billed: float | None = None) -> None:
    total = out.setdefault(model, Totals())
    total.calls += calls
    total.usage = total.usage + usage
    if billed is not None:
        total.billed_usd = (total.billed_usd or 0.0) + billed


def _int(value) -> int:
    try:
        return int(float(value)) if value not in (None, "") else 0
    except (TypeError, ValueError):
        raise ReconcileError(f"not a number: {value!r}") from None


def _pages(data) -> list[dict]:
    pages = data if isinstance(data, list) else [data]
    if not all(isinstance(p, dict) and isinstance(p.get("data"), list) for p in pages):
        raise ReconcileError("expected a usage report with a top-level 'data' list of buckets")
    return pages


def _buckets(data):
    for page in _pages(data):
        for bucket in page["data"]:
            yield bucket, bucket.get("results", [])


def _results(data):
    for _, results in _buckets(data):
        yield from results


def read_anthropic_keyed(data) -> Keyed:
    out: Keyed = {}
    for bucket, results in _buckets(data):
        day = _day_of_text(bucket.get("starting_at"))
        for row in results:
            if "uncached_input_tokens" not in row:
                raise ReconcileError("not an Anthropic usage_report result (no uncached_input_tokens)")
            created = row.get("cache_creation") or {}
            read = _int(row.get("cache_read_input_tokens"))
            written = (_int(created.get("ephemeral_5m_input_tokens"))
                       + _int(created.get("ephemeral_1h_input_tokens")))
            uncached = _int(row["uncached_input_tokens"])
            _add_k(out, ("anthropic", str(row.get("model") or ""), day),
                   Usage(uncached + read + written, _int(row.get("output_tokens")), read, written))
    return out


def read_openai_keyed(data) -> Keyed:
    out: Keyed = {}
    for bucket, results in _buckets(data):
        day = _day_of_epoch(bucket.get("start_time")) if bucket.get("start_time") else ""
        for row in results:
            cached = _int(row.get("input_cached_tokens"))
            _add_k(out, ("openai", str(row.get("model") or ""), day),
                   Usage(_int(row.get("input_tokens")), _int(row.get("output_tokens")), cached),
                   calls=_int(row.get("num_model_requests")))
    return out


def read_anthropic(data) -> dict[str, Totals]:
    return fold(read_anthropic_keyed(data))


def read_openai(data) -> dict[str, Totals]:
    return fold(read_openai_keyed(data))


def read_csv_keyed(text: str) -> Keyed:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ReconcileError("the CSV has no header row")
    columns = {name.strip().lower(): name for name in reader.fieldnames}
    for needed in ("model", "uncached_input_tokens", "output_tokens"):
        if needed not in columns:
            raise ReconcileError(f"the CSV needs a '{needed}' column; it has: {', '.join(reader.fieldnames)}")

    def get(row, name):
        key = columns.get(name)
        return row.get(key, "") if key else ""

    out: Keyed = {}
    for row in reader:
        model = (get(row, "model") or "").strip()
        if not model:
            continue
        raw_day = (get(row, "date") or get(row, "day") or "").strip()
        if raw_day and not _day_of_text(raw_day):
            raise ReconcileError(f"the CSV's date {raw_day!r} is not YYYY-MM-DD")
        read, written = _int(get(row, "cache_read_tokens")), _int(get(row, "cache_write_tokens"))
        cost = get(row, "cost_usd")
        _add_k(out, ((get(row, "provider") or "").strip().lower(), model, _day_of_text(raw_day)),
               Usage(_int(get(row, "uncached_input_tokens")) + read + written,
                     _int(get(row, "output_tokens")), read, written),
               calls=_int(get(row, "requests")), billed=float(cost) if cost not in ("", None) else None)
    return out


def read_csv(text: str) -> dict[str, Totals]:
    return fold(read_csv_keyed(text))


def read_export_keyed(path: str | Path, fmt: str = "auto") -> Keyed:
    text = Path(path).read_text(encoding="utf-8-sig")
    if fmt == "auto":
        fmt = "json" if text.lstrip()[:1] in "[{" else "csv"
    if fmt == "csv":
        return read_csv_keyed(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReconcileError(f"{path} is not valid JSON: {exc}") from None
    if fmt == "json":
        first = next(_results(data), None)
        if first is None:
            raise ReconcileError("the export has no usage rows")
        fmt = "anthropic" if "uncached_input_tokens" in first else "openai"
    if fmt == "anthropic":
        return read_anthropic_keyed(data)
    if fmt == "openai":
        return read_openai_keyed(data)
    raise ReconcileError(f"unknown format {fmt!r} (auto, csv, anthropic, openai)")


def read_export(path: str | Path, fmt: str = "auto") -> dict[str, Totals]:
    return fold(read_export_keyed(path, fmt))


# --- rates ---------------------------------------------------------------------------------

def parse_rate(spec: str) -> tuple[str, BudgetConfig]:
    """``MODEL:input=5,output=25[,cached=0.5][,write=6.25]`` (dollars per million tokens).
    ``*`` as the model applies to every model without its own rate."""
    model, _, body = spec.partition(":")
    if not model or not body:
        raise ReconcileError(f"rate {spec!r}: expected MODEL:input=..,output=..[,cached=..][,write=..]")
    values: dict[str, float] = {}
    for part in body.split(","):
        key, _, number = part.partition("=")
        if key.strip() not in {"input", "output", "cached", "write"}:
            raise ReconcileError(f"rate {spec!r}: unknown key {key!r}")
        try:
            values[key.strip()] = float(number)
        except ValueError:
            raise ReconcileError(f"rate {spec!r}: {number!r} is not a number") from None
    if "input" not in values or "output" not in values:
        raise ReconcileError(f"rate {spec!r}: input and output are both required")
    return model.strip(), BudgetConfig(
        usd_per_m_input=values["input"], usd_per_m_output=values["output"],
        usd_per_m_cached_input=values.get("cached"), usd_per_m_cache_write=values.get("write"))


# --- comparing -------------------------------------------------------------------------------

METRICS = ("input", "cache read", "cache write", "output")


def _tokens(usage: Usage) -> tuple[int, int, int, int]:
    return usage.input_tokens, usage.cached_input_tokens, usage.cache_write_input_tokens, usage.output_tokens


@dataclass
class Row:
    model: str
    harness: Totals
    export: Totals
    harness_usd: float | None = None
    export_usd: float | None = None     # export's own billed amount, else its tokens priced
    export_usd_billed: bool = False
    tolerance: float = DEFAULT_TOLERANCE

    def delta(self, harness: float, export: float) -> tuple[float, float | None]:
        """(difference, relative to the export). The export is the side billed, so it is the base."""
        diff = harness - export
        return diff, (diff / export if export else (0.0 if not harness else None))

    def within(self, harness: float, export: float) -> bool:
        diff, rel = self.delta(harness, export)
        return diff == 0 or (rel is not None and abs(rel) <= self.tolerance)

    def failures(self) -> list[str]:
        pairs = zip(METRICS, _tokens(self.harness.usage), _tokens(self.export.usage), strict=True)
        bad = [name for name, h, e in pairs if not self.within(h, e)]
        if self.harness_usd is not None and self.export_usd is not None \
                and not self.within(self.harness_usd, self.export_usd):
            bad.append("cost")
        return bad


@dataclass
class Report:
    rows: list[Row]
    tolerance: float
    priced: bool

    @property
    def ok(self) -> bool:
        return all(not r.failures() for r in self.rows)


def _merge_aliases(harness: dict[str, Totals], export: dict[str, Totals]) -> dict[str, Totals]:
    """A harness model ``claude-opus-5-5`` against an export ``claude-opus-5-5-20261101``:
    one unambiguous prefix match is the same model. Anything else stays apart, and shows
    as a model present on one side only."""
    export = dict(export)
    for name in harness:
        if name in export:
            continue
        near = [e for e in export if e.startswith(name + "-") or name.startswith(e + "-")]
        if len(near) == 1 and near[0] not in harness:
            export[name] = export.pop(near[0])
    return export


def reconcile(harness: dict[str, Totals], export: dict[str, Totals],
              rates: dict[str, BudgetConfig] | None = None,
              tolerance: float = DEFAULT_TOLERANCE) -> Report:
    rates = rates or {}
    export = _merge_aliases(harness, export)
    rows = []
    for model in sorted(set(harness) | set(export)):
        h, e = harness.get(model, Totals()), export.get(model, Totals())
        row = Row(model, h, e, tolerance=tolerance)
        rate = rates.get(model) or rates.get("*")
        if rate is not None:
            guard = BudgetGuard(rate)
            row.harness_usd = guard.price(h.usage)
            if e.billed_usd is not None:
                row.export_usd, row.export_usd_billed = e.billed_usd, True
            else:
                row.export_usd = guard.price(e.usage)
        rows.append(row)
    return Report(rows, tolerance, priced=bool(rates))


def render(report: Report) -> str:
    lines = [f"tolerance {report.tolerance:.1%} of the export's figure (the export is the side billed)"]
    for row in report.rows:
        side = "harness only" if not row.export.calls and not any(_tokens(row.export.usage)) else (
            "export only" if not row.harness.calls else "")
        lines.append("")
        lines.append(f"{row.model or '(no model)'}" + (f"  [{side}: no counterpart]" if side else ""))
        lines.append(f"  {'':<12}{'harness':>14}{'export':>14}{'diff':>12}{'diff %':>9}")
        for name, h, e in zip(METRICS, _tokens(row.harness.usage), _tokens(row.export.usage), strict=True):
            lines.append(_line(row, name, h, e, f"{h:,}", f"{e:,}", f"{h - e:+,}"))
        if row.harness_usd is not None and row.export_usd is not None:
            label = "cost (billed)" if row.export_usd_billed else "cost (priced)"
            lines.append(_line(row, label, row.harness_usd, row.export_usd, f"${row.harness_usd:.4f}",
                               f"${row.export_usd:.4f}", f"{row.harness_usd - row.export_usd:+.4f}"))
        if row.harness.calls and row.export.calls:
            lines.append(f"  calls: harness {row.harness.calls:,}, export {row.export.calls:,}")
    if not report.priced:
        lines.append("")
        lines.append("no --rate given: tokens only, no cost comparison")
    lines.append("")
    bad = [f"{r.model}: {', '.join(r.failures())}" for r in report.rows if r.failures()]
    lines.append("WITHIN TOLERANCE" if report.ok else "OUTSIDE TOLERANCE -- " + "; ".join(bad))
    return "\n".join(lines)


def _line(row: Row, name: str, h: float, e: float, hs: str, es: str, ds: str) -> str:
    _, rel = row.delta(h, e)
    pct = "n/a" if rel is None else f"{rel:+.1%}"
    flag = "" if row.within(h, e) else "  <-- outside"
    return f"  {name:<12}{hs:>14}{es:>14}{ds:>12}{pct:>9}{flag}"


# --- per model and day ---------------------------------------------------------------------------


@dataclass
class DayRow:
    provider: str
    model: str
    day: str
    row: Row

    @property
    def label(self) -> str:
        return f"{self.provider + '/' if self.provider else ''}{self.model or '(no model)'}"


@dataclass
class DailyReport:
    days: list[DayRow]
    tolerance: float
    priced: bool
    #: Why the day split was not possible (a side without dates), or "".
    note: str = ""

    @property
    def ok(self) -> bool:
        return all(not d.row.failures() for d in self.days)


def _fold_days(keyed: Keyed) -> Keyed:
    out: Keyed = {}
    for (provider, model, _), total in keyed.items():
        _add_k(out, (provider, model, ""), total.usage, total.calls, total.billed_usd)
    return out


def reconcile_daily(harness: Keyed, export: Keyed, rates: dict[str, BudgetConfig] | None = None,
                    tolerance: float = DEFAULT_TOLERANCE) -> DailyReport:
    """The same comparison as ``reconcile``, one row per (model, UTC day).

    A side whose records carry no date (a CSV without a ``date`` column) cannot be split, so
    both sides fall back to one row per model and ``note`` says so. The provider is a label:
    taken from the transcript when it recorded one, else from the export."""
    rates = rates or {}
    note = ""
    if any(not k[2] for k in harness) or any(not k[2] for k in export):
        note = "a side has no dates, so the days are summed: one row per model"
        harness, export = _fold_days(harness), _fold_days(export)
    h_models, e_models = {m for _, m, _ in harness}, {m for _, m, _ in export}
    alias = {}                      # an export's dated model name -> the harness's name for it
    for name in h_models - e_models:
        near = [e for e in e_models if e.startswith(name + "-") or name.startswith(e + "-")]
        if len(near) == 1 and near[0] not in h_models:
            alias[near[0]] = name
    h_by = {(m, d): (p, t) for (p, m, d), t in harness.items()}
    e_by = {(alias.get(m, m), d): (p, t) for (p, m, d), t in export.items()}
    days = []
    for model, day in sorted(set(h_by) | set(e_by)):
        hp, h = h_by.get((model, day), ("", Totals()))
        ep, e = e_by.get((model, day), ("", Totals()))
        row = Row(model, h, e, tolerance=tolerance)
        rate = rates.get(model) or rates.get("*")
        if rate is not None:
            guard = BudgetGuard(rate)
            row.harness_usd = guard.price(h.usage)
            if e.billed_usd is not None:
                row.export_usd, row.export_usd_billed = e.billed_usd, True
            else:
                row.export_usd = guard.price(e.usage)
        days.append(DayRow(hp or ep, model, day, row))
    return DailyReport(days, tolerance, priced=bool(rates), note=note)


def _cell(row: Row, name: str, h: float, e: float, hs: str, es: str) -> str:
    _, rel = row.delta(h, e)
    pct = "n/a" if rel is None else f"{rel:+.1%}"
    flag = "" if row.within(h, e) else " <--"
    return f"{name} {hs}/{es} ({pct}){flag}"


def render_daily(report: DailyReport) -> str:
    lines = [f"tolerance {report.tolerance:.1%} of the export's figure (the export is the side "
             "billed); each cell is harness/export (difference)"]
    if report.note:
        lines.append(f"note: {report.note}")
    current = None
    for item in report.days:
        if item.label != current:
            current = item.label
            lines += ["", item.label]
        row = item.row
        no_export = not row.export.calls and not any(_tokens(row.export.usage))
        no_harness = not row.harness.calls and not any(_tokens(row.harness.usage))
        side = "  [harness only]" if no_export else "  [export only]" if no_harness else ""
        cells = [_cell(row, name, h, e, f"{h:,}", f"{e:,}")
                 for name, h, e in zip(METRICS, _tokens(row.harness.usage),
                                       _tokens(row.export.usage), strict=True)]
        if row.harness_usd is not None and row.export_usd is not None:
            cells.append(_cell(row, "cost" if row.export_usd_billed else "cost*", row.harness_usd,
                               row.export_usd, f"${row.harness_usd:.4f}", f"${row.export_usd:.4f}"))
        lines.append(f"  {item.day or 'all days':<10}  " + "  ".join(cells) + side)
    if not report.priced:
        lines += ["", "no --rate given: tokens only, no cost comparison"]
    elif any(d.row.export_usd is not None and not d.row.export_usd_billed for d in report.days):
        lines += ["", "cost* = the export's tokens priced at your rates (the export has no billed amount)"]
    lines.append("")
    bad = [f"{d.label} {d.day or 'all days'}: {', '.join(d.row.failures())}"
           for d in report.days if d.row.failures()]
    lines.append("WITHIN TOLERANCE" if report.ok else "OUTSIDE TOLERANCE -- " + "; ".join(bad))
    return "\n".join(lines)
