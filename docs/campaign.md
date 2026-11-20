# Campaign mode: an unattended run, the game's AI executes, the model steers

A fresh 1936 game, one country (Germany), 1936-01-01 to 1948-12-31, objective
"conquer the world", a real model under a hard dollar cap, and a report at the end.

## The division of labour, and why

Research and army delegation have no faithful headless path, and unit orders need a
camera that is not validated. The console has `human_ai` ("Toggles AI for Human
countries", console_commands_documentation.md:1048): the game's own AI then runs the
player's country, fronts, battle plans, research, production, focuses and diplomacy
included. Campaign mode (`play --campaign`, `operational_control = "game_ai"`) uses that.
**The game AI executes; the model is the grand strategist.**

What the model is offered (`agent/hybrid.py`, `GAME_AI_OWNED`):

| Offered | Why |
|---|---|
| `set_ai_directive`, `clear_ai_directives` | Who the AI should invade, protect, contain. The mod turns it into `ai_strategy` blocks, one per (directive, target), for every country that owns a state in 1936 (92 tags). |
| `set_ai_posture` (global or per theater) | How hard it presses, by front. |
| `take_decision`, `hire_advisor` | The AI takes few decisions and may leave advisor slots empty. Both are cheap (no save). |
| `advance_time days` | In a campaign this is the model's own next review, 7-90 days. |
| `note` | Plans across wakes. |

Not offered: `set_national_focus`, `start_research`, `queue_construction`,
`set_production`, `design_division_template`, `deploy_divisions`, `delegate_army_to_ai`,
`set_trade`, `set_game_speed`, `diplomacy`, unit orders. The AI picks focus and research
itself and would undo or double the rest (`set_production` opens a second line beside the
one the AI keeps), a construction or production order costs two 66 MB saves to verify, and
the clock is the harness's. `--actions` / `--without` still narrow the list.

## Independent mode: the model plays the country (`--control llm`)

**Not yet verified against the live game.** Everything below is built and tested on the fake
game only (`tests/test_106_independent_campaign.py`).

```bash
hoi4-harness play --campaign --control llm --live --profile profiles/campaign-ger-world.json     --max-usd 40 --run-dir runs/campaign-ger-llm      # or "campaign_control": "llm" in the profile
```

`campaign_control` is `"game_ai"` (default, everything above) or `"llm"`. In `llm` mode:

- `human_ai` is **not** sent (`campaign_human_ai` is off) and the log is read as for any
  human country. If the state line already reads `ai=1` (an earlier game_ai run, or a save
  taken under `human_ai`) the run refuses to start: `human_ai` is a toggle and the harness will
  not guess which way it would flip. Turn it off in the console yourself, then run again.
- `operational_control` is `llm`, so the directive vocabulary (`set_ai_directive`,
  `set_ai_posture`, `clear_ai_directives`, `delegate_army_to_ai`) is not offered and the
  hands-on actions are. Which of those exist is the headless adapter's decision, not this
  mode's: with the campaign profile's `logtail` reader that is `set_national_focus`,
  `queue_construction`, `take_decision`, `hire_advisor` and `note` / `advance_time`.
  `set_production` needs a reader that sees production (`headless_reader` `log+save`, at the
  price of the verification saves), `set_game_speed` is removed because the clock is the
  harness's, and `start_research`, `move_division` and `attack_province` are mouse actions that
  headless refuses (`headless.MOUSE_ACTIONS`).
- What campaign mode turned off for the game's AI comes back: the **reflex layer** (an empty
  construction queue, idle military factories) is on, and the wake rules for a focus that ended
  (`no national focus is running`), an idle research slot and an empty production line are on.
  A wake is only issued when the model has the action that fills the slot (a headless run has no
  `start_research`, and waking it for a slot it cannot use would only spend money), and at most
  once per `campaign_wake_gap_days`. The wartime alarms (encirclement, supply, fronts going
  quiet) stay off: nothing in the tool list could answer them.
- The system prompt (`hybrid.describe`, `CAMPAIGN_LLM`) says the game's AI is not running the
  country and that no tool moves a unit; the guidance pack is `world-conquest-llm`.

