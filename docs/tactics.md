# Tactical simulator (#85, stage 2)

A small, pure-Python land-combat simulator for training and scoring micro controllers, plus two
scripted baselines. It is a model of HOI4's land combat, not the game: stage 1's real-game
benchmark and the sim-to-real calibration still need the live game (see the end of this page).
No runtime dependencies; nothing here touches the game or sends input.

    hoi4-harness tactics list
    hoi4-harness tactics run hold_line --controller scripted --seeds 20
    hoi4-harness tactics run all --controller both --seeds 30        # the table below
    hoi4-harness tactics episode break_line --controller vanilla --seed 3

## Layout (`src/hoi4_harness/tactics/`)

| file | what it holds |
|---|---|
| `data/constants.json` | the fixture: every constant, with where it came from |
| `data/scenarios/*.json` | the four scenarios, as data |
| `model.py` | constants, templates, provinces, divisions, `Order` |
| `combat.py` | the hourly combat round (pure functions, checked by hand in the tests) |
| `sim.py` | `World`: battles, movement, retreat, supply, encirclement, control |
| `env.py` | `TacticalEnv`: reset / observe / legal_actions / step / done, metrics, front helpers |
| `controllers.py` | `VanillaLikeController`, `ScriptedController` |
| `evaluate.py` | seeds x scenarios x controllers, summary table |

## The model

Time is hourly inside the sim; `step()` advances `step_hours` (6 by default) with the orders applied
at the start. Map: provinces with a terrain, a controller, optional supply hub, and edges with a
river flag (none, small, large). Divisions come from templates summed from a battalion table.

**Combat round** (`combat.py`, one battle per attacked province, both sides shoot at once):

- Attack is soft/hard blended by the targets' hardness, scaled by remaining strength, times
  `1 + modifiers`. Defense is summed the same way.
- Hits: `attempts = round(A * hit_rate)`, each lands with `p = min(1, 2A / (A + D'))`, where
  `D' = max(D - breakthrough, 0.25 D)`. Each hit lands on a random enemy division and does
  `randint(1, str_dice) * 0.06` strength and `randint(1, org_dice) * 0.053` organisation.
  Armor above the shooters' piercing adds dice (+2 strength, +6 org); armor above their
  piercing halves damage taken. Dice sizes and damage factors are the game's.
- Modifiers (additive): attacker terrain (forest -0.15 ... mountain -0.5), attacker river
  crossing (-0.3 / -0.6), armor's per-terrain penalties, supply lack, encirclement (-0.3),
  over-width (-1 per unit fraction, floor -0.33), stacking (-0.02 per division past five),
  defender attacked from several directions (-0.5 on defense).
- **Width.** Each side fills the terrain's combat width with divisions (width from the unit
  files; plains 70, forest 60, mountain 50, urban 80). Divisions below 40% organisation go to the
  back of the queue, so fresh reserves rotate in. Defenders also pull in idle divisions from
  adjacent provinces (reinforcing a battle); extra attack directions widen the attackers' share.
- **Retreat.** After the 4-hour minimum, a defender at 0 organisation falls back one province
  (to supplied ground first) or is destroyed if there is none. An attacker at 0 breaks off in
  place. Strength 0 destroys a division.

**Movement.** `ceil(60 km * terrain cost / speed)` hours per hop, speed cut for river crossings,
raised for retreats. Moving into a province that holds enemies becomes an attack. Re-issuing the
same move keeps progress.

**Control.** A province changes hands when only one side's divisions stand in it (an unopposed
arrival), with the game's org loss on conquest.

**Supply and encirclement.** Each side's network is the provinces connected to one of its hubs
through provinces it controls. Supply fades after four hops. A division outside the network is
encircled: no organisation or strength recovery, the encirclement penalty, strength attrition after
the 72-hour grace, and destruction if it must retreat with nowhere to go. A "pocket closed" is a
connected group of divisions newly cut off.

**Environment.** `reset(scenario, seed)`, `observe()` (a plain dict), `legal_actions()`,
`step(orders)` returning `StepResult(observation, reward, done, info)`, `done`, `metrics()`.
Orders: `hold`, `move` (any province, the sim routes around enemies), `attack` and `retreat`
(adjacent). `assign_line()` spreads divisions over a set of provinces. Deterministic per seed
(one `random.Random` per world). An `opponent` controller can play the other side.
Metrics: territory held, divisions destroyed and lost, strength lost both sides, casualty ratio,
pockets closed and suffered, objective met, illegal orders.

**Goals** (per side, in the scenario file): `hold` (keep listed provinces to the time limit),
`capture` (take them before it), `encircle` (get N enemy divisions cut off, or destroyed while cut
off), `escape` (N of the divisions that start cut off end alive and back in supply).

## Scenarios

| name | map | forces | win (side A) |
|---|---|---|---|
| `hold_line` | 7x3, river line, forest/town/forest | 6 inf vs 8 inf + 1 armor | keep the three column-3 provinces for 240 h |
| `break_line` | 7x3, open middle, hills/forest/mountain flanks | 7 inf + 2 armor vs 7 inf | take the two provinces behind the front in 720 h |
| `close_encirclement` | 7x5, enemy salient on one supply province | 4 inf + 1 armor vs 7 inf | cut 3 divisions off in 360 h |
| `escape_encirclement` | 7x3, 4 divisions at 0.8 strength cut off, relief force outside | 6 vs 6 | 3 of the 4 back in supply in 300 h |

They are synthetic and small on purpose. `Scenario.from_dict` takes the same shape a loader for the
real map layer would emit (provinces, edges with river flags, divisions, goals).

## Baselines

