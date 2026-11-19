# Hybrid experiment (#10): model-only vs AI-only vs hybrid

Mock world only. Model `claude-opus-5-5` (anthropic), 8 seeds (1936-1943) per
arm per scenario, same seeds across arms. Prices: $4 / $20 per M input / output,
$0.20 cached input, $5 cache write. Reproduce with

```bash
hoi4-harness experiment winter_crisis --seeds 8 --provider anthropic --model claude-opus-5-5 --max-usd 3 --markdown out.md
hoi4-harness experiment defensive_war --seeds 8 --provider anthropic --model claude-opus-5-5 --max-usd 3 --markdown out.md
```

Arms: model-only = `operational_control=llm`, reflexes on; AI-only = no model,
reflex layer delegates every army; hybrid = `operational_control=ai`, `--guidance hybrid`.
Calls, tokens and USD/run are means per run; USD total is the sum over the 8 runs.

| scenario | arm | seeds | score mean +- sd (min-max) | `center_held` | `no_pocket` | `army_intact` | LLM calls | tokens in | tokens out | USD total | USD/run |
|---|---|---|---|---|---|---|---|---|---|---|---|
| winter_crisis | model-only | 8 | 0.67 +- 0.00 (0.67-0.67) | 0/8 | 8/8 | 8/8 | 22 | 89,359 | 10,940 | $3.02 | $0.38 |
| winter_crisis | AI-only | 8 | 0.33 +- 0.00 (0.33-0.33) | 0/8 | 8/8 | 0/8 | 0 | 0 | 0 | $0.00 | $0.00 |
| winter_crisis | hybrid | 8 | 1.00 +- 0.00 (1.00-1.00) | 8/8 | 8/8 | 8/8 | 20 | 94,115 | 7,218 | $2.14 | $0.27 |

| scenario | arm | seeds | score mean +- sd (min-max) | `front_held` | `army_intact` | LLM calls | tokens in | tokens out | USD total | USD/run |
|---|---|---|---|---|---|---|---|---|---|---|
| defensive_war | model-only | 8 | 0.00 +- 0.00 (0.00-0.00) | 0/8 | 0/8 | 22 | 96,234 | 11,271 | $3.22 | $0.40 |
| defensive_war | AI-only | 8 | 0.00 +- 0.00 (0.00-0.00) | 0/8 | 0/8 | 0 | 0 | 0 | $0.00 | $0.00 |
| defensive_war | hybrid | 8 | 0.12 +- 0.33 (0.00-1.00) | 1/8 | 1/8 | 22 | 110,003 | 9,429 | $2.74 | $0.34 |

Spend: $5.16 (winter_crisis) + $5.96 (defensive_war) = $11.12 for the 16 paid
runs, plus a $0.70 single-seed pilot: $11.82 in all. AI-only is free.

## Reading it

- **Seeds barely vary the mock.** Scores are identical across seeds in every
  arm but one (hybrid on `defensive_war`: one run scored 1.00, seven 0.00). Spread
  reported is across mock seeds and model sampling, and it is nearly nil, so 8
  seeds say "reproducible", not "robust".
- **winter_crisis:** hybrid 1.00, model-only 0.67, AI-only 0.33, every seed.
  Hybrid is the only arm that holds the centre front (`center_held` 8/8 vs 0/8).
  AI-only keeps the army from being pocketed but loses it (`army_intact` 0/8).
  Hybrid also costs about 30% less per run than model-only ($0.27 vs $0.38).
- **defensive_war:** all three arms fail. Hybrid scored 1.00 in 1 of 8 runs; the
  other 23 runs scored 0.00. That is a tie with AI-only, not a win.
