# Division orders (#85)

The loop for orders at division level: **observe** the front, **decide**, **act** with
as little clicking as the game allows, and **verify** from telemetry. Everything here
was built and tested against the fake game (`fakearmy.py`); nothing has run in the
real game. `docs/live-session.md` step 12 is the plan for that, and it starts by
checking the assumptions this page marks as such.

Citations are `file:line` in the 1.19.5 install (`documentation/*.md` unless a path is
given). Line numbers are those of the install this was read from.

## 1. What a script can see of a division

A division is a scope (`every_country_division`, effects l.3859; `every_state_division`,
effects l.4174), but a poor one. The triggers the documentation lists for it are
`unit_strength` (triggers l.7773), `unit_organization` (l.7764), `has_officer_name`
(l.4540), `division_has_majority_template` (l.2656), `division_has_battalion_in_template`
(l.2647) and `is_unit_template_reserves` (l.6408). The effects are `set_unit_organization`
(effects l.7881), `destroy_unit` (l.3556), `add_divisional_commander_xp` (l.1136),
`promote_officer_to_general` (l.5183), `change_division_template` (l.2655) and a few
more of the same kind. **There is no location**: no province, no state, no coordinates,
and no dynamic variable for a division (`dynamic_variables_documentation.md` has scopes
global, country, state, unit_leader, MIO and special_project, and none is a division).
The only per-unit location anywhere is on the *leader*: `province_deployed` for an army
leader's headquarters (dynamic variables l.1376) and `num_units_in_state@<state>`
(l.1332), which counts a leader's units in a state.

So per-division location is **not scriptable**. What is, is asking a *state* for the
divisions standing in it, which the game's own scripts do
(`common/scripted_effects/00_scripted_effects.txt` l.4129: `every_state_division = {
limit = { OWNER = { has_war_with = FROM } } ... }`). The fallback the task allowed is
therefore the design: **the front picture is per state.**

### The front picture (mod)

`mod/llm_bridge/common/scripted_effects/llm_bridge_divisions.txt` defines
`llm_bridge_emit_front`. In the player's scope, when at war (`has_war`, triggers l.5073),
it visits the states the enemy controls and the player's own states that touch one
(`every_state`, `CONTROLLER = { has_war_with = ROOT }` (l.5100), `is_controlled_by`
(l.5430), `any_neighbor_state` (l.1502)), and in each counts divisions twice with
`every_state_division`: those whose `OWNER = { tag = ROOT }` (`tag`, l.7727) and those
whose owner is at war with the player, each with a count of the ones under half
`unit_strength`. It prints one line per state that holds a division of either side and a
closing line:

```
LLMB|v5|div|tag=SWE|state=64|ctl=GER|own=3|own_weak=1|foe=5|foe_weak=0
LLMB|v5|divend|tag=SWE|n=7
```

Two hooks call it: `llm_bridge_front_on_actions.txt` (a second `on_daily` block for the
player only; the install's own on_actions are split over a dozen files that each define
`on_daily`/`on_startup`, so the game runs every block it finds) and, on demand, the
console refresh (`effect SWE llm_bridge_emit_front`, the first line of a refresh run).
Not counted: allies' divisions, divisions in a state neither listed nor adjacent to one
the enemy holds. Nothing is printed in peacetime except `divend n=0`, which is what tells
the reader the last picture has gone stale.

The reader (`logtail._front`) keeps the last `n` `div` lines when a `divend` with a
matching count arrives, so a poll that lands mid-run, or a run with a line that did not
expand, never shows half a front. `GameState.state_forces` is `None` when the mod's
front lines are absent (a reader that cannot see them) and `[]` when the picture is
empty; the brief says nothing in either case.

### What the brief shows

```
Front picture (1939-09-03): own 3 vs foe 7 in 3 state(s)
  CONTACT Skane [GER]: own 3 (1 weak) | foe 5 (2 weak)
  state 66 [GER]: foe 2
  state 60 [SWE]: own 4
Order: #1 attack state 11 -> 12 (given 1939-09-03): landing: enemy divisions in state 12 went 5 -> 3
```

At most five states, contact first, the rest folded into one line; a picture more than
two days older than the state line says how old it is. The delta lists only the states
whose counts moved, and the order lines when they change.

**Verify in the game** (live step 12, part A): that `ROOT` inside `every_state` is the
player, that `OWNER` works in the division scope for the player's own divisions, and that
`[THIS.GetID]` and `[THIS.Controller.GetTag]` expand in a state scope
(`loc_objects_documentation.md` l.629, l.645). A token the game leaves literal drops the
line, and the count check then drops the whole run.

