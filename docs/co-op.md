# Co-op: playing alongside the model

The harness was designed as if the agent were the only player: it owns the clock
and the keyboard. With a person in the same campaign, both assumptions break,
and three settings fix them.

| Mode | Flag | Who acts |
|---|---|---|
| Advisor | `play --advisor` | You. The model recommends through real tool calls; nothing executes. Needs only a read-only adapter. |
| Player clock | `--player-clock` | Either, but the harness never pauses, resumes or sets speed. |
| Handover | `play --handover` | The model decides whenever it likes; its actions wait in a queue until you hand it the keyboard. |
| Split by domain | `play --human army,navy,diplomacy` | You hold those domains; the model acts in the rest and never in yours. |
| Multiplayer | `play --mp-client --country FRA --mp-human GER` | The model plays one country and a person another: [multiplayer.md](multiplayer.md). |

## Handover

Two people cannot share a keyboard, so handover is turn-taking:

```bash
hoi4-harness play --adapter logtail+input --live --player-clock --handover --run-dir runs/coop
# ...when there is a lull:
hoi4-harness handover --run-dir runs/coop            # shows the queue, then grants control
hoi4-harness handover --run-dir runs/coop --release  # take it back mid-run
```

- **Every action is queued, not executed**, and the tool result says so — the
  model is told not to plan as if it had happened. `note` and `advance_time`
  change nothing you could be clicking on and pass straight through.
- **What is queued is always visible**: `handover-queue.json` in the run
  directory, rewritten on every change, and a line in each brief. It is also
  what survives a restart: `play --resume --handover` reloads it, so what you
  were shown as queued is what runs when you hand over. A fresh run starts with
  it empty.
- **The grant is a file** (`handover.grant` in the run directory). `hoi4-harness
  handover` creates it; bind that command to a hotkey with whatever your OS
  offers, or let the harness do it (below). The harness runs the queue at the end
  of its next turn and deletes the file when done. Deleting it yourself takes the
  keyboard back: the harness stops *between* actions and keeps the rest queued.
- **Each action is re-checked before it runs.** The world moved while it waited,
  so a focus queued last week is reported stale if one is already running, a
  research call if no slot is free, a production change if the factories are no
  longer there. Stale actions are reported, never clicked. Everything is also
  validated again, and the adapter's own verification still applies.
- **The next brief says what the window actually did** — ran, stale, failed,
  aborted — once, so the model replans on what happened.
- **The transcript records every window**: when it was granted, what ran, and
  when each action was queued and when it ran. A model whose plans routinely go
  stale before they run is telling you the handover cadence is wrong.

### What opens a window: `--handover-mode`

| Mode | The window is open when | Stops when |
|---|---|---|
| `hotkey` (default) | you ran `hoi4-harness handover`, or pressed `--handover-hotkey` | you delete the grant, **or you touch the keyboard or mouse** (the grant is then gone and the rest stays queued) |
| `idle` | the game is paused (its date has stood still for `handover_paused_seconds` in game.log), the game is in front, and you have not touched the keyboard or mouse for `--handover-idle-seconds` | the date moves, you touch an input, or the game loses the focus |
| `approve` | always, but each action is shown first and runs only on a yes | you say no (a decision, recorded) |

```bash
hoi4-harness play --adapter logtail+input --live --player-clock --handover-mode idle
hoi4-harness play --adapter logtail+input --live --player-clock --handover-mode approve
hoi4-harness play --adapter logtail+input --live --player-clock --handover-hotkey ctrl+alt+h
```

- **Detecting you.** Windows' `GetLastInputInfo` through `ctypes` (`rival/safety.py`,
  `InputWatch`), reached only through an injected source: the tests give it plain numbers
  and never read the OS. The harness's own keys move that clock too, so the guard remembers
  the stamp after each key it sends and treats any other change as you; the input driver asks
  it before every key and typed character, so a window stops between keys, not only between
  actions. What it cannot see is input in the milliseconds between that look and the key
  event itself. On a platform with no idle clock `idle` mode never acts (it says why) and
  `hotkey` mode loses only the abort-on-input, not the grant.
- **The hotkey is off by default** and is not a keyboard hook: it registers one global
  key combination (`RegisterHotKey`, Windows) on a thread that only waits for it, so the
  listener sends nothing and reads no other keys. The combination is taken from every
  program while the run lasts, and it needs a modifier. It sits behind `HotkeyListener`;
  the tests use `FakeHotkeyListener`. Without it, the grant file is the trigger.
- **`approve`** uses `HOI4Env.confirm_hook` (the terminal prompt from `--confirm`, installed
  for you in this mode). Asked when the action is about to run, not when the model queues
  it, and no hook means no. `always` answers are remembered for that action and mode only.
- **How long it waited**: each queued action records the wall-clock time it was queued; a
  window's transcript record carries `waited_seconds` per action, `granted_at`,
  `window_seconds`, `mode` and, for an aborted one, `stopped_because`.

A file rather than a keyboard hook, because a hook in this process listening for
your keys would itself be input contention, and a file is the one signal every
platform, terminal and hotkey tool can produce.

## Rival: the model plays another country

In a single-player game there is one human country, so the model cannot be a second player.
`play --rival TAG` makes it the strategist of an AI country instead, with the same queue and
the same grant file as handover, plus a policy that opens windows on its own when you pause
and leave the keyboard alone: [rival.md](rival.md).

## Split portfolio

Divide by domain rather than by time: the model takes the economy, you keep the
army, navy and diplomacy.

```bash
hoi4-harness play --adapter logtail+input --live --player-clock --handover \
  --human army,navy,air,diplomacy
```

`--human` marks those domains `human` in the control map ([modular-control.md](modular-control.md));
it is the same as `--control-map "default=llm,army=human,navy=human,..."`. For the model `human`
is `off` plus three things: the system prompt says a person holds it, **every brief says so**
("Held by the player, not yours"), and an action in it is refused at queue time and again when a
queued call runs, whatever an allow-list or a script asks for. It cannot sit beside a `game`
domain (the game's AI would act there too) unless `allow_contested` is set. The older
`--actions a,b,c` whitelist still works and says less.

**Guidance.** `hoi4-harness guide --run-dir runs/coop "I will hold the Rhine, put the factories
on rifles"` works in a co-op run as it does in a campaign: shown in the brief, marked new once,
the model woken for it once, recorded in the transcript. Advice the model weighs, never executed.

## Not for scoring

Once a person acts too, a score says nothing about the model. The eval runner
refuses a run configured for handover, advisor mode, a `human` domain or multiplayer rather
than producing a number that means nothing.

Handover needs an input driver that can act (#3) to do anything in a real game; against the
mock it is exercised by `tests/test_handover.py` and `tests/test_coop.py` (every mode, the
abort, the waiting times, the domains, the hotkey through its fake). What only a live game can
settle is listed in [live-session.md](live-session.md), section 15.