**The gap: armies in wartime.** Without `human_ai` nothing moves the player's divisions. The
two things that could are both off in a headless campaign today: unit orders
([unit-orders.md](unit-orders.md)) are clicks, need a mouse, a calibrated camera and `unit_orders =
true`, and the headless driver refuses a mouse; `delegate_army_to_ai` is a mouse action too
(issue #8, not verified live). So in `llm` mode the model can queue buildings and pick focuses
and decisions, and if a war reaches it the army stands where it is. Do not read a good result in
this mode as the model conquering anything by force. What would close the gap is a verified
console path for delegating armies, or the game's AI taking the armies only; neither exists
here.

## Telemetry under `human_ai`

The mod used to emit only for `is_ai = no`, so `human_ai` would have silenced the log.
Schema v5 (both sides): every on_action limits on the scripted trigger
`llm_bridge_is_harness` = `is_ai = no OR has_country_flag = llmb_harness`
(`common/scripted_triggers/llm_bridge_triggers.txt`). The flag marks **any number of
countries**; each line carries its own tag, so a log can hold several countries and the
reader keeps the one it is given (`--country`). `effect TAG llm_bridge_mark_harness` /
`llm_bridge_unmark_harness` set and clear it. The campaign marks the player before it
toggles `human_ai`. Also new in v5: `ai=` on the state line (`is_ai`, the read-back that
proves `human_ai` took) and a weekly `camp` line (`on_weekly`): states owned/controlled,
divisions, enemies and capitulated enemies, faction membership, countries existing /
independent, states in the world. Tokens are cited in
`scripted_effects/llm_bridge_telemetry.txt`:

| Token | documentation/ |
|---|---|
| `human_ai` | console_commands_documentation.md:1048 |
| `is_ai` | triggers_documentation.md:5293 |
| `has_country_flag` | triggers_documentation.md:3660 |
| `num_owned_states` / `num_controlled_states` / `num_divisions` / `num_faction_members` | dynamic_variables_documentation.md:677 / 548 / 560 / 573 |
| `is_in_faction` / `is_faction_leader` | triggers_documentation.md:5764 / 5589 |
| `every_enemy_country` / `every_country` / `every_state` | effects_documentation.md:3898 / 3841 / 4156 |
| `has_capitulated` / `exists` / `is_subject` | triggers_documentation.md:3388 / 2743 / 6354 |
| `on_weekly` | common/on_actions/05_lar_on_actions.txt:1445, _documentation.md:11 |

Factories and manpower are already on the state line; wars are the daily `war` lines.
The harness keeps them in `GameState` (`ai_controlled`, `campaign`), in the brief (a
`CAMPAIGN:` block: states held, share, change since the start and since the last review,
independent countries left, wars since the last review, spend) and in
`campaign_progress.jsonl` (one row a turn).

## Guiding a run (`hoi4-harness guide`)

A person can leave a running campaign guidance without stopping it, in either control mode:

```bash
hoi4-harness guide --run-dir runs/campaign-ger-1936 "Take Poland before France; do not attack the USSR"
hoi4-harness guide --run-dir runs/campaign-ger-1936 --list
hoi4-harness guide --run-dir runs/campaign-ger-1936 --remove 2        # one item
hoi4-harness guide --run-dir runs/campaign-ger-1936 --expire 7        # items older than 7 days
hoi4-harness guide --run-dir runs/campaign-ger-1936 --clear           # all of it
```

It appends a JSON line to `<run-dir>/guidance_inbox.jsonl` (`{id, ts, text}`; a removal is a
`{"clear": [ids]}` line, so a writer in another process never races the reader). The campaign
reads the file at the top of every turn, and while the clock is running it checks on every poll,
so guidance is heard within seconds of real time rather than at the next scheduled review.

- **A wake reason.** New guidance wakes the model (`wake: guidance from the player`), past the
  wake gap, once per item.
- **A standing block in every brief** under `PLAYER GUIDANCE`, until cleared, with items the model
  has not yet been shown marked `NEW`. It tells the model it is advice to weigh, not an order, and
  to say in its note when it declines part of it. The mailbox player reads the same brief text.
- **Advice, not an instruction.** The harness never executes it; the model's actions are
  whatever the model chose.
- **Logged**: each item is a `guidance #N from the player: ...` line in `campaign.log` and a
  `guidance` record in `transcript.jsonl`; what the model has been shown is kept in
  `campaign_state.json` (`guidance_seen`) so a resume does not wake it again for old items.
- An item is cut to 1200 characters. If the budget is spent and the wake is refused, the item stays
  NEW and is shown on the next wake.

## Wake rules

No reflexes (the AI does that), no wakes for focus, research or quiet fronts. The model
wakes on a scheduled review (`campaign_review_peace_days` 30, `campaign_review_war_days`
14, or its own `advance_time`), and on a critical event (a war, a capitulation) at most
once per `campaign_wake_gap_days` (7); the player's own capitulation is the exception and
wakes it at once. Between wakes the harness runs the clock for the
days left to the next review; an event cuts the wait short.

## Commands

```bash
export HOI4_GAME_DIR="C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"
export HOI4_LOG_PATH="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
export HOI4_SAVE_DIR="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/save games"
export ANTHROPIC_API_KEY=...

# offline, any time: the whole stack on the fake game, then its report
hoi4-harness play --campaign --profile profiles/campaign-fake.json --run-dir runs/campaign-fake
hoi4-harness campaign-report --run-dir runs/campaign-fake --print
hoi4-harness prompt --profile profiles/campaign-ger-world.json | head -60

# pre-flight (see the checklist): a short real run, ~3 dollars, then keep going from it
hoi4-harness play --campaign --live --profile profiles/campaign-ger-world.json \
    --max-usd 3 --until 1936-02-15 --run-dir runs/campaign-ger-1936

# the run (continues the pre-flight run: spend and memory carry over)
hoi4-harness play --campaign --live --resume --profile profiles/campaign-ger-world.json \
    --max-usd 40 --until 1948-12-31 --run-dir runs/campaign-ger-1936 \
    2> runs/campaign-ger-1936.stderr

# after any stop (crash, reboot, budget, a window that never came back): the same line again
# (--resume reads the transcript; add --max-usd N more if the budget was the reason)

hoi4-harness campaign-report --run-dir runs/campaign-ger-1936
tail -f runs/campaign-ger-1936/campaign.log
```

`--max-usd` is mandatory for a priced model (the run refuses without it); the profile
prices claude-opus-5-5 at $4 / $20 / $0.20 / $5 per MTok and keeps a $0.75 reserve so the
last call cannot overshoot the cap. Exit codes: 0 the end date was reached, 1 stopped with
a reason, 2 could not start, 3 crashed.

## Stops and what the run keeps

| Situation | What happens |
|---|---|
| End date (`--until`, default 1948-12-31) | the last turn is cut to the days left; the pause lands at most a day late |
| Budget | stops with the game paused and `budget: ...` as the reason; `campaign_on_budget = "coast"` lets the AI play on with no calls |
| Game process gone (`tasklist`, `campaign_game_process`) or `game.log` silent for `campaign_silence_minutes` (20) while the clock runs | `GameLost`: a clean stop with the reason; also the case of the mod not reporting for the player |
| Historical AI Focuses off (`historical=0` in the first recovery save's header) | logged as `historical AI focuses: off`, kept in `campaign_state.json` and shown in the report summary; with `campaign_historical_ai = "on"` (`on`, `off` or `any`, default `any`) it is also a `WARNING` line. The run is never stopped for it: the AI picks the focuses, so on and off runs are not comparable |
| The player's own country capitulates | the run **goes on** (HOI4 keeps a capitulated country alive: government in exile, whatever states it still holds). The model is woken the turn it is seen, bypassing the wake gap (`wake: your own country capitulated on <date> to <tag>`); every brief after that opens its CAMPAIGN block with `GER CAPITULATED on <date> to <tag>; you are a government in exile holding N states`; `campaign_state.json` keeps `player_capitulation` and the report summary has an "Own capitulation" row. `campaign_stop_on_capitulation = true` restores the old stop (`GER capitulated (<date>)`) |
| The player's country holds no states | stops: `GER holds no states (0 on the weekly reports of <d1> and <d2>): it no longer exists, nothing is left to play`. The game logs no "country gone" line, so the signal is the weekly `camp` line's `ostates` at 0 on two different weekly lines (one 0 is not enough, and the same line read on two turns counts once). A country that stops reporting at all is the watchdog's `GameLost` instead |
| `ai=0` after the run started | stops: `human_ai` was toggled off or the game restarted; resume sets it up again |
| Game window not in front | every key is gated on it; the harness waits, retrying every minute (logged), up to `campaign_focus_wait_minutes` (360) in all, then stops. The game keeps running meanwhile. Nothing is typed elsewhere, and the window is not pulled forward unless `campaign_take_focus` is true (default false: it would steal the keyboard from whatever you are doing) |
| Harness crash | traceback summary in `campaign.log` and a `crash` record; resume with `--resume` |

A recovery `savegame` goes out every `campaign_save_months` (6) in-game months, about 66 MB
each (more late). The save folder is OneDrive-synced, so they are pruned: the harness keeps the
first save of the run and the newest `campaign_keep_saves` (6) and deletes the older ones. It
only ever deletes a file that appeared in the folder after it asked for a save (the names it
saw are in `harness_saves.json` in the run directory, with each file's size and modification
time), and only while the file is unchanged; your own saves, including ones named like the
game's (`GER_1938_...`), are never touched. It never presses Escape (a test checks
the key log). `human_ai` is a toggle: it is sent only when the log says `ai=0`, once, and
never on a resumed run that finds `ai=1`.

Files in the run directory: `transcript.jsonl` (everything), `campaign.log` (a line a turn),
`campaign_progress.jsonl`, `campaign_events.jsonl`, `campaign_state.json`, `memory.json`,
`campaign-report.md`, and `guidance_inbox.jsonl` once someone has used `guide`.

## Before you leave it overnight

1. `hoi4-harness campaign-check --profile profiles/campaign-ger-world.json --max-usd 40`
   (reads files and `powercfg` only; touches nothing, prints no key). It checks that the
   installed mod (`<Documents>/Paradox Interactive/Hearts of Iron IV/mod/llm_bridge` and
   `llm_bridge.mod`, Documents possibly under OneDrive) matches `mod/llm_bridge` byte for byte
   and is schema v5, that `dlc_load.json` enables it, `save_as_binary=no`, that
   `HOI4_LOG_PATH`, `HOI4_SAVE_DIR` and `HOI4_GAME_DIR` exist, free disk for the saves (after
   pruning), that the provider's API key variable is present, that `--max-usd` clears the
   reserve and the profile's prices, and the power plan's sleep / display / lock timeouts on
   AC. One PASS / WARN / FAIL line each; exit 1 on any FAIL. Fix every FAIL; read every WARN.
2. The PC is kept awake for you: a live run holds `SetThreadExecutionState` (system and display
   required) and logs `keep-awake: held` at the start and `released` at the end, on any exit.
   A locked or sleeping screen would swallow every key, so do not lock it (Win+L) or sign out,
   and do not close the laptop lid unless the power plan ignores it. The hold is a no-op off
   Windows and on the fake game; plan timeouts the check lists as covered rely on it.
3. Game loaded on the new 1936 game as Germany, paused, window in front, and nothing else
   that grabs focus is going to open (updates, chat, a notification that steals the window). If
   one does, the harness waits and logs it; it never types into another window.
4. The pre-flight run of the checklist below passed, then start the real run with `--resume`.
5. Look at `tail -f runs/<run>/campaign.log` once after the first review, then leave it.

### When it stops, and how to carry on

Every stop (game process gone, `game.log` silent for `campaign_silence_minutes`, the window
never came back, the budget, `ai=0`, a crash) writes `STOP: <reason>` to `campaign.log` and a
`game_lost` / `run_end` / `crash` record to the transcript, releases the keep-awake and leaves
the game as it is. A stop that is not the end date also logs a `RESUME:` line, and
`hoi4-harness campaign-report --run-dir runs/<run>` has a "Where to resume" section: where it
stopped, why, the newest recovery save the harness took, and the exact command. To continue:
load that save in the game (or leave the game as it is if it is still running), pause it, bring
the window to the front, and run the printed line (the one under "the run", with `--resume`;
add `--max-usd N` more if the budget was the reason).

## Live pre-flight checklist

1. Mod installed last in the playset, **schema v5** (`hoi4-harness verify-bridge --game-dir
   "$HOI4_GAME_DIR"`), a new 1936 game as Germany, paused, game window in front.
2. `grep "LLMB|v5|state" "$HOI4_LOG_PATH" | tail -1` shows `ai=0` (take *Emit telemetry
   now* if there is no line; the harness also asks the mod for one).
3. Run the pre-flight command. In `campaign.log`: `handing GER to the game's AI` then
   **`human_ai took: the state line reads ai=1`**. If it says `did not take`, look at the
   game: if the AI is visibly running Germany (research fills, factories build) but `ai=`
   stays 0, `is_ai` does not follow the toggle in this build: do not send it again by hand;
   rerun with `--no-verify-ai` (a resumed run will not re-toggle) and rely on watching the
   game. If nothing is running, the toggle itself failed.
4. **Telemetry still flows with the flag**: more state lines each in-game day (`grep -c
   "LLMB|v5|state" ...`), `campaign_progress.jsonl` growing, a `camp` line within a week with
   plausible numbers (Germany owns ~30 states of ~1000). No lines after `human_ai` means the
   flag did not reach the mod's on_actions.
5. **First turn verified**: the transcript's first `actions` record has `set_ai_directive`
   ok with a `directive_raised` event, and later briefs show `Directive effect (observed, not
   intended)`. Within a month or two of a standing `invade` the AI should declare the war
   (`war declared: GER on X` in the brief); if no war comes, the AI does not act on the
   directive and the experiment has nothing to steer.
6. Disk: recovery saves are pruned to the first and the newest `campaign_keep_saves`, so
   about 0.6-1 GB at a time instead of ~3 GB over the run (`campaign-check` computes it).

## Cost estimate

Method: wakes x cost per wake, from the measured numbers in
[cost-and-timing.md](cost-and-timing.md) (claude-opus-5-5 against the mock, 2026-11-16:
~5-7k input tokens and ~270-520 output tokens per wake, 1.4-1.7 calls per wake, $0.022 a
wake; ~4.3 wakes a game-month under the old weekly rule).

Wakes for 13 game-years (4,748 days). Assume the AI is at war about 70% of the time
(3,300 days) and at peace 30% (1,450):

- default rule: 3,300/14 + 1,450/30 = 236 + 48 = 284 scheduled; critical events that do not
  coincide with a review add ~25%: **about 355 wakes**;
- if the model sleeps longer (21 days at war, 45 in peace): 157 + 32, +25% = **about 235**;
- ceiling: one a week for the whole run = **680**.

Per wake. The campaign prefix is larger than the mock's (system + 450 tokens of campaign
text + ~700 of guidance + up to 900 of playset context + 7 tools: ~3.5k), the per-turn
message ~1.2k. A game-month takes minutes of real time, so assume the 5-minute prompt
cache has expired each wake: call 1 writes the prefix (3.5k x $5 = $0.018) and bills 1.2k
uncached ($0.005), call 2 reads it ($0.001) with ~1.6k uncached ($0.006); output ~1,000
tokens a wake at medium effort ($0.02). **About $0.05 a wake, $0.025 to $0.08 either side.**