## 2. Orders without clicking: every candidate and its verdict

| Candidate | Where documented | Verdict |
|---|---|---|
| `teleport_armies` (to_state / to_province) | effects l.8276 | **Cheat.** Skips the march, the supply and the battle. Rejected. |
| `teleport <province>`, `moveunit <unit id>, <province>` (console) | console l.1779, l.1217 | **Cheat** (debug commands; a unit id is not scriptable either). Rejected. |
| `set_province_controller`, `set_state_province_controller` | effects l.7616, l.7761 | **Cheat.** Takes ground with no army. Rejected. |
| `set_unit_organization`, `destroy_unit`, `damage_units` | effects l.7881, l.3556; console l.489 | **Cheat** (edit the force, order nothing). Rejected. |
| `start_border_war`, `set_border_war`, `finalize_border_war` | effects l.7992, l.6605, l.4243 | A scripted event with its own rules, not an order. Rejected. |
| `goto_province <id>`, `goto_state <id>` | console l.988, l.998; effects l.4631, l.4640 | **Accepted, for the camera only.** Moves the view and nothing else: it replaces the map search for "bring the area into view" (the search needs a click on its result and where it centres is unmeasured, `docs/adapters.md`). Used by the click path below, through the console channel. |
| `add_ai_strategy` of `front_control` (`state`/`tag`, `execution_type` careful/balanced/rush/rush_weak, `execute_order`, `manual_attack`) | effects l.742; `common/ai_strategy/_documentation.md` l.277-314 | **Faithful, indirect, not built.** Tells the game's AI to push or hold a front; the AI moves the divisions. The mod's existing posture and directive effects already use this vocabulary (`set_ai_posture`). A per-state push (`force_concentration_target_weight` with `state`, doc l.260; `front_unit_request` with `state`, l.316) would be a new directive action. Needs delegated armies, and cannot be verified from telemetry except by watching the front picture move, so it was left as a candidate. |
| `set_ai_posture` / `set_ai_directive` (already in the catalog) | `docs/hybrid-control.md` | The click-free path that exists: it hands the front to the AI. It is what a headless run keeps. |

So **no faithful script path moves a chosen division to a chosen place.** Headless runs
therefore keep `move_division` and `attack_province` off (`headless.MOUSE_ACTIONS`), and
say so in the prompt; the posture and directive verbs stay.

## 3. Orders by click

`move_division` and `attack_province` are in the catalog (`actions/catalog.py`), offered
only to a run whose input driver has a mouse and has had `enable_unit_orders` called
(config: `unit_orders = true`, `game_dir` for the map, `camera.json`). A delegating run
(`operational_control = "ai"`) does not get them (`agent/hybrid.py`).

**Coordinates are states**, because that is what the front picture knows:
`from_state` (a state with your divisions in the picture), `to_state`, and for a move
optional `via` waypoint states. `from_province` and `to_province` narrow an order to one
stack and one target when the caller knows the ids.

### The plan: checked before any input

`adapters/unit_orders.plan_order` refuses, with a message and nothing sent, when:

- a state has no land province, or does not exist on this map;
- the front picture is not observed, or shows no division of yours in `from_state` (it
  lists only states at or behind the front; an order from the interior is refused);
- a **move** targets a state the picture shows enemy divisions in (that is an attack),
  unless `to_province` is named;
- an **attack** targets a state that shows nothing hostile (no enemy division and not
  enemy-controlled), or one that does not border the attacker (land adjacency from the
  map layer; for `from_province`, that province's neighbours only);
- a move has no land route of 15 borders or fewer (a sea crossing is not a click order);
- a province named does not belong to its state.

Ownership comes from the picture (the controller of each listed state, and the player's
own tag), not from `history/states`, which is only the start of the game.

### The click path

1. `goto_province <p>` through the console channel, `p` the stack's province or the first
   province of `from_state`.
2. Select: with `from_province`, a left-click at the unit counter, which the game draws at
   the province's **type 0** anchor in `map/unitstacks.txt` (`mapdata.STACK_KIND`; the file's
   rows are described in `mapdata.STACK_TYPES`; the counter offset from that anchor is a
   recorded number, default 0). Without it, a **box drag** over the counters of every
   province of `from_state`. The box is refused if it would also catch the counter of any
   province of another state that is not known to hold none of yours: the picture lists only
   the front, so an unlisted neighbouring state is unknown, and the refusal says to name
   `from_province` instead.
3. Right-click the target's province (an interior point from `mapdata`, not the centroid,
   which can fall in the sea); each waypoint is a shift-right-click queued after the first.
   Shift is held as a scan code (`ScanCodeKeys.held`), the click through the injected gui.

