# Mod context: what a playset lets you do, and what it is about

`docs/playsets.md` covers the ids a playset defines (focuses, techs, states,
tags). This adds the rest of what a player can *do* in the files, a short
world brief per mod, and a path to take decisions headless. It reads the final
merged files -- the install, then each mod in load order, `replace_path` applied
-- and never runs the game.

```bash
hoi4-harness mod-actions --game-dir "$HOI4_GAME_DIR" \
    --mod ".../mod/ugc_2438003901.mod" --country GER          # a report
hoi4-harness mod-actions ... --country GER --json --all       # everything, as JSON
hoi4-harness mod-actions ... --country GER --offline          # never touch the network
hoi4-harness play --headless --game-dir ... --mod ... --mod-context
```

The library entry point is `IdentifierIndex.mod_context()` (a `ModContext`):
`decisions()`, `decision_for(tag)`, `focus_details(tag)`, `available_focuses(tag,
completed)`, `new_technologies()`, `ideas()`, `advisors(tag)`, `mechanics()`,
`event_chains(tag)`, and `surface(tag)` for all of it at once.

## What is read

| Part | From | Notes |
|---|---|---|
| Decisions and categories | `common/decisions/**`, `categories/` | id, category, English name and description, `visible`/`available`/`allowed` kept as script and as a readable line, PP cost, `days_remove`, `days_re_enable`, `fire_only_once`, targets and `target_trigger` |
| Focus tree | `common/national_focus` | prerequisites (AND of OR-groups), `mutually_exclusive`, `available`, `bypass`, cost; `available_focuses` applies prerequisites and exclusions, and shows `available` rather than judging it |
| New technologies | `common/technologies` | the playset's technologies minus the base game's, with folder, start year and English name |
| Spirits, laws, advisors | `common/ideas`, `common/characters`, `history/countries` | per country; laws are idea groups with `law = yes`; starting ideas stop at the default bookmark's date |
| What is different | `ideologies`, `resources`, `buildings`, `on_actions` | ids the playset adds, and how many base ones it drops; plus the folders a mod `replace_path`s |
| Event chains | `events/` | namespaces and event counts, as context: events are not actions |

A decision is attached to a country by the tag tests in its `allowed` and
`visible` blocks and its category's (a block that names another country rules it
out) and, failing that, by an id or category prefixed with another country's tag.
That is a superset of what is on screen at any moment: conditions only the game can
evaluate (`available`, scripted triggers) are shown, not decided. Decisions drawn
only in debug mode (`is_debug = yes`) are left out.

