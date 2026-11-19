# Headless play

Headless means the harness plays with **no mouse and no screen**. It reads the
game from the log the mod writes, acts through the game's console and a few
keyboard shortcuts, and runs the clock the same way. Nothing in the path takes a
screenshot, calls a vision model, moves the pointer or needs a calibration file.
`--headless` on `play`, `observe` and `doctor`, or `adapter = "headless"` in a
profile.

The reason to want it: every click path is fragile (resolution, UI scale, a panel
that did not close) and every screenshot costs a model call. The console and the
log do not care what is on screen.

## What runs where

| Job | Headless path | Evidence |
|---|---|---|
| See the game | `adapters/combined.py` (`headless_reader = "log+save"`, the default): the mod's daily `LLMB|v5|` lines from `game.log` for the live numbers, wars and events, plus a text save the console writes on demand for production, the construction queue, divisions and completed focuses | log live 1.19.5; save written live 1.20.0, the console-saves-then-harness-reads loop **not yet live** |
| `queue_construction`, `set_production`, `hire_advisor` | console: `eval_effect TAG = { ... }` in `llmb_turn.txt`, then console key, `run llmb_turn.txt`, enter, console key. Construction and production end the file with `savegame` and are verified through it | effects live 1.19.5; verification through the save not yet live |
| `set_ai_directive`, `set_ai_posture`, `clear_ai_directives` | console: `effect TAG llm_bridge_*` (the mod's scripted effects) | live 1.19.5 |
| `set_national_focus` | keyboard only: alt+q, ctrl+f, the localised name, shift+enter | live 1.19.5 |
| Resume | console `gamespeed N` (sets the speed outright), date watched to confirm | **documented, not yet live** |
| Pause | the space key (one press), console `gamespeed 0` as the other path | space live; `gamespeed` not yet |
| Exact turns (`headless_clock = "exact"`) | console `pause_in_hours`: the game stops itself after N days | **documented, not yet live** |
| Fresh telemetry while paused | the run file ends with two `effect TAG llm_bridge_emit_state` | **not yet live** (see below) |
| Wars | the mod's `war` lines (schema v4), one per enemy per day: enemy, its divisions, whether it borders us, how close it is to surrendering | **not yet live** (tokens are from the install's documentation; see "Wars") |

Every key goes out as a hardware scan code (`adapters/keys.py`). The harness
still refuses to send input unless the game is the focused window, so the game
has to be in front when keys go; `--no-window-guard` is the explicit opt-out.

### What it cannot do

These actions are removed from the tool list, and the system prompt says so
(`agent/prompts.py`, `HEADLESS`), because the model should not plan around a tool
that will not work:

* **`start_research`.** The tech tree has no search box and no keyboard
  navigation: `interface/countrytechtreeview.gui` binds only Escape (close),
  ctrl+Tab and ctrl+shift+Tab (cycle tabs) and Return (confirm a selection that
  already exists). A technology can only be chosen by clicking its icon.
* **`delegate_army_to_ai`.** No effect exists, and the per-army AI toggle is a
  button.

* **`set_production`, with the log alone.** `add_equipment_production` adds a
  *second* line for equipment that already has one (seen live on 1.19.5) and no
  trigger tells the effect a line exists, so it is only offered when the reader
  sees production (`log+save`, `savegame`). It then refuses an equipment that has
  a line and more factories than are free; changing a running line is not possible
  from the console (use the UI channel).

