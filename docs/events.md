# Events: reading the window and choosing an option

An event pops up for the player and the option chosen changes politics, focus and wars. In an
independent campaign (`--control llm`, the LLM holds the keyboard and mouse) nothing answered
events before this, except a blind click on "option 1" (`adapters/event_windows.py`). This page is
the reading half and the choosing half, what is verified offline and what only the live game can
settle, and the steps for the live session.

**Status: NOT live-verified.** Everything below is built and tested against a fake screen and a
fake OCR (`tests/events_helpers.py`), and the OCR wrapper is run once, in the test suite, on an image
the test draws itself. No test captures the real screen or clicks the real mouse.

## What the game exposes (checked against the 1.20 install)

| Question | Answer |
|---|---|
| Does any on_action fire when an event reaches the player? | No. `common/on_actions/_documentation.md` (updated 2024-11) and `00_on_actions.txt` list hooks for wars, capitulation, elections, factions, leaders, nukes, `on_startup`, daily/weekly/monthly pulses. There is none for an event firing, and a mod cannot add to a vanilla event's `immediate` block. So the mod cannot log the id. |
| Does the save list pending events? | No: `adapters/savegame.py` reads none, and the text save has no open-window list. |
| Does the log print events? | No. The `evt` lines of the bridge carry only war and capitulation facts. |
| Can the console or an effect choose an option of an open window? | No. `event <id> <TAG>` and `country_event = { id = ... }` only FIRE an event. |
| Can the AI-steering layer touch events? | No. `common/ai_strategy/_documentation.md` has no event token. An option's `ai_chance` lives in the event file itself. |

So the window is the only witness, and a click is the only way to answer it: the way a person does.

## The approach, and why

**Chosen: read the window from the screen with Windows OCR, match the text to the playset's events.**
Evaluated against the alternatives:

* *Mod-side logging (b).* Not possible: see the table. Stated plainly so nobody searches again.
* *A vision model on a screenshot.* Works, but costs money per look, adds a second model to a
  deterministic path, and still has to be matched to an id. OCR plus the files is free, local and
  repeatable, and the files are the ground truth for what each option does.
* *Windows OCR (WinRT `Windows.Media.Ocr`) through PowerShell.* No new Python dependency, ships with
  Windows 10/11, returns text with bounding boxes, which is what lets the harness click where an
  option's text IS rather than where a model of the layout says it should be. A look costs about
  1.3 s (a PowerShell start); measured here on a synthetic image. Degrades cleanly: no OCR language,
  not Windows, no PowerShell are all reported in words and the old blind answer remains.

The pieces:

| Piece | File | Job |
|---|---|---|
| Events of a playset | `modcontext/events.py` | Parses `events/*.txt` (layered, mods and `replace_path` apply) and the English localisation: id, kind, title keys, description variants with their conditions, options with `name`, `trigger`, effects, `ai_chance`, events fired. Matches window text to an event. |
| OCR | `adapters/ocr.py` | `WindowsOcr`: capture a screen rectangle and read it (PowerShell script passed `-EncodedCommand`, inputs through environment variables), parse the JSON into lines with boxes in screen pixels. `available()` says why not. |
| Reader and actor | `adapters/event_choice.py` | `EventReader.look()` (capture, OCR, match, locate options, time the window), the brief lines, `read_event`; `EventExecutor` (`choose_event_option`). |
| Wiring | `adapters/headless.py` `_wire_event_reader`, `env.py`, `adapters/hands.py` | Offers the action only when OCR and the playset are there; says why not otherwise in the start log. |

## How the match works

The localisation fixes the words; the game fills in the rest (`[FROM.GetNameDef] demands Danzig`).
Each title, option name and description is kept as **fragments**, the literal runs between
placeholders, with colour codes and icons dropped. Window text is OCR'd with typos, so a fragment is scored
by how well it fits approximately anywhere in the text (best window of about its length, difflib
ratio), not by equality.

1. Candidates: an inverted index of title and option words picks the 40 most likely events (IDF
   weighted); title words absent from the vocabulary are corrected to their nearest vocabulary words,
   so a typo does not lose the candidate. Hidden events (`hidden = yes`) have no window and are never
   candidates.