`VanillaLikeController` re-plans once a day, spreads divisions evenly along the front, attacks
straight across at the enemy province nearest its capital in any terrain (defenders attack only
with a 1.5x local edge), never plans an encirclement and never withdraws.

`ScriptedController` scores every enemy province by objective value, enemy divisions it would cut
off, own divisions it would free, and the odds after terrain and rivers; commits to the best one
for up to 96 h, fills its combat width with armor first, stages the rest beside it, and opens a
second axis where odds are good. It runs fast units into empty enemy ground next to the front. On
defence it weights divisions by threat (discounted by the terrain and river the enemy attacks
into) half and evenly half, abandons non-objective ground in bad terrain when outmatched, and
counter-attacks broken enemies. Always: it garrisons any province whose loss would cut off
divisions and pulls back what a lost choke would strand.

## Results

Controller under test plays side A; side B is the vanilla-like controller. 30 seeds (0-29) per row.
Casualty ratio is enemy strength lost over own (above 1 is good); the other columns are means.

| scenario | controller | win rate | casualty ratio | hours | territory | destroyed | lost | pockets |
|---|---|---|---|---|---|---|---|---|
| break_line | vanilla | 22/30 (73%) | 0.905 | 555.5 | 15.83 | 0 | 0 | 0 |
| break_line | scripted | 29/30 (97%) | 3.102 | 255.5 | 12.67 | 0 | 0 | 0 |
| close_encirclement | vanilla | 1/30 (3%) | 0.913 | 356.6 | 20.4 | 0.13 | 0 | 0.37 |
| close_encirclement | scripted | 30/30 (100%) | 4.375 | 237.8 | 27.7 | 2 | 0 | 3 |
| escape_encirclement | vanilla | 15/30 (50%) | 0.23 | 47.7 | 8.5 | 0 | 1 | 1 |
| escape_encirclement | scripted | 30/30 (100%) | 1.417 | 59.8 | 8 | 0 | 0 | 1 |
| hold_line | vanilla | 0/30 (0%) | 1.326 | 101.4 | 10.93 | 0 | 0 | 0 |
| hold_line | scripted | 20/30 (67%) | 1.681 | 186 | 11.53 | 0 | 0 | 0 |

The scripted controller wins on win rate in all four. Where it does not look better:

- `hold_line` is its weakest result (67%): a hold needs every province, and the sim's single
  weak flank division still breaks under two attackers before reserves arrive.
- Casualty ratio on `hold_line` is close (1.68 vs 1.33): the vanilla-like defender loses the
  line fast but trades evenly until it does.
- The scenarios and both controllers were tuned together against this sim. A 97-100% win rate
  says the scenario rewards the scripted ideas, not that the controller is strong. Scripted
  against a scripted opponent: `break_line` 30/30, `escape_encirclement` 30/30, `hold_line`
  30/30 (the scripted attacker is a poor attacker), `close_encirclement` 7/30.
- Both controllers see everything (no fog of war), and the scripted one runs hypothetical
  supply analyses the real game's UI would not hand a player.

Speed (this PC, one core, pure Python): about 1,500 environment steps per second with no
controller (about 9,000 game hours per second); 150-230 steps per second with a controller
included, the scripted one's planning dominating. Fast enough to batch for RL at small scale; a
numpy or compiled core would be the next step if it is not (kept out of the core on purpose).

## Stage 3: a learned controller

`tactics/learned.py` is a policy that scores orders; `tactics/train.py` trains it. Parameters
are committed in `tactics/data/learned_params.json`.

**Policy.** For each division the controller lists candidate orders: hold, attack or move to a
neighbour, retreat, and a move to a few far provinces worth heading for (objective, front,
hubs, the nearest enemy ground). Each becomes a feature vector (31 features, a separate weight
block per order kind, 124 weights): local odds, terrain and river, organisation, strength,
supply, objective distance, travel time, enemy pressure, cut-off potential for the enemy and
risk for us, combat-width usage, how many friendly divisions already head there, and the goal
kind. A linear score picks the best candidate; no randomness, so the same parameters on the same
game give the same orders. Divisions are handled in id order and see each other's choices through
the load feature. A division keeps its current order unless a different one scores higher.

**Training** (`hoi4-harness tactics train`, one process, 12 minutes of wall time):

1. Behaviour cloning: 10 seeds x 4 scenarios x 2 opponents of the scripted controller playing
   side A (about 19,000 decisions), softmax regression over each division's candidates.
2. Antithetic evolution strategies (6 pairs, sigma 0.15) with rank-shaped fitness, one game seed
   per scenario/opponent pair per generation. Fitness per episode is 1 for a win plus half the
   share of casualties the enemy took. Training seeds are 0-299 and the pool is all four
   scenarios against both the vanilla-like and the scripted opponent.
3. The ES result is kept only if it beats the clone on four validation seeds (200-203).

The run used the whole 12 minutes: 21 s of demonstrations, a few seconds of cloning (top-1
agreement with the scripted choice 85%), then 23 ES generations. Validation fitness went from
1.046 (clone) to 1.093 (ES), which is inside the noise of four seeds.

**Results.** Held-out seeds 1000-1029 (never used in training), learned/scripted/vanilla-like
as side A against each opponent. Regenerate with `scripts/tactics_heldout.py`; the numbers are
in `tactics/data/learned_results.json` and a test replays one row.

Against the vanilla-like opponent:

