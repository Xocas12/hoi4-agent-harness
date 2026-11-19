# The LLM Bridge mod

[`mod/llm_bridge/`](../mod/llm_bridge/) is a small HOI4 mod that gives the harness
a real bridge into a running game. It does two jobs:

- **telemetry** — the game prints a structured state line into `game.log` on a
  schedule, which the harness tails live;
- **directives** — decisions and `ai_strategy` blocks that let the agent set
  strategic intent for the native AI (see [hybrid-control.md](hybrid-control.md)).

## Why this beats the alternatives

| Path | Exact | Live | Cost per read | Works on ironman |
|---|---|---|---|---|
| Save file | yes | no (autosave cadence) | parse a large file | no |
| Screen + vision | approximate | yes | a vision call | yes |
| **Mod telemetry** | **yes** | **yes** | **a file read** | no (checksum) |

Tailing a log the game already writes is the cheapest exact reading available.
The mod is the only reason the harness can afford to look at the world often.

## Install

Copy `llm_bridge/` and `llm_bridge.mod` into
`Documents/Paradox Interactive/Hearts of Iron IV/mod/`, enable **LLM Bridge** in
the launcher, then:

```bash
export HOI4_LOG_PATH="$HOME/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
hoi4-harness doctor --adapter logtail
hoi4-harness play --adapter logtail+input --live
```

## The wire format

One pipe-delimited line per tick, prefixed so it can be found in a log full of
engine chatter:

```
LLMB|v3|state|date= 12:00, 1 January, 1936|tag=SWE|pp=1.933|stab=83.248|ws=0|civ=9|mil=3|doc=3|owned=15|mp=31153|techs=12|built_civ=0|built_mil=0|built_dock=0|built_infra=2
LLMB|v3|focus|tag=SWE|id=SWE_rearm
LLMB|v3|research|tag=SWE|id=gwtank
LLMB|v3|evt|tag=SWE|kind=13|detail=POL
```

Rules the parser holds to, all of them tested in
[`tests/test_logtail.py`](../tests/test_logtail.py):

- **The date comes from the engine, not the mod.** Every log line is stamped
  `[17:34:20][1944.05.16.01][file.cpp:123]` by the game itself, and the parser
  reads that in preference to any `date=` field. One less script token that has
  to be right.
- **Version match or refuse.** `LLMB_SCHEMA_VERSION` in the mod and
  `SCHEMA_VERSION` in `adapters/logtail.py` are compared on every line. A
  mismatch raises rather than guessing.
- **A field that did not expand is missing, not zero.** If the game does not
  recognise a token it prints it literally; the parser catches that and marks the
  field unknown. This is the single most important rule in the file — a
  fabricated `0` for war support is worse than a gap, because the agent will
  reason confidently from it.
- **Fields the mod does not emit yet are declared unknown up front** (research,
  production, construction, fronts, wars, focus).
- **Only new bytes are read**, and a truncated or rotated log resets the offset.
  A last line the game has not finished writing is left for the next read: read
  early, `mp=214000` is `mp=21` and `kind=12` is `kind=1`, a war.
- **Lines are kept to one country only when you name it** (`--country`,
  `HOI4_COUNTRY`); otherwise the reader follows whichever human country the game
  reports. An event matches on `tag` or `other`: a war is logged in the
  declarer's scope with the target as `other`, so a war declared on you counts.
- **Events stay queued until the loop has answered them.** No read drains the
  queue, the clock wait's polls included, so a war declaration that cuts the
  wait short is still there when the next turn's wake rule looks, and it is in
  that turn's brief. Before the clock moves on, the loop acknowledges the events
  its wake rule judged and its brief showed, and only those: one war
  declaration wakes the planner once, and anything that landed later in the
  turn waits for the next one. The exception is a wake whose model call fails
  before the model has replied. Then nothing is acknowledged: the war stays
  pending, the clock wait ends at once, and the next turn wakes for it again.
  Those retries count towards `max_consecutive_llm_errors` like any other
  failed call, so a provider that stays down stops the run at the war rather
  than playing on past it. The loop-level checks are in
  [`tests/test_logtail_wake.py`](../tests/test_logtail_wake.py).
- **What is already in the log when the harness attaches is history, not
  news.** The reader starts at the top of `game.log`, so its first read takes in
  every event the mod has logged this game session. `reset()` acknowledges the
  events that read returned, so a fresh start, `play --resume` and attaching to
  a game a person has been playing do not wake turn 1 for old wars, including
  ones a previous run already answered. Only that read's events: anything
  logged after it is news.

## What needs verifying before you trust a run

