# The first live session

Everything that can be built without the game has been. What is left needs one
sitting with a real install, in this order: each step either confirms a piece
works or produces the output the next piece of code is written from. Paste each
step's output into the issue named beside it.

You need: HOI4 installed, a non-ironman game you can save, and this repo
installed with the input extra.

```bash
pip install -e ".[dev,input]"
export HOI4_GAME_DIR="C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"
export HOI4_LOG_PATH="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
export HOI4_SAVE_DIR="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/save games"
```

(Use the plain `Documents` path if yours is not redirected into OneDrive; `doctor`
reports what it found.)

## 1. Before launching: the install's files (#1, #2, #7, #53)

```bash
hoi4-harness verify-bridge --game-dir "$HOI4_GAME_DIR"
hoi4-harness index --game-dir "$HOI4_GAME_DIR" --country SWE
```

- **on_actions MISSING** → those hooks never fire; the name has moved (#1).
- **completion on_actions** → the candidates for focus, research and
  construction completion events (#2). Paste the list; the hooks get written
  from it.
- **ai_strategy types the game never uses** → suspect directive types (#7). If
  any of `conquer`, `invade`, `protect`, `contain`, `befriend`, `antagonize`,
  `ignore` is listed, check `common/ai_strategy` for the current name.
- **index** → real countries and Sweden's actual focus tree (#53).

## 2. Install the mod, load it last

Copy `mod/llm_bridge/` and `mod/llm_bridge.mod` into your `mod/` folder, enable
**LLM Bridge** in the launcher, and put it **last** in the playset
([playsets.md](playsets.md) says why). Start a 1936 game as any country.

Check the **decisions** tab: the *LLM Bridge* categories should show readable
names ("Directive: hold the line", "Target: GER"). Raw keys like
`llmb_posture_defensive` mean the localisation did not load — the BOM question
in #59; say so in #1.

### Doing steps 2 and 5's file changes with a script

`scripts/live_setup.py` makes the file changes a live session needs and puts them back
afterwards. Close HOI4 and the launcher first; it refuses while `hoi4.exe` is in the
process list (a read-only look by image name) or when it cannot read the list.

```bash
python scripts/live_setup.py prepare --expert-ai            # dry run: prints what would change
python scripts/live_setup.py prepare --expert-ai --apply    # drop --expert-ai for vanilla AI
# ... play the session ...
python scripts/live_setup.py restore                        # dry run
python scripts/live_setup.py restore --apply
```

`prepare` (after backing up everything it will touch):

- makes `mod/llm_bridge` and `mod/llm_bridge.mod` in the user folder identical to the repo's
  `mod/llm_bridge` (changed and new files copied; files the repo no longer has removed from the
  installed copy, which the backup keeps);
- writes `dlc_load.json` with `llm_bridge` enabled **last**, and with `--expert-ai` Expert AI
  (Workshop 741805475, [expert-ai.md](expert-ai.md)) just before it. Other enabled mods and keys keep
  their order. `--expert-ai` needs `mod/ugc_741805475.mod` to exist, i.e. the Workshop item
  subscribed and the launcher started once; it refuses otherwise;
- sets `save_as_binary=no` in `settings.txt`, keeping the BOM, line endings and every other line.

`restore` puts back exactly the `dlc_load.json` and `settings.txt` of the newest `prepare` backup
(`--backup NAME` for another), verified against the hashes the backup recorded. The mod files stay
(the mod does nothing unless the playset enables it). A file that did not exist before `prepare`
is renamed `<name>.removed-by-restore-<stamp>`, not deleted.

Both are a **dry run unless `--apply`**, and a dry run writes nothing. Every apply creates a new
folder `live_setup_backups/<YYYYmmdd-HHMMSS>/` in the user folder (the same one the harness
finds: Documents, OneDrive-redirected Documents, or `--user-dir PATH`). Backups are never deleted
or overwritten by the script, and `restore` first saves whatever it is about to replace into a
`pre-restore` backup of its own. Afterwards run `hoi4-harness campaign-check`.

The tests (`tests/test_live_setup.py`) run against temp folders only; the real user folder and
the real process list are walled off in them.

## 3. Telemetry (#1)

Unpause for a few days, take the *Emit telemetry now* decision once, pause.

```bash
hoi4-harness verify-bridge --log "$HOI4_LOG_PATH"
hoi4-harness observe --adapter logtail
```

- Every field under **expanded** is real; anything under **LITERAL** is a token
  that did not expand and needs its name fixed.
- The brief from `observe` should have numbers for political power, stability,
  war support, factories and manpower. #1's acceptance is that brief plus the
  `verify-bridge` output.

### 3a. The telemetry cadence (#130)

The cadence below a day is a chain of hidden events that the generated mod schedules; whether the
game accepts them, fires them at the hour asked, keeps them through a save and load, and how much
they cost, is only known in the game. Needs the mod copied by `live_setup` (the cadence files are
new: `common/scripted_effects/llm_bridge_cadence.txt`, `events/llm_bridge_cadence_events.txt`),
the game loaded and paused, the country marked as in step 2. Do not touch the keyboard while a
run is typing. Everything here goes through the console.

1. Check the mod loaded: `error.log` has no line naming `llm_bridge_cadence` or `llmb_tick`.
2. Baseline, nothing set: unpause at speed 5 for 3 game days, pause. Read the state lines in
   `game.log`: one per day, hour 21 or so. Note the wall-clock seconds the 3 days took.
3. `hoi4-harness play --headless --telemetry-hours 6 ...` (any short headless run, or by hand:
   `effect <TAG> llm_bridge_telemetry_every_6h` in the console). `game.log` gets
   `LLMB|v5|evt|...|kind=20|detail=6`; the run's log says `telemetry cadence: every 6 in-game hours`.
4. Unpause 3 days, pause. Expect four state lines per day, six in-game hours apart (the engine
   prefix `[hh:mm:ss][Y.M.D.H]` shows the hour), each followed by its `army`, `war`, `focus`,
   `research` and `divend` lines, and the first line 6 hours after the set. No two lines with the
   same hour. Note the seconds the 3 days took against step 2: the cost of 4 ticks a day.
5. Set `1` (`--telemetry-hours 1` or `effect <TAG> llm_bridge_telemetry_every_1h`), unpause 2 days:
   24 lines a day, an hour apart, and none at the old 6-hour marks beyond the first firing that
   ends the old chain. Note the seconds per day and the size `game.log` grew by per day.
6. With 1 set, save, load that save, unpause a day: the chain continues, still one line per hour
   (the scheduled event is in the save); a doubled hour means the idempotence failed. Then set `6`
   and `1` and `6` again in a row with the game paused, unpause: still one chain.
7. Set `24` (`--telemetry-hours 24`): after at most one more firing there are no more sub-daily
   lines, and the next days print once a day again. Then `72`: one tick every 3 days.
8. Do the negative: take the generated cadence file out of the installed mod copy (or rename
   `llm_bridge_cadence.txt`), restart, and confirm step 3 now errors in `error.log` and prints no
   `kind=20` line, so the check can fail.

Report: the hours on the lines for steps 4 and 5, seconds per game day at speed 5 for 24, 6 and 1,
`game.log` growth per game day for each, and whether step 6 doubled anything. Any `hours =`
rejection or unknown-effect line in `error.log` goes in verbatim.

## 4. Directives through the mod (#7, #8)

In the decisions tab: *Target: POL*, then *Directive: protect*. Then *Directive:
hold the line*. Then:

```bash
hoi4-harness verify-bridge --log "$HOI4_LOG_PATH"   # events: directive_raised, posture_defensive
hoi4-harness observe --adapter logtail               # AI control: posture defensive | directives: protect POL
```

If both show up, directives and posture are verifiable through the log. Take
*Directive: stand down* and check both disappear.

## 5. A save (#6)

Saves are binary by default. Set `save_as_binary=no` in `settings.txt` (in the
`Hearts of Iron IV` folder under Documents), save the game (not ironman), then:

```bash
hoi4-harness observe --adapter savegame
```

The brief should carry real numbers: political power, manpower, factory counts,
the running focus, research slots, production lines and divisions by template.
Stability and war support appear only with the LLM Bridge mod loaded (the save
stores their base values, not what the game shows); without it the brief says
`unknown`. `hoi4-harness save-inspect` lists what a save contains if a field
looks wrong. What each field reads from is in the savegame section of
[adapters.md](adapters.md).

## 6. Clicking (#3, #8)

Keys reach HOI4 only as hardware scan codes (not pyautogui's), and the panel
hotkeys are the 1.19.5 ones: `alt+q` focus, `w` research, `t` construction, `y`
production, `q` political, `shift+q` decisions, `space` pause. Escape is never
sent: with no panel open it opens the game menu, which eats the keys after it.

```bash
cp profiles/ui_scripts.1.19.json runs/ui_scripts.json
```

The click paths are recorded for 1.19.5 at 1920x1080 (the keys, and the focus and
map-search flows, were run in the game). The research slot and folder tabs are derived
for any resolution (6a); record the other click targets they name at your resolution,
with `--game-dir` set so names and tech positions resolve:

```bash
hoi4-harness calibrate --game-dir "<HOI4 install>"
hoi4-harness check-scripts     # every placeholder valid, every concrete target calibrated
```

Each action is a different flow: **focus** is keyboard only (`alt+q`, `ctrl+f`,
the focus's name, `shift+enter`); **construction** is `t`, a building icon, the
map search for the state, a click at the map centre; **research** is `w`, a free
slot, a folder tab, then a computed grid cell; **hire_advisor** is `q`, a slot,
a candidate. Watch for a panel that does not close itself afterwards; there is no
Escape to rely on.

Try one action at a time, dry run first (it logs the path and sends nothing),
then live:

```bash
hoi4-harness play --adapter logtail+input --actions start_research,note,advance_time --turns 1
hoi4-harness play --adapter logtail+input --actions start_research,note,advance_time --turns 1 --live
```

What comes back per action is **verified**, **rejected** (the game did not
change — the script is wrong somewhere), or **unverified** (the reader cannot
see the proving field). Through the log reader, focus, research, construction
and production are unverified until the mod emits them or the save mapping
reads them — expected at this stage; the result is still the evidence that the
click path works, so note what you saw happen in-game. Paste a transcript
excerpt into #3; delegation's result into #8.

### 6a. Another resolution, UI scale or window mode (#3)

`start_research` no longer needs `research.free_slot` / `research.tab.<folder>`
calibrated: it derives them from the game's interface files and your display settings
(predicted values per resolution: [adapters.md](adapters.md), the table under
`start_research`). Only 1920x1080 at UI scale 1.0 has been measured. To check another
setup, with the game running in it:

1. Say what it is: window mode and resolution (Options > Graphics), the UI scale
   (`gui_scale` in `settings.txt`), and whether the window is borderless.
2. `hoi4-harness play --adapter logtail+input --actions start_research,note,advance_time --turns 1`
   (a dry run; it logs `click research.free_slot at (x, y)` and the tab point
   and sends nothing). With the game open, press `w`, take **one screenshot** of the
   screen with the research panel open and then **one more** with the tech tree open
   on the infantry tab, and mark on them the first free slot's centre and the
   infantry tab's centre.
3. Compare the logged points with the marks. Within about 10 px is a pass; report
   the setup, the logged points and the marked ones in #3, one row per setup.
4. If it refuses (`nothing was sent`), the message names why: the settings say a size
   the window is not (change the setting, or the window), or the window or settings
   could not be read. Report that too: in borderless mode the harness trusts the live
   window size, so a refusal there means `window.client_rect` could not see it.
5. Wide screens (3440x1440) and 16:10 (1920x1200) are the ones to look at first: if
   the panel or the tabs are not where the logged points say, the scale does not follow
   the height alone, and the message to #3 should include the screenshot's size.

### 6b. The rotated tech tabs (#3)

The armour, air, industry and electronics tabs are drawn turned: the years run
down the screen. The click points for them are computed from the interface file
(`techtree.py`) but only the infantry tab has been measured. In the research tree,
1920x1080, UI scale 1.0, the tab **not scrolled** (scrollbars at their start), put
the cursor on the centre of each tech's icon and compare with the prediction
(within about 10 px is a match; the percent-placed armour tab may be a little
further off). Predictions are what
`tech_point` returns, screen px (x, y):

| Tab | Technology | Predicted |
|---|---|---|
| Armour (`UP`, percent box) | `gwtank` (the root, top middle) | (948, 388) |
| | `basic_light_tank` | (598, 612) |
| | `basic_heavy_tank` | (1298, 612) |
| Armour, new tank design (`UPPER_LEFT`) | `gwtank_chassis` | (1168, 272) |
| | `armor_tech_1` | (318, 535) |
| | `improved_light_tank_chassis` | (905, 622) |
| Air (`UP`, no left margin) | `early_fighter` | (620, 438) |
| | `CAS1` | (320, 612) |
| | `tactical_bomber1` | (1564, 612) |
| Air, BBA (`UP`, no left margin) | `iw_small_airframe` | (358, 438) |
| | `iw_large_airframe` | (876, 438) |
| | `early_bombs` | (1682, 438) |
| Industry (`UP`) | `basic_machine_tools` | (336, 438) |
| | `construction1` | (1049, 612) |
| | `fuel_refining` | (1756, 612) |
| Electronics (`UP`) | `electronic_mechanical_engineering` | (361, 431) |
| | `radio` | (211, 581) |
| | `atomic_research` | (1374, 431) |

The names are ids; the tab shows localised names, so find each tech by its year
row and by its column (the light, medium and heavy tanks are three columns four
cells apart; `CAS1` is the leftmost air column).
If a tab is off by a constant, that is the origin (set
`research.tree.origin`); if the columns are mirrored or the rows run the other way,
the format rule is wrong. Report the measured centres in #3. The artillery tab is
not computed (its tree is a percent wide at an absolute `x`): measure one tech
there too (`interwar_artillery`, the middle of the second row) and report it, with
the predicted `x` 1135 under the rule that gives the other tabs, if you want it
enabled.

## 7. The console channel (#83)

Do this in a non-ironman game, with the mod loaded and telemetry flowing (step 3),
country known. The point is to settle the UNVERIFIED list in
[adapters.md](adapters.md#console-channel----effects-through-run-instead-of-the-ui-83).
One check at a time, unpaused for a day between them.

1. **Folder and syntax, with something harmless.** Put a file `probe.txt` in the
   game's user directory (the folder with `logs/` and `mod/`) holding one line,
   your tag in place of `SWE`:
   `eval_effect SWE = { add_political_power = 1 }`. In the game press the key
   left of `1`, type `run probe.txt`, `enter`. Political power rises by 1 (top
   bar) and `game.log` shows no error: the folder and the inline syntax are both
   right. Nothing happens, or the console says it cannot open the file: try the
   install folder, then a `script/` folder inside the user directory, and set
   `HOI4_CONSOLE_DIR` to the one that works. The file opens but the command
   errors: `game.log` names it; report that line. Press the console key again to
   close it, and note whether it did, and whether the console kept the text.
2. **With and without `-debug`.** Repeat 1 with the game started normally and
   with `-debug`; say which worked.
3. **Dry run, then live, for the harness.** One action at a time from a Python
   prompt (the input driver refuses when the game is not the focused window, so
   the script waits for you to click it):

   ```python
   import time
   from hoi4_harness.adapters import build_adapter
   from hoi4_harness.config import HarnessConfig
   from hoi4_harness.types import ActionCall

   cfg = HarnessConfig.from_env()      # HOI4_GAME_DIR, HOI4_LOG_PATH, HOI4_CONSOLE_DIR set
   cfg.adapter, cfg.dry_run = "logtail+input", True       # False for the live run
   game = build_adapter(cfg)
   print(game.read_state().country)
   call = ActionCall("hire_advisor", {"advisor_id": "SWE_some_advisor"})
   time.sleep(5)                       # click the game window now (live run only)
   print(game.apply(call))
   ```

   The dry run writes `runs/console/*-llmb_turn.txt` and returns `not_executed`
   with the keys it would send (`press grave`, `type run llmb_turn.txt`, `press
   enter`, `press grave`); read the file. A live run returns `changed.channel =
   console`, plus `unverified` until a fresh day of telemetry has passed: unpause
   a day and the next brief shows the change.
4. **Construction.** Queue one civilian factory in a state you own that has a
   free slot. In the construction panel it shows in the queue, using civilian
   factories; `game.log` shows the telemetry. Answers: does it use the queue, and
   does `level = 1` make one item. Then try a state you do not own: nothing
   should queue (the effect's limit), and the result should say the game did not
   change.
5. **Production.** Add a line for `infantry_equipment_1` with 1-2 factories. It
   appears in the production panel; note whether it is unlimited like one made
   by hand.
6. **Advisor.** Hire a cheap one. Political power falls by exactly the base cost,
   and the advisor is in the slot. Try one you cannot afford: the harness refuses
   before any key.
7. **Directives.** `ActionCall("set_ai_directive", {"directive": "protect", "target":
   "POL"})`, then `set_ai_posture` with `{"posture": "offensive", "theater": "GER"}`, then
   `clear_ai_directives`. `game.log` shows the mod's
   `LLMB|v2|evt` lines (kinds 13, 14, 15, 12) as with the decisions, and the
   brief's AI control line follows.
8. **Ironman.** Optionally try the console in an ironman game and say whether it
   is refused and whether the save is marked.

Paste the results into #83: the folder, whether `-debug` was needed, and the queue
and production answers. They go in the UNVERIFIED list in adapters.md as settled.

## 8. With API keys (#22, #23)

**Conformance (#22).** The suite in `tests/live` runs the same checks on every provider:
tool schema accepted, a call parsed back into a valid `ActionCall`, a tool result
accepted for a second round, usage populated, a neutral stop reason, a rejected key
classified fatal, and prefix caching where promised. The same claims run offline, on every
`pytest`, against recorded responses for all four providers (anthropic, openai-compatible,
gemini, claude-cli) in `tests/test_provider_conformance.py`.
A paid provider runs only when **both** are set: `HOI4_LIVE_PROVIDER_TESTS=1` and that
provider's key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`; for a local
OpenAI-compatible server, `HOI4_LIVE_OPENAI_BASE_URL` instead of a key). The `scripted`
provider runs the same checks for free, so a bare `pytest` never bills anything.

The one-command way (any of `HOI4_LIVE_PROVIDER_TESTS`, `HOI4_LIVE_TESTS` or
`HOI4_LIVE_PROVIDERS` set to `1` opts in; the command sets it for you and never prints a key):

```bash
hoi4-harness provider-check --plan                       # what would run and the worst-case cost; sends nothing
hoi4-harness provider-check --provider anthropic         # keys come from your shell: ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY
hoi4-harness provider-check --provider claude-cli --max-calls 4   # needs `claude` on PATH, uses the subscription login, no key
```

The raw form, for a fork or a CI job:

```bash
# what it would cost, before anything is sent: set the rates, run with -k nothing
# selected, or just read the plan line it prints first
export HOI4_USD_PER_M_INPUT=...  HOI4_USD_PER_M_OUTPUT=...   # from the price sheet
HOI4_LIVE_PROVIDER_TESTS=1 HOI4_LIVE_MAX_CALLS=8 ANTHROPIC_API_KEY=... pytest tests/live -v -rs
HOI4_LIVE_PROVIDER_TESTS=1 OPENAI_API_KEY=... pytest tests/live -v -rs
HOI4_LIVE_PROVIDER_TESTS=1 HOI4_LIVE_OPENAI_BASE_URL=http://localhost:11434/v1   HOI4_LIVE_OPENAI_MODEL=<local model> pytest tests/live -v -rs
```

Before the first call it prints the plan: calls per provider (Anthropic 5, OpenAI and
Gemini 3 each, claude-cli 2), a worst-case token and dollar bound (no cache discount, every call at
`max_tokens`; dollars only when the rates are set), and the hard cap. `HOI4_LIVE_MAX_CALLS`
(default 12) is counted across every provider in the run; the call past it is refused
before it is sent. `HOI4_LIVE_<PROVIDER>_MODEL` picks the model. The skip logic, the cap
and the request shapes of all three clients are tested without a key in
`tests/test_live_conformance.py`.

**Token accounting (#23).** Run a priced play, then export the provider's usage for the
same window and reconcile (details and export formats in
[cost-and-timing.md](cost-and-timing.md#reconciling-against-a-usage-export)):

```bash
hoi4-harness play --provider anthropic --turns 20 --run-dir runs/priced
hoi4-harness reconcile runs/priced/transcript.jsonl usage_export.json   --rate "<model>:input=..,output=..,cached=..,write=.."
```

Paste the output into #22 and #23.

## 9. The measurements (#10, #14)

With a real model configured and `tiktoken` installed:

```bash
pip install tiktoken
hoi4-harness measure --campaign wartime_1939_1941 --provider anthropic
hoi4-harness experiment winter_crisis --seeds 5 --provider anthropic
hoi4-harness experiment defensive_war --seeds 5 --provider anthropic
```

`measure` now counts tokens with a real tokenizer and reports billed tokens and
cache hits; the table in [cost-and-timing.md](cost-and-timing.md) gets
replaced with it (#14). The experiment tables go into
[hybrid-control.md](hybrid-control.md#the-experiment) with whatever they show,
including "no difference" (#10). Against the mock these measure the harness;
against the live game (`--adapter logtail+input`, once steps 3-6 hold) they
measure the thing the project exists to ask.

## 10. Headless: no mouse, no screen

[headless.md](headless.md) says what the mode is and what was checked in the game's
documentation. Everything below it is **assumed from the documentation** and
encoded in the fake game; this is the sitting that finds out which assumptions
the real game keeps. Run it with the game loaded and **paused**, SWE, the LLM
Bridge mod last in the playset (**regenerated for schema v5**: copy the repo's
`mod/llm_bridge` over the installed one, and **restart the game**, since mods load
at start), `save_as_binary=no` in `settings.txt`, and the game window in front. Keep your hands off
the keyboard and the mouse from step 4 on: the harness types into whatever has
the focus, and a stray key lands in the game.

Set once:

```bash
export HOI4_GAME_DIR="C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"
export HOI4_LOG_PATH="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
export HOI4_SAVE_DIR="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/save games"
```

(`--country SWE` below keeps the reader to Sweden. The game need not be in
`-debug`: the console is already known to work without it.)

**0. Offline first, no game needed.** What to expect from every later step:

```bash
PYTHONPATH=src python -m pytest -q
hoi4-harness headless-check --fake-game --run-dir runs/fake-check      # 9 of 9 passed
hoi4-harness play --profile profiles/headless-fake.json --run-dir runs/fake-demo
```

**1. Pre-flight, nothing sent.**

```bash
hoi4-harness doctor --headless --country SWE
hoi4-harness prompt --headless --game-dir "$HOI4_GAME_DIR" | grep -A3 "headless"
```

`adapter ok headless: read=True write=True`, and the prompt names `start_research`
and `delegate_army_to_ai` as unavailable.

**2. Read only.** Let the game run a few days, pause it, then:

```bash
hoi4-harness observe --headless --country SWE --game-dir "$HOI4_GAME_DIR"
```

A brief with real political power, stability, factories and manpower. `game.log`
should hold `LLMB|v5|state|...|tag=SWE|...` lines, one per game day since the
mod loaded (a `v3` line means the old mod is still loaded: the reader refuses it
by name). At peace there are no `war` lines, and the brief has no `AT WAR` line.
No input is sent; the mouse does not move, and with `observe` no save is taken
either (a dry run never sends `savegame`; production and the queue read
`unknown` until step 5).

**3. Dry run of the actions.** Writes the run files and logs the keys; sends nothing.

```bash
hoi4-harness play --headless --country SWE --game-dir "$HOI4_GAME_DIR" \
    --operational-control ai --script profiles/headless-live-script.json \
    --turns 1 --days 1 --run-dir runs/live-dry
```

Every action `not_executed` with "dry run: would ...". Read
`runs/live-dry/console/*-llmb_turn.txt`: the `eval_effect SWE = { ... }` and
`effect SWE llm_bridge_*` lines, each file ending in two
`effect SWE llm_bridge_emit_state`. (The turn waits up to 30 s for a date that
never moves; that is the dry run, not a hang.)

**4. The claims the clock rests on.** Game paused and in front.

```bash
hoi4-harness headless-check --live --country SWE --game-dir "$HOI4_GAME_DIR"
```

Takes one to two minutes (each "it stopped" proof waits the probe window, and the
save claim waits for a 66 MB file). Expect `9 of 9 passed`. In the game: the date
at the top moves during the three clock claims and stops after each, and
`game.log` shows the `[Y.M.D.H]` prefixes advancing. The two new claims read, for
example:

```
PASS  savegame writes a text save the harness can read: SWE_1938_02_04_22.hoi4 (66 MB, read in 1.0 s): 4 production line(s), 2 queued building(s), 12 division(s); save of 1938-02-04
PASS  the war lines are readable: at peace: nothing to read (the war lines can only be proved in a war; declare one, or run this again then)
```
What a FAIL means, and the setting that routes around it:

| FAIL | The assumption that broke | Set |
|---|---|---|
| the log has telemetry | mod not loaded, or `HOI4_LOG_PATH` wrong; take *Emit telemetry now* | fix that first |
| refresh: two fresh state lines | `effect SWE llm_bridge_emit_state` did not log (two `state` lines with the same date should appear in `game.log`) | `console_refresh = false`: actions stay "unverified" while paused |
| `gamespeed 3` starts the clock | the console command does not set the speed, or `run` stopped reading the file | `--headless-clock keys` |
| the space key stops it | the key did not reach the game (focus? the console left open?) | `--headless-clock console` |
| `gamespeed 0` stops it | informational only; space is the primary pause | none |
| `pause_in_hours` stops the game | the command differs from its one-line description | do not use `exact` |
| `savegame` writes a text save | no new `*.hoi4` in `save games/` within 30 s (is the console key right? is `HOI4_SAVE_DIR` the folder the game writes to?), or the save is binary (the message says `save_as_binary=no`), or it is not 1.20 layout (the message names the missing block) | `--headless-reader logtail`: production and the queue stay unseen, `set_production` is not offered |
| the war lines are readable | `wars are unknown` (old mod, or no complete day yet: run a day), a literal `war.divs=[?llmb_war_divs]` (that token did not expand), or `state lines tagged GER` (`[PREV.GetTag]` is the enemy, not us: the scope in `llm_bridge_emit_wars` is wrong) | fix `llm_bridge_telemetry.txt`; at peace this claim only proves the field exists |

Write down, from the game, **how many seconds a day takes at speed 3**: it sets
`clock_probe_seconds` (which must be longer than a day).

**5. One scripted campaign, live.** Hands off.

```bash
hoi4-harness play --headless --live --country SWE --game-dir "$HOI4_GAME_DIR" \
    --operational-control ai --script profiles/headless-live-script.json \
    --turns 2 --days 7 --run-dir runs/live-headless
```

Afterwards, per action (`runs/live-headless/transcript.jsonl`, `game.log`, and
the game itself):

| Action | Result in the transcript | `game.log` | In the game |
|---|---|---|---|
| `set_national_focus` SWE_defense_act | `unverified` | no `focus` line right after it; one from the next day on: `LLMB\|v3\|focus\|tag=SWE\|id=SWE_defense_act` | the focus is running (top left) |
| `queue_construction` state 38 | `done and verified` (the save shows one more civilian factory in state 38); `changed.channel = console`. If it says `unverified`, read the tail: `The save could not be used: ...` | nothing; `built_civ` rises on completion | a civilian factory in Gavleborg's queue |
| `set_ai_directive` protect POL | verified | `evt ... kind=13 detail=POL`, `kind=14 detail=protect POL` | the AI control line in the brief |
| `set_ai_posture` defensive GER | verified | `evt ... kind=13 detail=GER`, `kind=15 detail=defensive GER` | the brief shows `GER: defensive` |

The questions this step answers, each a plain yes or no:

* The keys: did the console open and close each time, with nothing left open? Did
  the game ever show the Escape menu, a panel left open, or text typed into the
  wrong place?
* After each console run, did `game.log` get **two** new `state` lines with the
  same date (the refresh)? If the game was paused, did the date stay put?
* Was anything wrong because a run file was only partly read? Compare
  `llmb_turn.txt` in the user directory with the copy under
  `runs/live-headless/console/`.
* Did each turn advance exactly 7 days, and was the game paused at the end?
  (`turn_end` dates in the transcript; the PAUSED banner.)
* Did the pointer ever move? It must not.

**5b. The save loop, on its own.** The claim the new reader rests on. Game paused,
in front, hands off. Use the save script (it queues a civilian factory in state 38
and opens an artillery line; **free one military factory first**, by taking the
stray empty line out of the production screen by hand or by starting from a save
that has one: with every factory busy the harness refuses the production order
before sending it, which is correct and is also worth seeing once):

```bash
hoi4-harness play --headless --live --country SWE --game-dir "$HOI4_GAME_DIR" \
    --operational-control ai --script profiles/headless-live-save-script.json \
    --turns 2 --days 3 --run-dir runs/live-save
```

Expected, in order (`runs/live-save/transcript.jsonl`, `runs/live-save/console/`,
the `save games` folder, the game):

1. **A save at the start**: before the first turn a `savegame` run
   (`runs/live-save/console/*-llmb_turn.txt` holds the single line `savegame`),
   and a new `SWE_<date>_<hh>.hoi4` in `save games/` a few seconds later. The
   first brief reads `Production: ...` and `Construction: ...` with the numbers in
   the production and construction screens, followed by
   `(production, construction queue and army as of the save of <date>)` only if
   the save is from an earlier day than the brief.
2. **Before the actions**, if that save is not from today's in-game day, a second
   `savegame` run; then one run file whose lines are
   `eval_effect SWE = { 38 = { if = { limit = { is_owned_by = SWE free_building_slots ... add_building_construction ...`,
   `eval_effect SWE = { add_equipment_production = { equipment = { type = artillery_equipment_1 } requested_factories = 1 } }`,
   two `effect SWE llm_bridge_emit_state`, and **`savegame` last**.
3. **Results**: `queue_construction` -> `done and verified`, `set_production` ->
   `done and verified`. In the game: the civilian factory is in the queue, and
   there is an artillery line with one factory.
4. **The next brief** shows `Construction: 1 queued` (more if the game already had
   some) and `Production: ... artillery_equipment_1 x1`.
5. Timing: from the `savegame` keys to the verified result should be 5-15 s. Note
   how long the file takes to appear (the harness waits up to 30 s).

Answer, each yes or no: Did the file appear in `save games/` with the console
still closed afterwards? Was it text (open it: `HOI4txt` on the first line)? Does
the game's own autosave rotation get disturbed (`autosave*.hoi4` still there)? Did
two `savegame` commands in the same in-game hour give one file or two (the harness
copes either way, but the answer tells whether `mark` by mtime was needed)? Does
the `amount=` in the queue entry match the levels you queued (two queued levels
read as `amount=2` in the checked save)?

Failure on purpose: set `save_as_binary=yes`, run the same command, and expect
`queue_construction` -> `unverified ... save_as_binary=no` and `set_production`
refused with the same hint in brackets, nothing crashed, and the factory still
queued in the game. Put it back to `no`.

**5c. A war.** The war lines are a mod change and have only been seen in the fake.
Pick a start with an imminent war, or declare one yourself in the game (the console
`declare_war` or the diplomacy screen: any war the player is in will do), then:

```bash
hoi4-harness observe --headless --country SWE --game-dir "$HOI4_GAME_DIR"
grep -c "LLMB|v5|war|" "$HOI4_LOG_PATH"
```

Expected `game.log` lines, one per enemy per day while the war lasts, none at peace:

```
[..][1939.09.03.21]: LLMB|v5|war|tag=SWE|enemy=GER|divs=84|border=1|surr=0
```

and in the brief `AT WAR with GER since 1939-09-03 (enemy 84 divisions, borders us,
surrender progress 0%)`. Then `hoi4-harness verify-bridge --log "$HOI4_LOG_PATH"
--country SWE` must show `war lines: N, enemies GER` and no `LITERAL` or `TAG` line.
Answer: Is `tag=` the player (not the enemy: that is `[PREV.GetTag]` inside
`every_enemy_country`)? Is `divs` plausible against the intel screen (it is the
enemy's own count, which may be more than the player can see)? Is `surr` 0-100 or
0-1 times 100 (the documentation gives no unit)? Does `border=1` match who actually
touches whom? Then, in a play run (`--turns 3 --days 14`): the clock stops early on
the declaration and the brief it wakes with names the enemy; when the war ends
(peace, or a capitulation) the lines stop and the next brief says
`War ended: GER`.

**6. The advisor.** Pick an affordable political advisor from
`common/characters/` (a `slot = political_advisor` block with a `cost`), add
`{"name": "hire_advisor", "arguments": {"advisor_id": "<character id>"}}` to a copy
of the script and run it as in step 5 with `--turns 1`. Expected: `done and
verified` (political power fell between the two refresh lines: compare `pp=` in
the last two `state` lines), and the advisor is in the political panel in the
game. Then try one you cannot afford: refused before anything is written, and the
console never opens.

**7. The exact clock.**

```bash
hoi4-harness play --headless --live --country SWE --game-dir "$HOI4_GAME_DIR" \
    --headless-clock exact --turns 3 --days 7 --run-dir runs/live-exact
```

Expect each turn 7 days (8 at most), the game stopped by itself at the PAUSED
banner, and no pause key press after the first turn. If `headless-check` failed the
`pause_in_hours` claim, skip it.

**8. Failure paths, on purpose.** One at a time, with the script edited:
`queue_construction` in a state Sweden does not own (the game should queue
nothing; look at the queue), and a count of 10 civilian factories in one state
(only the free slots should queue). Both come back `unverified`; the game must not
have queued what it had no slot for.

**9. Probes that decide what to build next.** Each is one line typed by hand into
the game's console (`grave`), outside the harness. Say what happened:

* `get_var llmb_pp`, `list_flags SWE`, `eval_trigger SWE = { has_tech = infantry_weapons }`:
  does the answer also reach `game.log`? If yes, on-demand probes (free building
  slots, an advisor) are possible without a mod change.
* `fast_forward 3`: what does it do, and does it stop by itself?
* `savegame headless_probe`: does the console command take a name? (The
  documentation says only "Creates a save file".) If it does, the harness can write
  to a distinct name and never collide with a save from the same game hour.
* Open the tech screen (`w`) and try Tab and the arrow keys: does anything select?
  If one does, `start_research` has a keyboard path after all.

**Not available headless, and why** (so a failing step is not mistaken for these):
`start_research` (the tech tree is click-only; no effect or console command starts
research faithfully), `delegate_army_to_ai` (no effect), changing a running
production line (only `add_equipment_production`, which adds a line), unit orders
and per-front figures (mouse-only; no trigger exposes them). The `war` lines give
the enemy list and a coarse picture, not fronts.

**Decision.** If steps 4, 5 and 5b hold, headless is the default for everything but
research and delegation, with construction and production verified. If only the
refresh fails, the mode still works but every log-verified action reads
"unverified" until a day has passed. If only the save claim fails, use
`--headless-reader logtail`: construction stays unverified and `set_production` is
not offered. If the war claim fails, the war lines are wrong but nothing else is:
fix `llm_bridge_emit_wars`.

## 11. Calibrating the tilted map camera (#85)

The model and the four points measured so far are in
[adapters.md](adapters.md#the-tilted-camera-a-homography-measured-4-points). Four
points fit a homography exactly, so this sitting is what tests it. Read only: the
harness sends nothing; you move the mouse and read the screen.

1. Game loaded and paused, 1920x1080, SWE, no windows open over the map. Note the
   zoom step; everything below holds for that step and that view only.
2. Open the map search, search for a state (Sodermanland), let the camera jump
   and zoom in. Take a screenshot. Do not pan or zoom after this.
3. In the screenshot, find at least **six** victory-point city markers and read
   each marker's screen position (+-3 px). Hovering gives the province id; the
   names are in `localisation/english/victory_points_l_english.yml`
   (`VICTORY_POINTS_<province>`). Use markers spread over the screen, not along
   one line. The four known at this camera: Stockholm 6050 (1488, 579), Uppsala
   9149 (1441.5, 418.5), Gavle 11091 (1047, 171), Falun 156 (483, 108); they are
   only a starting set, and a different view needs a fresh measurement.
4. Write the readings as `PROVINCE:SX,SY`. Pick two or three of the cities that
   are far from the others and keep them out of the fit (`--hold`).
5. Fit on the rest and predict the held-out ones:

   ```bash
   PYTHONPATH=src python scripts/predict_markers.py --game-dir "$HOI4_GAME_DIR" \
       --ref 6050:1488,579 --ref 9149:1441.5,418.5 --ref 11091:1047,171 --ref 156:483,108 \
       --ref <p>:<x>,<y> --ref <p>:<x>,<y> --hold <p>:<x>,<y> --hold <p>:<x>,<y>
   ```

6. Read the output. `model: perspective` with 5+ points prints real residuals; a
   residual over 8 px is refused (a marker misread, or the map is not a plane
   under this camera). `model: flat` means the camera is not tilted at this view,
   and the flat camera is used. The held-out lines print `error` in px.
7. Pass if every held-out city is within about 8 px of its prediction. If they are
   not, say so in #85 with the table: the model is not a homography at this
   zoom, or the marker anchor is not the stack point (check by adding the type 0
   or 9/10 anchors instead).
8. Record the matrix and the view (zoom step, what the search centred on) in #85.
   A matrix is valid for one view only; moving the camera needs a refit.
9. Then click-test: `screen_point(layer, "province", id, camera)` for a province in
   view, move the mouse there yourself and see that the tooltip names that province.

## 12. Division orders (#85)

[unit-orders.md](unit-orders.md) says what is built and what each assumption is. This is
the sitting that finds out whether the game keeps them. It needs step 3 (telemetry),
step 7 (the console channel) and step 11 (the camera) to have passed, and a game in which
**your country is at war with divisions on the front**: a 1939 start as Poland, or a save
a few weeks into any war, non-ironman, paused, 1920x1080 at UI scale 1.0, the LLM Bridge
mod last. Keep your hands off the mouse and keyboard whenever the harness is sending.

Order matters: camera validation first, then one move, then one attack. Stop at the
first step that does not give the expected result and paste what you saw into #85.

**A. The front picture (no input).** With the mod reloaded (the new files are
`llm_bridge_divisions.txt`; since #130 the daily hook is `llm_bridge_daily_tick` in
`llm_bridge_cadence.txt`), unpause two days, pause.

```bash
grep "LLMB|v5|div" "$HOI4_LOG_PATH" | tail -12
hoi4-harness observe --adapter logtail --country POL
```

Expected: a block of `LLMB|v5|div|tag=POL|state=<id>|ctl=<TAG>|own=<n>|own_weak=<n>|foe=<n>|foe_weak=<n>`
lines, then `LLMB|v5|divend|tag=POL|n=<how many>`, every day; and a brief with a
`Front picture (...)` line whose counts you can check against the map by hand (look at
two or three states: the number of counters should match `own` and `foe`). Things that
can go wrong, and what each says: no `div` lines and no `divend` means the on_actions file
did not load (the game's `error.log` will say); `divend` but no `div` lines while divisions
stand on the front means `ROOT` or `OWNER` did not resolve as assumed; a literal
`[?llmb_f_own]` or `[THIS.GetID]` in a line means that token does not expand in a state
scope (paste the line). Also run `effect POL llm_bridge_emit_front` from the console once
and check that it prints a run on demand.

**B. The camera (this settles the numbers orders aim with).**

1. Console: `goto_province <P>`, P a province on the front with a victory-point city.
   Do not pan, zoom or click afterwards. Screenshot.
2. Read the screen position of at least five VP markers (step 11's method) and run
   `scripts/record_camera.py --fit-province <P> --ref ... --hold ... --out runs/camera.json`
   with two held out. Expected: `model: perspective` (or `flat`), held-out errors under 8
   px, and a **centre rule** table: one rule within a few map pixels of where the viewport
   centre falls. That answers "where does `goto_province` centre the view"; paste the table.
   `validated: True` is printed only when every held-out marker is within 8 px.
3. Second province, to test the slide: `goto_province <Q>`, a province 100+ map pixels from
   P, and **without recording anything** read one marker. The record predicts where it should
   be: `CameraRecord.load("runs/camera.json").camera_for(layer, Q)` then `.to_screen(*layer.anchor(city, 38))`.
   Within about 10 px passes; a constant offset means the zoom changed on centring
   (re-record per view, or set the zoom first); anything else means the shift model is wrong
   and orders may only be given in the view the fit was recorded in.
4. Unit counters: hover a stack in P and note whether the counter sits at the province's
   **type 0** anchor (`layer.anchor(P, 0)` through the camera). If it is offset, say by how
   many px; that is `--counter-offset dx,dy`.

**C. One move order.** `profiles/unit-orders.json` turns the actions on (`unit_orders`,
`camera_path`, the console refresh that also asks for the front picture, dry run on); set
`HOI4_GAME_DIR`, `HOI4_LOG_PATH` and `HOI4_COUNTRY` as before. Pick two adjacent states on the front: A holding your divisions (the picture says so) and
B with none of the enemy's, so the order is a plain move. Dry run first, from a Python
prompt as in step 7:

```python
cfg = HarnessConfig.from_file("profiles/unit-orders.json")   # dry_run is true; set it False to go live
game = build_adapter(cfg)
print(game.apply(ActionCall("move_division", {"from_state": A, "to_state": B})))
```

Expected: `not_executed`, the message listing `drag (x0, y0) -> (x1, y1) (box over state A)`
and `right-click (x, y) (target province ...)`, and `runs/console/*-llmb_turn.txt`
holding `goto_province <p>`. Check by eye that the box covers the counters of A and that
the right-click point is inside B on the screen. Then live (`dry_run` False, click the game
window within five seconds): the console opens and closes, the view jumps, the divisions of
A are selected (the box), and a movement arrow appears towards B. The result is
`unverified` with `order #1 sent`. Unpause two days: the `Order:` line should read
`underway: N division(s) have left state A`, and the front picture's `own` count for A falls
and B's rises (if B is listed). A selection that took nothing (no arrow) is the likeliest
failure: the counter offset or the camera.

**D. One attack.** An enemy-held state E adjacent to A that the picture shows with enemy
divisions. `ActionCall("attack_province", {"from_state": A, "to_state": E})`. Dry run
shows the same shape; live, the divisions get an attack arrow into a province of E. Unpause
a few days: `Order:` goes `landing: enemy divisions in state E went x -> y` while the
battle is fought and `taken` if the state falls to you. Paste the `div` lines for A and E
before and after.

Then, if all four held: a short scripted-controller session on the live front, as a
smoke test of the plumbing only, not a result:

```python
from hoi4_harness.tactics import ScriptedController
from hoi4_harness.tactics.bridge import ControllerBridge
bridge = ControllerBridge(layer, ScriptedController())
print(bridge.decide(game.read_state()))       # look at the actions it would take; apply none yet
```

## 13. Rival mode: steering an AI country while you play another (#31, #33, #91)

[rival.md](rival.md) says what the mode is and what each safety rule is. Everything below
the offline step is **assumed from the documentation and the fake game**. This is the sitting
that finds out which assumptions the real game keeps, and it is the first time the harness
types into a game while you are at the keyboard, so go in this order and stop at the first
step that does not give the expected result. Paste each step's output into the issue named at
the end.

Setup: a new 1936 single-player game as **GER** (country A, you), the LLM Bridge mod last in
the playset and **regenerated for schema v5**, the game restarted since. B is **POL**. The
console works without `-debug`. Use the same three `export`s as step 10.

**0. Offline first, no game needed.** The whole stack against the fake game, with a fake person
who plays 45 s, pauses 90 s and repeats:

```bash
hoi4-harness play --rival SWE --fake-game --provider scripted \
    --script profiles/rival-live-script.json --until 1936-09-01 --run-dir runs/rival-fake
```

Expect `marked: the mark event and a state line for SWE`, `Window on ...: 2 ran`, a second
window, `SWE unmarked`, exit code 0.

**1. The flag, by hand (no harness).** In the game's console type
`effect POL llm_bridge_mark_harness`, unpause for 2 days at speed 3, pause.

```bash
grep "LLMB|v5|evt|tag=POL" "$HOI4_LOG_PATH" | tail -3
grep "LLMB|v5|state|tag=POL" "$HOI4_LOG_PATH" | tail -2
grep "LLMB|v5|state|tag=GER" "$HOI4_LOG_PATH" | tail -1
```

Expect an `evt` with `kind=16`, daily `state` lines for POL with `ai=1` (POL is an AI country
and is reported only because of the flag), and still GER's own lines with `ai=0`. This is the
claim the whole mode rests on: **an `is_ai = yes` country with the flag is reported.** If
there are no POL state lines, `llm_bridge_is_harness` does not reach the on_actions for an AI
country; say so in #91 and stop. Also look for a weekly `camp` line for POL.
Then `effect POL llm_bridge_unmark_harness`, two days, and POL's lines must stop (`kind=17`).

**2. B-scoped orders, by hand.** Mark POL again, then `effect POL llm_bridge_select_SOV`,
`effect POL llm_bridge_raise_contain` and `effect POL llm_bridge_set_posture_defensive`.

```bash
grep "LLMB|v5|evt|tag=" "$HOI4_LOG_PATH" | tail -6
```

Expect `tag=POL` on kinds 13, 14 (`detail=contain SOV`) and 10, **not** `tag=GER`. A `tag=GER`
here means `ROOT` in a console `effect POL` is not POL: the mod's log lines and every
verification would be wrong for a steered country; paste the lines in #91. Then
`list_flags POL` in the console: `llmb_contain_SOV` and `llmb_posture_defensive` should be
there. (B's flags are not visible in your decisions tab: the log and `list_flags` are the
evidence.) Whether POL's AI then *acts* on `contain` is the question of campaign.md's
pre-flight 5 and takes weeks of game time; for an `invade`, give it a month at speed 5.

**3. Your input clock, and whether our own keys count.** The game need not be in front:

```python
import time
from hoi4_harness.rival.safety import system_input_watch
from hoi4_harness.adapters.keys import ScanCodeKeys
w = system_input_watch()
print("supported:", w.supported)
for i in range(12):
    print(f"{w.idle_seconds():.1f}")          # press a key or move the mouse during this loop
    time.sleep(0.5)
ScanCodeKeys().chord("shift")                  # a harmless key, sent as the harness sends them
print("after our own key:", w.idle_seconds())
```

Expect `supported: True`, the number resetting to ~0 when you press a key or move the mouse and
growing otherwise, and **~0 right after our own `shift`**. That is the assumption the guard's
design rests on: our keys are input too, so it compares stamps and not idle time. If our own
key does not reset it, the guard still works but the idle gate is looser than documented.
Paste the output in #31.

**4. A pause, read from the log.** Game running at speed 1, then speed 5, then paused.

```python
import os, time
from hoi4_harness.rival.safety import LogClock
c = LogClock(os.environ["HOI4_LOG_PATH"], time.monotonic)
for i in range(60):
    print(c.last_date(), f"{c.still_for():.0f}s")   # watch it while you pause and unpause
    time.sleep(1)
```

Expect the stillness to stay under 10 s at every speed while the game runs and to grow without
bound once you pause. **Measure the longest gap between two date changes at speed 1** and paste
it: `--rival-paused-seconds` (10) must exceed it. Also try the escape menu and an event popup
left open: does the date stand still there too? Say which, because a window would open over
them (rival.md, residual risks).

**5. A real window, paused policy.** Game running as GER, then pause it and take your hands off
the keyboard and the mouse.

```bash
hoi4-harness play --rival POL --live --provider scripted --script profiles/rival-live-script.json \
    --game-dir "$HOI4_GAME_DIR" --until 1936-06-01 --run-dir runs/rival-live
tail -f runs/rival-live/rival.log
```

Expect, in `rival.log`: `marking POL ... waiting for a safe window`, then after about 10 s of
stillness the console opens once, flashes, closes, and `POL marked: the mark event and a state
line for POL are in the log`. Then unpause, play for a month, pause and wait: `Window on
1936-...: 2 ran`. The console must open and close **once per window**, however many actions
are queued. Check:

```bash
grep "LLMB|v5|evt|tag=POL" "$HOI4_LOG_PATH" | tail -8      # kinds 14 (contain SOV) and 10 (posture)
cat runs/rival-live/console/*-llmb_turn.txt                  # every line starts effect POL / eval_effect POL
grep -L "gamespeed\|pause_in_hours\|human_ai\|savegame" runs/rival-live/console/*-llmb_turn.txt
```

Every console file must be listed by the last command (none contains a clock command), and
the game's speed and pause state must be exactly what you left them. Paste `rival.log`.

**6. Not while you are playing.** Unpause and play. `rival.log` should say `waiting: the game is
not paused` and **nothing opens**. Pause, then keep moving the mouse: `waiting: there was
keyboard or mouse input`. Alt-tab to another window: `waiting: the game window is not in
front`. None of these may type anything. Paste.

**7. You come back mid-sequence.** The mode's central risk. Use `--rival-idle-seconds 3` so a
window opens quickly, have something queued (the script's second wake does), pause, and watch
the console open. **Press a letter key (say `x`) while it is typing.**

- Expect the console to close within a moment, `rival.log` to say `stopped before it finished
  (the person started using the keyboard or mouse), 1 still queued`, nothing to have run, and
  the next window to carry it out after you go quiet again.
- **Then open the console yourself and look at its input line.** Empty is what the design
  assumes. If it still holds the half-typed `run llmb_...` (or your `x`), say so in #31: the
  next window would append to it and fail, and the abort needs to clear the line first.
- Do it twice more with the key at different points (right after the opening backtick, and
  just before the enter). Note how many of the three were caught.

**8. The policies.** `--rival-act-when handover`: nothing happens until you run
`hoi4-harness handover --run-dir runs/rival-live`, then one window, and the grant is gone
afterwards. Run `hoi4-harness handover --release` while a window is typing: it must stop at
the next key. `--rival-act-when never`: no console ever opens, the brief says the orders are
advice, `rival.log` lists what was shown. Paste the lines.

**9. You declare war on B.** Declare war on Poland as Germany. Within a few days of game time
`rival.log` must show `wake: critical event: war declared: GER on POL`, and:

```bash
hoi4-harness observe --adapter logtail --country POL
```

shows `AT WAR with GER` and **nothing of GER's own numbers** (political power, factories,
GER's other wars). A GER figure there is a leak: paste the brief in #91.

**10. Leaving.** Ctrl-C the run while the game is paused and quiet: expect `POL unmarked` and
a `kind=17` event. Run it again, pause nothing, Ctrl-C: expect `POL is still marked ... To undo
it type in the console: effect POL llm_bridge_unmark_harness`. Type it.

**11. Several rivals.** `--rival POL,SWE,FIN` (with GER played): `rival.log` should show all three
marked in one window and, after queued orders, one `Window` line naming each country's part; the
console file for it holds `effect POL`, `effect SWE` and `effect FIN` lines, and each brief shows
only its own country.

**What to paste where.** Steps 1, 2 and 5 (the flag works on an AI country, the scope is B's,
the window is one file): #91. Steps 3, 4, 6 and 7 (the idle clock, the pause signal, the abort,
the console line): #31 and #33; they decide whether `paused` is safe enough to be the default
or `handover` should be. Step 9: #91.

## 14. Independent campaign with the mouse: research, an order, a delegation (`--control llm`)

Do this on a spare non-ironman 1936 game as one country (these steps say GER), after steps 1 to 5
and 10 hold. The harness takes the mouse and the keyboard for the whole run: **do not touch either
while it runs**, keep the window in front, and do not zoom or scroll the map. Everything here
except research through the input driver (run live 2026-11-19: `y` then `w`, a second Return for
the military-industrial-organisation chooser, rotated tabs +40 GUI y) is new and unverified.
Paste each step's output into the PR that added this (campaign-hands) and the issue it names.

**0. Install the mod again.** The state line gained `rslots=` (the research slot count, which the
idle-slot wake needs). `hoi4-harness campaign-check --profile profiles/campaign-ger-world.json`
must pass the mod check (byte for byte), and after loading, `grep "LLMB|v5|state" "$HOI4_LOG_PATH" |
tail -1` should end `...|rslots=N`. **If `rslots=` is empty or 0, say so: the expression
`amount_research_slots` did not expand, and research will never wake on an idle slot.**

**1. Record what a person has to record.** In a paused game with a war on a front (or any
contact you can see), once, with the current scripts:

```bash
# the camera, steps 11-12 above, with the CURRENT scripts/record_camera.py: it writes the display
# fingerprint an unattended run accepts at any age
PYTHONPATH=src python scripts/record_camera.py --game-dir "$HOI4_GAME_DIR" --fit-province <p> ... --out runs/camera.json
# the army list and the AI toggle: aim the mouse at each and press Enter (names with spaces quoted)
hoi4-harness calibrate army.ai_control_toggle army.all "army.<name of one of your armies>"
```

Open the army list so those controls are visible when you aim. If you cannot find an AI toggle for
an army, stop here and say so: the delegation path is a guess (a click on the army, a click on its
toggle) and the first thing the live run decides is whether that guess is right.

**2. Start, and read the start log.** `hoi4-harness campaign-check ...` first (WARN lines say what
is missing), then

```bash
hoi4-harness play --campaign --control llm --live --profile profiles/campaign-ger-world.json \
    --max-usd 3 --until 1936-03-01 --run-dir runs/hands-1 --game-dir "$HOI4_GAME_DIR"
tail -f runs/hands-1/campaign.log
```

Expected in the first lines: `independent mode: ... human_ai is not sent`, then `mouse tools live:
...` and one `mouse tool refused: <tool>: <why>` for each tool that is off. Note which. A refused
tool is the harness doing its job, not a failure: fix what it names and start again.

**Research on 1.20, measured live 2026-11-21.** The 1.19 path (`y`, `w`, slot, tab, technology,
Return twice) fails on 1.20 at the last two steps: a technology opens a details popup whose
Research button Return does not press, and the military-industrial-organisation chooser that
follows ignores Return (closing it cancels the research). The slot, the tabs (now a horizontal
strip) and the technology cells the harness computes were all where the screen has them. Use
`profiles/ui_scripts.1.20.json`'s path, which the harness now **chooses by itself when the game is
1.20** (`launcher-settings.json` in `game_dir` says `1.20.x`; the start log reads `research path:
1.20 click path`; on 1.19, or when the version cannot be read, it keeps the Return-twice path).
Its two extra points are computed, not recorded: `research.confirm` (the popup's Research button,
750,252 at 1920x1080 UI scale 1) and `research.mio_choice` (the chooser's "Research without a
Military Industrial Organization" row, 1207,345), scaled from the screen's centre the way
`uilayout.Geometry` scales GUI units (both are on centred popups). Only 1920x1080 at scale 1 was
measured: at any other size or UI scale they are an extrapolation, so record them there
(`hoi4-harness calibrate research.confirm research.mio_choice`; a recorded point wins). A
`ui_scripts.json` in the run dir still overrides the path (do not leave a 1.19 one there on 1.20).
Clicked by hand, that sequence started Improved Infantry Equipment I. Through the harness it was
not yet seen to work, for two reasons that are not the click path: the technologies asked for
were not researchable yet (Radio needs Electronic Mechanical Engineering; the popup opens, its
button does nothing), and an event window (the Rhineland news) lay over the popup's Research
button. The second is what section 14c is for.

**3. Research is picked and verified.** In the first wake (`no national focus` or `research slot(s)
idle`) the model should call `start_research`. Watch the game: the research panel opens, a free slot
is clicked, the tab, the technology, Return twice. Then check, in `transcript.jsonl`, the result:
`done and verified` means the technology shows in a slot through the telemetry; `unverified` means
the click went in and the log did not show it (say whether the game did); `rejected` means the
game did not change. Confirm in the game that the slot is really busy. Also: with another slot
still free, the log should show `wake: N research slot(s) idle` a week or two later, and not when
none is free.

**4. One attack order.** Needs a war with a contact (start one however you normally would, a
focus or a decision, and let the armies meet). Check the
brief names a state with your divisions and the enemy's, then let the model order or order for it:
`attack_province` between those states. Watch the cursor: one box or click on the counter, one
right-click on the target. Then `Order: #1 ...` in the next brief should read `underway` or
`arrived`. Say which units moved, and if the wrong stack was selected, which point was off (the
camera fit is the first suspect, then the unit counter offset).

**5. One army delegated.** The model calls `delegate_army_to_ai` for the army you recorded. Watch:
the army is selected and its AI toggle pressed. Check in the game that the army shows AI control.
The result is `unverified` by design (nothing reads the toggle), so the game is the only evidence:
report whether it took, and whether calling it again took it back (`delegate` false).

**6. Guidance.** While it runs: `hoi4-harness guide --run-dir runs/hands-1 "Hold the east, push the
west"`. The next wake should name it, and the model should mention it in its note.

**7. The guards.** Click away to another window during a wake: the harness must log `waiting` and
send nothing (no clicks, no keys in the other window), and continue when you bring the game back.
Check `runs/hands-1/campaign.log` for `STOP:` lines when you end it, and that the game was left
paused.

### 14c. Event and news windows in independent mode (#125)

(Reading the open event and choosing its option, instead of the blind first option, is
`docs/events.md`, with its own live steps; this section is the blind guard it falls back to.)

In `--control llm` nothing answers a country event or closes a news window (`human_ai` is not
sent), and every mouse tool clicks fixed points an open window covers. What was found about
answering them without a click: the game has **no on_action for an event reaching a country**
(so the mod cannot log one), the save has no pending-event list, and the console's `event <id>
<TAG>` only *fires* an event; no console command or effect picks an option of one already open.
So the window is closed the way a person does, by a click, guarded
(`src/hoi4_harness/adapters/event_windows.py`): before every mouse tool the driver probes for an
open window, answers it (country event: its first option; news: the close control), records the
answer, and refuses to click while one is open; each click of a tool checks again. The fake game
shows this works (`tests/test_event_windows.py`); the real game needs three things measured, and
until they are in the profile the start log says `event windows: NOT guarded`.

**1. The probe.** For each kind, one pixel that is a fixed colour while that window is open and
something else without it. Open the window (or wait for the Rhineland news, GER, early 1936, and
leave it open), aim the mouse at the pixel, and read its colour **without clicking**:

```bash
PYTHONPATH=src python scripts/sample_pixel.py --wait 5      # prints x,y and r,g,b under the mouse
```

Take it on the window's frame or header, not on text or the map. Check the same pixel with the
window closed (the colour must differ by more than the tolerance) and with another panel open
(research, production). Put the result in the profile (the zeros are the numbers to measure; the
points are screen pixels at the resolution you run):

```json
"event_windows": {"probes": {
  "news":    {"x": 0, "y": 0, "rgb": [0, 0, 0], "tolerance": 24},
  "country": {"x": 0, "y": 0, "rgb": [0, 0, 0], "tolerance": 24}}}
```

**2. The answers.** `hoi4-harness calibrate event.news_close event.option1`: aim at the news
window's close control, and at where the first option of a country event usually is (its position
depends on the text; pick a spot every first option covers, or give a kind a different step list
under `event_windows.answers`, for instance a key press if Return or Space closes it, which nobody
has tried). Leave `settle` at 0.5 s, or raise it if the window fades out.

**3. Check it.** Start as in step 2 with the profile above and read the start log: `event windows:
guarded` and no `a window that opens cannot be closed` line. With the Rhineland news open, run
`start_research`: the news window closes first, then the research runs, and `events.jsonl` in the
run dir has one line (`kind`, `answer`, `closed`); the tool's result in `transcript.jsonl` starts
`Before this, a news event window was open and I ...` and carries `events_answered`. Then leave a
country event open and repeat: the first option is taken (say which event it was, and whether the
first option is a choice you are content to have made for the model; the harness cannot tell the
events apart). Then rename the profile's `event_windows` key and confirm the old failure is back,
so the check can fail. If the probe fires with no window open, or never fires with one, say where
the pixel was and its colour in both states.

**4. The policy (#125).** With `"policy": "model"` in `event_windows` and event reading on
(docs/events.md), leave a country event open and send a mouse tool: it is refused with `call
read_event ... choose_event_option`, nothing is clicked, `events.jsonl` has a `refused the tool`
line. Answer with `choose_event_option`, send the tool again: it runs. With `"policy": "refuse"` the
same tool is refused until you answer by hand. Say whether the refusal text reached the model in
`transcript.jsonl`.

### 14b. Production by click: edit a line, never duplicate it (#112)

**Run live 2026-11-21 (1.20.0, 1920x1080, UI scale 1, GER 1936, `runs/hands-1`, `runs/hands-2`).**
Two edits went through the harness and a fresh save confirmed both: support equipment, row 2,
2 -> 4 (`+` twice) and 4 -> 3 (`-` once), `done and verified`, no second line. What it settled:

* **P3 fails as written, and the code now copes.** The 1.20 screen has `-`, `+` and a remove
  button on EVERY line, so one shared `production.factories.plus` only ever reaches row 1. Record
  `production.line.N.minus`, `production.line.N.plus` and `production.line.N.remove` per row; the
  harness clicks a row's own button when it is recorded, without selecting the row. At 1920x1080
  the buttons are at x 497 / 592 / 627, y = 482 + 138 (N - 1) for the four rows the panel shows
  without scrolling; `production.line.N` (the equipment picture, which opens the chooser) at
  x 110, y = 550 + 138 (N - 1).
* **P1 holds for the four visible rows**: the save's `military_lines` order (infantry, support,
  artillery, light tank) is the screen's.
* **P2 holds**: research then production leaves production open.
* A click on a line's picture opens the equipment chooser directly, so
  `production.equipment.change` is optional now (clicked only when recorded). The chooser itself
  (P4) was not measured.


Built against the fake game only. `set_production` over the console DUPLICATES a line that exists
(seen live), so the click path edits it on the production screen instead. It needs a reader that
sees the lines (`headless_reader = log+save`, the default; the rows are read from the save), a
spare game with 3 or 4 military factories, and these steps. About ten minutes. Do not touch the
mouse or keyboard during step 3.

**1. Record the points.** Open the production screen (`y`), with at least two lines, say
Infantry Equipment I with 2 factories and Artillery with 1, and the equipment list reachable.
Aim the mouse at each target and press Enter:

```bash
hoi4-harness calibrate production.line.1 production.line.2 production.line.3     production.factories.plus production.factories.minus production.line.remove     production.equipment.change production.equipment.infantry_equipment_1     production.equipment.artillery_equipment_1
```

* `production.line.N`: the Nth row of the line list, any spot on the row that selects it (not the
  + / - buttons, not the equipment icon). Record as many rows as you have lines (up to 8).
* `production.factories.plus` / `.minus`: the + and - that change the SELECTED line's factories.
  If the game puts them on each row, record the ones on row 1 and say so: that is assumption P3
  failing.
* `production.line.remove`: the button that deletes the selected line (skip it to leave removal
  off; the refusal then names it).
* `production.equipment.change`: the button that opens the equipment list for the selected line;
  `production.equipment.<id>`: the entry for each equipment you want a line to be able to switch
  to (the id as the game names it, e.g. `infantry_equipment_2`).

**2. Read the start log.** `hoi4-harness campaign-check ...` must show `production: set_production
edits an existing line by click`, and `campaign.log` the line `production by mouse live`. With a
point missing, the log says which: `production by mouse: ... not offered: record ...`.

**3. Drive each case.** With the harness (or `game.apply` in a Python session on the live
adapter, game window in front), one call at a time, checking the game after each:

| Call | What the screen must show |
|---|---|
| `set_production(infantry_equipment_1, 3)` on a 2-factory line | the SAME line now at 3 factories; no second Infantry line |
| `set_production(infantry_equipment_1, 1)` | that line at 1 |
| `change_production_equipment(infantry_equipment_1, infantry_equipment_2)` | the line makes the new variant, keeps its factories; progress may reset (say so) |
| `set_production(<equipment with no line>, 1)` | one new line, opened by the console effect |
| `set_production(<that equipment>, 0)` | that line gone |
| `set_production(infantry_equipment_1, 99)` | refused before any click (not enough free factories) |

**4. What to check, and report.** After each call: the result in `transcript.jsonl` (`done and
verified` means a fresh save agreed; `rejected ... did not change` means a click missed: say which
point, at what resolution and UI scale; `unverified` means no save could be read), and the screen:
number of lines unchanged for an edit, the production panel left open (the next research call
presses `w` then `y` and does not care). Also settle these assumptions, each one a line in your
report: P1 row N of the screen is the Nth `military_lines` entry of the save (reorder the lines in
the game and repeat step 3 once); P2 `w` then `y` always leaves the production screen open; P3
selecting a row then + / - acts on that row; P4 a recorded equipment entry is where the list puts
it (the list may scroll or filter by category: say if it does).

If an edit lands on the wrong line the click path is wrong, not the model: leave production edits
off (do not record the points) and report which assumption failed.

What to report: for each step, what the log said, what the game did, and the transcript result.
Whatever does not match, with the log line, is the next thing to fix.

## 15. Co-op handover, domains and multiplayer (#31, #33, #91)

Everything here is built and tested against the mock and the fake game. These steps are what
only the real game settles. Use a spare non-ironman game as one country (say GER), with the
LLM Bridge mod, `--live`, and **you at the keyboard**: this is the first time the harness types
while a person is playing. Use `--no-llm` or a scripted planner (`--script`) for steps 1 to 5,
so no model call is involved.

**1. The idle clock is real.** `hoi4-harness play --adapter logtail+input --live --player-clock
--handover-mode idle --handover-idle-seconds 5 --run-dir runs/coop-live`. In a Python session
on the same machine, `from hoi4_harness.rival.safety import system_input_watch; w =
system_input_watch(); w.supported, w.idle_seconds()`: `True` and a number that restarts at 0
when you press a key or move the mouse. If it is `False` or does not move, say so in #33.

**2. Idle mode, the happy path.** Queue one action (a focus). Play, then pause and take your
hands off the mouse and keyboard. After about 15 s (10 s paused, 5 s quiet) the window opens,
the action runs, and the transcript has a `handover` record with `mode: idle`, `waited_seconds`
and `window_seconds`. Check the focus in the game. Then unpause and play and queue something:
it must not run while the date moves or while you click.

**3. You come back mid-queue.** Queue four research or production actions. Pause, go quiet,
and as soon as the console opens, press a harmless key (Shift) or move the mouse. The window
must stop at the next key (`stopped_because: the player started using the keyboard or mouse`),
the grant must be gone and the rest still queued in `handover-queue.json`. **Look at the
console input line**: empty is what the design assumes; a half-typed `run llmb_...` means the
abort needs to clear it first (the same open question as rival step 7). Do it three times at
different points and count how many were caught.

**4. Hotkey.** Add `--handover-hotkey ctrl+alt+h --handover-mode hotkey`. The run must say
`hotkey ctrl+alt+h grants a window`. Queue an action and press the combination in the game:
the queue runs and the grant is gone. Press it with nothing queued: nothing happens. Start a
second run while the first holds the combination: it must refuse to start, naming the clash.
Confirm the combination reaches nothing else (it is consumed system-wide) and that stopping
the run frees it.

**5. Approve.** `--handover-mode approve`. Queue two actions, answer yes to one and no to the
other: only the yes runs, the transcript has two `confirmation` records and the window record
lists the declined one. End the terminal's input (Ctrl-Z, Enter on Windows): the answer is no.

**6. Domains.** `--human army,navy,diplomacy` with a real model on a short run. The model must
never propose an army order, and the brief must carry the `Held by the player` line. Leave
guidance with `hoi4-harness guide --run-dir runs/coop-live "..."` and confirm the model is
woken for it and the transcript has a `guidance` record.

**7. Multiplayer, stage 0 (#91).** Needs a second machine and licence: fill the table in
[multiplayer.md](multiplayer.md), one row per channel, each from the non-host client, with the
log line or a screenshot as evidence. Stage 2 is the acceptance run of #91.

**What to paste where.** Steps 1 to 5 settle the acceptance of #33 (no input until handed
over, the queue runs, stale is reported, it hands back) and the handover half of #31; step 6
the domain half of #31; step 7 is #91.

## 16. The input guard: the harness never types over you (#33)

Headless runs (`--adapter headless`, not the fake game, not a dry run) wrap every key, typed line
(the console, `run <file>`), click and drag in `adapters/input_guard.py`. `input_quiet_seconds`
(5) is how long keyboard and mouse must have been quiet before a sequence starts; it waits up to
`input_wait_seconds` (30) and then refuses with `not_executed`. Every handover goes to
`handovers.jsonl` in the run dir and the driver log. Built and tested with injected stamps
(`tests/test_input_guard.py`); only the real idle clock is left.

1. **Start refused.** Start a headless run with the game in front, then keep moving the mouse
   when an action is due: the log has `handover: waiting`, and after 30 s `refused`, with nothing
   typed or clicked in the game. Stop moving: the next action runs (`resumed`, `completed`).
2. **Stop mid-line.** During a console run-file (`run ...`), press a key of your own. The game's
   console must not receive a half command: `handovers.jsonl` has `aborted` for `type` or `key`,
   and the console is closed (the console key is the one key that still goes out). Try it in the
   gap after the console key, mid-text, and just before Enter, as #33's comments ask.
3. **Mid-click.** During a research click path, touch the mouse. The path stops (`aborted` for
   `click`); a drag never leaves the button down.
4. **Focus.** With `campaign_take_focus` on and the game behind another window, keep typing in
   that window: it stays in front (`focus_withheld`); stop for 5 s (and the 120 s of
   `campaign_take_focus_idle_seconds`): it is taken (`focus_taken`).

Not covered, in principle: input in the milliseconds between the last look and the event itself
(also true of rival mode, docs/rival.md).