| scenario | controller | win rate | casualty ratio | pockets closed |
|---|---|---|---|---|
| break_line | vanilla | 27/30 (90%) | 1.03 | 0 |
| break_line | scripted | 30/30 (100%) | 3.43 | 0 |
| break_line | learned | 29/30 (97%) | 2.68 | 0 |
| close_encirclement | vanilla | 3/30 (10%) | 0.89 | 0.4 |
| close_encirclement | scripted | 29/30 (97%) | 4.32 | 2.9 |
| close_encirclement | learned | 30/30 (100%) | 8.32 | 4.9 |
| escape_encirclement | vanilla | 14/30 (47%) | 0.22 | 1 |
| escape_encirclement | scripted | 30/30 (100%) | 1.42 | 1 |
| escape_encirclement | learned | 30/30 (100%) | 1.20 | 2 |
| hold_line | vanilla | 1/30 (3%) | 1.39 | 0 |
| hold_line | scripted | 19/30 (63%) | 1.83 | 0 |
| hold_line | learned | 23/30 (77%) | 3.08 | 0 |

Against the scripted opponent:

| scenario | controller | win rate | casualty ratio | pockets closed |
|---|---|---|---|---|
| break_line | vanilla | 28/30 (93%) | 3.60 | 0 |
| break_line | scripted | 29/30 (97%) | 3.85 | 0 |
| break_line | learned | 24/30 (80%) | 4.01 | 0.03 |
| close_encirclement | vanilla | 0/30 (0%) | 0.35 | 0 |
| close_encirclement | scripted | 5/30 (17%) | 4.14 | 0.4 |
| close_encirclement | learned | 0/30 (0%) | 3.46 | 1 |
| escape_encirclement | vanilla | 0/30 (0%) | 0.18 | 1 |
| escape_encirclement | scripted | 30/30 (100%) | 3.87 | 1 |
| escape_encirclement | learned | 30/30 (100%) | 1.86 | 1 |
| hold_line | vanilla | 30/30 (100%) | 2.52 | 0 |
| hold_line | scripted | 30/30 (100%) | 2.48 | 0 |
| hold_line | learned | 26/30 (87%) | 2.56 | 0 |

**Reading it honestly.**

- The learned controller is a distillation of the scripted one that ends up near it, not past
  it. It is ahead on `hold_line` and `close_encirclement` against the vanilla-like opponent, and
  behind on `break_line` and `hold_line` against the scripted opponent (80% vs 97%, 87% vs 100%)
  and on `close_encirclement` against it (0/30 vs 5/30).
- Gaps of one or two games out of 30 (97% vs 100%) are not distinguishable. The hold_line
  win-rate gain (23 vs 19) is also within what 30 seeds can show; the casualty ratio gain there
  (3.08 vs 1.83) is larger.
- ES added little. Fitness on its batches (one seed per pairing) was noisy, centre fitness moved
  between 0.83 and 1.25 with no trend, and the validation gain is not significant. Nearly all of
  the behaviour comes from cloning the scripted controller; a fair summary is "ES found no
  clear improvement in 23 generations at this budget".
- The scenarios were co-tuned with the scripted controller, and the learned one is cloned from
  it, so high win rates here say little about play outside these four maps. The policy is tied
  to this simulator's features (full information, hop-distance supply).
- Sim-only. Whether it transfers to the real game is stage 1/2's open question: the constants
  are calibrated by hand, and the real game has fog of war, which these features ignore.

Try it: `hoi4-harness tactics run all --controller all --seeds 30 --first-seed 1000`.

## Stage 4: battle plans and the front planner

HOI4's engine executes a drawn battle plan (a front line, an offensive arrow, an army) reasonably
well; what its AI does badly is *choosing* where to attack. Mods cannot change that layer. Stage 4
adds a deterministic, non-LLM planner that chooses plans, and an engine executor that carries
them out, so the choice can be scored separately from the execution. **Everything below is a
simulator result**, tuned against this simulator, and none of it has been seen in the game.

**Plans** (`tactics/plans.py`). `BattlePlan(name, side, divisions, front, offensive, aggressiveness,
kind)`: assigned divisions, a front line (own provinces to hold), an optional offensive arrow
(enemy provinces to attack), aggressiveness 0..1, and a label (`defend`, `offensive`, `fallback`,
`reserve`).

**The engine executor** (`EngineExecutor`) turns plans into the sim's per-division orders every
step. The game's real plan execution is engine code no file describes, so each rule is an
assumption:

- A1 divisions on a plan stand on its front line, spread evenly by count, nearest province first;
  one already on (or marching to) a front province with room keeps it.
- A2 provinces facing the arrow get double weight.
- A3 a division attacks across the line only next to an arrow province, with organisation at
  least `0.3 + 0.5 (1 - aggressiveness)` and a local power ratio at least `2 (1 - aggressiveness)`
  (aggressiveness 1: any odds; 0.25: 1.5x; 0: never). The mapping is ours, not a game setting.
- A4 an enemy arrow province with no division in it is walked into.
- A5 a captured arrow province joins the line; divisions advance to it and re-form.
- A6 divisions in combat keep fighting; retreat on zero organisation is the sim's.
- A7 divisions on no plan get no order.
- A8 the executor never chooses where to attack or hold, plans no pocket and never withdraws.

