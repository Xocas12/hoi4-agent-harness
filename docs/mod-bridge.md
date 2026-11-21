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
LLMB|v5|state|date= 12:00, 1 January, 1936|tag=SWE|pp=1.933|stab=83.248|ws=0|civ=9|mil=3|doc=3|owned=15|mp=31153|techs=12|built_civ=0|built_mil=0|built_dock=0|built_infra=2
LLMB|v5|focus|tag=SWE|id=SWE_rearm
LLMB|v5|research|tag=SWE|id=gwtank
LLMB|v5|evt|tag=SWE|kind=13|detail=POL
LLMB|v5|war|tag=SWE|enemy=GER|divs=84|border=1|surr=3
LLMB|v5|div|tag=SWE|state=64|ctl=GER|own=3|own_weak=1|foe=5|foe_weak=0
LLMB|v5|divend|tag=SWE|n=7
```

The `div` and `divend` lines are the front picture (divisions per state, at war only; one
closing `divend` per tick whatever happens). They come from their own effect, called with the
state line on every tick (see "Telemetry cadence" below), and the reader keeps a picture only when
`n` of them arrived: see
[unit-orders.md](unit-orders.md).

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

## Telemetry cadence: how often the mod prints (#130)

The state line (with its `army`, `war`, `focus` and `research` lines) and the front picture
(`div` / `divend`) are one **tick**. By default the mod prints one tick per in-game day, from
`on_daily`; the `camp` line stays weekly and the `evt` lines happen when they happen. The cadence
is configurable both ways with one number, the hours between two ticks:

| `telemetry_hours` | what the mod does | ticks per game day |
|---|---|---|
| unset (default) | nothing is sent; the mod prints once a day, or whatever the loaded save was set to | 1 |
| 1, 2, 3, 4, 6, 8, 12 | a hidden event reschedules itself every H hours | 24, 12, 8, 6, 4, 3, 2 |
| 24 | once a day, set explicitly (also undoes an earlier setting) | 1 |
| 48, 72, 96, 120, 144, 168, 240, 336, 720 | every N days (2, 3, 4, 5, 6, 7, 10, 14, 30), counted in the daily tick | 1/N |

Any other value is refused when the run starts, with this list. Set it with the config key
`telemetry_hours` (a profile) or `--telemetry-hours H` on `play` (so on `--campaign` and
`--rival` runs too). It needs a headless run: the harness sends
`effect <TAG> llm_bridge_telemetry_every_<H>h` through the console at the start (campaign: after the
mark and the base-AI step; rival: in the same run file as the mark), says what it set in its log
(`campaign.log` for a campaign or a rival), and the mod answers with an `evt` line of kind 20
(`telemetry cadence: every H in-game hours` in the brief's events), so `game.log` shows it took. The
same line can be typed in the game's console by hand. `campaign-check` lists the cadence a profile
will set.

**How the sub-daily chain works.** HOI4 has no hourly `on_action`, but `country_event = { id = ..
hours = N }` fires an event N game hours later. The documentation shows `hours` only as a literal
and no vanilla file passes a variable, so `modgen.py` writes one hidden event per supported value
(`events/llm_bridge_cadence_events.txt`, ids `llmb_tick.N`) and the effects in
`common/scripted_effects/llm_bridge_cadence.txt`. The player's country keeps `llmb_tick_hours` (the
cadence) and, per value N, `llmb_tick_inflight_N` (events of that chain waiting to fire):

- setting a cadence schedules the first event only while none of that value is waiting, so the same
  setting twice, or A then B then A before A's event fires, leaves exactly one chain;
- an event that fires prints a tick and schedules the next only while `llmb_tick_hours` still equals
  its N, so changing the cadence ends the old chain at its next firing, nothing to cancel;
- the variables and the scheduled event are in the save, so a save and load continues the chain;
- each firing sets `llmb_tick_alive` and the daily tick clears it: a day without a firing (a lost
  event) restarts the chain within one to two days. A live chain fires at least twice a day, so it is
  never restarted by mistake;
- cost per firing: two variable checks, plus the tick when the country is the player's.

`tests/test_130_telemetry_cadence.py` runs the generated script in a small interpreter through these
cases (same value twice, A-B-A, a change to daily and to every N days, a lost event).

**What it costs.** A tick is the whole state line plus its probe (one trigger per focus and
technology the install defines, run for the player's country) and the front picture, so a tick costs
what the daily tick costs today and the cost is linear in ticks per day:

- *game.log*: roughly 0.5 to 1 KB per tick in peace (a state line, an army line, the probe lines of
  what is running, a `divend`), several KB at war (a `div` line per front state, a `war` line per
  enemy). At 1 hour that is 24 times a day: order of 15 to 25 MB per game year in peace and well
  beyond that in a big war. A multi-year campaign at 1 hour should expect hundreds of MB. These are
  estimates from the line formats, not measurements: the live step below records the real ones. 6
  hours is 4 times today's volume.
- *game speed*: the extra work is the tick's script, the same as one daily tick, 24 times a day at 1
  hour. How much that slows a game day at speed 5 depends on the playset (the probe is the long
  part in a big mod). Not measured; the live step records days per second with and without.
- *the reader*: every state line closes the previous tick, so a war, a focus or a research that ends
  is still reported once, now up to 24 times a day. `state.date` stays the in-game day and
  `state.hour` is the in-game hour of the line it came from (the engine's `[Y.M.D.H]` prefix).

Slower is free: every N days prints 1/N of today's volume, but a focus or research that starts and
finishes between two ticks is never seen, and a war the length of the gap goes unreported by its
`war` lines (the `evt` lines for a declaration still arrive).

The line format is unchanged, so the schema stays v5; the only addition is the `evt` kind 20.

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
- **Schema v5 (campaign mode): who is "the player".** Every on_action used to limit on
  `is_ai = no`. The console's `human_ai` hands the player's country to the game's AI, so
  every one of those limits went false and the log fell silent. Every limit now asks the
  scripted trigger `llm_bridge_is_harness` (`is_ai = no` OR the country flag `llmb_harness`),
  and the harness sets the flag with `effect <TAG> llm_bridge_mark_harness` before it toggles
  `human_ai` ([campaign.md](campaign.md)). The state line also carries `ai=` (the read-back
  of `is_ai`), and a weekly `camp` line (`on_weekly`) carries the conquest numbers.
- **Weekly `observe` lines** (`llm_bridge_observe.txt`, `llm_bridge_emit_observe`, also from
  `on_weekly`): `obsbegin`, `res`, `tension`, `equip`, `garrison`, `peace`, `fac`, `vol` and
  `collab` carry the numbers the text save lacks (resource income, equipment against need, world
  tension, garrison, the peace conference, faction influence, volunteers, collaboration). They are
  additive: an older harness skips the kinds it does not know. Every token is quoted from the
  installed `documentation/` and **unverified in a live game**; one the game does not expand prints
  `[?llmb_x]` and the reader drops that field (never 0). The coverage table in
  [observation.md](observation.md) says which row each line serves.
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
