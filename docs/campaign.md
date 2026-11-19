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

## Wake rules

No reflexes (the AI does that), no wakes for focus, research or quiet fronts. The model
wakes on a scheduled review (`campaign_review_peace_days` 30, `campaign_review_war_days`
14, or its own `advance_time`), and on a critical event (a war, a capitulation) at most
once per `campaign_wake_gap_days` (7). Between wakes the harness runs the clock for the
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
| `ai=0` after the run started | stops: `human_ai` was toggled off or the game restarted; resume sets it up again |
| Game window not in front | every key is gated on it; the harness waits, retrying every minute, up to `campaign_focus_wait_minutes` (360), then stops. The game keeps running meanwhile. Nothing is typed elsewhere |
| Harness crash | traceback summary in `campaign.log` and a `crash` record; resume with `--resume` |

A recovery `savegame` goes out every `campaign_save_months` (6) in-game months, about 66 MB
each (more late); the harness never deletes saves. It never presses Escape (a test checks
the key log). `human_ai` is a toggle: it is sent only when the log says `ai=0`, once, and
never on a resumed run that finds `ai=1`.

Files in the run directory: `transcript.jsonl` (everything), `campaign.log` (a line a turn),
`campaign_progress.jsonl`, `campaign_events.jsonl`, `campaign_state.json`, `memory.json`,
`campaign-report.md`.

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
6. Disk: ~3 GB of recovery saves over the run.

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