**The planner** (`tactics/planner.py`, `tactics/picture.py`). It sees a `Picture`: by default the
resolution of the mod's front picture (per region, which is a state on the real map: own and
enemy counts, how many are under half strength, controller, plus static terrain, rivers, hubs and
objectives; no per-division strength, organisation, template, supply state or exact province;
power is estimated from counts alone). `observe="full"` gives it province-level numbers for
comparison. It scores enemy regions like `ScriptedController` scores provinces (objective, enemy
divisions its capture would cut off, own divisions it would free, times the force ratio after
terrain and river, discounted by how far our mass is from the sector); thin sectors score high.
Pairs of targets whose joint capture cuts off more than the sum of each are pincers. It extends
an arrow one region deeper when the target is empty or all weak (exploiting a breakthrough),
garrisons chokes and draws a fallback plan from a forming pocket, shares the remaining
divisions over threatened sectors half by threat and half evenly, gives up outmatched
non-objective ground for better ground behind it, counter-attacks all-weak enemy regions on
defence and keeps about a tenth (a fifth on defence) in reserve. A plan is kept 96 h unless
something scores 1.8x better. It re-plans every 24 h and at once when a region changes hands, a
division is lost, more of ours are cut off, or an axis is completed. Registered as `planner` in
`tactics/registry.py`.

**Baselines.** `vanilla_plans` (the "HOI4 AI" analogue): every day, all divisions on one plan, the
whole frontier as the line, the arrow straight across at the enemy neighbour nearest its capital,
attack at any odds on offence. It runs through the same executor, so the gap to `planner`
measures the *choice* of plans. `vanilla` is the older division-level vanilla-like controller.

### Benchmark

`scripts/tactics_plans_bench.py`; numbers in `tactics/data/plans_results.json` (a test replays two
rows). Held-out seeds 2000-2029 (the development iterations used 1000-1009, which overlap the
earlier learned-controller table). Side A is the controller, side B the opponent. The fifth
scenario is the real-map one below.

Against the vanilla opponent:

| scenario | controller | win rate | casualty ratio | pockets closed |
|---|---|---|---|---|
| break_line | vanilla | 24/30 (80%) | 0.87 | 0 |
| break_line | vanilla_plans | 27/30 (90%) | 1.73 | 0 |
| break_line | planner | 29/30 (97%) | 2.74 | 0.13 |
| break_line | planner_full | 29/30 (97%) | 3.06 | 1.73 |
| break_line | scripted | 30/30 (100%) | 3.70 | 0.07 |
| break_line | learned | 29/30 (97%) | 2.98 | 0 |
| close_encirclement | vanilla | 1/30 (3%) | 0.84 | 0.23 |
| close_encirclement | vanilla_plans | 14/30 (47%) | 2.00 | 2.03 |
| close_encirclement | planner | 1/30 (3%) | 0.84 | 0.13 |
| close_encirclement | planner_full | 22/30 (73%) | 3.20 | 2.23 |
| close_encirclement | scripted | 29/30 (97%) | 4.40 | 2.9 |
| close_encirclement | learned | 30/30 (100%) | 8.60 | 4.87 |
| escape_encirclement | vanilla | 8/30 (27%) | 0.19 | 1 |
| escape_encirclement | vanilla_plans | 30/30 (100%) | 1.27 | 1 |
| escape_encirclement | planner | 30/30 (100%) | 1.48 | 1.1 |
| escape_encirclement | planner_full | 30/30 (100%) | 1.49 | 1.1 |
| escape_encirclement | scripted | 30/30 (100%) | 1.42 | 1 |
| escape_encirclement | learned | 30/30 (100%) | 1.32 | 2 |
| hold_line | vanilla | 0/30 (0%) | 1.32 | 0 |
| hold_line | vanilla_plans | 0/30 (0%) | 1.23 | 0 |
| hold_line | planner | 30/30 (100%) | 3.48 | 0 |
| hold_line | planner_full | 30/30 (100%) | 3.48 | 0 |
| hold_line | scripted | 24/30 (80%) | 1.68 | 0 |
| hold_line | learned | 22/30 (73%) | 3.14 | 0 |
| germany_poland_1939 | vanilla | 26/30 (87%) | 1.53 | 0.9 |
| germany_poland_1939 | vanilla_plans | 26/30 (87%) | 3.12 | 1.9 |
| germany_poland_1939 | planner | 18/30 (60%) | 2.19 | 3 |
| germany_poland_1939 | planner_full | 25/30 (83%) | 2.07 | 2.97 |
| germany_poland_1939 | scripted | 4/30 (13%) | 2.23 | 1.97 |
| germany_poland_1939 | learned | 3/30 (10%) | 2.19 | 1.37 |

Against the scripted opponent:

| scenario | controller | win rate | casualty ratio | pockets closed |
|---|---|---|---|---|
| break_line | vanilla | 26/30 (87%) | 3.36 | 0 |
| break_line | vanilla_plans | 19/30 (63%) | 2.83 | 0.03 |
| break_line | planner | 23/30 (77%) | 3.21 | 0 |
| break_line | planner_full | 27/30 (90%) | 3.58 | 1 |
| break_line | scripted | 27/30 (90%) | 4.28 | 0.03 |
| break_line | learned | 20/30 (67%) | 2.96 | 0 |
| close_encirclement | vanilla | 1/30 (3%) | 0.35 | 0.03 |
| close_encirclement | vanilla_plans | 9/30 (30%) | 0.81 | 1.33 |
| close_encirclement | planner | 30/30 (100%) | 0.00 | 1 |
| close_encirclement | planner_full | 12/30 (40%) | 0.85 | 0.4 |
| close_encirclement | scripted | 4/30 (13%) | 4.59 | 0.2 |
| close_encirclement | learned | 0/30 (0%) | 5.83 | 1 |
| escape_encirclement | vanilla | 0/30 (0%) | 0.18 | 1 |
| escape_encirclement | vanilla_plans | 30/30 (100%) | 0.58 | 1.73 |
| escape_encirclement | planner | 30/30 (100%) | 7.24 | 1 |
| escape_encirclement | planner_full | 30/30 (100%) | 7.24 | 1 |
| escape_encirclement | scripted | 30/30 (100%) | 3.83 | 1 |
| escape_encirclement | learned | 30/30 (100%) | 1.94 | 1 |
| hold_line | vanilla | 30/30 (100%) | 2.53 | 0 |
| hold_line | vanilla_plans | 0/30 (0%) | 0.75 | 0 |
| hold_line | planner | 30/30 (100%) | 2.45 | 0 |
| hold_line | planner_full | 30/30 (100%) | 2.45 | 0 |
| hold_line | scripted | 30/30 (100%) | 2.50 | 0 |
| hold_line | learned | 25/30 (83%) | 2.53 | 0 |
| germany_poland_1939 | vanilla | 27/30 (90%) | 2.38 | 0.37 |
| germany_poland_1939 | vanilla_plans | 17/30 (57%) | 4.77 | 0.2 |
| germany_poland_1939 | planner | 14/30 (47%) | 3.26 | 0.27 |
| germany_poland_1939 | planner_full | 18/30 (60%) | 4.20 | 0.57 |
| germany_poland_1939 | scripted | 0/30 (0%) | 5.11 | 0.1 |
| germany_poland_1939 | learned | 0/30 (0%) | 4.33 | 0 |


