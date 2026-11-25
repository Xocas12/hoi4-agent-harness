"""Token accounting against a provider's usage export (#23), on fixture files only.

The fixtures hold the numbers of the one run priced so far (15 calls collapsed to 3
records: 9,334 uncached + 33,964 cache read + 2,426 cache write input, 3,838 output).
The rates below are illustrative, not any provider's price sheet.
"""

import json
from pathlib import Path

import pytest

from hoi4_harness import cli
from hoi4_harness.eval import reconcile as rc

FIX = Path(__file__).parent / "fixtures" / "reconcile"
RATE = ["claude-opus-5-5:input=5,output=25,cached=0.5,write=6.25"]
MODEL = "claude-opus-5-5"


def test_the_transcript_is_folded_into_the_neutral_four_numbers_and_a_torn_line_is_skipped():
    totals = rc.read_transcript(FIX / "transcript.jsonl")
    assert set(totals) == {MODEL}
    u = totals[MODEL].usage
    assert totals[MODEL].calls == 3
    assert (u.input_tokens, u.cached_input_tokens, u.cache_write_input_tokens, u.output_tokens) == (
        45724, 33964, 2426, 3838)


def test_the_anthropic_report_sums_buckets_and_counts_cache_creation_as_input():
    totals = rc.read_export(FIX / "anthropic_usage.json")
    u = totals[MODEL].usage
    assert (u.input_tokens, u.cached_input_tokens, u.cache_write_input_tokens, u.output_tokens) == (
        45724, 33964, 2426, 3838)


def test_the_csv_reads_the_same_numbers_and_carries_the_billed_cost():
    totals = rc.read_export(FIX / "usage.csv")
    assert totals[MODEL].usage.input_tokens == 45724
    assert totals[MODEL].billed_usd == pytest.approx(0.1750)


def test_a_matching_export_is_within_tolerance_in_every_format():
    harness = rc.read_transcript(FIX / "transcript.jsonl")
    for name in ("anthropic_usage.json", "usage.csv"):
        report = rc.reconcile(harness, rc.read_export(FIX / name),
                              dict(rc.parse_rate(r) for r in RATE))
        assert report.ok, (name, [r.failures() for r in report.rows])
        assert "WITHIN TOLERANCE" in rc.render(report)


def test_the_cost_is_priced_per_class_so_cached_input_is_not_billed_at_the_full_rate():
    harness = rc.read_transcript(FIX / "transcript.jsonl")
    report = rc.reconcile(harness, rc.read_export(FIX / "anthropic_usage.json"),
                          dict(rc.parse_rate(r) for r in RATE))
    row = report.rows[0]
    expected = (9334 * 5 + 33964 * 0.5 + 2426 * 6.25 + 3838 * 25) / 1e6
    assert row.harness_usd == pytest.approx(expected)
    assert row.harness_usd < 45724 * 5 / 1e6 + 3838 * 25 / 1e6     # not the all-at-input-rate figure


def test_a_drifted_export_is_flagged_per_metric_and_fails():
    harness = rc.read_transcript(FIX / "transcript.jsonl")
    report = rc.reconcile(harness, rc.read_export(FIX / "anthropic_usage_drift.json"),
                          dict(rc.parse_rate(r) for r in RATE))
    assert not report.ok
    row = report.rows[0]
    assert row.failures() == ["input", "cache read", "cost"]
    text = rc.render(report)
    assert "OUTSIDE TOLERANCE" in text and "<-- outside" in text


def test_the_tolerance_is_a_fraction_of_the_export():
    harness = rc.read_transcript(FIX / "transcript.jsonl")
    export = rc.read_export(FIX / "anthropic_usage.json")
    export[MODEL].usage.output_tokens = 3838 + 100            # the export is 2.6% over the harness
    assert rc.reconcile(harness, export, tolerance=0.03).ok
    assert not rc.reconcile(harness, export, tolerance=0.02).ok


def test_openai_input_includes_cached_and_has_no_cache_write():
    harness = rc.read_transcript(FIX / "transcript.jsonl")
    export = rc.read_export(FIX / "openai_usage.json")
    assert export[MODEL].usage.input_tokens == 45724 and export[MODEL].usage.cache_write_input_tokens == 0
    assert export[MODEL].calls == 3
    report = rc.reconcile(harness, export)
    assert report.rows[0].failures() == ["cache write"]  # harness wrote 2,426, OpenAI has no writes


