# Multiplayer: the harness plays one country, a person plays another (#91)

Single-player HOI4 has one human country. For "the harness plays France while I play
Germany" the game has to be multiplayer, and that changes what the harness can use. This
page says what is built, how a hosted session is set up, and what only a live two-client
game can settle. **Nothing here has run against a real multiplayer session.**

## What is built

```bash
hoi4-harness play --mp-client --country FRA --mp-human GER \
  --adapter logtail+input --live --handover-mode approve --run-dir runs/mp
```

| Setting | Meaning |
|---|---|
| `--country FRA` | The tag the harness plays (the client it runs on). |
| `--mp-human GER[,SOV]` | The countries people play. Sets `multiplayer`. |
| `--mp-client` | Sets `multiplayer` and the player clock: the host owns speed and pause, so the harness never pauses, resumes or sets speed. |

- **Refusal.** An action aimed at a country in `--mp-human` (a `diplomacy` target, a
  `set_ai_directive` target, a `set_trade` source, a theater, a front's enemy) is refused at
  queue time and again when a queued call runs, case-insensitively. The brief says who plays
  whom every turn.
- **Checked at start**: `country` and `mp_human_tags` are required, the harness's country
  cannot also be a person's, and the clock must be the player's.
- **Not scoreable**: the eval runner refuses a multiplayer run, as it does any run a person
  acts in.
- **Fair play** is yours to agree with the other player before the game. The refusal above
  keeps the harness off their country; it does not make a console effect on your own country
  fair. If the agreement is "nothing the UI cannot do", narrow the run with `--actions` to
  the click-backed actions. Combine with `--human` and `--handover-mode` as in a single-player
  co-op game if a person also shares the harness's client.

Tested on the mock and on the fake multiplayer game: `tests/test_coop.py` (refusal, brief,
checks, eval refusal) and `tests/test_91_multiplayer_fake.py` (the whole loop, below).

## What the harness needs, and where each part is

| It needs | Where | Proved on the fake |
|---|---|---|
| The player clock: the harness never pauses, resumes or sets the speed | `--mp-client` sets `clock_owner player`; `set_game_speed` is not offered; `validate` refuses `clock_owner harness` | `test_91_the_harness_never_pauses_resumes_or_sets_the_speed`: the fake client counts every space / speed key and gamespeed command, and there are none |
| It waits for the host's clock | the composite's clock wait polls the date | `test_91_the_game_clock_is_the_hosts_and_the_loop_waits_for_it` (the host pauses mid-run) |
| The country it plays, and only that country's telemetry | `--country` keeps the log reader to that tag; both players are `is_ai = no`, so both are on the log | `test_91_per_country_telemetry_never_shows_the_other_players_numbers` (the other player's numbers are on the log and in no brief) |
| Refuse actions on a human country | `coop.refusal`, at queue time and again at run time | `test_91_an_action_aimed_at_the_human_country_is_refused...`, `...refused_again_when_it_runs` |
| Who plays whom, said every turn | `coop_lines` | `test_91_the_brief_says_who_plays_whom_every_turn` |
| MP chat as the message channel | `adapters/mp_chat.py`, through the input driver | `test_91_the_message_channel_is_the_games_chat...` |
| The harness says what it did | `CoopGuidance.after_turn` | `test_91_what_the_model_did_is_said_in_the_games_chat` |
| The host can hand a domain over or take it back | `hoi4-harness domains`, or a chat line (`take army`, `give army`) | `test_91_the_host_can_move_a_domain_by_chat` (the chat line comes through a seam, see below) |
| A console that may be shut | the fake's `console="blocked"` | `test_91_a_client_without_the_console_cannot_apply_a_console_order` |

`FakeMultiplayerGame` (`fakemultiplayer.py`) is the fake: the harness's country and a person's,
both human, on one log; a host who owns the clock (a client's space and speed keys do nothing and
are recorded); an optional shut console; an in-game chat box that Enter opens and sends. **It is a
model of what the live game is believed to do, not of what it does.** Not simulated: the lobby,
latency between the two clients, desync, a real chat box's length and character limits, the
host's saves.