**Reading it honestly.**

- The planner beats what the same executor does with the vanilla-like plans where the choice of
  sector matters and ground is defensible: `hold_line` (30/30 and casualty ratio 3.5, against 0/30
  for vanilla plans) and `break_line` against the vanilla opponent (97% vs 90%). With full
  information it also gets `close_encirclement` 22/30 against vanilla plans' 14/30.
- It does **not** beat the others everywhere. With the default aggregated observation it
  fails `close_encirclement` against the vanilla opponent (1/30): the salient's neck and head sit
  in one two-province region, so the pocket that the full-information planner (22/30) and the
  scripted controller (29/30) see is invisible. That 21-game gap is the measured cost of the
  coarse observation on that map; on `break_line` (29 vs 29), `hold_line` and
  `escape_encirclement` aggregation costs nothing, and on the real-map scenario it costs 18/30
  against 25/30.
- Against the vanilla opponent `vanilla_plans` already beats the division-level `vanilla` in
  most rows (`close_encirclement` 14/30 vs 1/30, `escape_encirclement` 30/30 vs 8/30): a good part
  of the original vanilla controller's weakness is its execution, which the executor fixes, not
  its choice of target. The planner's edge over `vanilla_plans` is therefore smaller than its edge
  over `vanilla`.
- Scripted and learned (division-level, full information) are ahead of the planner on
  `close_encirclement` and `break_line`; the planner is ahead of both on `hold_line` against the
  vanilla opponent (scripted 24/30, learned 22/30).
- On the real map, `vanilla` and `vanilla_plans` win 26/30 against the vanilla opponent and
  `planner_full` 25/30, so the scenario does not separate them; the planner's 18/30 with aggregated
  observation is behind. Against the scripted opponent the scripted and learned controllers win
  0/30, the planner 14/30 (18/30 with full information) and `vanilla` 27/30. Scripted and learned
  are tuned to the four small maps and do poorly on a bigger one, so that row is not evidence the
  planner is strong.
- `close_encirclement` against the scripted opponent shows the planner at 30/30 with a casualty
  ratio of 0.00: the scripted defender gives up the pocket without a fight in this scenario, so
  the win says more about that defender than about the planner.
- Gaps of one to three games in 30 are within seed noise. The planner and the scenarios were
  tuned against this simulator; a high win rate says the scenario rewards those ideas, not that
  the planner is strong in the game.

### A larger scenario from the real map

`tactics/realmap.py` turns a map layer and a spec into the dict `Scenario.from_dict` reads:
`scenario_from_layer(layer, spec, rivers)`, state or province granularity (province nodes carry
their state as `region`), terrain the commonest of a state's land provinces, adjacency from
touching provinces, river flags from `map/rivers.bmp` along each border (`border_rivers`; the
thresholds are a heuristic of ours). `scripts/build_realmap.py --game-dir ...` reads the install
read-only and writes `tactics/data/realmap/germany_poland_1939.json` and the compact test
fixture `tests/fixtures/realmap_layer.json` (35 provinces: ids, adjacency, terrain, no map
pictures).

`germany_poland_1939` has 34 nodes (one per state, 75 edges, 15 with a river): the 18 Polish
states against 9 German and 7 former-Czechoslovak states treated as German-held. Assumptions, also
in the file: Lithuanian, Latvian, Soviet and Romanian states are left out; forces are about 40% of
the historical order of battle, 22 German divisions (18 `inf_9`, 4 `armor_6`) against 20 Polish
`inf_9`; hubs Brandenburg for Germany and Warsaw and Lwow for Poland; objectives Poznan, Lodz,
Krakow and Warsaw in 30 days; 12 h steps. Poland's weaker equipment and the Soviet invasion are not
modelled.

## Stage 5: a learned plan chooser, distilled from the full-information planner

The benchmark above found that the aggregated planner loses to its full-information twin where the
coarse picture hides the target (`close_encirclement` 1/30 against 22/30, the real map 18/30 against
25/30), and the real game only ever gives the aggregated picture. This stage asks whether a learned
scorer, trained to pick what the full-information planner picks while seeing only the aggregated
picture, closes that gap. **Everything below is a simulator result.**

