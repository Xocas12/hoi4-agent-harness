# The inbox: an LLM plays a country with no keyboard, console or focus

`hoi4-harness native --rival POL` makes an LLM the strategist of Poland while you play
another country in the same single-player game. The harness never sends a key, never opens
the console, never needs the game window in front and never waits for you to pause. You
play, and Poland is steered in the background.

## Why it works the way Expert AI does

Expert AI never reads input from outside the game. It is script the game runs on a timer:
- its `on_daily`, `on_weekly` and `on_monthly` hooks run effects that take decisions, pass
  laws, queue construction and spend political power;
- its `ai_strategy` blocks are switched on and off by flags and variables those effects set.

An LLM can sit in the same seat. The difference is that its decisions come from outside the
game, so they need a way in while the game runs. Everything the harness already does to
steer an AI country is the same kind of script: directives and postures are `ai_strategy`
weights set by scripted effects, and decisions are `activate_decision`. All that was missing
was a way to hand that script to a running game without the console.

## How the orders get in

Started with `-debug_mode`, HOI4 watches its files and reloads the database a changed file
belongs to. Events are on the list (dev diary for 1.5.2). The mod has a static hook and two
hidden events:

```
on_daily (each harness country)  ->  llmb_inbox.1  ->  TAG = { country_event = llmb_inbox.2 }
                                                        llmb_inbox.2 in TAG's scope: the orders
```

The harness rewrites `events/llm_bridge_inbox_events.txt` in the installed mod. The game
reloads it, and the next in-game day runs it. Each batch:
- runs in the scope of the country it is for, so `ROOT` is that country, exactly as
  `effect TAG name` typed in the console;
- is guarded by a global flag named after the batch (`llmb_inbox_<id>`), so it runs once
  however many countries fire the hook, and loading an older save neither replays an old
  batch nor blocks a new one;
- logs `LLMB|v5|inbox|tag=TAG|id=<id>|via=event`, which the harness reads as the
  acknowledgement. An acknowledged batch leaves the file.

The same orders are also written into a scripted effect (`llm_bridge_inbox_run`) that the
events call. If 1.20 reloads scripted effects and not events, that copy delivers them
(`via=effect` in the log). Both copies share the flag, so only one runs.

What a batch can hold is what the console channel would have typed, converted line by line:
directives, postures, theatre postures, taking decisions, advisors, construction. A console
command that is not an effect (`savegame`, `gamespeed`) has no inbox form, so it is refused.
That is also why `native` uses the log alone to read the game, with no text saves.

## The model

`native` defaults to `--provider claude-cli`: each turn is one `claude -p` call on your
Claude Code subscription (haiku unless `--model` says otherwise). The CLI runs:
- with no tools of its own (`--tools ""`);
- without your settings, hooks or MCP servers (`--setting-sources ""`, `--strict-mcp-config`);
- in a scratch folder outside any repo, with `ANTHROPIC_API_KEY` removed from its
  environment, so it never bills the API;
- through `claude.exe` with no console window, so nothing flashes over the game.

One turn measured at about 350 input and 360 output tokens; the CLI reported $0.003
(notional on a subscription). Any other provider works too (`--provider mailbox` for a
Claude Code subagent, or an API provider with `--max-usd`).

## Running it

Once, with the game closed, install the mod (adds the inbox and the hook):

```
python scripts/live_setup.py prepare --apply
```

Then every time:
1. Start the game in debug mode: launcher, the arrow next to Play > **Open game in Debug
   Mode**. To skip that step, add `-debug_mode` to the Steam launch options once, or use
   `--launch --game-dir "<HOI4 install>"`.
2. Run:
   ```
   hoi4-harness native --rival POL --keep-running --telemetry-hours 6
   ```
3. Load or start your game and play. Poland is marked at the first in-game day that passes,
   and the model takes over from there.

`hoi4-harness inbox status` shows what is posted and whether the game runs in debug mode.
`hoi4-harness inbox probe` posts a batch that only logs and times the acknowledgement.

## What can go wrong

- **The game was not started with `-debug_mode`.** It never reloads the file, so no order
  lands. `native` reads the game's command line and refuses to start (override with
  `--no-debug-check`). The rival start also times out with that reason.
- **The game is paused.** Orders wait in the file and land at the next in-game day. That is
  the point: they are applied while you play, never by interrupting you.
- **OneDrive.** The installed mod is under Documents, which OneDrive may sync. The writer
  retries for 2 s if the file is locked. A sync that rewrites the file with an older copy
  would bring back acknowledged batches, but their flags stop them running twice.
- **Debug mode itself.** It shows an error counter in the corner and runs extra checks; it
  does not change the game's rules.

## Live plan (not yet run)

| Step | What it shows | How |
|---|---|---|
| 1 | 1.20 reloads the inbox from Documents | `inbox probe` with the game unpaused: ACKNOWLEDGED, and which copy (`via`) |
| 2 | latency | the probe's seconds, at speed 3 |
| 3 | rival start | `native --rival POL`: the mark lands, POL state lines appear |
| 4 | orders while playing | the model's directive appears as kind=14 for POL while you play, with no key in driver.log |
| 5 | saves | save, reload an older save: no batch runs twice, new batches still land |
| 6 | no debug mode | start without it: `native` refuses with the reason |
| 7 | balance of power | a rival with a bar (SWE, ITA): the brief's `Balance of power` line matches the bar on screen within 0.01; a decision from its category moves it (#135) |
| 8 | custom GUI | with a playset that has one (Expert AI, a TNO country): `list_gui_buttons` lists the window, `click_gui_button` lands and the window shows it; a button whose condition fails comes back "did not hold" (#136) |
| 9 | the campaign | only after 1 to 8 pass: `native --rival <TAG> --keep-running` for a whole campaign, the strategist on the strategy sheet (`rival+strategy`); judged by states held, wars won and surrender progress, against the same country left to the game's AI |
