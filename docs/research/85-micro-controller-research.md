# Research: a learned micro controller under the LLM (#85)

Issue #85 asks for a controller that runs the fronts at division level under the LLM and is good enough
to beat competitive players. The issue is parked ("a separate research project"). This note is the
research behind restarting it: what strong game AIs do with operational control, what HOI4's own AI
does wrong at the front and why (from the game's files), what a good player does instead, and a staged
plan that fits this repo's constraints. Every game constant below was read from the install
(Hearts of Iron IV **1.20.1**, `launcher-settings.json`, `common/defines/00_defines.lua` unless another
file is named). Note that the issue's acceptance names 1.19.5, and the install has moved on.

## Recommendation in brief

1. **Do not train RL against the game.** At about 1.5 s a game day (speed 5, world war, 11-22 notes)
   and one instance per licence, the real game yields hundreds of episodes a month, where published
   RL controllers used millions. Use the game for evaluation and for calibration data only.
2. **The bottleneck is the executor, not the chooser.** `set_front_order` steers the game's AI, and the
   game's AI is what spreads attacks, sends 45%-organisation divisions in, and gives up on a stalled
   push. Its defines show this directly (section 3). A learned chooser on top of that executor has a low
   ceiling. Beating competitive players needs the harness to own the executor: battle plans or
   division orders, aimed through the camera work already started.
3. **The fitting model class is search over a strong scripted planner, with learned pieces where data
   is plentiful.** That is the CICERO / AlphaStar Unplugged pattern scaled down: a learned battle
   outcome model trained on every land battle in every save (AI against AI included, which is free
   data), used by the planner to pick targets; and a conservative, propensity-logged bandit over plan
   choices that can only move away from the planner where the off-policy estimate says so.
4. **The simulator is calibrated, not learned.** Fit `tactics/` constants to the logged battle outcomes,
   randomise what cannot be fitted, and report the sim-to-real gap per scenario. A learned world model
   from scratch needs far more transitions than the game can give.
5. **Milestones are measured in the real game on fixed saves:** attack win rate, provinces per 30 days,
   casualty ratio and pockets closed, 10 paired runs per arm per scenario. "Competitive players" is the
   last milestone, and needs real-time play and the click layer, which are at least three milestones away.

## 1. Where the project stands

Built (see `docs/micro-controller.md`, `docs/tactics.md`, `docs/unit-orders.md`):

- **Front-director layer** (`src/hoi4_harness/micro/`): `Intent` and `Observation` in, per-state
  `push / attack / probe / hold / reserve` out through `set_front_order`; a rule baseline (main effort on
  the best force ratio, hold elsewhere, rotate tired sectors); epsilon exploration with propensities;
  per-sector outcomes from the front history; IPS / self-normalised / doubly robust off-policy
  evaluation; a ridge bandit and an optional scikit-learn boosted bandit; a toy sector simulator. On
  the toy, the loop recovers a better policy (DR estimate +0.27 against a truth of +0.25). No real
  exploration data exists yet.
- **Tactical simulator** (`src/hoi4_harness/tactics/`): province-level combat with the game's dice and
  modifiers, four scenarios plus Poland 1939 and France 1940 maps, scripted, learned (BC + evolution
  strategies) and planner controllers. The scripted controller beats the vanilla-like one in the sim,
  and the learned plan chooser (#111) did **not** beat the hand planner: its France gain was an
  artefact of axis order (#117), and no principled axis order beat best-first (#123).
- **Observation:** a daily per-state front picture from the mod (`own`, `own_weak`, `foe`, `foe_weak`
  per state), monthly text saves with every division and every battle in flight. No per-division
  location is scriptable (`docs/unit-orders.md` section 1).
- **Action:** `set_front_order` works headless through the console. Division clicks need a perspective
  camera model (#85 comments); battle plans by click are not live-verified.

What the game's AI did at the front in the two overnight campaigns:

- **11-22 (Germany, game AI steered by Haiku):** the Soviet front never moved from the 1939 border in
  four years. August 1941 battles were piecemeal: 3 German divisions against 8, 6 against 7, attacker
  at 14-41%, while 38 divisions were encircled in Kurzeme and 21 were under 30% organisation.
- **11-27 (same setup, DirectX 11):** France never fell (7% surrender at best). The front report flagged
  the Belgian front STUCK, "unchanged for 193 days; 3 of 3 attacks under 50% of the defender; 12%
  armour". Germany capitulated in November 1943. "The tools the model has ... steer the AI; they do
  not fight the battle."

Both are the failures a player exploits: attacks spread thin, attacks with tired divisions, no
concentration of armour on one axis, no encirclement, and offensives that stall and are abandoned.

## 2. How strong game AIs handle operational and tactical control

### Self-play RL at scale: AlphaStar, OpenAI Five

- **AlphaStar** (StarCraft II, Grandmaster level): supervised learning on human replays first, then a
  league of self-play RL agents with exploiters to avoid strategy cycles. It plays through a camera
  interface with APM limits. The league trained for weeks on TPUs, with many game instances running
  faster than real time.
  [Vinyals et al., Nature 2019](https://www.nature.com/articles/s41586-019-1724-z)
- **OpenAI Five** (Dota 2, beat the world champions): PPO with a single LSTM policy over 10 months,
  roughly 180 years of games a day, and "surgery" to keep training through environment changes.
  [Berner et al., arXiv:1912.06680](https://arxiv.org/abs/1912.06680)

*Lesson for us:* both depend on a fast, parallel simulator of the real game. HOI4 offers neither
(no headless mode, real time, one instance per licence). What transfers is the **bootstrap from human
or scripted play** before any RL, and the **league idea** for evaluation: test against several distinct
opponents (game AI, Expert AI, scripted), not one.

### Offline RL from logged games: AlphaStar Unplugged

- Offline RL on Blizzard's released replays. Behaviour cloning, offline actor-critic and offline MuZero
  baselines; the best offline agents won 90% against the published AlphaStar behaviour-cloning agent.
  The earlier workshop version found the winners were behaviour value estimation with a **single step
  of policy improvement** over the cloned policy.
  [Mathieu et al., arXiv:2308.03526](https://arxiv.org/abs/2308.03526);
  [StarCraft II Unplugged, NeurIPS 2021 workshop](https://neurips.cc/virtual/2021/35708)

*Lesson:* with a fixed dataset, the reliable gain is a conservative improvement over the logging
policy, not a policy learned from scratch. That is the design `micro/` already has (logging policy =
rule baseline; learned policy = one step of improvement scored by doubly robust OPE).

### Hierarchical scripted control: TStarBots

- TStarBot2 used **hand-coded rules over a hierarchical macro-micro action structure** and beat the
  StarCraft II built-in AI at every level, including the cheating levels 8-10; the flat deep-RL agent
  TStarBot1 also did, with far more compute.
  [Sun et al., arXiv:1809.07193](https://arxiv.org/abs/1809.07193)

*Lesson:* beating the built-in AI is the easy target and a scripted hierarchy gets there. That matches
the repo's own sim results (scripted 29/30 against vanilla-like on `close_encirclement`).

### Language plus planning: CICERO, Diplodocus, piKL

- **CICERO** (Diplomacy with negotiation, top 10% of human players on webDiplomacy): a language model
  for dialogue conditioned on *intents*, and a separate strategic planner that computes those intents
  with RL-trained value and policy models and search.
  [Meta FAIR Diplomacy Team, Science 2022](https://www.science.org/doi/10.1126/science.ade9097)
- **piKL / DiL-piKL / Diplodocus:** search and RL regularised (KL) toward a human-imitation anchor
  policy. Diplodocus ranked first and third in a 200-game tournament with 62 humans.
  [Jacob et al., arXiv:2112.07544](https://arxiv.org/abs/2112.07544);
  [Bakhtin et al., arXiv:2210.05492](https://arxiv.org/abs/2210.05492)

*Lesson:* the split #85 proposes is the CICERO split. The LLM states intent and does not pick moves;
the move-level layer is a planner with learned evaluation. The **anchor** is the useful idea: a learned
policy that is penalised for leaving a trusted base policy (here, the scripted planner) is safe to
deploy with little data, and becomes bolder as evidence accumulates.

### LLM agents in strategy games

- **TextStarCraft II / Chain of Summarization:** LLM agents over a text interface beat the built-in AI
  at Harder (level 5), not at the top levels.
  [Ma et al., arXiv:2312.11865](https://arxiv.org/abs/2312.11865)
- **CivRealm** (Freeciv): "canonical RL-based agents exhibit reasonable performance in mini-games,
  whereas both RL- and LLM-based agents struggle to make substantial progress in the full game."
  [Qi et al., ICLR 2024, arXiv:2401.10568](https://arxiv.org/abs/2401.10568)
- **Digital Player / CivAgent** (Unciv): numerical reasoning and long-term planning named as the main
  LLM weaknesses. [Wang et al., arXiv:2502.20807](https://arxiv.org/abs/2502.20807)
- **HIMA** (StarCraft II): a strategic planner over imitation-learned specialist agents.
  [Ahn et al., COLM 2025, arXiv:2508.06042](https://arxiv.org/abs/2508.06042)
- **LLM planning, RL execution** (2v2 King of the Hill): an LLM meta-controller choosing among
  pretrained RL skills at a slow timescale **matched** a behaviour tree (46.4% against 51.5%,
  p = 0.103) and beat flat end-to-end RL.
  [Hösch et al., arXiv:2606.20014](https://arxiv.org/abs/2606.20014)
- **SayCan:** the LLM proposes skills; learned value functions say which are feasible now.
  [Ahn et al., arXiv:2204.01691](https://arxiv.org/abs/2204.01691)

*Lesson:* no published LLM agent beats strong human players in an RTS or grand-strategy game. LLMs help
at the slow, strategic layer, and hierarchical LLM-over-skills designs reach the level of a good
scripted controller, not beyond it. The repo's own overnight runs agree: Haiku read the front report
correctly and still could not break a stalled offensive through the AI.

### Sample efficiency: multi-agent RL, world models, offline bandits

- **Multi-agent micro in StarCraft (SMAC, MAPPO):** decentralised unit control is learnable, with
  budgets of millions of environment steps per map.
  [Samvelyan et al., arXiv:1902.04043](https://arxiv.org/abs/1902.04043);
  [Yu et al., arXiv:2103.01955](https://arxiv.org/abs/2103.01955)
- **Learned world models (MuZero, DreamerV3):** much better sample efficiency than model-free RL, still
  at hundreds of thousands to millions of real steps.
  [Schrittwieser et al., Nature 2020](https://www.nature.com/articles/s41586-020-03051-4);
  [Hafner et al., arXiv:2301.04104](https://arxiv.org/abs/2301.04104)
- **Offline RL:** distribution shift is the core problem; conservative methods (CQL, IQL) stay near the
  data. [Levine et al., arXiv:2005.01643](https://arxiv.org/abs/2005.01643);
  [Kumar et al., arXiv:2006.04779](https://arxiv.org/abs/2006.04779);
  [Kostrikov et al., arXiv:2110.06169](https://arxiv.org/abs/2110.06169)
- **Contextual bandits from logs:** doubly robust evaluation and learning, replay evaluation, and an
  open benchmark of the estimators. [Dudík et al., arXiv:1103.4601](https://arxiv.org/abs/1103.4601);
  [Li et al., arXiv:1003.5956](https://arxiv.org/abs/1003.5956);
  [Saito et al., Open Bandit Pipeline, arXiv:2008.07146](https://arxiv.org/abs/2008.07146)
- **Sim-to-real:** randomising the parameters a simulator cannot pin down makes a policy robust to the
  real values. [Tobin et al., arXiv:1703.06907](https://arxiv.org/abs/1703.06907)
- **Labelling unlabelled play:** VPT trained an inverse-dynamics model on a small labelled set, then
  labelled 70k hours of video with the actions taken.
  [Baker et al., arXiv:2206.11795](https://arxiv.org/abs/2206.11795) The HOI4 analogue: infer the
  orders a human gave from consecutive saves of their games (division positions before and after).

*Lesson:* with real-game data in the hundreds or low thousands of decisions, the approaches that work
are supervised models on plentiful sub-problems (battle outcomes), contextual bandits with logged
propensities and conservative improvement, and a hand-built simulator calibrated and randomised.
Deep RL over the full front is a simulator-only exercise and only as good as the simulator.

## 3. HOI4's own AI at the front, from its files

The game's front AI is engine code, steered by defines and `ai_strategy` blocks. What the defines say
explains the observed failures.

### The battle plan executor

| define | vanilla 1.20.1 | what it does | consequence |
|---|---|---|---|
| `PLAN_SPREAD_ATTACK_WEIGHT` | 12.0 | "the higher the value, the less it should crowd provinces with multiple attacks" | attacks are spread along the line: piecemeal by design |
| `PLAN_ATTACK_MIN_ORG_FACTOR_LOW / MED / HIGH` | 0.85 / 0.70 / 0.45 | minimum organisation to attack, by execution aggressiveness | under rush, divisions at 45% organisation attack |
| `PLAN_ATTACK_MIN_STRENGTH_FACTOR_LOW / MED / HIGH` | 0.60 / 0.50 / 0.30 | minimum strength to attack | under rush, divisions at 30% strength attack |
| `PLAN_EXECUTE_RUSH` | -200 | score a province needs to be attacked under rush | almost any province is attacked |
| `PLAN_FACTION_STRONG_TO_EXECUTE` / `WEAK_TO_ABORT` | 0.50 / 0.65 | share of units ready to execute / weak to abort | executes with half the units in place |
| `RESERVE_TO_COMMITTED_BALANCE` | 0.3 | reserves per committed division in a battle | thin reserves to rotate in |
| `MICRO_POCKET_SIZE` | 4 | pockets this size or smaller are micro'd | larger pockets are left to the plan |

The harness's `push` order maps to rush execution (`docs/front-control.md`), so a `push` from the LLM
or the rule controller makes this worse, not better: it lowers the bar for tired divisions to attack.

### AI force concentration (AIFC)

AIFC was added in late 2024 because the AI used to push along the whole front evenly; it forms an
offensive group for its most important fronts, presses while it progresses and retargets when it
stalls ([Paradox, "Developer Diary | Small Features"](https://forum.paradoxplaza.com/forum/developer-diary/developer-diary-small-features.1710063/),
read through search excerpts; the forum blocked a direct fetch). The defines bound it tightly:

| define | value | consequence |
|---|---|---|
| `AIFC_UNIT_RATIO_BASE` | 0.15 | only 15% of the *extra* units, after the front's minimum needs, go to concentration (mobile warfare doctrine adds 15: `common/ai_strategy/doctrines.txt`, "base + 15 = 30 %") |
| `AIFC_MAX_NR_FRONTS` | 4 | up to four concentrated fronts at once |
| `AIFC_FRESHNESS_BASE_VALUE`, `AIFC_REFRESH_NEED_PER_DAY`, `AIFC_FRESHNESS_ADD_ON_PROGRESS` | 55, 1.0/day (+0.8 x supply ratio), +25 per province | an offensive with no progress is abandoned after roughly 30-55 days: the "stalled, then given up" pattern |
| `AIFC_ACTIVATE_AVG_ORG_RATIO_THRESHOLD` | 0.2 | the offensive activates at 20% average organisation |
| `AIFC_PATH_MAX_COST` (river 2, large river 3, mountains 3, forest 1.2) | 7.0 | target paths are short: a few provinces deep, no deep encirclement |
| `AIFC_TARGET_*` | supply hubs 20, VP x1, capital +5 | it aims at hubs and victory points, not at cutting off divisions |

### What Expert AI changes

Expert AI ([Workshop 741805475](https://steamcommunity.com/sharedfiles/filedetails/?id=741805475),
[source](https://github.com/Kondikka/expertai), `common/defines/EAI_defines.lua`, read 2026-11-27)
raises the bar for attacking and for executing a plan:

| define | vanilla | Expert AI |
|---|---|---|
| `PLAN_ATTACK_MIN_STRENGTH_FACTOR_LOW / MED / HIGH` | 0.60 / 0.50 / 0.30 | 0.85 / 0.65 / 0.50 |
| `PLAN_ATTACK_MIN_ORG_FACTOR_MED / HIGH` | 0.70 / 0.45 | 0.65 / 0.50 |
| `PLAN_EXECUTE_RUSH` | -200 | -10 |
| `PLAN_FACTION_STRONG_TO_EXECUTE` | 0.50 | 0.65 |
| `PLAN_FACTION_WEAK_TO_ABORT` | 0.65 | 0.50 |

It also rewrites country plans around the AIFC strategies (`GER_operation_strats.txt` and the like),
templates and production (`docs/expert-ai.md`). It does not change combat: that is engine code.
So Expert AI is the stronger AI baseline to beat, and its numbers are a cheap starting point for
the harness's own `push` mapping (rush with a higher strength bar).

### Community view of the AI's front play

Player guides agree the AI does not pursue encirclements or exploit breakthroughs, and that
multiplayer opponents exploit far more mechanics
([r/hoi4 "Complete meta guide 2025"](https://www.reddit.com/r/hoi4/comments/1kas290/hoi4_complete_meta_guide_2025/),
community source). That matches the AIFC target scoring above.

## 4. What a competitive player does at the front

Each technique with the mechanic behind it, checked against the 1.20.1 files.

| technique | mechanic (file value) | what the AI does instead |
|---|---|---|
| **Concentrate on one axis to fill combat width** | Terrain width (`common/terrain/00_terrain.txt`): plains 70, hills 70, forest 60, mountain 50, urban 80, each +half of that per extra attack direction (`combat_support_width` 35 / 35 / 30 / 25 / 40). Infantry battalion width 2 (`common/units/infantry.txt`). Over width: -1 per %, capped at -0.33 (`COMBAT_OVER_WIDTH_PENALTY(_MAX)`); stacking -0.02 per division past 5, +3 allowed per extra direction | spreads attacks (`PLAN_SPREAD_ATTACK_WEIGHT` 12) |
| **Attack from several directions** | defender -0.5 when attacked from multiple directions (`MULTIPLE_COMBATS_PENALTY`) and more attacker width | single-front pushes along the plan line |
| **Prepare, then execute; do not micro during preparation** | planning bonus +0.02/day to 0.3 (`PLANNING_GAIN`, `PLANNING_MAX`), decays 0.01/day once executing, **0.03/day for each manual order** (`PLAYER_ORDER_PLANNING_DECAY`); scaled 0.8-1.0 by HQ distance (`PLANNING_CAP_COMMS_SCALING`) | executes at 50% readiness |
| **Encircle** | encircled: -0.3 (`ENCIRCLED_PENALTY`), no supply: attrition to 0.20, speed -0.8, organisation regain -0.2 over 30 days (`OUT_OF_SUPPLY_*`, `MAX_OUT_OF_SUPPLY_DAYS`); disbanded encircled units return only 20% of manpower | aims at hubs and VPs, paths at most cost 7 |
| **Air superiority + CAS on the attack axis** | enemy air superiority: -0.35 defence (`ENEMY_AIR_SUPERIORITY_IMPACT`) and -0.3 speed; division AA recovers up to 0.75 of it (`ENEMY_AIR_SUPERIORITY_DEFENSE`). The MP guide's "up to 35% penalty to defence and breakthrough" agrees ([GuildOrder, air war for MP](https://guildorder.com/games/hoi4/guides/air-war-for-mp-tables)) | air allocation by strategic importance, not tied to the land axis |
| **Hold on rivers, attack where there are none** | river crossing -0.3 / -0.6 attack, -0.25 / -0.5 speed (`RIVER_CROSSING_*`); dig-in +0.02 per level (`DIG_IN_FACTOR`) | AIFC treats rivers as path cost only |
| **Rotate before organisation breaks; pull back before a pocket closes** | divisions under the minimum organisation stop attacking; conquering a province costs 20% of max organisation (`ORG_LOSS_FACTOR_ON_CONQUER`) | attacks at 45% org under rush; no anticipatory retreat |
| **Armour spearheads where the enemy cannot pierce** | armour outclassing piercing: +2 strength / +6 organisation damage dice, half damage taken (`LAND_COMBAT_*_ARMOR_ON_SOFT_DICE_SIZE`, `*_ARMOR_DEFLECTION_FACTOR`) | AIFC scores armour per unit (`AIFC_UNIT_OFFENSIVE_SCORE_FACTOR_ARMOR` 30) but only for 15% of spare units |
| **Time pushes to supply** | AIFC's freshness drains with supply; players stage depots and rail first | gives up when the push stalls |

One example of why the files matter: the 1.11 dev diary on combat width
([Paradox forum](https://forum.paradoxplaza.com/forum/threads/hoi4-dev-diary-combat-and-stats-changes.1472362/))
is widely quoted for province widths of 75-96 (plains 90), yet the 1.20.1 files give plains 70 and
mountains 50. Guides quote the diary; the controller has to read the files.

## 5. This repo's constraints

| constraint | measured or known | consequence |
|---|---|---|
| Game speed | about 1.5 s a game day at speed 5 in a world war (11-22); DirectX 11 holds 7.5-7.9 GB for hours (11-27) | a 60-day scenario is about 90 s of game time plus load; roughly 15-20 scenario runs an hour without the LLM (an estimate, to measure), about 150 a night |
| Instances | one per licence, real time, needs the screen for clicks | no parallel rollouts; scenario runs need the machine to themselves |
| Observation, live | daily per-state counts (own, own weak, foe, foe weak) | enough for sector-level features, not for division-level control |
| Observation, saves | every division (template, province, organisation, strength, supply, entrenchment, encircled) and every battle in flight with both sides' divisions, tactic, damage and the last 24 hours of losses (`docs/observation.md`; 43 land battles in one 1941 save) | the battle outcome dataset exists in every save already written, for all countries, AI against AI included. Monthly saves catch only battles in flight; benchmark runs can save every few days through the console |
| Action, headless | `set_front_order` per state (steers AIFC weights and front execution) | front-director level only |
| Action, mouse | division and battle-plan orders, gated on the perspective camera model | needed for Level B (below) |
| Manual orders | each costs planning (0.03/day decay) | a controller that micro-manages continuously loses the planning bonus; prefer plans |
| Compute | CPU only, Python, optional numpy and scikit-learn | supervised and bandit models; sim RL only at small scale |
| Multiplayer | pause shared or disabled | real-time decision budget; out of scope until the last stage |

## 6. Which approach fits

Two levels of control, with different ceilings:

- **Level A, front director (exists):** choose per-sector orders that steer the game's AI. The chooser
  can be learned with what `micro/` has (exploration, OPE, bandits). Its ceiling is the AI executor:
  AIFC commits 15-30% of spare units, spreads attacks and abandons stalled pushes whatever the weights.
  Expected gain: better choice of axis and timing, fewer suicidal pushes. Not encirclement.
- **Level B, own the executor (to build):** draw battle plans (front line, offensive line or spearhead
  with the planning bonus kept) or move divisions directly. The `tactics/` planner is that executor in
  the sim. This is the level that can encircle, concentrate and rotate, and the only one that can
  plausibly beat a competitive player.

The model class, in order of when it pays off:

1. **A scripted planner as the policy** (exists in the sim). It is TStarBot2's lesson and the repo's
   own: scripted concentrate-and-encircle beats the vanilla-like AI in the sim on every small map.
2. **A learned battle outcome model** (to build): given both sides' divisions, terrain, width use,
   directions, planning, air superiority, supply and entrenchment, predict the attacker's organisation
   and strength losses and the battle result. Gradient-boosted trees on scikit-learn, trained on every
   battle in every save. This is supervised learning with thousands of examples per campaign, not
   hundreds of decisions, and it is what the planner lacks: its attack odds are the sim's hand-set
   formula. It also calibrates the simulator (next item).
3. **A calibrated simulator** (exists, uncalibrated): fit `hit_rate`, recovery rates and the armour
   estimates in `tactics/data/constants.json` so that the sim's battle outcomes match the outcome
   model's on the same inputs; randomise what the data cannot pin. Report the gap on the benchmark.
4. **Plan-level learning anchored to the planner:** a contextual bandit over a few plan parameters
   (axis, concentration share, patience, when to rotate) with exploration in the real game, scored by
   doubly robust OPE, and allowed to deviate from the planner only where the interval excludes zero
   (AlphaStar Unplugged's one-step improvement; piKL's anchor). #111/#117 showed that a chooser trained
   in an uncalibrated sim finds sim artefacts; anchoring and real-game OPE are the guard.
5. **Imitation from human saves** (later): saves of expert games taken every few days, orders inferred
   from consecutive positions (the VPT idea), as a prior for item 4 and as the human anchor.
6. **Deep RL over the full front:** only inside the calibrated sim, only once 3 reports a small gap,
   and only to propose plans that item 4 then tests. Not a goal in itself.

The LLM keeps what it is good at: which theatre matters, when to commit, what to build, and the
stance (the `Intent` it already sends). It does not issue unit orders.

## 7. Staged milestones

Scoring rules (fixed before any run, per scenario):

- **attack win rate:** battles won by our side as attacker / battles we started (from the saves'
  battle records, closed battles matched across saves);
- **provinces per 30 days:** provinces gained less lost on the scenario's front, per 30 game days;
- **casualty ratio:** enemy strength lost / own strength lost;
- **pockets:** enemy divisions encircled and destroyed while encircled;
- **scenario win:** the scenario's own goal (hold / capture / encircle / escape) by its time limit.

Runs are paired: the same save and the same date for every arm. 10 runs per arm per scenario detects a
difference of about 1.25 standard deviations at 80% power (two-sided, alpha 0.05, n = 15.7 / d^2), or a
win rate of 80% against 20%; report bootstrap intervals and the losses, not only the means. Whether
reloading one save gives different outcomes (the game's RNG on load) must be measured first: if it
does not, vary the start by a few hours.

| stage | build | data and runs | target (pass) | stop rule |
|---|---|---|---|---|
| **M0: live plumbing** | the "Live check" of `docs/micro-controller.md`; a scenario runner that loads a save, sets the arm, runs N days and collects saves every 5 days | 2-3 game months with `micro_controller: rule`, `micro_explore: 0.1` | one record per sector per turn with the right propensities; `reward_kind: sector` rows; save-to-save battle matching works | fix before anything else |
| **M1: real-game benchmark** | 4 scenarios as saves (hold, break, encircle, escape) plus two fixed historical saves (Germany against Poland 1939-09-01, Germany against France 1940-05-10) | arms: vanilla AI, Expert AI, rule front director; 10 runs per arm per scenario: 180 runs, about 1-2 nights | the table exists with intervals; RNG variance measured | this is the issue's first acceptance clause |
| **M2: battle outcome model** | battle extraction from saves (`battles.py` already reads them) into a dataset; boosted model of attacker win and losses | all existing campaign saves plus M1's: target 5,000+ land battles | held-out campaigns: log loss and Brier score beat a force-ratio logistic baseline and the sim's combat formula | if it cannot beat force ratio, the features are missing something: check width, air, planning |
| **M3: calibrated simulator** | fit `tactics` constants to M2; domain randomisation of the rest; replay M1's scenarios in the sim | M1 + M2 | per scenario, sim win rate within 15 points of the real one, and the arms in the same order (rank agreement on all 6 scenarios) | the issue's sim-to-real clause; if the order is wrong, do not train in the sim |
| **M4: learned front director (Level A)** | exploration campaigns, OPE, boosted bandit | about 30 wars at epsilon 0.1 (a few thousand sector-months); then a confirmatory A/B on M1 | DR interval on learned minus rule excludes zero; in the A/B, attack win rate +10 points and provinces per 30 days x1.3 against the rule director on 2 of the historical saves | if the interval straddles zero after 30 wars, stop: the executor is the ceiling |
| **M5: own executor (Level B)** | battle plans or division orders in the real game through the camera (perspective model); the `tactics` planner driving it | 10 runs per scenario per arm | beats vanilla and Expert AI on 3 of 4 scenarios; France 1940: provinces per 30 days x1.5 against Expert AI | the issue's "beats HOI4's AI" clause can close here |
| **M6: learned planner components** | outcome model inside the planner; plan bandit anchored to the planner; sim proposals tested by OPE | M5 exploration logs | beats the M5 planner on held-out saves, paired | France and Poland stay held out of tuning, as in #117 |
| **M7: humans** | a protocol: mirrored sides on the same save, single player with the harness owning the pause first, then real time; a player rating per opponent | willing players of known level; 10+ games per player | win rate and casualty ratio against named players; an Elo-style rating against game-AI and Expert-AI anchors | roadmap decision on the non-goals |

## 8. What to build next

In order, each small enough for one PR:

1. **Scenario runner** for the real game: load a save by name, set the arm (game AI, Expert AI,
   front director, later planner), run N days at speed 5 with a console save every 5 days, collect the
   saves and the front history, stop. No LLM. This is M0 and M1's tool.
2. **Battle dataset**: match battles across consecutive saves by id and location, take the first
   snapshot's features and the last snapshot's (or the disappearance's) outcome, both sides, all
   countries. Features already in the save: divisions and templates per side, terrain, tactic, air
   support and anti-air flags, flank flag, hours, the 24-hour loss history.
3. **Outcome model** (M2) on the `ml` extra, stored as JSON trees like `boosted.py`.
4. **Calibration script** for `tactics/data/constants.json` against the outcome model, with the gap table.
5. **Change the `push` mapping** to rush with a higher strength and organisation bar (Expert AI's
   values are the reference), behind a flag, and measure it in M1: a cheap test of whether the
   executor's thresholds are what lose the piecemeal battles.
6. **Perspective camera model** from `map/unitstacks.txt` anchors (the #85 comments), then battle-plan
   drawing in the real game: M5's prerequisite.

Not next: deep RL, a learned world model, multiplayer, PyTorch.

## 9. Risks and open questions

- **Saves are a lossy record of battles.** Battles shorter than the save interval are missed; the
  save lacks named river and crossing modifiers (`docs/observation.md`). Benchmark runs should save
  every few days; campaign saves stay monthly.
- **The executor may cap everything at Level A.** That is a result worth having: it decides whether
  M5 is mandatory, and the M4 stop rule exists to find it cheaply.
- **Fair play.** The controller must see only what a player sees. Enemy divisions outside battle are
  visible only by intel level; the outcome model trains on full saves but must be fed fogged
  features at play time, or it learns from information it will not have.
- **The clock.** Single player lets the harness pause; competitive play does not. A planner that
  needs seconds per decision is fine paused and too slow at speed 3-4 in multiplayer.
- **What "competitive" means.** Multiplayer games use house rules (no exploits, historical
  restrictions, MP mods). The human milestone needs a written ruleset before any game.
- **Version drift.** Defines move between patches (the issue still names 1.19.5; the install is 1.20.1).
  Read constants from the install at run time, as `tactics` already notes for its fixture.

## Sources

Game files (Hearts of Iron IV 1.20.1, read 2026-11-27): `common/defines/00_defines.lua`,
`common/terrain/00_terrain.txt`, `common/units/infantry.txt`, `common/ai_strategy/doctrines.txt`,
`common/ai_strategy/CHI.txt` (`front_control` examples). Expert AI: `common/defines/EAI_defines.lua`
and `common/ai_strategy/` at [github.com/Kondikka/expertai](https://github.com/Kondikka/expertai)
(branch `current`, pushed 2026-06-21).

Repo: `docs/micro-controller.md`, `docs/tactics.md`, `docs/unit-orders.md`, `docs/front-control.md`,
`docs/observation.md`, `docs/expert-ai.md`, `docs/results/overnight-2026-11-22.md`,
`docs/results/overnight-2026-11-27.md` (branch `docs/overnight-2026-11-27`), issues #85, #111, #117, #123.

Papers and primary pages:

- AlphaStar: https://www.nature.com/articles/s41586-019-1724-z
- AlphaStar Unplugged: https://arxiv.org/abs/2308.03526 ; StarCraft II Unplugged: https://neurips.cc/virtual/2021/35708
- OpenAI Five: https://arxiv.org/abs/1912.06680
- TStarBots: https://arxiv.org/abs/1809.07193
- CICERO: https://www.science.org/doi/10.1126/science.ade9097
- piKL: https://arxiv.org/abs/2112.07544 ; Diplodocus: https://arxiv.org/abs/2210.05492
- TextStarCraft II: https://arxiv.org/abs/2312.11865
- CivRealm: https://arxiv.org/abs/2401.10568
- Digital Player / CivAgent: https://arxiv.org/abs/2502.20807
- HIMA: https://arxiv.org/abs/2508.06042
- LLM planning, RL execution: https://arxiv.org/abs/2606.20014
- SayCan: https://arxiv.org/abs/2204.01691
- SMAC: https://arxiv.org/abs/1902.04043 ; MAPPO: https://arxiv.org/abs/2103.01955
- MuZero: https://www.nature.com/articles/s41586-020-03051-4 ; DreamerV3: https://arxiv.org/abs/2301.04104
- Offline RL tutorial: https://arxiv.org/abs/2005.01643 ; CQL: https://arxiv.org/abs/2006.04779 ; IQL: https://arxiv.org/abs/2110.06169
- Doubly robust policy evaluation: https://arxiv.org/abs/1103.4601 ; replay evaluation: https://arxiv.org/abs/1003.5956 ; Open Bandit Pipeline: https://arxiv.org/abs/2008.07146
- Domain randomisation: https://arxiv.org/abs/1703.06907
- VPT: https://arxiv.org/abs/2206.11795

Game community (secondary; mechanics claims checked against the files above):

- Paradox, "Developer Diary | Small Features" (AIFC): https://forum.paradoxplaza.com/forum/developer-diary/developer-diary-small-features.1710063/
- Paradox, "HOI4 Dev Diary: Combat and Stats Changes" (1.11 width): https://forum.paradoxplaza.com/forum/threads/hoi4-dev-diary-combat-and-stats-changes.1472362/
- Expert AI on the Workshop: https://steamcommunity.com/sharedfiles/filedetails/?id=741805475
- GuildOrder, "Air war for MP tables": https://guildorder.com/games/hoi4/guides/air-war-for-mp-tables
- r/hoi4, "HOI4 complete meta guide 2025": https://www.reddit.com/r/hoi4/comments/1kas290/hoi4_complete_meta_guide_2025/

The HOI4 wiki (hoi4.paradoxwikis.com) refused automated fetches during this research, so no claim
here rests on it.
