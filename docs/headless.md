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
| See the game | `adapters/logtail.py` reads the mod's daily `LLMB|v3|` lines from `game.log` (a text save is an option, `headless_reader = "savegame"`) | live 1.19.5 |
| `queue_construction`, `set_production`, `hire_advisor` | console: `eval_effect TAG = { ... }` in `llmb_turn.txt`, then console key, `run llmb_turn.txt`, enter, console key | live 1.19.5 |
| `set_ai_directive`, `set_ai_posture`, `clear_ai_directives` | console: `effect TAG llm_bridge_*` (the mod's scripted effects) | live 1.19.5 |
| `set_national_focus` | keyboard only: alt+q, ctrl+f, the localised name, shift+enter | live 1.19.5 |
| Resume | console `gamespeed N` (sets the speed outright), date watched to confirm | **documented, not yet live** |
| Pause | the space key (one press), console `gamespeed 0` as the other path | space live; `gamespeed` not yet |
| Exact turns (`headless_clock = "exact"`) | console `pause_in_hours`: the game stops itself after N days | **documented, not yet live** |
| Fresh telemetry while paused | the run file ends with two `effect TAG llm_bridge_emit_state` | **not yet live** (see below) |

Every key goes out as a hardware scan code (`adapters/keys.py`). The harness
still refuses to send input unless the game is the focused window, so the game
has to be in front when keys go; `--no-window-guard` is the explicit opt-out.

### What it cannot do

Two actions are removed from the tool list, and the system prompt says so
(`agent/prompts.py`, `HEADLESS`), because the model should not plan around a tool
that will not work:

* **`start_research`.** The tech tree has no search box and no keyboard
  navigation: `interface/countrytechtreeview.gui` binds only Escape (close),
  ctrl+Tab and ctrl+shift+Tab (cycle tabs) and Return (confirm a selection that
  already exists). A technology can only be chosen by clicking its icon.
* **`delegate_army_to_ai`.** No effect exists, and the per-army AI toggle is a
  button.

Also still on the mouse, and not offered by any adapter today: unit orders on the
map (#85 is the province layer). The idle research slots stay idle, so headless
turns the "research slot idle" wake rule off.

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
| construction and production queues | no trigger or variable exposes them (only finished buildings, counted by `total_constructed_*`). So those two are **unverifiable from the log**; they stay "unverified" and a person or a save has to confirm. |
| console `savegame <name>` | "Creates a save file". With `save_as_binary = no` the save has production, construction and research. A save-then-read loop would make the queues observable with no mouse. Not built; the plan is in `live-session.md`. |

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
* **Unverified, because the log cannot show it:** `queue_construction` and
  `set_production`. Whether the game did it is in the fake's state in tests, and
  in a live run it is something a person (or a save) has to check.
* **Unverified for now:** a focus. The probe reports a focus only once
  `focus_progress > 0`, which takes a day. The brief shows it after the first
  day. The system prompt tells the model not to repeat it.
* **Rejected before any key is sent:** an advisor the country cannot afford, an
  unknown state, advisor or equipment id.
* **Declined by the game:** a state the country does not own, no free building
  slot, no free military factory, an advisor slot already taken. The effect's own
  `limit` makes the game do nothing, which the harness sees as "unverified" or,
  for an advisor, as political power not falling.

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

`fakegame.py` is the other end of the three interfaces. It writes schema-v3
telemetry with the engine's date prefix (one `state`, `army`, `focus`, `research`
set per in-game day, `evt` lines for directives and postures, a war line on the
mock's scripted war), reads the run file when a `run` arrives through the key
sink, applies `eval_effect` and `effect` to the mock's world with the refusals
above, and honours the keyboard: console key, typed text, the focus panel's
search, space, kp_plus, kp_minus, and the Escape menu that swallows a key. Typed
text that lands outside a text field triggers hotkeys, as it would.

It proves the **harness**: the log reader, the console channel, the key layer,
the clock logic, verification and the loop work together, and every failure path
has a test. It does **not** prove the **game**: whether HOI4 accepts a given
console line, how it splits it, what `gamespeed` and `pause_in_hours` really do,
or whether the probe emits a focus at progress zero. Every such assumption is a
step in the live plan, and the fake encodes the documented behaviour so a live
run that disagrees fails visibly against it.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `adapter = "headless"` / `--headless` | | the mode |
| `headless_clock` | `auto` | `auto` (console resume, key pause, each falling back to the other), `console`, `keys`, `exact` (`pause_in_hours`) |
| `headless_game_speed` | 3 | speed the harness runs the game at (`set_game_speed` changes it for the next advance) |
| `headless_reader` | `logtail` | or `savegame` (no telemetry, so no refresh and no `unknown` fields filled by the log) |
| `console_refresh` | on for headless | the two `llm_bridge_emit_state` lines at the end of a run file |
| `clock_probe_seconds` | 10 | how long the date must stand still before the game counts as paused; lower it only when the game runs fast |
| `headless_poll_seconds`, `headless_time_scale` | 0.5, 1.0 | polling interval; a multiplier on every real sleep (tests shrink it) |
| `fake_game`, `fake_game_dir`, `fake_game_files` | off | the fake, in this process or by its files |
| `console_dir`, `console_filename`, `console_key` | user dir, `llmb_turn.txt`, `grave` | as for the console channel ([adapters.md](adapters.md)) |

A `ui_scripts.json` in the run directory is still read, but only scripts with no
`click` step are used; the focus script is built in.
