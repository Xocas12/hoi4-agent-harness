# A micro controller under the LLM (#85)

Issue #85 asks for a learned controller good enough to beat competitive players. That is a research
project, and this is its infrastructure, not a strong player. What exists is a place to put a
controller, a rule-based one to beat, a way to collect training data from real runs, and an offline
loop that fits and scores a small model, an exploration mode that logs what an off-policy estimate
needs, per-sector outcomes, that estimator, and a toy simulator to check the loop on. What it does not
have is real exploration data (no campaign has been run with it on), a simulator faithful to the game,
a model class that could play well, or an opponent to measure against. Those are listed at the end.

## What exists

All of it is in `src/hoi4_harness/micro/`, pure Python, no required dependencies (the learner is a
ridge regression solved with Gaussian elimination). An optional `ml` extra adds a boosted-tree model
class (`boosted.py`, see "The ML model class" below).

| piece | file | what it does |
|---|---|---|
| Interface | `interface.py` | `Intent` (front, sector, posture, concentration) and `Observation` (sectors with forces in contact, tired and unsupplied divisions, front movement, the orders standing) go in; `MicroOrder`s (`push`/`attack`/`probe`/`hold`/`reserve` on a state) come out and become `set_front_order` calls (`orders_to_calls`). `build_observation` reads a `world.Picture` and the front history. |
| Baseline | `baseline.py` | `RuleController`: the LLM's intent decides the main sector; with none, push or attack the weakest enemy sector by force ratio; hold everywhere else on that front; rotate sectors with 25% or more of their divisions low on organisation by putting a reserve order on the rear state. A front the LLM stood down gets no offensive of the controller's own. Orders that already stand are not repeated. |
| Features | `features.py` | One nine-number sector vector shared by the live controller, the dataset and the learner. |
| Data capture | `dataset.py` | Rows of (features, action, outcome over the next N days) from run directories. |
| Learner | `learn.py` | `LinearBandit`: one ridge model per order predicting the outcome; the policy takes the best predicted order among those with at least 5 logged examples, else the baseline's. `LearnedController` is the rule controller with its main-effort order chosen by the model. |
| Evaluation | `evaluate.py` | Held-out comparison of learned, baseline and logged policies. |
| Runtime | `runtime.py` | Runs a controller each turn of a campaign and logs every decision with its features. |
| Exploration | `explore.py` | `ExploringController`: epsilon exploration per sector, with the propensity of each choice logged. |
| Off-policy evaluation | `ope.py` | Inverse propensity, self-normalised and doubly robust estimates of a policy's value from logged exploration, with bootstrap intervals. |
| ML model | `boosted.py` | `BoostedBandit`: gradient-boosted trees per order, trained from logged propensities (doubly robust or importance-weighted). Training needs the `ml` extra; a saved model runs without it. |
| Simulator | `sim.py` | A toy sector-graph front simulator, for testing the loop. Not the game. |
| CLI | `__main__.py` | `python -m hoi4_harness.micro extract / train / eval / report / simulate`. |

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

## Exploration: data a different policy can be scored on

A controller that always does what it thinks best logs only that, so nothing can be said about any
other policy. With exploration on:

```json
{ "observe_world": true, "micro_controller": "rule", "micro_explore": 0.2, "micro_explore_seed": 11 }
```

each turn, for every sector the controller orders (an enemy state we have divisions against), it draws
from a coin seeded by (seed, date, state): with probability `1 - epsilon` its own order, otherwise one
of the other four, uniformly (`epsilon / 4` each). Every sector decision goes to
`micro_decisions.jsonl`, including the ones that left the standing order unchanged, as one record:

| field | meaning |
|---|---|
| `date`, `state`, `order` | the decision; `state` is the sector (or the rear state for a rotation order) |
| `enemy`, `sector_state` | the front and the sector it belongs to, which the outcome joins on |
| `base_order`, `explored` | what the controller would have done, and whether the coin overrode it |
| `propensity` | the probability that this policy chose `order` on this sector: `1 - epsilon` or `epsilon / 4` |
| `valid`, `alternatives` | the orders it could have chosen, and those it did not |
| `epsilon`, `seed` | the settings |
| `features`, `own`, `foe`, `org_average` | what it saw |

Rules worth knowing. The LLM's own orders are not perturbed and not logged (they are its intent). A
sector with none of our divisions has one valid order, `hold`, with propensity 1. Rotation (`reserve`
on a rear state) is not a choice among a sector's orders: it is logged with propensity 1 and
`is_sector: false` and left out of the estimate. A decision the order cap dropped is not logged, since
the order did not stand. Because the draw is per sector, a theatre with 10 sectors and epsilon 0.2 has
two or three sectors on a different order each month; that is deliberate (it is what makes the outcome
attributable to the sector), and it is also a real cost in a real game, so keep epsilon small there.

## Outcomes per sector and per front

`front_history.json` (frontreport.py) now also keeps, per save and front, the provinces taken and lost
per state (`gained_by_state`, `lost_by_state`) next to the front totals. `dataset.horizon_outcomes`
reads the saves after a decision up to and including the first one at least N days later (`--horizon`,
default 60) and returns:

* `front`: provinces and states gained and lost against that enemy, battles won and lost on it;
* `sector`: provinces of the sector's own state gained and lost, battles won and lost on it, and from
  the controller's own later log the change in our divisions there (`own_change`) and in average
  organisation (`org_change`).

A decision whose N days are not yet covered by a save is censored (`reward: null`), not zero. The
sector reward, `dataset.sector_reward`, is provinces gained less lost, plus 0.25 per battle won less
lost, less 0.02 per division fewer than at the decision. The weights are a judgement, not a
measurement. Limits: the saves are monthly, so a battle that begins and ends between two saves is
invisible; a province the enemy takes from our own states is not in any enemy sector, so those losses
are in `front` only; a run whose history predates the per-state counts gives front outcomes and no
sector reward, and an exploration row is then left censored rather than scored with the theatre
number. The outcome over N days also includes the orders of the next turn(s) on the same sector;
`--horizon 30` (one save) keeps one decision per outcome, 60 does not.

## Off-policy evaluation

`ope.py` scores a policy that was never run from exploration rows, three ways: inverse propensity
(`1[a == pi(x)] / p * r`, unbiased given overlap, noisy), self-normalised (weighted mean, biased a
little, steadier) and doubly robust (the reward model's estimate plus a propensity-weighted correction;
right if either the propensities or the model are right). `compare` gives the paired difference of two
policies with a bootstrap interval that resamples whole runs when there are five or more. Each estimate
reports `off_support` (the share of rows where the policy picks an order the logger could not have),
`ess` (effective sample size after weighting) and `max_weight`. `evaluate` uses it whenever the held-out
rows carry propensities, and its verdict then reads the interval, not matched rows: "learned ahead"
only if the 95% interval on the doubly robust difference excludes zero and each policy has an
effective sample of 30. A log from a controller that never explored (propensity 1 everywhere) cannot
score another policy, and the numbers say so (low `ess`, mostly unmatched) instead of returning a
confident value.

## The toy simulator

`sim.py` is a toy, and says so at the top. A line of sectors, each with our and their divisions,
terrain, organisation, supply and a breakthrough meter; push, attack, probe, hold and reserve differ in
effort, losses and how much a push depends on supply; 30 day turns, like the monthly save. Rates were
set by hand to roughly the battle numbers in `docs/results/overnight-2026-11-22.md` (piecemeal attacks
mostly bounced, 2 to 1 or better mostly succeeding; `calibrate()`), not fitted, and the toy is harsher
on piecemeal attacks than the 14 to 41 per cent seen there. Terrain is in the dynamics but not in the
controller's features, so some of what matters is hidden, as in the game. It writes
`micro_decisions.jsonl` and `front_history.json` in the real formats, so the extractor, learner and
evaluator run on it unchanged.

```
python -m hoi4_harness.micro simulate -o data/sim --episodes 60 --epsilon 0.3
python -m hoi4_harness.micro report data/sim/sim-* --horizon 30
```

What the test (`tests/test_85_micro_exploration.py`) does with it: logs 56 episodes with the baseline as
the logging policy at epsilon 0.4, trains the linear bandit on 40, and scores baseline and learned on
the other 16 from the log alone. The simulator is then asked what the log cannot say: both policies
replayed from the same logged states. In that run the baseline is worth about +0.09 per sector decision
and the learned policy +0.25; the doubly robust estimates from the log were +0.08 and +0.27, and the
interval on their difference excluded zero. With no exploration the same data cannot tell. This shows
the loop recovers a better policy when one exists and the features can express it. It shows nothing
about the game.

## Data capture

```
python -m hoi4_harness.micro extract runs/overnight-ger-1936 runs/other -o data/micro.jsonl
```

A row: `episode` (the run), `date`, `source`, `state`, `action`, `features`, `reward`, `horizon_days`.
Sources, best first:

* `micro_decisions.jsonl` (`source: micro`): real per-sector features and the order given. Only runs
  made with a controller on have it. When the run kept a `front_history.json` and the row names its
  sector, the reward is the sector reward above (`reward_kind: sector`, with the `outcome` detail);
  otherwise it is the theatre outcome below (`reward_kind: theatre`).
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

## The ML model class (optional `ml` extra)

```
pip install 'hoi4-agent-harness[ml]'          # numpy + scikit-learn, to train
python -m hoi4_harness.micro train data/micro.jsonl --model-type boosted -o data/micro-model.json
python -m hoi4_harness.micro report runs/* --model-type boosted --mode dr
```

`micro_controller: "learned"` with `micro_model` pointing at that file works as before; `eval` and the
runtime detect the model class from the file. Without the extra, `--model-type boosted` stops with
exit code 2 and a message naming the install command; the linear model needs nothing.

**Library choice: scikit-learn (numpy), primary.** Constraints: Windows and Linux, Python 3.10 to 3.12,
CPU only, offline training on hundreds to tens of thousands of logged rows with propensities.