def test_a_dated_export_model_matches_the_harness_model_by_prefix():
    harness = rc.read_transcript(FIX / "transcript.jsonl")
    data = json.loads((FIX / "anthropic_usage.json").read_text())
    for bucket in data["data"]:
        for row in bucket["results"]:
            row["model"] = MODEL + "-20261101"
    export = rc._merge_aliases(harness, rc.read_anthropic(data))
    assert set(export) == {MODEL}


def test_a_model_on_one_side_only_is_reported_not_dropped():
    harness = rc.read_transcript(FIX / "transcript.jsonl")
    export = rc.read_export(FIX / "usage.csv")
    export["other-model"] = rc.Totals()
    export["other-model"].usage.input_tokens = 10
    report = rc.reconcile(harness, export)
    assert not report.ok
    assert "other-model" in rc.render(report)


def test_zero_against_zero_is_fine_and_something_against_zero_is_not():
    row = rc.Row("m", rc.Totals(), rc.Totals())
    assert row.within(0, 0)
    assert not row.within(5, 0)


def test_bad_inputs_raise_a_readable_error():
    with pytest.raises(rc.ReconcileError, match="uncached_input_tokens"):
        rc.read_csv("model,output_tokens\nm,1\n")
    with pytest.raises(rc.ReconcileError, match="top-level 'data'"):
        rc.read_anthropic({"nope": []})
    with pytest.raises(rc.ReconcileError, match="input and output"):
        rc.parse_rate("m:input=5")
    with pytest.raises(rc.ReconcileError, match="unknown key"):
        rc.parse_rate("m:input=5,output=1,speed=2")


def test_the_command_exits_zero_when_within_and_one_when_not(capsys):
    t = str(FIX / "transcript.jsonl")
    assert cli.main(["reconcile", t, str(FIX / "anthropic_usage.json"), "--rate", RATE[0]]) == 0
    assert "WITHIN TOLERANCE" in capsys.readouterr().out
    assert cli.main(["reconcile", t, str(FIX / "anthropic_usage_drift.json")]) == 1
    out = capsys.readouterr().out
    assert "OUTSIDE TOLERANCE" in out and "no --rate given" in out


def test_the_command_refuses_a_missing_file_and_a_transcript_with_no_calls(tmp_path, capsys):
    assert cli.main(["reconcile", str(tmp_path / "nope.jsonl"), str(FIX / "usage.csv")]) == 2
    empty = tmp_path / "t.jsonl"
    empty.write_text('{"kind": "observe"}\n', encoding="utf-8")
    assert cli.main(["reconcile", str(empty), str(FIX / "usage.csv")]) == 2
    assert "no llm records" in capsys.readouterr().err


# --- per model and day -------------------------------------------------------------------------

DAYS = FIX / "transcript_days.jsonl"


def test_the_transcript_is_split_by_provider_model_and_utc_day():
    keyed = rc.read_transcript_keyed(DAYS)
    assert sorted(keyed) == [("anthropic", MODEL, "2026-11-16"), ("anthropic", MODEL, "2026-11-17"),
                             ("openai", "gpt-5", "2026-11-17")]
    first = keyed[("anthropic", MODEL, "2026-11-16")]
    assert first.calls == 2 and first.usage.input_tokens == 35000 and first.usage.output_tokens == 2700


def test_the_exports_carry_their_days_and_providers():
    anthropic = rc.read_export_keyed(FIX / "anthropic_usage_days.json")
    assert sorted(anthropic) == [("anthropic", MODEL, "2026-11-16"), ("anthropic", MODEL, "2026-11-17")]
    csv_keys = rc.read_export_keyed(FIX / "usage_days.csv")
    assert ("openai", "gpt-5", "2026-11-17") in csv_keys
    assert csv_keys[("anthropic", MODEL, "2026-11-16")].billed_usd == pytest.approx(0.11)
    openai = rc.read_export_keyed(FIX / "openai_usage.json")        # start_time 1 = 1970-01-01
    assert list(openai) == [("openai", MODEL, "1970-01-01")]


def test_the_folded_readers_still_give_per_model_totals():
    assert rc.fold(rc.read_transcript_keyed(DAYS))[MODEL].usage.input_tokens == 45724