The mod is a skeleton, and the risky part is not the structure — it is the script
tokens. Variable names, scope functions, `on_action` names and `ai_strategy`
types all move between HOI4 versions, and Paradox script fails *silently*: an
unrecognised token prints literally, an unknown `ai_strategy` type is ignored.

So before a run that matters, check against `common/` in your own install:

- the resource variables in `llm_bridge_telemetry.txt`;
- the `on_action` names in `llm_bridge_on_actions.txt`;
- the `ai_strategy` types in `llm_bridge_directives.txt`.

CI runs static checks on the mod — balanced braces, descriptor keys,
localisation for every decision, no decision calling an undefined effect, schema
versions in sync — but no CI can tell you whether a token is real. Only the game
knows that.

`hoi4-harness verify-bridge` does most of that checking for you, from the
install's files and one log:

```bash
hoi4-harness verify-bridge --game-dir "C:/.../Hearts of Iron IV" --log ".../logs/game.log"
```

- **Against the install**: every `on_action` the mod hooks must be defined in
  the game's `common/on_actions` (a missing one never fires); every
  `ai_strategy` type the mod raises is compared with the types the game's own
  `common/ai_strategy` files use (one the game never uses is flagged suspect —
  possibly valid, but the first place to look); every trigger the telemetry
  reads is looked up in the install's `documentation/`. It also lists the
  install's on_actions whose names look like focus, research or construction
  completion — the candidates for the missing completion events (#2).
- **Against a log**: which telemetry fields expanded to real values and which
  printed a literal token, whether the engine's date prefix is present, and
  which events arrived. A field that expanded on any line counts as real.

It exits non-zero when a hooked on_action is missing or a field never expanded,
so it can gate a run. Pasting its output into #1 is the acceptance evidence.

## Boundaries

`log` is a stock script effect and `game.log` is a file the game already writes.
Nothing here hooks the engine, reads process memory, or patches the executable —
and it should stay that way; that boundary is the difference between an
automated player and a cheat tool. A checksum-breaking mod is single-player only
regardless, which suits a project that has no business in multiplayer.


## Verified on 1.19.5.0

The `focus` and `research` lines above are the probe's (see below). A live session on Sweden, 1936, printed these (engine prefix `[hh:mm:ss][Y.M.D.H]: `):

```
    LLMB|v1|boot|tag=SWE|start= 12:00, 1 January, 1936
    LLMB|v1|state|date= 12:00, 1 January, 1936|tag=SWE|pp=1.933|stab=83.248|ws=0|civ=9|mil=3|doc=3|mp=31153
    LLMB|v1|army|tag=SWE|divisions=12|delegated=0
    [11:04:29][1936.01.02.21]: LLMB|v1|state|date= 21:00, 2 January, 1936|tag=SWE|...
    LLMB|v1|evt|tag=SWE|kind=13|detail=0              <- "Target: POL" (before the fix; now detail=POL)
    LLMB|v1|evt|tag=SWE|kind=14|detail=protect POL    <- "Directive: protect"
```

What that established:

- **The date text** is `" 12:00, 1 January, 1936"`: a leading space, an hour prefix
  and commas. `parse_date` reads it; the engine's own `[Y.M.D.H]` prefix is still
  preferred. After the first day the state line arrives at 21:00, once a day.
- **`on_daily` is per country, and `ROOT` is the country that is ticking.** The
  first version logged from `ROOT`-less effects inside `on_daily`, so every country
  printed the player's numbers under its own tag (LIT, BRA, POL, with `mp=0`), once
  per country per day. The mod now emits only from the player's own tick.
  `verify-bridge` flags a blank tag, or a tag that varies across state lines
  ("tag varies across state lines: LIT, BRA, POL -- on_daily scope bug?"); with
  `--country` it also flags any tag that is not the player's.
- **`front_unit_request` takes a literal country tag or none**, not a variable, so
  the per-target `ai_strategy` blocks stay generated rather than variable-driven.
- **Completion events have no on_action.** The only completion hooks in 1.19.5 are
  `on_mio_design_team_assigned_to_tech`, `on_mio_tech_research_cancelled` and
  `on_mio_tech_research_completed`, all military-industrial-organisation ones. So
  schema v2 derives them from the daily tick: `techs`
  (`num_researched_technologies`) and cumulative `total_constructed_*` counters
  rise on completion (`research_done`, `construction_complete`), and a generated
  probe (`common/scripted_effects/llm_bridge_probe.txt`, `hoi4-harness mod-probe`)
  logs a `focus` line for the focus in progress (`focus_progress`, gated on
  `has_focus_tree`) and a `research` line for each technology being researched
  (`is_researching_technology`). A focus or technology that stops appearing is
  reported as complete when the next day's state line arrives, so a completion
  is reported one day late.
