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