**What is learned** (`tactics/learned_plans.py`). One decision: the attack axes of an offensive plan.
Holds, fallbacks, the reserve and the engine executor (assumptions A1-A8) are the planner's,
unchanged, so the comparison with `planner` is the scorer against the hand-written axis score and
nothing else. On an offensive goal each re-plan builds a candidate set from the aggregated picture:
the empty plan, the best 8 single enemy regions, every pair among the best 6, a triple, and whatever
the hand-written aggregated planner would draw. A linear scorer (39 features) ranks them. Features:
counts, weak counts, the planner's own odds and value estimates, the joint cut-off and its gain over
the two axes taken apart (the pincer signal), static map shape (provinces per region, width,
neighbours, distance from the enemy hub, rivers), and a `History` of earlier pictures of the same
game: the change in the enemy count in a region and around it since the last re-plan, a decayed
running change, and the peak count ever seen there. The history is the part meant to reveal what
aggregation hides (an enemy that left a region shows up next door). Features are computed from a
`Picture` with `observe == "aggregated"` and refuse anything else; a test shows that organisation
and strength above the half-strength line cannot change them. A drawn set is kept while it scores
within 0.6 (log scale) of the best candidate, for at most 96 h. Defensive goals are not touched, so
`hold_line` is the planner's own play.

**Training** (`hoi4-harness tactics train-plans`, one process, 16 minutes of wall time, pure Python).

1. Pool: the four small scenarios and the Poland map, each with procedural variants
   (`tactics/variants.py`: some open terrain swapped, rivers added or dropped, stack sizes nudged by
   one, the time limit stretched 0.9-1.15x, all by seed). **The France front is never loaded in
   training.** Training games use seeds 0-299.
2. Distillation: the teacher (the planner with `observe="full"`) plays 240 episodes and, at every
   offensive re-plan, the candidates built from the aggregated picture of the same game are recorded
   with the candidate that is the teacher's axis set. The teacher's choice was among the candidates
   in 4,511 of 5,192 decisions (87%). Two DAgger rounds follow: the student plays and the teacher is
   asked what it would have drawn on the states the student reached (4,500 samples in all). Softmax
   regression over the candidates.
3. The first run fitted the plain clone (weights from zero). It reached 59-61% top-1 agreement with
   the teacher and **scored below the hand-written planner on validation games** (0.94 against 0.98
   mean episode fitness; the full-information planner 1.05). The committed run adds two clones that
   pull toward a prior that draws the planner's own pick (L2 toward the prior, strengths 0.05 and
   0.3) and takes the best on selection games (seeds 200-201 on variants 900-901): 0.94 plain,
   **1.00 at 0.05 (taken)**, 0.98 at 0.3.
4. ES (antithetic, 5 pairs, sigma 0.1, 14 generations over about 9 minutes, fitness one win plus
   half the casualty share) on the selected clone, kept only if it beats it on validation games no
   other stage used (seeds 202-203 on variants 902-903). **It did not**: 1.155 for both, so the clone
   is kept (the first run: 0.917 after ES against 0.942 for the clone). Validation fitness of the
   committed run: clone 1.155, hand-written aggregated planner 1.126, full-information planner
   1.117, on 40 games, which cannot separate them.

The committed weights are the planner prior (`is_default` 2.98) plus small adjustments; `bias_k0`
0.31, `bias_k1` 0.36 and `enc_cut_gain` -0.68 are the only others above 0.3 in size.

**Benchmark.** `scripts/tactics_learned_plans_bench.py`; numbers in
`tactics/data/learned_plans_results.json` (per-game outcomes included; a test replays rows). Seeds
3000-3029, which no earlier development, training or table used. Side A is the controller.
`germany_poland_1939` was a training front (with variants); **`germany_france_1940` was never seen**.
It is `tactics/data/realmap/germany_france_1940.json`, built by `scripts/build_realmap.py` from the
install read-only: 40 state nodes, 93 edges, 29 with a river; 23 German divisions against 25 allied
(Netherlands, Belgium, Luxembourg and France north of the Midi, folded into one side); Germany takes
Antwerp, Picardy, Champagne and Ile de France in 40 days; the Allies' only hub is Ile de France.
Hubs, forces, objectives and the limit are assumptions of ours, listed in the file; the limit was
raised from 30 to 40 days on development seeds 1000-1009 with baseline controllers only, because 30
days left every controller near 0. `scripted` and `learned` are the old division-level controllers.

Against the vanilla opponent:

| scenario | controller | win rate | casualty ratio | pockets closed |
|---|---|---|---|---|
| break_line | vanilla_plans | 28/30 (93%) | 1.764 | 0 |
| break_line | planner | 29/30 (97%) | 2.515 | 0.1 |
| break_line | planner_full | 27/30 (90%) | 3.119 | 1.63 |
| break_line | learned_plans | 20/30 (67%) | 2.249 | 0.43 |
| break_line | scripted | 30/30 (100%) | 3.386 | 0.07 |
| break_line | learned | 29/30 (97%) | 2.808 | 0.07 |
| close_encirclement | vanilla_plans | 10/30 (33%) | 2 | 1.7 |
| close_encirclement | planner | 2/30 (7%) | 0.834 | 0.2 |
| close_encirclement | planner_full | 20/30 (67%) | 2.438 | 2.1 |
| close_encirclement | learned_plans | 3/30 (10%) | 1.08 | 0.33 |
| close_encirclement | scripted | 30/30 (100%) | 4.494 | 2.87 |
| close_encirclement | learned | 30/30 (100%) | 8.616 | 4.97 |
| escape_encirclement | vanilla_plans | 30/30 (100%) | 1.32 | 1 |
| escape_encirclement | planner | 30/30 (100%) | 1.499 | 1 |
| escape_encirclement | planner_full | 30/30 (100%) | 1.507 | 1 |
| escape_encirclement | learned_plans | 30/30 (100%) | 1.499 | 1 |
| escape_encirclement | scripted | 30/30 (100%) | 1.411 | 1 |
| escape_encirclement | learned | 30/30 (100%) | 1.228 | 2 |
| hold_line | vanilla_plans | 0/30 (0%) | 1.201 | 0 |
| hold_line | planner | 30/30 (100%) | 3.404 | 0 |
| hold_line | planner_full | 30/30 (100%) | 3.385 | 0 |
| hold_line | learned_plans | 30/30 (100%) | 3.404 | 0 |
| hold_line | scripted | 19/30 (63%) | 1.625 | 0 |
| hold_line | learned | 23/30 (77%) | 3.21 | 0 |
| germany_poland_1939 | vanilla_plans | 26/30 (87%) | 2.927 | 1.73 |
| germany_poland_1939 | planner | 21/30 (70%) | 1.95 | 2.8 |
| germany_poland_1939 | planner_full | 23/30 (77%) | 2.115 | 3.03 |
| germany_poland_1939 | learned_plans | 30/30 (100%) | 3.461 | 3.13 |
| germany_poland_1939 | scripted | 0/30 (0%) | 2.241 | 1.07 |
| germany_poland_1939 | learned | 0/30 (0%) | 2.594 | 1.27 |
| germany_france_1940 | vanilla_plans | 15/30 (50%) | 1.861 | 3.9 |
| germany_france_1940 | planner | 11/30 (37%) | 4.112 | 2.23 |
| germany_france_1940 | planner_full | 18/30 (60%) | 4.081 | 2.8 |
| germany_france_1940 | learned_plans | 19/30 (63%) | 2.316 | 1.17 |
| germany_france_1940 | scripted | 0/30 (0%) | 4.285 | 0 |
| germany_france_1940 | learned | 0/30 (0%) | 1.183 | 1.33 |

Against the scripted opponent:

| scenario | controller | win rate | casualty ratio | pockets closed |
|---|---|---|---|---|
| break_line | vanilla_plans | 19/30 (63%) | 3.18 | 0 |
| break_line | planner | 27/30 (90%) | 7.049 | 0.03 |
| break_line | planner_full | 28/30 (93%) | 4.264 | 0.83 |
| break_line | learned_plans | 22/30 (73%) | 5.295 | 0.27 |
| break_line | scripted | 29/30 (97%) | 4.129 | 0 |
| break_line | learned | 24/30 (80%) | 2.74 | 0.13 |
| close_encirclement | vanilla_plans | 10/30 (33%) | 0.95 | 1.33 |
| close_encirclement | planner | 30/30 (100%) | 0 | 1 |
| close_encirclement | planner_full | 15/30 (50%) | 0.86 | 0.73 |
| close_encirclement | learned_plans | 30/30 (100%) | 0 | 1 |
| close_encirclement | scripted | 3/30 (10%) | 4.162 | 0.3 |
| close_encirclement | learned | 0/30 (0%) | 3.557 | 1 |
| escape_encirclement | vanilla_plans | 30/30 (100%) | 0.529 | 1.73 |
| escape_encirclement | planner | 30/30 (100%) | 7.441 | 1 |
| escape_encirclement | planner_full | 30/30 (100%) | 7.441 | 1 |
| escape_encirclement | learned_plans | 30/30 (100%) | 7.441 | 1 |
| escape_encirclement | scripted | 30/30 (100%) | 3.906 | 1 |
| escape_encirclement | learned | 30/30 (100%) | 1.859 | 1 |
| hold_line | vanilla_plans | 0/30 (0%) | 0.761 | 0 |
| hold_line | planner | 30/30 (100%) | 2.409 | 0 |
| hold_line | planner_full | 30/30 (100%) | 2.409 | 0 |
| hold_line | learned_plans | 30/30 (100%) | 2.409 | 0 |
| hold_line | scripted | 30/30 (100%) | 2.463 | 0 |
| hold_line | learned | 23/30 (77%) | 2.51 | 0 |
| germany_poland_1939 | vanilla_plans | 15/30 (50%) | 5.747 | 0.33 |
| germany_poland_1939 | planner | 17/30 (57%) | 3.249 | 0.23 |
| germany_poland_1939 | planner_full | 22/30 (73%) | 4.069 | 0.57 |
| germany_poland_1939 | learned_plans | 13/30 (43%) | 3.309 | 0.57 |
| germany_poland_1939 | scripted | 0/30 (0%) | 5.093 | 0.1 |
| germany_poland_1939 | learned | 0/30 (0%) | 4.577 | 0.03 |
| germany_france_1940 | vanilla_plans | 6/30 (20%) | 2.569 | 1.37 |
| germany_france_1940 | planner | 0/30 (0%) | 5.452 | 0 |
| germany_france_1940 | planner_full | 0/30 (0%) | 5.215 | 0 |
| germany_france_1940 | learned_plans | 3/30 (10%) | 4.318 | 0.57 |
| germany_france_1940 | scripted | 8/30 (27%) | 3.87 | 0.93 |
| germany_france_1940 | learned | 0/30 (0%) | 2.314 | 0.37 |


**Reading it honestly.**

- **The gap is not closed.** `close_encirclement` against the vanilla opponent: learned plans 3/30,
  planner 2/30, planner with full information 20/30. Over all 72 rows the student has 260 wins of
  360 against the aggregated planner's 257 and the full-information planner's 273. That is a
  negative result for what the stage was for: the student did not learn to see the pocket the coarse
  picture hides. Its weights stay at the planner's pick; the clone agrees with the teacher in only
  about 60% of decisions, and what it agrees on is mostly what the hand-written scorer already picks.
