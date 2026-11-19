# Rival mode: the model steers one country in your single-player game

You start a single-player game as country A. The model is the grand strategist of country B,
an AI country in the same game. B's own AI runs its units, research, production and focuses;
the model only sets the standing orders above that (who to invade, whom to protect, how hard
to press, which decisions to take). You play A at your own pace, with your own clock.

```bash
export HOI4_GAME_DIR="C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV"
export HOI4_LOG_PATH="$HOME/OneDrive/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
export ANTHROPIC_API_KEY=...

# game running as A (say GER), mod schema v5 installed last in the playset; then:
hoi4-harness play --rival POL --live --model claude-opus-5-5 --max-usd 5 \
    --game-dir "$HOI4_GAME_DIR" --run-dir runs/rival-pol
tail -f runs/rival-pol/rival.log

# offline, no game: the whole stack against a fake game with a fake person in it
hoi4-harness play --rival SWE --fake-game --provider scripted --until 1936-06-01 --run-dir runs/rival-fake
```

## Why it is not simultaneous

A single-player game has one human country, one keyboard and one clock, and the model has no
other way into the game than the console and the keys. Two things follow, and the mode is
built around them rather than hiding them:

- **The model's country is an AI country.** It cannot be played, so it is steered: exactly
  the division of labour of [campaign mode](campaign.md), applied to a tag that is not yours.
  The mod already reports "any country flagged `llmb_harness`" (schema v5), so the harness
  flags B, reads B's lines out of the shared `game.log` and sends its orders in B's scope
  (`effect B llm_bridge_raise_invade`, `eval_effect B = { ... }`).
- **Typing happens in your pauses.** Synthetic keys land in whatever you have on screen. So
  the model decides whenever it likes, every action is queued, and the queue is typed only
  in a window your own behaviour makes safe. Orders reach B days or weeks of game time after
  the model chose them. That is slow on purpose, and the prompt says so.

## What it can and cannot do

| | |
|---|---|
| Can | `set_ai_directive` / `clear_ai_directives` (invade, protect, contain, befriend, antagonize, ignore, per target), `set_ai_posture` (global or per theater), `take_decision` and `hire_advisor` (with B's political power, guarded by the decision's own conditions), `note`, `advance_time` (its next review, 7-90 days) |
| Cannot | Pause, resume or change the speed (you own the clock: `clock_owner = player`, and `set_game_speed` is removed from its tool list, so no whitelist can give it back). Start research, pick a focus, queue construction or production, move a unit: B's AI would undo or double them, and a construction or production order costs two 66 MB saves to verify. No `human_ai`, no console command that acts on "the player" |
| Sees | B's own telemetry: its states, factories, manpower, wars with the divisions the game reports for each enemy, the weekly camp line (states held, share of the world). If you declare war on B it shows up as a war and wakes the model, as any war would |
| Does not see | Your plans, economy or orders. The reader keeps to B's tag on a log that also holds yours (you are `is_ai = no`, so the mod always logs you); nothing from your lines reaches the brief. A test puts distinctive numbers in the fake person's lines and checks none appears |

## When it types: `--rival-act-when`

| Policy | A window opens when | Use it |
|---|---|---|
| `paused` (default) | the in-game date in game.log has stood still for `--rival-paused-seconds` (10) **and** the game window is in front **and** nobody has used the keyboard or mouse for `--rival-idle-seconds` (5) | you pause to think and take your hands off. Windows only (the idle clock is `GetLastInputInfo`); elsewhere it refuses and says to use `handover` |
| `handover` | you run `hoi4-harness handover --run-dir runs/rival-pol` (bind it to a hotkey; the grant is a file, as in [co-op](co-op.md)). `--release` takes it back | you want to decide when. Works on any platform |
| `never` | never. What the model asks for is shown, not run: the result says so, the next brief says so, `rival.log` lists it | advice only. You type what you like yourself |

The model is told which policy is in force. A window is one pass of the same four steps the
console channel always uses (console key, `run llmb_turn.txt`, enter, console key) however
many actions are queued, so the queue goes out as **one file**. The file ends with a telemetry
refresh, so the result can be checked while the game is paused.

## What is checked, and when

1. **Before a window**: the policy's own test, every poll. The reason it is closed is logged
   once, and again only when it changes (`2 queued, waiting: the game window is not in front`).
2. **Before every key and every typed character** (the input driver asks `KeyWatch`): did
   the date move (you unpaused), did anyone else press something, did the window lose the
   focus, was the grant withdrawn. Our own keys also move `GetLastInputInfo`, so the guard
   keeps the last-input stamp from right after each key event it sent, looks again right before
   the next one, and treats any other stamp as you.
3. **If you come back mid-sequence**: the harness stops at the next key. The console key
   that closes a console this window opened still goes through, because an open console
   swallows your keys. Nothing else is sent, and Escape never is. The run file may be on
   disk but it is run only by the `enter` that was never pressed. Every action stays queued,
   in order, for the next window.
4. **Before an action runs**: re-checked against the world as it is then. Stale ones are
   reported, never typed: a directive already standing, a posture already set, nothing to
   clear, `protect` or `befriend` aimed at a country B is now at war with, and anything queued
   more than `rival_stale_days` (180) in-game days ago (0: never by age). The adapter's own refusals still apply
   (cost, conditions, unknown ids). The next brief says what the window did, once.