**Custom GUIs (#136).** `common/scripted_guis` is read too: every window's buttons,
their `_click_enabled` and `_visible` triggers, the window's own `visible`, and the
`ai_weights` the game's AI presses them by. `list_gui_buttons` shows them (labels come
from the `interface` files and localisation) and `click_gui_button(gui, button)` presses
one, as `if = { limit = { <window visible> <button visible> <click enabled> } <click> }`
in the country's scope, with a marker logged inside the guard as the proof. Refused,
with the reason: windows scoped to a selected state or country or a map icon, buttons
in list rows (they read the row's `v`/`i`), FROM/PREV, and anything that changes more
than the country (global flags, every/random country or state, another country's scope,
also inside the scripted effects a click calls) unless `gui_allow_global` is set. A
decision category that names a `scripted_gui` is still flagged `custom GUI`; its
decisions are ordinary decisions.

**Balance of power (#135).** `common/bop` is read for each bar's sides, ranges,
modifiers and decision category; the probe logs the active bar's value, and the brief
shows where it stands, the band it is in and what moves it.

## Taking a decision headless

`take_decision(decision_id, target?)` is a console action. The console effect
`activate_decision` is documented only as "Activates specified decision for scope
country": it says nothing about cost, `available` or cooldown, so none of those can
be assumed. A faithful take does what the decisions screen does, in script, in one
`if`:

```
eval_effect SWE = { if = { limit = {
    <allowed> <visible> <available> <custom_cost_trigger>   # the decision's own blocks, verbatim
    has_political_power > 24.99
    NOT = { has_decision = swe_cheap } }
  activate_decision = swe_cheap add_political_power = -25 } }
```

The game evaluates the conditions, not the harness; the harness checks the same
political-power balance first (and refuses without a reader that sees it). A
targeted decision uses `activate_targeted_decision` (which ignores conditions and
cost, hence the explicit guards) and tests its `target_trigger` in the target's
scope. The result lists what no trigger can test (`unchecked`): the cost
*modifiers* the screen applies (the base cost is charged), a cooldown, and
`fire_only_once` -- the harness refuses a second take of one within a run.

Verification is honest: decisions are not in the telemetry, so a take is "done"
only when political power fell by at least half the cost; otherwise the result is
`unverified`, never `ok`.

| Kind | Headless | Why |
|---|---|---|
| Ordinary decision with a plain PP cost (or none) | `take_decision` | guarded as above |
| Targeted decision, targets listed, conditions free of `FROM`/`PREV` | `take_decision` with `target` | the target must be one of its `targets` |
| Mission (`days_mission_timeout`) | observe-only | the game activates it; a player cannot take it |
| Cost is a variable / script value | observe-only | cannot be evaluated, so cannot be charged |
| `available = { always = no }`, or no effect block | observe-only | a timer, spacer or information line |
| Conditions read `FROM`/`PREV`; dynamic targets (`target_array`) | observe-only | no value in a console scope; targets cannot be listed |
| Quoted text with a space (`has_dlc = "Man the Guns"`), or a very long line | observe-only | the console splits quoted text on spaces and `=` |
| National focus | `set_national_focus` (keyboard script, as before) | no faithful effect exists |
| Technology | observe-only here | `start_research` needs the tech tree's clicks |
| Spirits, laws | observe-only | granted by focuses, events and decisions; laws change in the politics screen |
| Advisors | `hire_advisor` (as before) | |

`describe_action(id)` reports which of these a given decision is, with the reason.

Not verified against a live game, because the harness never launches one for this:
that `activate_decision` runs the decision's `complete_effect` and starts its timer,
and that `ROOT` inside the target scope is the country the `eval_effect` names. The
next live session should take one cheap decision and read the result back.

## In the prompt

`--mod-context` (or `mod_context` in a profile) adds a `## This playset` section to the
system prompt, in the cached prefix, and two read-only tools:

- `list_decisions(category?, query?, limit?)` and `describe_action(id)`, answered from
  the files; they never reach the game, never count against the per-turn action
  cap, and are not journalled.
- the world brief for each content mod, the country's focus tree and where it opens,
  its decision categories and a few decisions to start from, advisors, and what
  the mods add. The full lists stay behind the tools.

The text is deterministic (sorted, no dates, nothing that changes between turns)
and is cut to `mod_context_tokens` (default 900, counted with the harness's token
estimator) by dropping its least useful lines first. `take_decision` ids are validated
against the index like focus ids: an invented one is refused with the nearest real
ones, and one from another country's decisions is refused as such.

## The world brief and the network

For each mod with a `remote_file_id` (in its descriptor, or the numeric folder name
under `workshop/content/394360`), the brief comes from
`https://steamcommunity.com/sharedfiles/filedetails/?id=<id>`: its title, then the
description with HTML and BBCode stripped. It is **extractive**, not generated: the
page's tagline and opening, a sentence that places the setting in time, a few
feature-list lines, with links, credits and community lines dropped; under ~200 words
per mod and a cap over the playset. No model is called.

- One request per mod id, then a disk cache (`$HOI4_CACHE_DIR/workshop`, default
  `~/.cache/hoi4-harness/workshop`) with a 7-day TTL. The next run reads the cache and
  sends nothing; if a refetch fails, the old copy is used.
- The request is the public page URL and a generic User-Agent. Nothing about the
  user, machine, playset or run is sent.
- `mod-actions` fetches what is missing unless `--offline`. A run's prompt reads the
  cache only unless `--fetch-workshop` (`mod_context_fetch`) is set, so starting a game
  never waits on Steam.
- Without a page (no id, offline with nothing cached, the page gone) the brief is the
  mod's `descriptor.mod` and a readme in its folder, if any.

Tests use a trimmed recording of the page for mod 2438003901
(`tests/fixtures/workshop/`) and never touch the network (`conftest.py` makes a real
fetch fail the test).