2. Score: title fit (0.5) against the title band of the window (the top 121 GUI units), the mean of
   the options that were found (0.3), the best description variant (0.2); missing parts are
   re-weighted away. A title that does not fit (below 0.55) cuts the score, so options reading "OK"
   cannot rescue it.
3. Verdict: **identified** at confidence 0.92 or more, or 0.80 or more with a margin of 0.05 over the
   runner-up. Otherwise the window is *unidentified*: `read_event` shows the text seen and the best
   guesses with confidence, and `choose_event_option` refuses. A title that two events share
   (`generic.1` and `generic.2` in the fixture) is told apart by description and options; if they
   cannot be, the margin is too thin and nothing is clicked.

Offline results (`tests/test_events_catalog.py`): six fixture events including a shared title and a
placeholder title, each recognised with 0, 1 in 12, 1 in 9 and 1 in 7 letters damaged, confidence
1.00 down to about 0.88; map text ("Berlin", "Prussia 12th Army") scores under 0.6 and is not an event.
Not tested offline, and the main unknown: how well the game's typewriter fonts read.

## What the model gets

When an event is open, **every brief** carries it (the OCR runs once per brief, about 1.3 s):

```
EVENT OPEN: germany.75 "The Danzig Question" (matched 0.97, open 41 s, first seen 1936-03-07)
  The Polish government has refused to discuss ... how to proceed.
  Option 1: Press for the return of Danzig -> add_political_power 50, add_stability 0.05, country_event{id germany.76, days 3}
  Option 2: Offer a guarantee of the Polish border -> add_war_support -0.1, set_country_flag refused_the_pact
  Option 3 [never shown to this country]: Wait for Germany to blink -> add_stability -0.05
  Choose with choose_event_option (read_event has the full text). If you send another mouse tool first, option 1 is taken blind.
```

`read_event` returns the same as JSON with more: every description variant and the condition that
picks it, each option's key, effects (`modcontext/script.readable` of the option block minus `name`,
`trigger`, `ai_chance`), the readable `shown_if` trigger, the events it fires, the AI's `ai_chance`, whether its
text is `on_screen`, the match (`confidence`, `margin`, `runner_up`, `candidates`), how long it has
been open (seconds, and the game date it was first seen). `read_event id=<id>` reads an event from the
files with no window needed.

**Option triggers.** An option with a `trigger` is hidden when it fails. What can be decided from the
files alone is decided: a tag test (`tag = GER`) against the country, so `shown: no` options are
labelled and refused. Anything that needs the game's state (`has_war`, flags) is `shown: unknown`, the
readable trigger is given, and the screen says: an option whose text was read is on the screen.

## Choosing

`choose_event_option(option)`: `option` is the number shown (1, 2, ...), the option's key, or its text.

1. Reads the window again (fresh), so the choice is made against what is open now, not what the
   brief said earlier. No window, an unavailable OCR, the game out of front, an unidentified
   window, an unknown option or one not shown to the country: **refused, nothing clicked**.
2. Finds where to click. Preferred: **where the option's text was read** on the screen (the centre of
   its OCR line, or the union of two lines when the name wraps). Fallback, when the text was not read
   but the slot above it is known: computed from `interface/eventwindow.gui` (below). The result reports
   how it was found, and, when both are known, how far the layout model was from the text (`layout_note`):
   that number is what the live session measures.
3. Clicks through the driver's `click_at` family: focus guard (the game must be in front), mouse
   enabled, dry run only logs, the point must be inside the screen. The open-window gate that refuses
   other mouse tools is deliberately passed, since answering the window is the point.
4. Verifies: waits `settle` (0.8 s), reads again. The window must be gone. Same event still open ->
   `rejected: the window is still open` (one click, never a retry storm). Text gone but the probe or
   other text still sees a window -> `unverified`. A chained event that opens at once is named in the result.
5. Records `events.jsonl` in the run dir (`tool`, `event`, `answer`, `how`, `closed`).

`choose_event_option` does not count against the per-turn action cap.

### The window layout (`EventLayout`)

