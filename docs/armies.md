# Armies by click (#8, the armies part of #137)

Built and tested against the fake game and fixture saves only. **Nothing here has run in the real
game.** This page says what is built, what each step assumes, and the live steps that settle it.

## Why there is no "AI toggle"

The issue's own live exploration (1.19.5, Sweden at peace) found no AI-control toggle in the army
panel or the battle-plans bar, and the 1.19.5 effects documentation has no effect that hands an army
to the AI. What the game does have is the plan: an army with divisions assigned to a plan carries it
out itself, at the pace set in the pace window. So delegation here is **giving an army a front
against a named enemy and a pace**, and the proof it happened is the next text save, not a toggle.
`delegate_army_to_ai` (a recorded toggle point, never verified) stays in the catalog; it is not
extended.

## The actions (`adapters/armies.py`, off unless `army_ui = true`)

| action | clicks | confirmed by the next save when |
|---|---|---|
| `assign_army_front(army, enemy, province?, mode?)` | army card, `z`, one click on an enemy province on the border, `plan.assign_units` if recorded, `shift+s`, `plan.execute_<mode>` | the army's `orders_group` has a plan with divisions assigned whose `path` touches the province or a neighbour of it, and `execution_type` is the mode |
| `set_army_execution(army, mode)` | army card, `shift+s`, `plan.execute_<mode>` | `execution_type` equals the mode (0 careful, 1 balanced, 2 rush) |
| `release_army(army)` | army card, `ctrl+h` | no plan of the army has divisions assigned |
| `create_army(from_state, from_province?)` | box (or stack click) over the divisions, `army.create` | an `orders_group` id that was not there before |
| `assign_divisions(army, from_state, from_province?)` | the same selection, drag a counter onto `army.<name>` | the army's division count rose |
| `draw_fallback_line(army, states)` | army card, `c`, drag through the states | a `fallback` plan with divisions through those states' provinces |

The border province is chosen from the front picture (the mod's `div` lines): an enemy-controlled
state's land province with a land neighbour in one of the player's states, those next to the state
with the most of the player's divisions first. `province` names one instead. The view is centred on
it with the console's `goto_province`, and its screen point comes from the camera record exactly as
unit orders' do (`docs/unit-orders.md` section 3).

Refused before any input, with the reason and "nothing was sent": an army with no recorded point,
no recorded pace button or create-army card, an enemy that is the player's own tag or borders
nothing, a province that is not on the border, a state with none of the player's divisions in the
picture, a stale or mismatched camera, a point off the view.

## What the save says (`armies.py`)

`theatres > theatre > orders_group` is an army: `name`, `member={ unit= }` divisions (a
`member={ request= }` is a reinforcement request), `execution_type`, and `order_instance` plans
(`path` provinces, `postfix`, `fallback`, `scheduled_member` for the divisions assigned, `type=5` for a
garrison). Read off a real 1.20 save (`GER_1945_06_21_02.hoi4`). `GameState.army_groups` carries them
when a text save was read (None otherwise); the brief's `Army order #N` lines come from
`ArmyLedger`, which re-judges each order on every read and gives up after 4 days.

**`GameState.delegated_armies` cannot lie.** It gains an army only once a save shows its plan with
divisions assigned through the front's province at the asked pace, and loses it the read the plan is
gone. An unconfirmed order is in the Army order line, never in the AI-control line.

What the save does not say, so nothing claims it: which enemy a line faces (the check is the
province), and whether it was the game's AI or a person that set the plan.

## Control

`army: llm` without `human_ai` is offered all six (and the older army actions). With `army: game`,
no `human_ai` and `steer = llm`, only `assign_army_front`, `set_army_execution` and `release_army`
are: that is "the model owns the country, the game's AI runs the armies" (#161 step 2). See
`docs/modular-control.md`.

## Assumptions to settle live (A1-A6)

1. **A1** clicking the army's card selects it, and drawing with it selected assigns the plan to it
   (else record `plan.assign_units`, which is clicked when present).
2. **A2** the tool's shortcut selects the tool, and ONE left click on a border province draws a front
   line the game snaps to the border (the issue's wording). If the tool needs a drag, `assign_army_front`
   has to become a path like `draw_front_line`.
3. **A3** `shift+s` opens the pace window and a recorded click on its button sets the pace (the three
   buttons are `btn_execute_0/1/2` of `planning_speed_window`, whose absolute position the interface
   file does not give).
4. **A4** selecting divisions and pressing the create-army card makes "Army N" (seen live on 1.19.5).
5. **A5** dragging a selected counter onto an army's card adds the selection to it.
6. **A6** `ctrl+h` with the army selected takes its divisions off the plan (`btn_unassign_units`).
7. Which `order_instance` `type` numbers mean what beyond garrison (5): the reader names kinds by
   `postfix` and `fallback`, and falls back to `line`.

## Live check (a person at the game, at war, not the model)

1. Profile: `army_ui = true`, `unit_orders = true`, a fresh `camera.json`, `save_as_binary=no`.
2. `hoi4-harness calibrate army.create army.<name> plan.assign_units plan.execute_careful
   plan.execute_balanced plan.execute_rush` (the card, the army, the assign button, the three pace
   buttons, with the pace window open).
3. Play to a war with a land border. Run `assign_army_front` for one army with `--dry-run` first: the
   log lists every gesture and the province.
4. Run it for real with the game paused. Watch: the army selected, the tool lit, the line drawn on the
   border, the pace window set.
5. Let the game save (the harness's save cadence). The brief must say `confirmed: ... holds a ... line
   through province N, executing balanced` and list the army in `AI control`. If it says `not seen in
   the save after 4 days`, compare with the screen: the click path (A1/A2) or the pace point is wrong,
   not the reader, so look at `orders_group` in the save first.
6. `release_army` and a save later the army must drop off the AI-control line.
7. Repeat for `create_army`, `assign_divisions`, `set_army_execution` and `draw_fallback_line`.