Found while building it: the chat line used to begin `[SWE to GER]`, and the key layer cannot type
brackets (it refuses the whole string before sending a key), so no chat line would ever have been
sent. The line is now `SWE to GER, text`, and anything the key layer cannot type in the text
(brackets, colon, question mark, accents) is dropped or flattened first (`typable`). The chat was
also unreachable from the headless adapter (`find_driver` did not look through the router).

**Reading the other player's chat** is built, from the screen, and UNCALIBRATED (see "Reading the
other player's chat" below). The game's chat is not in `game.log`, so the harness reads the chat box
with Windows OCR (`adapters/mp_chat_reader.py`) and feeds the new lines into `CoopGuidance`'s
`incoming` seam: a line like `take army` moves a domain, and every new line from the other player
is in the model's brief and wakes it. The file chat (`hoi4-harness chat`) and `hoi4-harness
domains` still work beside it.

## Setting up a hosted session: the exact live steps

Two machines (the person's PC and a second one for the harness; the same PC cannot run two
copies), two Steam accounts that both own HOI4 1.20, the same playset and the LLM Bridge mod
([mod-bridge.md](mod-bridge.md)) enabled on both, this repository on the harness machine.

1. **Host (the person).** Launch HOI4, Multiplayer, Host game. Pick Germany. Leave France open.
   **Ironman off.** The harness never touches the clock either way. Do not start yet.
2. **Harness machine.** Launch HOI4 from the launcher with **Open game in Debug Mode** (the
   console needs it). Multiplayer, Join game (Steam friend or the host's address). Pick
   France, ready up.
3. **Host starts the game** and unpauses.
4. **Settle stage 0 first**, before any model call: on the harness machine, with the game in
   front, open the console by hand and type `run llmb_turn.txt`, `gamespeed 3`, `savegame`,
   and press space, as the client; fill the table below. What fails decides which of the two
   commands in step 5 is the real one.
5. **Harness machine, a terminal in the repository:**

   ```bash
   hoi4-harness doctor
   hoi4-harness play --mp-client --country FRA --mp-human GER      --adapter headless --headless-reader logtail --live      --handover-mode approve --run-dir runs/mp --max-usd 5
   ```

   If the console is blocked for a client: `--adapter logtail+input`, with `--actions` narrowed
   to what the clicks can do.
6. **Talk.** The model's `send_message` to GER types into the game's chat (Enter, text, Enter);
   watch whether the line arrives. To move a domain mid-game, on the harness machine:
   `hoi4-harness domains --run-dir runs/mp --take diplomacy` or `--give diplomacy`.
7. **Stage 2, the acceptance run:** one game-year with the host playing Germany, then a written
   report (what the model did, what was refused, what went stale, the latency).
8. Only the host saves. Production and construction verification fall back to the log on a client
   (`log+save` loses its on-demand save).

## Talking to the other player

In a multiplayer game (`--mp-client` / `--mp-human`) the person is in the game, not at a chat file, so the model's `send_message` to a human (`person`, `global` or a country in `--mp-human`) is typed into the **game's own chat**: Enter opens the chat box, the line goes in as `FRA to GER, text`, Enter sends it (`adapters/mp_chat.py`, behind the input driver, so the focus check, the key guard and dry-run all apply). `send_message` to another LLM country still goes to the file chat when the run has one; single-player runs are unchanged.

**UNVERIFIED.** Nothing here has run in a live multiplayer game. Unconfirmed: that Enter opens and sends in the chat box on a client, the line length it accepts (200 characters is a guess), characters beyond plain ASCII, and whether a line typed while the host has the game paused is taken. The two keys are `OPEN_KEY` and `SEND_KEY` in `mp_chat.py`. Reading is a separate module, below. Tested with a fake driver in `tests/test_chat.py`.

## Reading the other player's chat

Windows OCR (the engine the event reader uses; nothing new to install) reads one rectangle of the
game window once per turn. Per read:

1. **Capture.** Only the game window, and only while it is the foreground window (the input
   guard's check); otherwise the read is skipped and the brief says why. The rectangle is
   `mp_chat_screen.rect`, four fractions of the window (`x, y, w, h`), clamped to the window, so the
   desktop is never captured. A dry run reads nothing.
2. **Parse.** `Sender: text`, optionally after `[hh:mm]`. A row with no sender continues the line
   above (the box wraps). Replace the pattern with `mp_chat_screen.pattern` (a regex with `sender`
   and `text` groups) if the game's format differs.
3. **De-duplicate.** Each read overlaps the last one; only the lines after the overlap are new,
   matched loosely so one misread letter does not repeat a line. The first read of a run is a
   baseline: talk already in the box is context, not a command (`"baseline": false` turns that
   off).
4. **Own lines.** Anything the harness typed (`MultiplayerChat.sent_lines`, and any line starting
   `<TAG> to `) and any sender in `mp_chat_screen.own_names` is dropped.
5. **Use.** New lines go to `CoopGuidance`: `take army` / `give army` are parsed as in the file chat,
   the lines show in the brief as `NEW Name: text`, and a new line wakes the model.

Profile keys, all optional (`mp_chat_screen`):

```json
"mp_chat_screen": {"enabled": true, "rect": [0.01, 0.60, 0.32, 0.28], "upscale": 2.0,
                   "own_names": ["YourInGameName"], "pattern": null, "baseline": true}