def test_a_matching_day_by_day_export_is_within_tolerance():
    report = rc.reconcile_daily(rc.read_transcript_keyed(DAYS),
                                rc.read_export_keyed(FIX / "usage_days.csv"), tolerance=0.03)
    assert [(d.provider, d.model, d.day) for d in report.days] == [
        ("anthropic", MODEL, "2026-11-16"), ("anthropic", MODEL, "2026-11-17"),
        ("openai", "gpt-5", "2026-11-17")]
    assert report.ok, rc.render_daily(report)


def test_a_drift_on_one_day_is_flagged_on_that_day_only():
    report = rc.reconcile_daily(rc.read_transcript_keyed(DAYS),
                                rc.read_export_keyed(FIX / "anthropic_usage_days_drift.json"))
    assert not report.ok
    failing = {(d.model, d.day): d.row.failures() for d in report.days if d.row.failures()}
    assert failing[(MODEL, "2026-11-17")] == ["output"]
    assert (MODEL, "2026-11-16") not in failing
    text = rc.render_daily(report)
    assert "2026-11-17" in text and "<--" in text and "OUTSIDE TOLERANCE" in text
    assert "anthropic/gpt-5" not in text                   # openai's day is its own row


def test_the_daily_cost_uses_the_billed_amount_and_names_the_difference_per_day():
    rates = dict([rc.parse_rate(RATE[0])])
    report = rc.reconcile_daily(rc.read_transcript_keyed(DAYS),
                                rc.read_export_keyed(FIX / "usage_days.csv"), rates, tolerance=0.5)
    day = next(d for d in report.days if d.day == "2026-11-16" and d.model == MODEL)
    assert day.row.export_usd == pytest.approx(0.11) and day.row.export_usd_billed
    assert "cost " in rc.render_daily(report)


def test_a_side_without_dates_falls_back_to_one_row_per_model_and_says_so():
    report = rc.reconcile_daily(rc.read_transcript_keyed(DAYS),
                                rc.read_export_keyed(FIX / "usage.csv"))
    assert "no dates" in report.note and {d.day for d in report.days} == {""}
    assert any(d.model == MODEL for d in report.days)


def test_an_export_day_the_run_never_touched_is_reported_not_dropped():
    keyed = rc.read_transcript_keyed(DAYS)
    export = rc.read_export_keyed(FIX / "anthropic_usage_days.json")
    seen = export[("anthropic", MODEL, "2026-11-16")]
    export[("anthropic", MODEL, "2026-11-18")] = rc.Totals(usage=seen.usage)
    report = rc.reconcile_daily({k: v for k, v in keyed.items() if k[0] == "anthropic"}, export)
    extra = next(d for d in report.days if d.day == "2026-11-18")
    assert extra.row.failures() and "[export only]" in rc.render_daily(report)


def test_a_dated_export_model_still_matches_by_prefix_per_day():
    export = {("anthropic", MODEL + "-20261101", d): t
              for (_, _, d), t in rc.read_export_keyed(FIX / "anthropic_usage_days.json").items()}
    keyed = {k: v for k, v in rc.read_transcript_keyed(DAYS).items() if k[0] == "anthropic"}
    assert rc.reconcile_daily(keyed, export).ok


def test_a_csv_with_a_bad_date_is_refused():
    with pytest.raises(rc.ReconcileError, match="YYYY-MM-DD"):
        rc.read_csv_keyed("date,model,uncached_input_tokens,output_tokens\n16/11/2026,m,1,1\n")


def test_by_day_through_the_command(capsys):
    args = ["reconcile", str(DAYS), str(FIX / "usage_days.csv"), "--by", "day"]
    assert cli.main(args) == 0
    assert "2026-11-17" in capsys.readouterr().out
    drift = ["reconcile", str(DAYS), str(FIX / "anthropic_usage_days_drift.json"), "--by", "day"]
    assert cli.main(drift) == 1


def test_a_real_loop_run_records_provider_and_a_day_that_reconcile_reads(tmp_path):
    from test_agent_smoke import run

    _, _, _, records = run(tmp_path)
    assert {r["provider"] for r in records if r["kind"] == "llm"} == {"scripted"}
    keyed = rc.read_transcript_keyed(tmp_path / "run.jsonl")
    assert keyed and all(p == "scripted" and len(d) == 10 for p, _, d in keyed)
