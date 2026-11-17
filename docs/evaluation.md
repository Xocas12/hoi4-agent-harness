# Evaluation

Five commands, each answering a different question.

| Command | Question |
|---|---|
| `hoi4-harness eval [SCENARIO]` | Did the agent do the thing this scenario asks? |
| `hoi4-harness compare SCENARIO --models P:M,...` | Which model does it better, and for how much? |
| `... --seeds N` (on either) | Is that a result, or one lucky run? |
| `hoi4-harness experiment SCENARIO` | Does model + game AI beat either alone? ([hybrid-control.md](hybrid-control.md#the-experiment)) |
| `hoi4-harness measure` | What does a campaign cost to play, in peace and in war? |

## Scenarios and the baseline

A scenario is a fixed start, an end **date**, and objectives that can be checked
from the game state — some at the end, some over the whole run ("a focus was
running on 80%+ of turns"). Seven ship in `eval/scenarios.py`; `winter_crisis` is the
one that tests a multi-front war — three fronts, a reserve, and a counteroffensive
against one of them that has to be seen and answered with enough, in time. Every score is
printed against the recorded reflex-only baseline (`eval --no-llm
--write-baseline`): the reflex layer plays for free, so a model is only
interesting where it beats it.

## Comparing models

`compare` runs the same scenario once per model and prints one table, with the
baseline as a row, spend beside score, and the fixed configuration (scenario,
seed, guidance pack, control mode, pacing) above it. Guidance moves scores more
than model choice at the small end, so an unstated pack makes every row
uninterpretable.

## Variance

One run of one scenario against one model is an anecdote. `--seeds N` runs each
configuration at N consecutive seeds starting from the scenario's own, and
reports the median with its range:

```
$ hoi4-harness eval economy_ramp --seeds 5
economy_ramp: median 0.50 [0.50-0.50] over 5 seeds (baseline 0.25, +0.25)
  [0/5] civ_factories_20
  [5/5] stability_held
  ...
```

On `compare`, rows gain a range column and the cost columns come from the
median-scoring run, so score and spend in one row describe the same run. Each
seed writes its own transcript under `seed-<n>/`.

What varies between seeds is what the harness can vary: the mock's seeded noise
today, the game's own once the harness drives a real game. A model sampling at
temperature varies on its own, which is the variance this mostly exists to
expose.

### What a seed varies

Until the mock grew noise, a seed drew research durations and nothing else, so
most scenarios scored identically on every seed (sd 0.00) and `--seeds 8` showed
that a run was reproducible, not that a result was robust. Now a seed also
draws, all in `adapters/mock.py` and all scaled by `MockAdapter(variance=...)`
(1.0 by default, 0 for the old fixed mock; `run_scenario(mock_variance=...)`
passes it through):

| What | Band at variance 1 | Matters for |
|---|---|---|
| Opening enemy divisions on each front | +-6% | every wartime scenario |
| Daily combat dice on each front (a wandering shock on the enemy's effective strength) | sd 3.5%, clamped at +-9% | whether a thin hold holds on the last day |
| Winter blizzards (Nov-Feb, per front: -0.15 supply, friendly effectiveness x0.95 for 4-9 days) | about 1.2% chance per front-day | the same |
| Strength of a scripted counteroffensive | +-6% | `winter_crisis` |
| Dates of scripted history and of a scenario's dated war changes | +-3 days (the war deadline never moves) | how long the agent has to react |
| Attrition rate on a losing front | x0.5 to x1.5 per day | `army_intact` |
| Construction cost overrun, drawn once per project | x0.90 to x1.20 | deadline races (`rearmament_race`) |
| Focus length | +-3 days | peacetime pacing |
| Opening stockpiles, manpower, line efficiency, stability | +-3%, +-5%, +-0.04, +-0.02 | thin economic margins |
| Research duration (as before) | 90-150 days | `research_*` objectives |

Two rules keep this usable for comparisons. Every draw comes from a stream that
depends only on the seed and a front's name or an item's order, never on what
the agent did, so two agents on the same seed meet the same dice and weather
(paired comparisons stay paired). And a scenario is calibrated so its designed
answer still wins most seeds while a thin one loses some: the dice decide the
margin, they do not decide whether ignoring the crisis works (it never does).

The noise only shows where a run is near a threshold. Reflex-only and the
offline scripted provider sit far below most thresholds (that is what makes the
scenarios failable), so their spread stays 0.00 by design; the baseline is the
same on every seed and `baselines.json` did not change. A policy that makes the
designed move is where the spread appears: `scripts/seed_spread.py` prints all
three arms over N seeds, free (no model is called).

```
$ PYTHONPATH=src python scripts/seed_spread.py --seeds 10
reference defensive_war       0.85  0.32  0.00  1.00  front_held=8/10 army_intact=9/10
reference winter_crisis       0.97  0.10  0.67  1.00  center_held=9/10 no_pocket=10/10 army_intact=10/10
reference rearmament_race     0.93  0.13  0.67  1.00  mil_factories_6=8/10 ...
```

**Rule of thumb for a claim that goes in a README:**

- at least **five seeds** per configuration — fewer and `compare`, `experiment`
  and `eval --seeds` label their output "an anecdote, not a result" (a single
  `eval` run prints a score card, which makes no claim across seeds);
- report **median and range**, never a mean on its own;
- "A beats B" needs, at the very least, A's worst run to beat B's median;
  ranges that overlap heavily are a tie whatever the medians say;
- the baseline's range counts too — a model whose median sits inside the
  reflexes' range has not shown it is worth waking.

## Measuring cost

`measure` reads a transcript — or plays a long measurement campaign first with
`--campaign peacetime_1936_1939` / `wartime_1939_1941` — and reports per phase:
game days, wakes and wakes per game-month, brief size (full and delta apart), the
request size split into cacheable prefix and per-turn message, and, for a real
provider, billed input tokens per wake and the cache hit rate. The results and
their caveats are in [cost-and-timing.md](cost-and-timing.md#the-arithmetic).
