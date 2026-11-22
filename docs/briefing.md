# The briefing agent (#165)

An optional scout that reads the world before a campaign (and, if you ask, again during it),
and hands the playing model an **orientation**: the situation, the paths that look open and
what each asks, and what to watch. It does not decide. It names no single plan, and it does
not argue with an instruction you gave the country. The playing model chooses; your objective
and the country's own instruction come first, and the briefing is read in their light. With no
instruction, the player chooses on its own from the options.

Off by default. It is one call (more with a re-briefing schedule) to a model you configure
separately, usually a stronger one than the player.

## What it reads (the dossier)

All of it without the game running. `hoi4-harness brief-world --dossier-only` prints exactly
what the model is sent.

| Part | From |
|---|---|
| Objective and instruction for the country | the config (`objective`, `instruction`), first, so the model reads everything in its light |
| Playset | the install and the mods in load order |
| The world on the save's date | every major power by industry (ruling party and its popularity, factories, divisions, faction, wars, guarantees) and the countries around the player |
| Your country | leaders, political power, stability, war support, manpower, laws, spirits, advisors, fleets and planes, equipment, stockpile, fuel, production lines, equipment shortfalls |
| Factions | members, leader, ideology |
| Balance of threats | each country read, its military factories against the player's, divisions, who is at war with the player; the guarantee web |
| Resources | oil, steel, aluminium, rubber, tungsten, chromium: what the player holds and who holds the most (state history files, owners from the save) |
| Paths | the country's AI behaviour rule options and focus tree branches (the `paths` command, #160) |
| Mods | one short brief per mod, the Steam Workshop page first, then the mod's readme, then its descriptor |
| Skill packs and the strategy guide | `guidance/strategy.md` and any skill packs the run uses |
| Your last briefing | on a re-briefing, so the new one can say what changed |

The save is the newest text save of the country (`briefing_save` or `--save` to pick one). A
save is not needed: without one the dossier has the playset's files only and says so in its
gaps list. Binary saves are not readable (`save_as_binary=no`). Without the map files neighbours
are not known.

## What it asks the model for

Markdown with these headings: Strengths, Weaknesses, Opportunities, Threats, Viable paths (each
with what it asks, what it gains, what it risks and its timing window), Decision points and
what to watch, What would change the picture. A reply missing a heading is kept and the log
names the missing ones.

## Turning it on

```
hoi4-harness brief-world --country GER --game-dir <install> [--save PATH] [--out DIR]
                         [--provider anthropic --model <model>] [--web] [--dossier-only]
```

prints the briefing, saves `briefing.md` and `briefings/<date>-<TAG>.md` (with the dossier
beside it) in `--out` (default the run dir).

In a campaign:

```
hoi4-harness play --campaign ... --briefing [--rebrief yearly,war] [--briefing-web]
```

or in a profile:

```json
{
  "campaign_briefing": true,
  "campaign_rebrief": ["yearly", "1939-09-01", "war", "capitulation", "faction"],
  "briefing_web_search": false,
  "briefing": {"provider": "anthropic", "model": "<a stronger model>", "max_tokens": 6000,
               "extra": {"usd_per_m_input": 15, "usd_per_m_output": 75}}
}
```

An empty `briefing.provider` (the default) means the player's own model briefs. The briefing
is written before the first turn, next to the path choice (#163): with `campaign_choose_path`
on, the briefing is in front of the model when it chooses its path, which it still does itself.

Where it goes:

- `<run dir>/briefing.md` (the current one), `briefings/` (every one, with its dossier), the
  history in `campaign_state.json` (`briefing`);
- the console, in full; `campaign.log` keeps where it is saved, the model and the tokens;
- the player's context: pinned in its memory block, under the standing objective, framed as
  "orientation, not orders... the choice is yours". A resumed run keeps the briefing it wrote
  and does not ask again.

A briefing that fails (model error, no playset) is logged as a warning and the campaign goes
on without it.

## Re-briefing

`campaign_rebrief` is a list (or a comma string) of: `yearly`, `halfyearly` (months since the
last briefing), a date `YYYY-MM-DD` (once, on the first turn on or after it), and the events
`war` (a new enemy), `capitulation` (the player's), `faction` (joined, left or changed). At
least 30 days pass between briefings. The new dossier reads the newest save of the country
and the previous briefing. A bad entry fails at the start of the run.

## What it costs

One call per briefing. The dossier is about 20,000 tokens, most of it the strategy guide
(65 KB), plus a few thousand for the world; the answer is 1,000 to 2,500 tokens. On a
frontier model that is cents to a dollar a briefing. The briefing is also in every later turn
of the player's context (a few thousand tokens a wake), which is the larger cost over a long
campaign. Briefing calls are **not** counted in `budget.max_usd`: the log line says the tokens
and, with prices in `briefing.extra` (or the CLI's own cost), the dollars.

## Web search (opt-in)

`briefing_web_search` / `--web` / `--briefing-web`. Off by default, and off whatever the flag
says for providers without a search tool (scripted, mailbox, and the OpenAI-compatible and
Gemini clients today); the log says so.

1. The Workshop brief is read first. With web off the dossier reads the Workshop cache only;
   with web on it may fetch a missing page.
2. A mod whose brief did not come from its Workshop page (readme or descriptor only) is
   listed under "Mods to look up", and the model may search for what those mods change.
3. Anthropic API: the server-side `web_search_20250305` tool (max 5 uses a call). `claude-cli`:
   `claude -p` is started with `--tools WebSearch,WebFetch`, and only for the briefing call.
   The player's own calls on the same client are unchanged.

Search results are not checked by the harness; the model is asked to say which claims came
from a search.

## What it does not do

- It does not read the live game: the picture is the save's, as old as the newest save.
- It does not read other countries' hidden state beyond what the save holds (it is the save,
  so it knows more than the player's intelligence would; the player is told it is a scout's
  summary and to trust the situation reports over it).
- It does not choose, click or change anything.
