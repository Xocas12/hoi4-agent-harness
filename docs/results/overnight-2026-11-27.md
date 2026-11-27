# Overnight 2026-11-27: verification, the memory leak, and a Germany campaign

The second unattended night. The plan was to verify the features built since 11-22 against the
real game, look into the memory leak (#157), add a real ML library for #85, then play Germany
from 1936 to 1948 again.

## The memory leak (#157): the OpenGL renderer

`hoi4-harness memory-probe` on the same 1941 save, with no harness running:

| variant | GB/hour |
|---|---|
| OpenGL, mod on, `-debug_mode`, speed 5 | +20.1 |
| OpenGL, mod off, `-debug_mode` | +29.1 |
| OpenGL, mod off, no debug | +32.1 |
| OpenGL, paused | +31 |
| OpenGL, paused, window minimised | +25.7 |
| **DirectX 11, paused** | **-0.07** |
| **DirectX 11, running** (88 game days in 5 min) | **+3** (the game's own growth) |

The leak is the OpenGL renderer. It runs on a wall-clock timer, so it continued while paused and
while minimised, and it does not depend on the mod, debug mode or the harness. This PC's
`pdx_settings.txt` had `renderer = opengl`. The same night's crash (01:48) was an access violation
in the NVIDIA OpenGL driver, reached through the Steam overlay (`gameoverlayrenderer64.dll`).

On DirectX 11 the campaign held 7.5-7.7 GB for hours, and the game also runs faster. The preflight
now warns on OpenGL. The renderer was switched to `dx11`; the original file is kept as
`pdx_settings.txt.before-dx11-test-20261127`.

## Bugs found live and fixed (PR #181, merged)

1. **The automatic restart never armed.** It looked for `adapter.driver`, but a console-only
   campaign keeps its driver on the router, so the run logged "no live game to restart".
2. **The campaign could not take the focus** for 19 minutes. Its long-lived process is refused the
   foreground; it now calls the fresh-process focus.
3. **The briefing read another game's save, twice.** A 1936 start was briefed from 1941 saves: first
   through a date-free fallback, then because the game date was unknown at the start. It is now
   bounded by the game date, or by the campaign start.
4. **Path choice always fell back to the default.**
   - 600 tokens cut Haiku off before its JSON; the limit is now 2000.
   - The reply is now logged.
   - A prose reply naming one option is accepted.
   - Later the reply came back empty. Still open.
5. **A console left open swallowed the pause key**, and the campaign crashed (`ClockUnobservable`).
   The clock now closes the console and retries.

## What could not be verified without a person

- **Sovereign mode and the army clicks (#175, #179).**
  - A camera fit was recorded from four city markers (Stockholm, Oslo, Göteborg, Helsinki): flat
    model, residuals 0.2-3.0 px, not validated, since four points leave none to hold out. It is in
    `runs/camera.json`.
  - The army cards cannot be calibrated blind. The first card in the bar is the army group, not an
    army. Names appear only in the panel or tooltip, so `army.<name>` needs a hover per card.
- **Path choice lobby (#160).** Custom Game Rules is a long, collapsible, scrolling list. The
  per-country AI rows have no fixed position, so the fixed-point design needs to change: collapse
  the sections, then find the rule by scrolling.
- **The save list scrolls by mouse wheel:** it did not move. A save was brought to the top by
  copying it under a new name.

## Machine notes

- **First launch after installing the mod:** the menu took more than 3 minutes to appear. The
  restart's menu wait has to allow for that.
- **A Windows Firewall prompt** for a `node.exe` in another Claude Code session's scratch folder
  appeared over the game twice and held the focus.
  - It refused simulated drags.
  - It was answered "Cancelar", which keeps Node.js blocked as it already was. Reversible in the
    firewall settings.
- **`live_setup.py prepare`** once refused because listing the processes timed out, which left the
  mod off for one launch.

## #85: a real ML library (PR #180, merged)

scikit-learn gradient boosting, as the optional `ml` extra. On the toy simulator it beats the
baseline but not the ridge bandit: the toy's rewards are close to linear. A trained model saves as
JSON and predicts without the extra.

## The campaign (Germany 1936-1948, game AI, Haiku, DirectX 11)

Run dir `runs/overnight4-ger-1936`. Features on:
- the Sonnet briefing at the start and yearly;
- path choice (fell back to DEFAULT);
- the front report;
- contested production (flag on);
- the memory guard with restart.

The progress notes and the result are added below as the run goes.

### How the war went

| date | owned | controlled | divisions | capitulated | France surrender |
|---|---|---|---|---|---|
| 1938-07 | 30 | 30 | 73 | | |
| 1939-12 | war on Poland (three months after the historical date) | | | | |
| 1940-07 | 34 | 50 | 111 | POL, HOL, LUX, ETH | 7% |
| 1941-01 | 34 | 60 | 126 | + NOR, YUG | 2% |
| 1942-07 | 34 | 59 | 146 | | 0% |
| 1943-01 | 34 | 54 | 150 | | 0% |
| 1943-11-23 | **Germany capitulated** | | | | |
| 1944-01 | 27 | 27 | 4 | rump state, plays on | |

Unlike the 11-22 run, France never fell: 7% surrender at best, 0% from 1942. Belgium sat at 2% for
years. There was never a war with the USSR. The USA, Mexico, Brazil and China joined against
Germany, and it capitulated in November 1943, earlier than the 11-22 run (August 1945).

What the new features did:
- **The front report worked.** From 1940 it flagged the Belgian front STUCK ("unchanged for 193
  days; 3 of 3 attacks under 50% of the defender; 12% armour"). The model read it: it switched the
  west to defensive "while medium-tank production matures", then turned to Yugoslavia, which was
  nearer surrender and fell in early 1941. It did not get the game's AI to break France. The tools
  the model has (postures, directives, front orders, production steers) steer the AI; they do not
  fight the battle.
- **The briefings** were written at the start, yearly and on the capitulation. They are orientation:
  paths, decision points, what to watch.
- **Contested production** was on but never used: no `add_production_line` in the transcript. The
  model steered production instead.
- **Political power** stayed the binding constraint: `change_law closed_economy` was refused for 150
  PP with 6.7 held.