| | wakes | $/wake | total |
|---|---|---|---|
| light | 235 | 0.025 | ~$6 |
| expected | 355 | 0.05 | **~$18** |
| heavy | 355 | 0.08 | ~$28 |
| ceiling | 680 | 0.08 | ~$54 |

A cap of **$40** covers the heavy case with room; the fake proves the cap stops the run.
These are mock-world per-wake figures extended to a longer prompt; real brief size and wake
rate in a 1944 world war are unmeasured. Compare against `campaign-report`'s "Spend and
tokens" afterwards, and `hoi4-harness measure transcript.jsonl` for per-phase rates.

Wall clock is unmeasured: ~190-360 turns, each with a pause/resume (`clock_probe_seconds`
10 s by default, so ~25 s of overhead a turn, 1.5-2.5 h; lower it to 5 if the game runs
fast) plus the game's own time for 4,748 days at speed 4.

## What the fake proved, and what only the live run can

Proved on the fake game (tests/test_campaign_*.py, test_fakegame_campaign.py): the start
sequence and its read-back, the toggle sent once, the flag keeping telemetry alive and its
absence silencing it, directives raising and a war declared, fought and won, a capitulation
and the territory change, the budget stop and the reserve, the end date, resume after a
crash (with both readers, and across a game restart), the watchdog on a dead and a silent
game, the focus guard waiting and giving up, recovery saves, no Escape, the report.

Only the real game can show: that `human_ai` flips `is_ai` and the console accepts it from a
run file; that the AI acts on a mod directive (`conquer`/`invade` ai_strategy blocks for the
player's country) and declares the war; that `on_weekly` and the `camp` counters expand
(ROOT inside `every_enemy_country` / `every_country`, `exists`); that the game stays
controllable and pausable under `human_ai` (no event popups taking focus); `gamespeed`
and the pause key at campaign speed; how long a day takes late in the game; real brief size
and wake rate.