Also still on the mouse, and not offered by any adapter today: unit orders on the
map (#85 is the province layer). The idle research slots stay idle, so headless
turns the "research slot idle" wake rule off.

**What is still not available headless, and why**, in one place: research (the
tech tree is click-only and no effect starts research: the "research" table below),
delegating an army to the AI (no effect), changing a production line's factory
count (no effect; `add_equipment_production` only adds), unit orders and fronts
(no per-front data exists in the log, and orders are mouse-only), the hybrid
fields `delegated_armies` (the mod cannot see the per-army toggle). Research and
delegation are settled: do not reopen them.

## The hunt for a no-mouse path

Everything below was checked in the game's own `documentation/` and
`interface/` files (1.19.5). "Faithful" is the bar: nothing that finishes
research, grants a technology, makes anything free or skips a cost.

### Research

| Candidate | What the game says | Verdict |
|---|---|---|
| console `research <slot id or "all">` | "Researches an technology from research slot or all" | Rejected. It takes a slot, not a technology, so it cannot choose; if it does anything it completes what is there. |
| console `research_fast`, `add_tech`, `sp_*`, `research_on_icon_click` | cost set to 1 RP; grants; debug | Rejected: cheats, and `research_on_icon_click` is still a click. |
| effect `set_technology` | "sets technology level(s)", with `popup = no` | Rejected: grants the technology instantly and free. |
| effect `add_tech_bonus` | "adds a limited use tech bonus" | Not a start: it is a discount on research the player starts. |
| effects `add_research_slot`, `set_research_slots`, `inherit_technology`, `add_to_tech_sharing_group` | slots / sharing | Not a start. |
| any effect that starts research in a slot | there is none in `effects_documentation.md` | This is the whole problem: nothing the mod could call, from a decision, a scripted effect or the console, begins research. |
| a mod decision that "researches" (`activate_decision`, a `days_remove`, then `set_technology`) | decisions run effects | Rejected: it would bypass the slot, the research speed, the cost and the game's own progress. That is a different game. |
| scripted GUI (`dbg_enable_scripted_gui`) | a mod's own buttons | A button runs an effect, so the missing effect is still missing. |
| keyboard in the tech tree | see above: Escape, Return, ctrl+Tab | Rejected. One thing left to probe live: whether Tab or the arrow keys move a selection (nothing in the interface files says they do). |
| console `window open <name>` | "Opens or closes the specified window" | Opens the tech screen without the hotkey; does not choose a tech. |
| console `human_ai` | "Toggles AI for Human countries" | Not army-scoped: it hands the *whole country* to the AI, research included. Not offered; it would end the experiment. |
| ai_strategy `research_tech`, `research_weight_factor` | steers the AI's research | Applies to AI countries, not the player's. |

### Delegating an army

| Candidate | Verdict |
|---|---|
| any effect that sets AI control of an army | none in `effects_documentation.md` (`add_ai_strategy` is what the mod already uses for directives and postures) |
| console `ai`, `enable_ai`, `disable_ai`, `human_ai` | global toggles; no read-back, wrong scope |
| `moveunit <unit id> <province id>`, `teleport`, `createarmygroup` | a relocation (the documentation calls it moving a unit; `teleport` says so) and a selection-based grouping; neither is an order, and unit ids are not in the log. Not faithful. |

### The clock

| Candidate | Verdict |
|---|---|
| space, kp_plus, kp_minus | space toggles pause (live); the speed keys step the speed but nothing reads the speed back. Space is how the harness pauses, because it is one key press. |
| console `gamespeed <0-5>` | "Set the current game speed, pausing the game at speed 0". **Used** to resume and as the second path to pause. Sets a speed outright, so no toggle to get wrong. |
| console `pause_in_hours` | "Pauses the game after X hours have passed after command is called". **Used** in `exact` mode: a turn of N days is handed to the game with 12 hours of margin, so it stops itself with no overshoot. |
| console `pause_on_trigger` | "Pauses the game when trigger activates". Not used yet; the natural next step for waking on a war without polling. |
| console `fast_forward <days>, <observer>` | the second argument suggests observer mode. Not used; worth one live look next to `pause_in_hours`. |
| console `auto_unpause`, `sleep` | "Not available in release build". |
| console `observe` | switches to no country at all. |

Why pausing is a key and not the console: typing `run llmb_turn.txt` takes about
two seconds of key presses during which the game is still running. A week became
two. A single space is immediate. Resuming does not have the problem (the game is
paused while the command is typed), and the console command sets an exact speed.

### Seeing the game

| Candidate | Verdict |
|---|---|
| the mod's `log` lines | **Used.** The only channel known to reach `game.log`. |
| console `get_var`, `list_vars`, `get_flag`, `list_flags`, `eval_trigger`, `trigger`, `focus_count`, `time` | these print to the console window; the documentation does not say they reach `game.log`. If one does, it is an on-demand probe (free building slots, an advisor) the daily line cannot give. A live check. |
| trigger `is_hired_as_advisor` | exists. A generated probe line per advisor would make `hire_advisor` verifiable exactly instead of by political power falling. A mod change (schema v4). |
| construction and production queues | no trigger or variable exposes them (only finished buildings, counted by `total_constructed_*`). So they are **unverifiable from the log** and come from a save. |
| console `savegame` | "Creates a save file" (the documentation names no argument). **Used.** Written live on 1.20.0: a text save, 66 MB, `<TAG>_<Y>_<MM>_<DD>_<HH>.hoi4` in `save games/`, within a few seconds, with `save_as_binary=no`. The queue is `production.general_lines` ([adapters.md](adapters.md)). Whether `savegame <name>` takes a name is unknown, so the default name is used, and two saves in the same in-game hour are one file (the harness compares modification times, not names). |

## Verification while the clock is stopped

The mod logs once a day, so a paused game says nothing new and the verifier
reported every action "unverified" until a day had passed. Headless fixes that
with the console: each run file ends with two
`effect TAG llm_bridge_emit_state` lines (the mod's own scripted effect, the one
its *Emit telemetry now* decision calls). The first closes the tick that was
open before the action, the second shows what the action did.

Results you will see:

* **Verified:** advisor hired (political power fell), directive raised, theater
  posture set. Those show up in the log at once.
* **Verified through a save** (`log+save`): `queue_construction` and
  `set_production`. See "The save reader" below. With the log alone
  (`headless_reader = "logtail"`) they stay "unverified", because the log cannot
  show them.
* **Unverified for now:** a focus. The probe reports a focus only once
  `focus_progress > 0`, which takes a day. The brief shows it after the first
  day. The system prompt tells the model not to repeat it.
* **Rejected before any key is sent:** an advisor the country cannot afford, an
  unknown state, advisor or equipment id.
* **Declined by the game:** a state the country does not own, no free building
  slot, no free military factory, an advisor slot already taken. The effect's own
  `limit` makes the game do nothing, which the harness sees as "unverified" or,
  for an advisor, as political power not falling.

## The save reader (`log+save`)

`adapters/combined.py`. The log is the live view; a save sits under it:

* **The log wins** for what it has (political power, stability, war support,
  factories, manpower, the running focus and research, wars, events). **The save
  fills** production, the construction queue, divisions, completed focuses and
  fuel, and anything the log has not yet said right after a load. A field that
  came from a save is as old as the save: the brief says
  `(production, construction queue and army as of the save of 1938-02-04)` when
  that is not today.
* **When a save is taken**, never inside a poll: once at the start; before any
  `queue_construction` or `set_production` batch if the last save is not from the
  same in-game day (so the "before" is today's, and set_production can see the
  existing lines); after such a batch, as the last line of the same run file
  (`savegame` after the effects and the telemetry refresh); and every
  `headless_save_days` (default 7, 0 = never) in-game days otherwise, once a turn.
* **How it finds the save**: `mark` the newest modification time in `save games/`
  before the command, then wait (up to `headless_save_timeout`, 30 s) for a
  `*.hoi4` whose mtime is newer and whose size has stopped changing. By time and
  not name because two saves in the same game hour share a name. It never deletes
  or overwrites anything it did not just ask the game to write.
* **Cost**: the game writes 66 MB in a few seconds; reading it is 0.85-1.0 s (see
  [adapters.md](adapters.md)). A turn that acts on construction or production
  therefore costs two saves (one before, one after), about two console runs and
  ~2 s of parsing.
* **Failure**: a binary save is refused with `save_as_binary=no` (set it in
  `settings.txt`), a save that never appears times out, an unreadable one is an
  error. In every case the fields the save filled turn **unknown** (not stale) and
  the reason goes on the result: `queue_construction` is "unverified ... The save
  could not be used: ...", `set_production` is refused with the reason in
  brackets, and the harness does not crash.
* **Names**: the save gives a state by id; the reader reports the name from the
  game's files, and the console checks "state 10" or "capital" against that name.

## Wars

Schema v4 adds one `war` line per enemy per in-game day while the player is at
war and none at peace:

```
LLMB|v5|war|tag=SWE|enemy=GER|divs=84|border=1|surr=3
```

`divs` is the enemy's `num_divisions`, `border` is `is_neighbor_of` the player
(controlled territory), `surr` is `surrender_progress` times 100. Tokens, all in
the install's `documentation/`: `has_war` (triggers_documentation.md:5073),
`every_enemy_country` (effects_documentation.md:3898), `num_divisions`
(dynamic_variables_documentation.md:560), `is_neighbor_of`
(triggers_documentation.md:6075), `surrender_progress`
(dynamic_variables_documentation.md:798, triggers_documentation.md:7718). The
effect is `llm_bridge_emit_wars` in `llm_bridge_telemetry.txt`, called from
`llm_bridge_emit_state`; the variables are set on the enemy's scope, not the
player's.

The reader keeps `state.wars` from them. A war shows **at once**, on its first
line (or the mod's own `war_declared` event, which arrives a day sooner and puts
the enemy on the list with nothing known about it), and comes off only once a
complete day passes without a line for it. Events: `war_declared` (critical: it
wakes the planner and ends the clock wait early) once per new enemy, and
`war_ended` (notable) when the lines stop. A war already on when the harness
starts is a baseline, not news (its `since` is "already on when telemetry
began"). The brief reads
`AT WAR with GER since 1939-09-03 (enemy 84 divisions, borders us, surrender progress 3%)`.

Not provided: fronts. `state.fronts` stays unknown headless; `border` and the enemy
division count are the coarse picture, and there is no per-front supply, pocket or
pressure figure in any trigger the install documents. To read the war lines live,
`headless-check --live` claims "the war lines are readable" (at peace it can only
say it cannot prove them) and `verify-bridge --log` reports them and flags a
literal token or a wrong tag.

## Running it

### Against the fake game (no HOI4)

```bash
hoi4-harness play --headless --fake-game --turns 8 --days 14 \
    --script profiles/headless-demo-script.json --run-dir runs/fake-demo
hoi4-harness play --profile profiles/headless-fake.json
```

`--fake-game` builds `fakegame.FakeGame` in the same process: its scan-code
receiver replaces SendInput and its clock replaces `time.sleep` everywhere in the
stack, so a run is instant and the same every time. Its files are under
`<run-dir>/fakegame` (`--fake-game-dir` moves them): `user/logs/game.log`,
`user/llmb_turn.txt`, and a demo playset. `--game-dir <install>` runs it on the
real game's focus trees, states and advisors instead. `--fake-game` implies
`--live`: there is no window to focus and nothing a keystroke can hurt.

As two processes that share only files (the harness's keys travel as a log of scan
codes, `keys.log`, and the game runs on the wall clock):

```bash
hoi4-harness fakegame --dir runs/fakegame --time-scale 5       # terminal 1
hoi4-harness play --headless --fake-game-files runs/fakegame/user \
    --game-dir runs/fakegame/playset                           # terminal 2
```

`--script FILE` is the offline model: a JSON list of responses, each
`{"text": "", "calls": [{"name": "...", "arguments": {...}}]}`, replayed in order,
then the keyword policy. The scripted provider and `--no-llm` make no paid call.

### Against the real game, and checking its assumptions

`hoi4-harness headless-check --live` tries each claim the mode rests on (the
console clock, the telemetry refresh, `pause_in_hours`) and says PASS or FAIL with
the dates it saw; it works against the fake too, which is what its tests do.

See the live test plan in [live-session.md](live-session.md#10-headless-no-mouse-no-screen).
In short: the mod loaded, the game paused and in front, `HOI4_LOG_PATH` and
`HOI4_GAME_DIR` set, then `hoi4-harness play --headless --live --country SWE`.
Without `--live` it is a dry run: the run files are written to `runs/console/`
and nothing is sent.

## The fake game, and what it proves

`fakegame.py` is the other end of the interfaces: the log, the run file and the keyboard, and (`fakesave.py`) the console's `savegame`. It writes schema-v4
telemetry with the engine's date prefix (one `state`, `army`, `focus`, `research`
set per in-game day, a `war` line per enemy, `evt` lines for directives, postures and
war declarations), reads the run file when a `run` arrives through the key
sink, applies `eval_effect` and `effect` to the mock's world with the refusals
above, and honours the keyboard: console key, typed text, the focus panel's
search, space, kp_plus, kp_minus, and the Escape menu that swallows a key. Typed
text that lands outside a text field triggers hotkeys, as it would.

It proves the **harness**: the log reader, the console channel, the key layer,
the clock logic, verification and the loop work together, and every failure path
has a test. It does **not** prove the **game**: whether HOI4 accepts a given
console line, how it splits it, what `gamespeed` and `pause_in_hours` really do,
whether the probe emits a focus at progress zero, whether `savegame` writes while the game is paused and lands where `save games` is, or whether `PREV` and `surrender_progress` mean in the war lines what the documentation says. Every such assumption is a
step in the live plan, and the fake encodes the documented behaviour so a live
run that disagrees fails visibly against it.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `adapter = "headless"` / `--headless` | | the mode |
| `headless_clock` | `auto` | `auto` (console resume, key pause, each falling back to the other), `console`, `keys`, `exact` (`pause_in_hours`) |
| `headless_game_speed` | 3 | speed the harness runs the game at (`set_game_speed` changes it for the next advance) |
| `headless_reader` | `log+save` | the log every tick plus on-demand text saves; `logtail` (the log alone: no production or queue, so no `set_production`); or `savegame` (a save only: no telemetry, so no refresh) |
| `headless_save_days` | 7 | with `log+save`: at most one periodic save per this many in-game days (0: only at the start and around verified actions) |
| `headless_save_timeout` | 30 | seconds to wait for a save to appear after `savegame` |
| `save_dir` (`HOI4_SAVE_DIR`) | the game's `save games` folder | where the saves are read from |
| `console_refresh` | on for headless | the two `llm_bridge_emit_state` lines at the end of a run file |
| `clock_probe_seconds` | 10 | how long the date must stand still before the game counts as paused; lower it only when the game runs fast |
| `headless_poll_seconds`, `headless_time_scale` | 0.5, 1.0 | polling interval; a multiplier on every real sleep (tests shrink it) |
| `fake_game`, `fake_game_dir`, `fake_game_files` | off | the fake, in this process or by its files |
| `console_dir`, `console_filename`, `console_key` | user dir, `llmb_turn.txt`, `grave` | as for the console channel ([adapters.md](adapters.md)) |

A `ui_scripts.json` in the run directory is still read, but only scripts with no
`click` step are used; the focus script is built in.