- **Where it differs from the planner, it is not because of what it learned.** An ablation with only
  the planner-prior weight (no learned adjustment; same candidates, same keep rule) wins
  `break_line` 20/30 and 22/30, Poland 30/30 and 14/30, France 23/30 and 4/30 (vanilla / scripted
  opponent); the learned student wins 20, 22, 30, 13, 19, 3. Identical on `break_line` and on Poland
  against vanilla, a game or four apart elsewhere, and 4 games worse on France against vanilla for
  the learned version. So the differences from `planner` come from the candidate machinery and its
  keep rule (re-deciding every re-plan unless the previous set is still the planner's pick, instead
  of the planner's 96 h patience), not from training. On the small maps that costs: `break_line`
  against vanilla 20/30 for the student and 29/30 for the planner (1 game won only by the student,
  10 only by the planner), against scripted 22 and 27. On the real maps it pays: Poland against
  vanilla 30/30 against 21/30 (9 games won only by the student, none only by the planner), held-out
  France against vanilla 19 against 11 (12 against 4) and against scripted 3 against 0, but Poland
  against scripted is 13 against 17 (6 against 10). A side finding for the planner: its patience
  rule was tuned on the small maps and may hurt on the big ones.
- **Held-out front.** On France the student beats the aggregated planner (19 vs 11, 3 vs 0) and even
  the full-information planner (18, 0), and the old controllers collapse again (scripted and learned
  0/30 against vanilla). This is not generalisation of anything learned: the same gain appears with
  the untrained prior. It says a keep rule that re-plans freely suits these two real maps, on one
  held-out map, 30 seeds, two opponents.
- Seed noise: 30 games make gaps of a few games indistinguishable; the paired counts above (games won
  by one controller only) are the better guide. `escape_encirclement` and `hold_line` are identical
  for the student and the planner in every game (the student does not touch defence, and both escape
  with the same plan), and the 30/30 against the scripted `close_encirclement` defender carries the
  0.00 casualty-ratio caveat of the table above.
- Tuned against the sim: the scenarios, the candidate set, the keep margin and the features were all
  built and iterated against this simulator, the teacher is the same hand-written planner, and the
  France front's time limit was set on baseline runs. None of it is evidence about the real game,
  which has fog of war, real supply and a plan executor we only guess at (A1-A8).
- What would make learning matter: a teacher worth cloning (the full-information planner itself
  loses `close_encirclement` to the scripted defender, 15/30, where the aggregated planner wins
  30/30), features that carry the hidden neck/head split (provinces per region is the only trace of
  it), or a student that outputs the plans themselves rather than choosing among a fixed candidate
  set.

Retrain with `hoi4-harness tactics train-plans --minutes 16`; the parameters it writes carry their
own provenance (rounds, selection and validation scores, held-out front, wall time).

## Gap list against the real game

Simplifications, each with the mechanic it stands in for:

1. **Hit resolution.** The game resolves hits per battalion with its own dice and targeting;
   here `attempts = A * hit_rate` and `p = 2A/(A+D')` stand in. `hit_rate` (0.30) is a
   calibration knob, not a game value. Targets are uniform among engaged divisions (the game
   weights the first target).
2. **Breakthrough.** Read as armour-piercing against the defender's defense. The game's exact
   use of breakthrough is not in the data files.
3. **Division stats.** Summed from per-battalion equipment values (1936 infantry equipment read
   from the install); armor battalion hp, attack, piercing and breakthrough are estimates.
   No support companies, artillery, engineers, leaders, doctrine or research.
4. **Org recovery, reinforcement and attrition rates** are the sim's own.
5. **Provinces.** Synthetic graphs, uniform 60 km hops; the real map has varying sizes,
   coasts, forts, infrastructure, railways and VP provinces.
6. **Supply.** A hop-distance model to a hub; no railways, trucks, trains, convoys, stockpile
   or fuel. Encirclement ignores zones of control: only province control cuts supply.
7. **Pockets.** Encirclement needs a province to change hands; a division standing in a pocket
   with no way out is only destroyed through retreat or attrition (no surrender mechanic).
8. **Combat.** One battle per province, no multi-province divisions in motion being pulled into
   combat, no flanking width rules beyond a flat extra-direction bonus, no air, naval,
   amphibious or paradrops, no night or weather, battle plans only as stage 4 assumptions
   (the player-level order is the unit of control). Retreat is instant relocation.
9. **Movement.** One order per division, divisions not in combat move at full speed, no
   organisation loss while moving, no unit stacking limits on provinces.
10. **No fog of war**, perfect information for both sides.
11. **Fidelity of constants.** Values marked `install` in the fixture were read by hand from
    1.19.5 and are not re-read automatically.

## How stage 1 plugs in

Stage 1 plays the same four scenarios in the real game and records, per scenario and controller,
the win condition, casualty ratio, territory and pockets, using the same `metrics()` keys. To
calibrate:

1. Build real-game setups matching each scenario's forces and goals (province IDs map through
   the scenario file's `provinces`, so a real-map loader only has to emit that JSON).
2. Run each scenario in the real game with the AI and the scripted baseline, 10+ runs, and
   record the same metrics.
3. Fit `hit_rate`, the recovery rates and the armor estimates in `data/constants.json` so the
   simulator's win rates and casualty ratios sit inside the real runs' spread; report the gap
   per scenario (the issue's "sim-to-real gap").
4. Add a controller that drives the real game through the harness's input layer and implements
   `act(view) -> list[Order]`, so any controller trained here can be scored there unchanged.

An optional numpy/gymnasium wrapper can sit around `TacticalEnv` behind an extra; it is not
needed by the core or the tests and is not built yet.
