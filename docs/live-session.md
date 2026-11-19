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
map-search flows, were run in the game). Record the click targets they name at
your resolution, with `--game-dir` set so names and tech positions resolve:

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
| | `basic_light_tank` | (598, 562) |
| | `basic_heavy_tank` | (1298, 562) |
| Armour, new tank design (`UPPER_LEFT`) | `gwtank_chassis` | (1168, 222) |
| | `armor_tech_1` | (318, 485) |
| | `improved_light_tank_chassis` | (905, 572) |
| Air (`UP`, no left margin) | `early_fighter` | (620, 388) |
| | `CAS1` | (320, 562) |
| | `tactical_bomber1` | (1564, 562) |
| Air, BBA (`UP`, no left margin) | `iw_small_airframe` | (358, 388) |
| | `iw_large_airframe` | (876, 388) |
| | `early_bombs` | (1682, 388) |
| Industry (`UP`) | `basic_machine_tools` | (336, 388) |
| | `construction1` | (1049, 562) |
| | `fuel_refining` | (1756, 562) |
| Electronics (`UP`) | `electronic_mechanical_engineering` | (361, 381) |
| | `radio` | (211, 531) |
| | `atomic_research` | (1374, 381) |

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

```bash
HOI4_LIVE_TESTS=1 ANTHROPIC_API_KEY=... pytest tests/live -v -rs

# Set all four rates from the provider's price sheet first:
# HOI4_USD_PER_M_INPUT, _OUTPUT, _CACHED_INPUT, _CACHE_WRITE
hoi4-harness play --provider anthropic --turns 20 --run-dir runs/priced
```

Paste the conformance output into #22. Compare the run's `spend.usd` with the
provider's usage page for the same window and paste both numbers into #23.

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
Bridge mod last in the playset, and the game window in front. Keep your hands off
the keyboard and the mouse from step 4 on: the harness types into whatever has
the focus, and a stray key lands in the game.

Set once:

```bash
export HOI4_GAME_DIR="C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"
export HOI4_LOG_PATH="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
```

(`--country SWE` below keeps the reader to Sweden. The game need not be in
`-debug`: the console is already known to work without it.)

**0. Offline first, no game needed.** What to expect from every later step:

```bash
PYTHONPATH=src python -m pytest -q
hoi4-harness headless-check --fake-game --run-dir runs/fake-check      # 7 of 7 passed
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
should hold `LLMB|v3|state|...|tag=SWE|...` lines, one per game day since the
mod loaded. No input is sent; the mouse does not move.

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

Takes one to two minutes (each "it stopped" proof waits the probe window). Expect
`7 of 7 passed`. In the game: the date at the top moves during claims 3, 5 and 6
and stops after each, and `game.log` shows the `[Y.M.D.H]` prefixes advancing.
What a FAIL means, and the setting that routes around it:

| FAIL | The assumption that broke | Set |
|---|---|---|
| the log has telemetry | mod not loaded, or `HOI4_LOG_PATH` wrong; take *Emit telemetry now* | fix that first |
| refresh: two fresh state lines | `effect SWE llm_bridge_emit_state` did not log (two `state` lines with the same date should appear in `game.log`) | `console_refresh = false`: actions stay "unverified" while paused |
| `gamespeed 3` starts the clock | the console command does not set the speed, or `run` stopped reading the file | `--headless-clock keys` |
| the space key stops it | the key did not reach the game (focus? the console left open?) | `--headless-clock console` |
| `gamespeed 0` stops it | informational only; space is the primary pause | none |
| `pause_in_hours` stops the game | the command differs from its one-line description | do not use `exact` |

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
| `queue_construction` capital | `unverified` ("reader cannot see construction"), `changed.channel = console` | nothing; `built_civ` rises on completion | a civilian factory in the capital's queue (look after the run) |
| `set_production` infantry_equipment_1 | `unverified` | nothing | a line for it, or the game declined because no factory was free: say which |
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
* `savegame headless_probe` with `save_as_binary=no`: where does the file land, and
  does `hoi4-harness observe --adapter savegame` show production and construction?
  If so, a save-then-read after each action would make the two "unverified"
  actions verifiable with no mouse.
* Open the tech screen (`w`) and try Tab and the arrow keys: does anything select?
  If one does, `start_research` has a keyboard path after all.

**Decision.** If steps 4 and 5 hold, headless is the default for everything but
research and delegation. If only the refresh fails, the mode still works but every
log-verified action reads "unverified" until a day has passed.
