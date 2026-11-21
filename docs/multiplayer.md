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

Tested on the mock and the fake game only: `tests/test_coop.py` (refusal, brief, checks, eval
refusal).

## Setting up a hosted session

1. Two machines, two licences, two Steam accounts. The person hosts. The harness's client
   runs on the second machine (or a VM) with the same playset, the LLM Bridge mod
   ([mod-bridge.md](mod-bridge.md)) and this repository.
2. Host: Multiplayer, create a game, pick Germany for yourself, leave France for the second
   client, **ironman off** (the console is unavailable otherwise), start.
3. Harness client: join, pick France, ready up. Let the host start. Do not touch the harness
   machine's keyboard once the run is going.
4. Run the command above on the second machine.
5. Only the host saves. Production and construction verification fall back to the log on a
   client (`log+save` loses its on-demand save).

## Talking to the other player

In a multiplayer game (`--mp-client` / `--mp-human`) the person is in the game, not at a chat file, so the model's `send_message` to a human (`person`, `global` or a country in `--mp-human`) is typed into the **game's own chat**: Enter opens the chat box, the line goes in as `[FRA to GER] text`, Enter sends it (`adapters/mp_chat.py`, behind the input driver, so the focus check, the key guard and dry-run all apply). `send_message` to another LLM country still goes to the file chat when the run has one; single-player runs are unchanged.

**UNVERIFIED.** Nothing here has run in a live multiplayer game. Unconfirmed: that Enter opens and sends in the chat box on a client, the line length it accepts (200 characters is a guess), characters beyond plain ASCII, and whether a line typed while the host has the game paused is taken. The two keys are `OPEN_KEY` and `SEND_KEY` in `mp_chat.py`. Reading the other player's chat back is not built: the model only sends. Tested with a fake driver in `tests/test_chat.py`.

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
