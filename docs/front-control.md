# Front control: how far an LLM can micromanage a front

Three levels, from the most direct to the most hands-off. Which one a run gets depends on whose seat the LLM is in.

## 1. The mouse, in the LLM's own seat (campaign mode)

When the LLM is the player (`campaign` with LLM hands, no person at the keyboard), it can order divisions the way a person does: select a stack, right-click a target.

| Tool | What it does |
|---|---|
| `move_division` | march divisions from a state (or one province) to another, with waypoints |
| `attack_province` | attack an adjacent enemy-held province or state |
| `draw_front_line`, `draw_offensive`, `assign_battle_plan` | battle plans for an army |
| `delegate_army_to_ai` | hand an army to the game's AI |

Everything is computed from the map files and a camera record (`scripts/record_camera.py`, a one-off calibration of where the map sits on screen). The click path, its refusals and the verification ledger are in `docs/unit-orders.md`. The orders read where divisions stand from the front picture (the mod) and, since #139, from the newest save. Built and tested on the fake game; **not yet run in the real game** (live plan below).

## 2. The mouse in rival mode: not possible in single player

In single player the game lets the player command only their own country's units. A rival country's divisions cannot be selected, so no click can order them while you play another country. The console's `tag` command would switch your seat, which is exactly what a rival must never do. Script cannot order a march either: the only effects that move units are cheats (`teleport_armies`, console `moveunit`).

The route to mouse-level control of a second country is multiplayer: the LLM gets its own game client, and its own keyboard and mouse (`docs/multiplayer.md`, #91). That needs a second machine or account and is not built for tonight.

## 3. Headless: front orders through the game's own front AI

What a rival (or any run without a mouse) has is the game's front AI, steered per state through the inbox. `set_front_order(state, order)`:

| Order | What the game's AI does on the front at that state |
|---|---|
| `push` | rush execution, attack forced on, probing attacks, +150 division request, and its concentrated offensives aimed at that state (target weight +300, front factor +150) |
| `attack` | balanced execution, attack on, +80 request, concentration +120 / +60 |
| `probe` | careful execution, attack on with probing, +30 request, concentration +40 |
| `hold` | attack forced off, no probing, +60 request, concentration steered away (-80 / -60) |
| `reserve` | a tenth of the army kept stationed in that state, behind the line (`put_unit_buffers`, one generated block per state) |
| `clear` | drop the order |

The mechanism: the inbox sets a state flag; fixed blocks in `llm_bridge_front_orders.txt` use `state_trigger = { has_state_flag = ... }` on `front_control`, `front_unit_request`, `force_concentration_target_weight` and `force_concentration_front_factor` (all documented to take a state trigger in 1.20's `common/ai_strategy/_documentation.md`); reserves are `llm_bridge_reserves.txt`, generated per playset by `hoi4-harness mod-probe`. | `invade` | the naval invasion planner aims at that coastal state: `front_control` with `ordertype = invasion`, attack on, rush, and +200 `invasion_unit_request` |

**Navy and air, by strategic region:** `set_region_order(region, order)`:

| Order | Strategy (value) |
|---|---|
| `dominate` | `naval_dominance` (90%) |
| `raid_convoys` | `naval_convoy_raid_region` (300) |
| `avoid` | `naval_avoid_region` (500) |
| `home_base` | `strike_force_home_base` (250) |
| `air_superiority` | `strategic_air_importance` (40,000; a main front in active combat is about 35,000) |

These take a region id, not a trigger, so `hoi4-harness mod-probe` generates one block per (region, order) from the playset's `map/strategicregions` (naval orders for the 92 vanilla sea regions, air for all 304), keyed on a country flag on the ordering country: two LLM countries never read each other's region orders. The values follow the game's own `ENG.txt`.

Divisions still move as the AI moves them: the LLM chooses where and how hard, the AI executes. Orders stand until changed, and the brief lists them. **Unverified in the live game**: that `state_trigger` is honoured for each of these types, and how strongly the weights move the AI.

## Live plan

| Step | Shows | How |
|---|---|---|
| 1 | a front order lands | rival at war: `set_front_order` push on a contact state; the confirmation, then divisions shifting to that stretch over the next days (Forces and map) |
| 2 | hold stops attacks | hold on an active stretch: no new battles started there |
| 3 | reserve | reserve on an interior state: divisions gather there |
| 4 | mouse in own seat | campaign mode, after `scripts/record_camera.py`: `move_division` then `attack_province` on a quiet front, with the person watching and idle |