From `interface/eventwindow.gui`, `EventWindow` (read from the playset's layered file, so a mod's
version is followed; built-in numbers are the 1.20 file's when it is absent), in GUI units, scaled
by `Geometry` (height / 864 x UI scale): the window is `Orientation = CENTER`, at `position =
{ x=-282 y=-310 }`, size 581 x 427; the top section 121 high holds the title; the bottom section
starts 221 down; `options_grid` is one column of 300 x 47 slots starting at (215, -1) in it. At 1920x1080,
UI scale 1 the window's left edge is x = 608 and its top y = 152, and the first option's slot starts
about y = 427.

What the file does NOT give, and is therefore an **assumption** the live session must check:

* the midsection grows with the description text (nominal height 100), pushing the options down:
  `event_screen.grow` (GUI units) models it, 0 by default; the OCR placement does not need it;
* beyond four options the window is taller; whether it is then re-centred (so the top moves up by
  half the growth) or keeps its top: `event_screen.centre_growth`, false by default;
* the window kinds `EventWindow_Operative` and `EventWindow_leader` have other layouts; the OCR placement
  does not care, the computed fallback is for the standard window only.

## Setup

Automatic in an independent campaign with the mouse (`campaign_input = "mouse"`), when the playset's
files are indexed and Windows OCR works. Profile keys (all optional), `event_screen`:

```json
"event_screen": {"enabled": true, "upscale": 2.0, "settle": 0.8, "grow": 0.0, "centre_growth": false}
```

The start log (`campaign.log`) says `event reading: ON (UNVERIFIED ...)` or `event reading: OFF: <why>`
(no OCR language: add the English (US) language pack with its Optical character recognition feature;
not Windows; no playset index; dry run; disabled). With OFF, `choose_event_option` and `read_event`
are not offered and the blind fallback below applies.

Privacy and the desktop: the harness captures only the event window's rectangle, only when the
game window is in front (otherwise the brief says it is not read), and keeps no image: the capture
is made inside the PowerShell process, scaled, read, and the temp file deleted.

## The blind fallback (kept, and the model is told)

`adapters/event_windows.py` is unchanged in behaviour: with `event_windows` probes in the profile,
**any other mouse tool** first answers an open country event with its first option (and closes news),
because a click under the window would go to the event. The brief says so ("If you send another
mouse tool first, option 1 is taken blind"), as does the tool description, and the guard's record
(`events.jsonl`) now names the event when the reader identified it. A window the reader could not
identify is still answered blind, and the brief says that too. Without probes nothing knows a window is open
and the mouse tools click under it (the start log says `event windows: NOT guarded`).

### Policy: who answers a country event (#125)

`event_windows.policy` in the profile picks what the guard does with an open COUNTRY event (the
player's own); a news window is always closed.

* `default` (as before): the recorded first option, blind.
* `model`: the mouse tool is refused with the reason ("call read_event, then choose_event_option,
  then repeat the tool"), nothing is clicked, and the refusal is a line in `events.jsonl`. Once the
  model has chosen, the window is gone and the next mouse tool runs. Where the event reader is off
  (no OCR, no playset), the model cannot answer, so this falls back to `default`.
* `refuse`: always refused; a person answers.

## Rival mode

In rival mode the LLM does not hold the keyboard and mouse: the person plays another country, and the
rival is run by the game's AI. **The game's AI answers a rival country's events**, each by its
options' `ai_chance`. There is no input for a country the person is not playing, so nothing here
reads or chooses for a rival, and `read_event` / `choose_event_option` are not offered there. Looked
for a faithful way to influence it and found none that exists in the game's own scripting:

* `common/ai_strategy` has no event token (`_documentation.md`), so the steering layer cannot touch it;
* `ai_chance` is part of each event file, and the only mod-side lever is shipping a replacement events
  file with edited `ai_chance` blocks (for example a modifier on a country flag the inbox sets).
  That means copying vanilla event files wholesale and keeping them in step with every patch;
  it is not built, and no syntax is invented for it here. If it is ever worth it, the inbox already
  sets country flags and an `ai_chance = { base = N modifier = { factor = F has_country_flag = X } }`
  block is the documented shape of that lever.

What a rival's AI chose is still visible after the fact: the effects show up in the save and the
brief.

## What is verified offline

* `tests/test_events_catalog.py`: parsing, localisation placeholders and colour codes, option effects
  and fired events, tag triggers, description variants, hidden events, and the matcher against typos,
  a shared title, a placeholder title, map text and empty text.
* `tests/test_ocr.py`: the parser (scale, origin, blank and malformed rows, errors), every degrade
  (not Windows, no PowerShell, timeout, no language pack) with a fake runner; and **the real Windows
  OCR on an image the test draws** (skipped where Windows OCR or PIL is missing): it reads the text and
  the boxes land within a few pixels of where it was drawn.
* `tests/test_event_choice.py`: the layout read from the gui file, the window and option boxes for 1 to
  6 options at 1920x1080 and 2560x1440 and with a UI scale, growth and re-centring; locating
  wrapped option names; the reader (timing, probe, unavailable, not in front); the brief; choosing
  against a fake window (the click lands in the slot of the text, with typos, the layout fallback, the
  recorded `event.option1` anchor, chained events); all refusals; dry run; verification outcomes; the env
  query and brief.
* `tests/test_event_wiring.py`: what is offered and refused at the start, and the log lines.

## What needs the live game

1. **Does the OCR read the game's text?** The typewriter fonts (`hoi4_typewriter22/16`) on the paper
   textures. The match tolerates typos, but a title read as garbage cannot be matched. Unknown.
2. **Where the options really are**, against `EventLayout`. Measured by `layout_note`.
3. **That the click closes the window**, per window kind (country, news, operative).
4. **How the game reacts to a click while a window fades in** (the event window animates in).
5. **That `settle` 0.8 s is enough** for the window to disappear.

## Live calibration steps (tonight)

Needs: the game on 1.20 at the resolution the run uses, windowed or borderless, the profile as
for an independent campaign, a Windows OCR English language pack. Keep the PC otherwise idle:
these steps click the real game.

1. **OCR works.** `python -c "from hoi4_harness.adapters.ocr import WindowsOcr; print(WindowsOcr().available())"`
   prints `None`. Anything else is the sentence to act on (language pack).
2. **A start line.** Start the campaign dry first; then live. `campaign.log` must say `event reading: ON`.
3. **Make an event.** Fastest: with the console, fire one that has 2 or 3 options for your country:
   `event <id>` (any `country_event` of the player's country, e.g. one of the country's early events;
   `read_event id=<id>` lists what it should show). Pause the game with the window open.
4. **Read it.** Ask the model `read_event`, or run
   `PYTHONPATH=src python -c` against the same reader. Check: `status: open`, the right `id`,
   `confidence` above 0.9, `on_screen: true` for each shown option. If `status` is `unidentified`,
   copy `text_seen` (what OCR made of the window) into the issue: that is the data to tune
   `upscale` (try 1.5 and 3.0) or the title band.
5. **Dry choice.** `--dry-run` style (or `driver.dry_run`): `choose_event_option option=1`; the
   result names the point it would click and `layout_note`. Aim by eye: is the point on option 1?
   Record how far `layout_note` says the layout model is off, at this resolution and UI scale:
   that is the correction for `grow` (vertical) and the layout.
6. **Live choice.** Take a harmless option; the result should say `the window closed`, and the next
   `events.jsonl` line `closed: true`. Check in the game that the option's effect happened (the
   politics screen, the flag, the next event).
7. **Refusals hold.** With no window open: `choose_event_option` is refused with nothing clicked.
   With the game alt-tabbed away: refused (not in front). With a window whose text OCR cannot read:
   refused and the brief says it will be answered blind.
8. **A longer description and more options** (4, 5, 6): pick an event with many options; compare
   `layout_note` per option count. If the numbers jump past the fourth option, the window is
   re-centred: set `"centre_growth": true`. If every option is off by the same amount that grows with
   the description, set `"grow"` accordingly.
9. **News and other windows.** Let a news event open (`event` a `news_event`): `read_event` should
   identify it; clicking its option closes it. A leader or operative event shows a different window:
   record whether OCR placement still works (it should; the layout fallback will not).
10. **Report** per step: what the result said, the `layout_note` values, and the OCR text of any window
    that was not identified. Without those the numbers cannot be tuned.

## What this does not do

* It does not read events for a rival (no input exists), nor popups that are not events (peace
  offers, faction invitations, access requests: `docs/interaction.md` row 77).
* It does not evaluate option triggers that need game state; it says so and relies on the screen.
* It does not make a choice for the model. Which option is right is the model's call, from the effects
  shown; the harness only makes the information available and the click faithful.
