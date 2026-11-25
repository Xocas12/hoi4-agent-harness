# A micro controller under the LLM (#85)

Issue #85 asks for a learned controller good enough to beat competitive players. That is a research
project, and this is its infrastructure, not a strong player. What exists is a place to put a
controller, a rule-based one to beat, a way to collect training data from real runs, and an offline
loop that fits and scores a small model. What it does not have is data, a simulator the policy can
practise in, or a model class that could play well. Those are listed at the end.

## What exists

All of it is in `src/hoi4_harness/micro/`, pure Python, no new dependencies (the project has none to
speak of, and no numpy; the learner is a ridge regression solved with Gaussian elimination).

| piece | file | what it does |
|---|---|---|
| Interface | `interface.py` | `Intent` (front, sector, posture, concentration) and `Observation` (sectors with forces in contact, tired and unsupplied divisions, front movement, the orders standing) go in; `MicroOrder`s (`push`/`attack`/`probe`/`hold`/`reserve` on a state) come out and become `set_front_order` calls (`orders_to_calls`). `build_observation` reads a `world.Picture` and the front history. |
| Baseline | `baseline.py` | `RuleController`: the LLM's intent decides the main sector; with none, push or attack the weakest enemy sector by force ratio; hold everywhere else on that front; rotate sectors with 25% or more of their divisions low on organisation by putting a reserve order on the rear state. A front the LLM stood down gets no offensive of the controller's own. Orders that already stand are not repeated. |
| Features | `features.py` | One nine-number sector vector shared by the live controller, the dataset and the learner. |
| Data capture | `dataset.py` | Rows of (features, action, outcome over the next N days) from run directories. |
| Learner | `learn.py` | `LinearBandit`: one ridge model per order predicting the outcome; the policy takes the best predicted order among those with at least 5 logged examples, else the baseline's. `LearnedController` is the rule controller with its main-effort order chosen by the model. |
| Evaluation | `evaluate.py` | Held-out comparison of learned, baseline and logged policies. |
| Runtime | `runtime.py` | Runs a controller each turn of a campaign and logs every decision with its features. |
| CLI | `__main__.py` | `python -m hoi4_harness.micro extract / train / eval / report`. |

## Putting a controller under the LLM

Off by default. In a campaign profile:

```json
{ "observe_world": true, "micro_controller": "rule" }
```

`"learned"` with `"micro_model": "path/to/model.json"` uses a trained model. Each turn, before the LLM
is woken, the supervisor reads the newest save's picture, takes the front orders the LLM has standing
(an order on a state it gave itself is its intent; one the controller gave is not) and lets the
controller order the rest through the same `set_front_order` the LLM uses (so it ends up as the same
state flags in the mod, see `docs/front-control.md`). The LLM's own orders are never overwritten, and a
controller that raises costs only its own turn's orders. It needs `observe_world` and a text save
(the observation comes from saves, which the game writes monthly), so it cannot react inside a month.
It has not been run against the real game; see "Live check" in the PR.

Every order it gives is appended to `micro_decisions.jsonl` in the run directory with the sector's
features. That file is the data the learner wants.

## Data capture

```
python -m hoi4_harness.micro extract runs/overnight-ger-1936 runs/other -o data/micro.jsonl
```

A row: `episode` (the run), `date`, `source`, `state`, `action`, `features`, `reward`, `horizon_days`.
Sources, best first:

* `micro_decisions.jsonl` (`source: micro`): real per-sector features and the order given. Only runs
  made with a controller on have it.
* `transcript.jsonl` (`source: transcript`): every accepted `set_front_order` the LLM gave. The log
  holds no per-sector forces, so the features are the theatre-wide "Front picture" line of that turn's
  brief (own against foe divisions in the visible states), plus front movement when the run kept a
  `front_history.json`. The sector the order names is not observed. These rows are a weak sample.
* `campaign_progress.jsonl`: the outcome series. The reward is the change in controlled states from the
  decision to the first sample 60 days later (`--horizon`), less 0.02 per division lost. A decision
  with no sample that far on is kept with `reward: null` and never trained on.

Not used: the monthly saves themselves (60 to 100 MB each; the front history is already derived from
them when a run keeps one).

The sample, `runs/overnight-ger-1936` (a Germany 1936 campaign, 45 front orders, 32 accepted): 32 rows,
all `transcript`, all with an outcome (hold 19, attack 8, push 2, reserve 2, probe 1). The outcome of
an order here is the whole theatre's, and the LLM gave several orders a turn, so an order and a result
are confounded. This is enough to test the pipeline and nowhere near enough to learn from.

## The offline loop

```
python -m hoi4_harness.micro train data/micro.jsonl -o data/micro-model.json
python -m hoi4_harness.micro eval  data/micro.jsonl --model data/micro-model.json
python -m hoi4_harness.micro report runs/overnight-ger-1936     # extract, train and eval in one go
```

The split is by episode (the last runs held out); with a single run, the later part of its decisions
is held out. Evaluation reports three numbers and their limits:

* reward-model error on the held-out rows against predicting the training mean;
* matched-action reward: the mean logged outcome where the policy would have chosen the order that
  was given, and how many rows that is;
* the model's own estimate of each policy's value, which favours the learned policy.

Logged decisions cannot say what a different order would have done, so no number here is a win rate.
The verdict is "inconclusive" unless each policy has 30 matched held-out rows.

On the one real sample it says exactly that: 22 training rows, 10 held out, the model trusts only
`attack` and `hold`, its error on held-out rows is worse than the training mean (MSE 94.7 against
40.4, two jumps of 28 and 30 controlled states in 60 days dominate the rewards), and the verdict is
"inconclusive: 8 baseline-matched and 8 learned-matched held-out rows, need 30 each". On synthetic data
where the best order really depends on the force ratio the same code recovers the right policy
(`tests/test_85_micro_learning.py`), so the loop works; the sample has nothing in it to find.

## The path to a strong player

1. **Data with real sector features and varied actions.** Turn on `micro_controller: rule` for runs,
   with a randomised mode for exploration (not built: it needs a per-turn coin flip over orders and its
   propensity logged, so that off-policy estimates are unbiased). Dozens of campaigns, not one.
2. **A better outcome.** Per-order outcome rather than theatre-wide: provinces gained and lost on the
   sector's own front, division losses, from `front_history.json` and the battle list. The theatre
   reward moves with annexations and other fronts.
3. **Finer actions than the front tools.** `set_front_order` steers the game's AI; it cannot move a
   division. A player-level micro controller needs division and battle-plan orders (`docs/unit-orders.md`,
   the tactical simulator in `docs/tactics.md`), which are gated on the live camera work in #85's
   comments (perspective model for aiming).
4. **A simulator to practise in.** The tactical simulator exists, but #111 and #117 found its learned
   plan chooser did not beat the hand planner. A policy needs a faster and more faithful environment
   than a month-long save cycle: learning from 50 campaigns of saves is not going to get there.
5. **A real model class and its dependency.** Linear per-order reward models cannot represent
   sequencing, timing or multi-sector coordination. That means a sequence or graph model and a
   training library (torch or similar), which this repo does not carry and this change does not add.
6. **A yardstick.** Beating "competitive players" needs an opponent: a rating against the game's AI at
   a fixed difficulty first (`docs/evaluation.md`), then human matches.

Until 1 to 3 exist, a learned controller here will at best match the rule baseline.
