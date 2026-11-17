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
   amphibious or paradrops, no night or weather, no battle plans or AI-driven execution
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