Every point is computed from the map files and the **camera record** (`camera.json`,
`adapters/unit_orders.CameraRecord`): landmark pairs fitted by `mapcamera.fit_camera`
(flat, or the homography of the tilted view), the province the view was centred on when
they were measured, and the rule by which the game centres on a province (`anchor0`,
`anchor38`, `centroid` or `interior`, settled live). For any other province the camera is
the fit **slid** by the difference of the two centre points. That is exact for a pinhole
camera moving parallel to a flat map, provided centring does not change the zoom: an
assumption the live step checks on a second province. It refuses when:

- there is no `camera.json`, or it is older than 24 h (zoom or UI scale may have changed;
  nothing here can see either), or was recorded at another resolution;
- the fit is a homography through exactly four landmarks and was not `validated` (a fifth
  marker held out and landing within 8 px; `scripts/record_camera.py` does the check);
- a click point is outside the map viewport, or the stack and a target are under 12 px
  apart, or the target is too far from the stack to share one view.

`InputDriverAdapter.click_at` and `.drag` carry the driver's guards: no mouse refuses
(headless), the game must have the focus, a dry run only logs, a point outside the
calibrated screen is refused. A dry run of an order reports every gesture it would make.

### Verification: honest about what telemetry shows

A march takes days and the game is paused when the order is given, so the result is always
`unverified` ("order #N sent ... do not repeat it meanwhile") and never `ok`. The
**ledger** (`OrderLedger`) then reads the front picture on each later read and puts a line
in the brief:

| status | meaning |
|---|---|
| `sent, nothing to see yet` | no change in the picture yet |
| `underway: N division(s) have left state A` | your count in the origin fell |
| `arrived: your divisions in state B went x -> y` | your count in the target rose |
| `landing: enemy divisions in state B went x -> y` | an attack is wearing them down |
| `taken: state B is now yours` | the target's controller is you and it was not |
| `no change after 4 days: the order may not have taken` | probably the wrong stack, or still marching inside the state |

Because the picture lists only front states, a move to the rear can be seen to leave but
never to arrive; the ledger says `underway` and no more. An order inside one state is
refused for the same reason. Settled orders stay in the brief three days.

## 4. The fake game

`fakearmy.py` puts divisions on a real `MapLayer`: they march along land adjacency at
1 to 3 days a province by terrain, an enemy-held province is fought for (the defenders
lose 12 % strength a day, the attacker 4 %), a state changes hands when only one side
stands in it, and the mod's `div` lines are printed from that with the same rule for which
states are listed. Its mouse (`FakeGui`) resolves clicks through a **true camera** the
harness does not see, so a harness that aims with a wrong fit selects nothing and the
ledger says so (`tests/test_unit_orders.py`). `tests/orders_helpers.py` builds a 4x3 grid
map and the whole stack around it. It is not a combat model (that is `tactics/`) and its
speeds are round numbers.

## 5. The simulator as a controller

`tactics/bridge.py`, `ControllerBridge(layer, controller).decide(game_state)`: builds a
simulator world from the front picture and the map layer (provinces, terrain, adjacency
for the listed states and their neighbours), places each state's divisions together in
that state's middle province, runs the controller's `act` on side A, and turns each order
into `move_division` or `attack_province` between states. The scripted and the
vanilla-like controllers both run through it (`tests/test_tactics_bridge.py`), and on the
fake game a controller's attack becomes real clicks.

What it cannot do, stated so it is not mistaken for a result: position is per state, so
the controller sees fewer, fatter stacks; the picture gives a count of weak divisions, so
strength is two-valued (40 % or full); a click selects the whole state, so "send one of
the three" becomes "send all three"; supply hubs are guessed. None of it is validated
against the game.

## 6. Files

| file | what |
|---|---|
| `mod/llm_bridge/common/scripted_effects/llm_bridge_divisions.txt` | `llm_bridge_emit_front` |
| `mod/llm_bridge/common/on_actions/llm_bridge_front_on_actions.txt` | the daily hook (new file; `llm_bridge_on_actions.txt` is untouched) |
| `observation/forces.py` | the brief's front and order lines |
| `adapters/unit_orders.py` | plan, camera record, gestures, ledger, executor |
| `adapters/input_driver.py` | `click_at`, `drag`, `enable_unit_orders` |
| `fakearmy.py`, `fakegame.py` | divisions, movement, mouse, telemetry |
| `tactics/bridge.py` | simulator controller <-> actions |
| `scripts/record_camera.py` | fit and write `camera.json` |
