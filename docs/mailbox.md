# Mailbox: an agent outside the harness plays the turns

`--mailbox` replaces the model API with a folder. The harness writes each request there
and waits; anything that can read a file and write one answers it. The point is to let a
Claude Code agent (any model it runs, on a subscription, no API key) play through the
harness, with the harness still doing everything else: the brief, the action catalog and
its validation, the console channel, verification, the transcript, the campaign report.

```bash
# the harness side: any play mode, with --mailbox instead of a provider
hoi4-harness play --campaign --live --profile profiles/campaign-ger-world.json \
    --mailbox --until 1948-12-31 --run-dir runs/campaign-ger-1936

# the answering side, in a loop
hoi4-harness mailbox system --dir runs/campaign-ger-1936/mailbox     # once: the rules
hoi4-harness mailbox wait   --dir runs/campaign-ger-1936/mailbox --timeout 600
#   prints the request (tools, then the conversation); exit 3 = the run has ended
hoi4-harness mailbox answer --dir runs/campaign-ger-1936/mailbox --file answer.json
#   answer.json: {"text": "why", "calls": [{"name": "set_ai_directive",
#                 "arguments": {"directive": "invade", "target": "POL"}},
#                {"name": "advance_time", "arguments": {"days": 30}}]}
```

## The folder

| File | Written by | What |
|---|---|---|
| `system.md` | harness | the system prompt; rewritten only when it changes |
| `request.json` | harness | `{seq, tools, messages}`: the conversation in the provider-neutral shape |
| `request.md` | harness | the same for a reader: one line per tool, then the turns |
| `response.json` | answerer | `{seq, text, calls}`; taken only when `seq` matches the pending request |
| `closed` | harness | the run has ended; `mailbox wait` exits 3 |

`mailbox answer` checks the answer before handing it over (tool names offered, required
arguments present, enum values valid) and refuses it otherwise, so a typo costs a retry
rather than a turn. Everything else is the harness's usual validation: a refused action
comes back as a tool result, as it would for a real model.

## What it changes

- **Spend:** the budget sees zero tokens. `max_llm_calls` still caps the number of
  requests, and `--max-usd` has nothing to stop.
- **Waiting:** the harness waits for as long as it takes (`--mailbox-timeout MIN` to give
  up). In a campaign the game is paused while it waits, and the silence watchdog only
  counts time while the game runs, so a slow answerer does not trip it.
- **Resume:** sequence numbers carry over a `--resume`, so an answer left over from the
  last run is never read as the new run's.
- **Several rivals:** `--rival A,B --mailbox` gives each country its own folder,
  `<mailbox>/<TAG>/`, so each is its own conversation. The per-country folders do not get
  a `closed` marker yet; an answerer stops on its `--timeout`.

## Keep wakes short

Answer with every order **and** `advance_time` in the same response. If the harness
comes back with the orders' results first, a request ending in tool results, answer it
with `advance_time` alone (or a correction). It asks at most `max_tool_rounds_per_turn`
times per wake.