The three real stops are unit-tested with injected clocks and end to end against the fake
game: a person who types between two keys of the sequence, one who unpauses, one who withdraws
the grant. A control test turns the guard off and shows the same keypress corrupting the
command line.

## Residual risks

Read these before trusting it with a game you care about. None is a crash; each is a way the
harness can still touch your game.

- **Your key in the console.** The guard sees your input between two of our key events, not
  in the few milliseconds between its look and the event. A key you press then goes into the
  console line, not the game: you lose that keypress (a pause you meant, a hotkey), the
  command we were typing is corrupted and fails with "could not open file" or similar, and
  the actions stay queued. A window takes about 3 seconds; do not touch the keyboard while
  the console is on screen.
- **A console you left open.** The console key is a toggle and the log cannot say whether the
  console is open. If you left it open, our first press closes it and the text we type goes
  to the game as hotkeys. The 5 seconds of keyboard quiet is the only mitigation: do not
  leave the console open.
- **Text left in the console line.** If a window stops after some characters were typed and
  before `enter`, the console is closed but the typed text may still be in its input line.
  Unverified in the real game (live plan, step 13.7); if it is, the next window would append
  to it and fail.
- **A pause is read from the log.** A game that is paused in a menu, or waiting on an event
  popup you have not answered, looks like a pause. The window then opens over the popup,
  which is harmless to the popup but surprising. The date resolution is a day, so
  `--rival-paused-seconds` must be longer than a day takes at the slowest speed you play.
- **Other input.** `GetLastInputInfo` is per session and sees any input source, so typing in
  a browser on another monitor also closes the window (conservative). A controller or
  remote input that bypasses it does not.
- **Mouse jitter** (a noisy wireless mouse, a hand resting on one) that never allows 5 quiet
  seconds means it never acts. `rival.log` says so each time the reason changes; use `handover`.
- **Marking is a console command too.** `llm_bridge_mark_harness` at the start and
  `..._unmark_harness` at the exit go through the same windows. At the exit it waits
  `--rival-exit-wait` seconds (60) for one, then prints the line to type by hand. A
  rival left marked is harmless: it keeps logging.

## Marking, reading, clock, waking

- **Start.** If B already reports (a state line for B whose date is the log's), nothing is
  sent. Otherwise `effect B llm_bridge_mark_harness` plus a telemetry refresh go out in the
  first window, and the start waits for the log to show the mark event (kind 16) and a state
  line for B before the first brief. With `never` it prints the line for you to type and
  watches the log. No window within `--rival-start-wait` minutes (30) is a start failure
  that says what to type.
- **Reading.** `country = B`: the reader keeps B's state lines and every event line that
  names B as either side, so a war declared on B shows even though the declarer is you.
- **Clock.** The loop never pauses, resumes or sets the speed. A turn waits on the log date
  with no deadline: if you pause for an hour, nothing wakes the model and nothing runs
  except queued actions in their windows. The game process going away ends the run cleanly
  (`GameLost`).
- **Waking.** As campaign mode: a scheduled review (30 days in peace, 14 at war, or its own
  `advance_time`) and a critical event (a war, a capitulation) at most once per 7 days.
- **Exit.** B is unmarked in a window if one comes soon enough. The queue stays on disk
  (`handover-queue.json`); `--resume` reloads it.

## Checking what it did to B

B's flags are not in your UI. The evidence is in game.log, B's own lines:

```bash
grep "LLMB|v5|evt|tag=POL" "$HOI4_LOG_PATH" | tail -20
# kind 16 = marked, 13 = target selected, 14 = directive raised (detail=invade POL),
# 15 = theater posture, 10/11 = global posture, 12 = cleared, 17 = unmarked
```

`runs/rival-pol/rival.log` has a line per turn and per window, `transcript.jsonl` every
record (`handover` records list what ran, what went stale and how long each action waited),
`handover-queue.json` the current queue, `campaign_progress.jsonl` B's numbers over time.

## Files, flags, exit codes

`--rival TAG`, `--rival-act-when paused|handover|never`, `--rival-paused-seconds`,
`--rival-idle-seconds`, `--rival-start-wait`, `--rival-exit-wait`, `--until`, `--resume`, `--max-usd` (a priced model needs one),
`--fake-game --fake-human-script PATH` (what the fake person does, as
`[[seconds, "pause"], [seconds, "type", "x"], [seconds, "war"]]`). Configuration fields are
`rival`, `rival_act_when`, `rival_paused_seconds`, `rival_idle_seconds`, `rival_stale_days`,
`rival_start_wait_minutes`, `rival_exit_wait_seconds`. Exit codes: 0 ended (or ran out of
turns), 1 stopped with a reason, 2 could not start, 3 crashed.

Rival mode refuses `--campaign`, `--advisor` and `--handover` (it has its own queue and
policy), a dry run (it types: `--live` or `--fake-game`) and `--no-window-guard`.

## What is proved and what is not

On the fake game: the marking, the B-only brief, actions queued while the game runs and typed
in the next pause as one file, the stop when you type or unpause or withdraw the grant, the
handover and never policies, the stale re-check, your declaration of war waking the model,
the unmark, no pause key or `gamespeed` ever sent, no key outside a console, everything in B's
scope. Not proved: that the real game accepts B-scoped `effect` / `eval_effect` from the
console, that `is_ai`-gated on_actions keep logging B once flagged, that the log date is a
usable pause signal, that the real AI acts on B's directives, how `GetLastInputInfo` behaves
around SendInput on your machine. Those are the live plan: [live-session.md](live-session.md)
step 13.