```

**UNCALIBRATED.** Without `rect` the harness uses a guess (`0.01, 0.60, 0.32, 0.28`: the lower left
of the window) and the start line says `UNCALIBRATED`. Nothing about the real box is verified: its
position, its row format, how long lines stay, whether the typewriter font reads at your resolution.

### Calibrating it live

1. Join the hosted game as in the setup steps and have the host type a few lines, one long enough
   to wrap, and one starting with a time if the box shows one.
2. With the game in front, run (no `--provider`, so the scripted stand-in model is used and nothing is spent)
   `hoi4-harness play --mp-client --country FRA --mp-human GER --adapter headless
   --headless-reader logtail --live --turns 3 --run-dir runs/mp-chat`.
   Each turn appends one line to `runs/mp-chat/mp_chat_reads.jsonl`: the raw OCR rows, what they
   parsed to, and what was heard.
3. Read the file. Rows empty or garbage: the rectangle is off. Take a screenshot yourself, measure
   the box as fractions of the window (box left / window width, and so on), put them in `rect`, and
   repeat. Rows right but `parsed` wrong: set `pattern`. A line missing its first word or its
   sender: widen the rectangle or raise `upscale`.
4. Have the host type `take army`; the model's brief should show `NEW Host: take army` and the army
   should change hands. Have the harness send a line: its own `FRA to GER, ...` row must not come
   back as heard (if the game prefixes your in-game name differently, add it to `own_names`).
5. Record the final `mp_chat_screen` and the resolution in the table below.

Tested on fake captures only: `tests/test_91_chat_read.py`.

## What needs a live session

Stage 0 of #91 is a table, filled by running each channel from a non-host client of a real
two-client game. Every row is open.

| Channel the harness uses | Host | Non-host client | Needs debug | Evidence |
|---|---|---|---|---|
| Console opens at all | | | | |
| `run llmb_turn.txt` | | | | |
| `eval_effect` / `effect` | | | | |
| `gamespeed` | | | | |
| `savegame` | | | | |
| Space (pause) | | | | |
| Focus, research and production clicks (no console) | | | | |
| `game.log` carries the mod's lines for the client's own country | | | | |
| The chat box read by OCR: rectangle, row format, own-line filter | | | | |

Open questions that no test here can answer, each a line to settle on the live session:

1. Which console commands work for a non-host client, with and without debug.
2. Whether `game.log` on the client gets the mod's lines for its own country, and whether the
   mod must log by the harness's country flag rather than `is_ai = no`, since the other human
   is `is_ai = no` too (campaign mode already adds the flag).
3. Whether the keyboard and click paths alone are enough when the console is blocked: that
   brings back the layout problem headless mode solved.
4. The latency budget: the harness reads, decides and acts while the game runs, with no
   pause. How stale is a queued action by the time it runs, at the host's speed?
5. Stage 2 of #91: a person hosts Germany and the harness plays a neighbour for a game-year,
   with a written report. This is the acceptance run.