* scikit-learn gradient boosting takes `sample_weight` (importance weights) and arbitrary real targets
  (doubly robust pseudo-outcomes), ships wheels for every platform and Python version here, and on a
  few thousand rows trains in seconds. Trees handle the nonlinearities that matter in this data
  (force ratio thresholds, supply and organisation cliffs) with the nine features as they are.
* LightGBM would do the same and is faster on large data, but adds a compiled dependency (and an OpenMP
  runtime on some Linux images) for a speed this data size does not need. It is the drop-in swap if
  logs reach millions of rows.
* PyTorch is the right tool for a neural or graph policy over the sector graph, and it is a poor
  default: a 200 MB-plus install, CPU and CUDA wheel variants to choose between, and nothing to gain
  on a nine-feature table with a few thousand rows. It becomes worth it with a graph model where
  neighbouring sectors' state is part of the input, which needs the logs to carry it (they do not yet).

**Training from logged propensities.** `--mode dr` (default) fits a ridge reward model, cross-fitted by
episode, and gives every row and every order the pseudo-outcome
`q(x, a) + 1[A = a] / p * (r - q(x, a))`, then fits one boosted regressor per order on it. `--mode ipw`
fits each order only on the rows where it was taken, weighted `1 / p` (clipped at 10). A log without
propensities (no exploration) counts as `p = 1`, and the model then only knows what the logger did.

**Storage.** The model file is JSON (the trees as arrays) and prediction is pure Python, so a machine
with no extra can run a model trained elsewhere, and there is no pickle to trust.

**Measured on the toy simulator** (`tests/test_85_micro_boosted.py`, 40 training and 16 held-out
exploration episodes at epsilon 0.4, 2,880 and 1,152 rows; every policy scored by the same doubly
robust estimator, the simulator's own one-step value as the truth):

| policy | doubly robust estimate (95% interval) | simulator truth | minus baseline (95% interval) |
|---|---|---|---|
| rule baseline | +0.077 (-0.022, +0.186) | +0.093 | |
| ridge bandit | +0.271 (+0.079, +0.469) | +0.254 | +0.194 (+0.085, +0.307) |
| boosted, dr | +0.245 (+0.086, +0.406) | +0.258 | +0.168 (+0.097, +0.237) |
| boosted, ipw | +0.246 (+0.080, +0.430) | +0.249 | +0.169 (+0.079, +0.283) |

Boosted dr minus ridge: -0.026 (-0.092, +0.046). So the boosted model clearly beats the baseline, and
**does not beat the ridge bandit** here: the toy's rewards are close to linear in the features, so
trees have nothing extra to find. That is the expected result and a statement about the toy, not about
the game. Boosted trees are the model to try once real exploration data exists and the ridge model's
held-out error stops improving; until then the linear one is the cheaper default.

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

Done here: item 1's exploration mode and item 2's per-sector outcome (code and tests, not data), and a
toy for item 4. Not done: everything that needs the game or a new dependency.

1. **Real exploration data.** The mode exists (`micro_explore`); nothing has been run with it. It needs
   dozens of campaigns with `micro_controller: rule` and a small epsilon, and the live check below.
   A learned policy scored by `ope.py` on that data is the first honest number this project can get.
2. **A better outcome.** Sector and front outcomes from the front history exist. Still missing: the
   division losses and organisation of the sector from the saves themselves (today they come from the
   controller's own next-turn log), and the weights of the sector reward, which are a guess.
3. **Finer actions than the front tools.** `set_front_order` steers the game's AI; it cannot move a
   division. A player-level micro controller needs division and battle-plan orders (`docs/unit-orders.md`,
   the tactical simulator in `docs/tactics.md`), which are gated on the live camera work in #85's
   comments (perspective model for aiming).
4. **A simulator worth practising in.** `sim.py` is a hand-set toy that tests the loop. A policy that
   is meant to play the game needs a simulator fitted to logged sector transitions (own and foe
   strength, organisation, supply, terrain, order -> next state), and #111 and #117 found the tactical
   simulator's learned plan chooser did not beat the hand planner, so fidelity is the open problem.
5. **A real model class.** Done in part: the boosted bandit above (optional `ml` extra) handles
   nonlinear sector features. It still treats each sector-month as independent and cannot represent
   sequencing, timing or multi-sector coordination. A sequence or graph model needs torch or jax for a
   graph network or a policy-gradient method, and logs that carry the neighbouring sectors. The logged
   format (features, order, propensity, outcome) is what such a model would train on.
6. **A yardstick.** Beating "competitive players" needs an opponent: a rating against the game's AI at
   a fixed difficulty first (`docs/evaluation.md`), then human matches. Nothing here measures that.

Until real exploration data exists, a learned controller here will at best match the rule baseline, and
the verdict will say "inconclusive" until the effective sample is large enough to say otherwise.

## Live check (needs the game)

Not run. On a campaign profile with `observe_world: true`, `micro_controller: "rule"`,
`micro_explore: 0.1`: play two or three game months, then confirm `micro_decisions.jsonl` has one
record per sector per turn with `propensity` 0.9 or 0.025, that `front_history.json` snapshots carry
`gained_by_state`, and that `python -m hoi4_harness.micro extract <run> --horizon 30` gives rows with
`reward_kind: sector` for decisions at least a month old.
