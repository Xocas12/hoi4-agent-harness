# Adapters

An adapter is a bridge to one running game. Each sees a different subset of the
world, so the contract makes that subset explicit rather than papering over it.

```python
class GameAdapter:
    supported_actions: frozenset[str]
    def info(self) -> AdapterInfo: ...
    def read_state(self) -> GameState: ...           # never consumes events
    def acknowledge_events(self, events) -> None: ... # the loop has answered these
    def apply(self, call: ActionCall) -> ActionResult: ...
    def pause(self) -> None: ...
    def resume(self, speed: int = 3) -> None: ...
    def advance(self, days: int) -> GameState: ...   # returns early on a critical event
```

Two invariants hold everywhere:

- **Refuse, don't fake.** An action outside `supported_actions` returns
  `error_kind="unsupported"` with the list of what *is* supported. The model can
  work with that; it cannot work with a silent no-op reported as success.
- **Unknown, not zero.** A field the adapter could not read goes in
  `unknown_fields` and renders as `unknown`. A fabricated `0` for war support is
  worse than a gap, because the agent will reason confidently from it.

And one for adapters that queue events, which today means logtail: **a read is
not a consumer.** A turn reads state several times (the clock wait polls, the
brief, verification), and only the loop knows which read its wake rule judged.
Events stay in `state.events` until the loop passes them to
`acknowledge_events()` at the end of the turn. `env.reset()` does the same for
what its first read found already queued: a backlog in the log at attach is
history, not news.

## mock — complete

Seeded, deterministic, no HOI4 install. A clock, resources that accrue, queues
that drain, scripted historical events (including a critical one on 1939-09-01).
Every test runs against it, and `hoi4-harness play` uses it by default, so the
whole harness is exercisable for free.

Not a simulator. It is shaped like the game, not accurate to it — use it to test
the loop, never to draw conclusions about strategy.

## logtail — complete, needs the mod

Tails `game.log` and rebuilds `GameState` from the telemetry the
[LLM Bridge mod](mod-bridge.md) emits. Exact numbers, live, at the cost of a file
read — the best read path in the repo, and the only one cheap enough to poll
often.

Read-only, so compose it: `--adapter logtail+input`. It refuses to guess: a
version mismatch raises, an unexpanded script token is marked unknown rather
than read as zero, and fields the mod does not emit yet are declared unknown up
front.

### Finding the game

Both the log-tail and savegame adapters locate the game's user directory through
`hoi4_harness.paths`, which searches OneDrive-redirected Documents as well as the
plain one. That is not hypothetical: on the machine this was checked against, the
only copy lived under `OneDrive\Documents` and every previous default missed it.
The native Linux build keeps the directory under `~/.local/share` (or
`$XDG_DATA_HOME`), which is searched too.

A path you supply explicitly is used or it fails — discovery never runs as a
fallback, because searching on after you named a directory would mean reading a
different campaign than the one you pointed at.

## savegame -- text saves, mapped against a 1.19.5 save

Reads the most recent `*.hoi4` autosave and maps the player's numbers onto
`GameState`. `parse_clausewitz()` handles Paradox's `key=value` / `key={...}`
text format, including repeated keys and keyless `{ ... }` blocks inside a list
(`country_leader={ { ... } { ... } }`).

**Saves are binary unless you turn that off.** The game's default is
`save_as_binary=yes` in `settings.txt`; set it to `no` and save again. A binary
save (it starts `HOI4bin`) is refused with that instruction. This is separate
from ironman, which also forbids reading.

A save is 60-100 MB, so nothing parses all of it. `read_save()` cuts out four
blocks by **brace matching** and parses each alone: the player's country inside
`countries`, `states`, `division_templates` and `equipments`. (Brace matching,
not indentation: the real save writes some nested blocks at column 0, such as
`CAN={` inside ENG's block, so "the next line at the same indent" cuts a country
short.) On the 63 MB checked save the whole read is about 4 s, nearly all of it
reading the file.

### What the 1.19.5 save contains, and where each field comes from

Checked against the live game at 23 April 1937 (Sweden, started from the 1936
bookmark; `tests/fixtures/swe_1937_04_23.hoi4` is a trimmed copy and
`tests/test_savegame_mapping.py` asserts each figure).

| GameState field | Where | Checked against |
| --- | --- | --- |
| `political_power` | country `politics.political_power` | 891.41 (top bar ~886-891) |
| `manpower` | sum of `manpower_pool.available` over states the player **owns** | 32,980 (top bar "32.98K") |
| `civilian_factories`, `military_factories`, `dockyards` | sum of `industrial_complex`, `arms_factory`, `dockyard` `level` over owned states | 9 / 3 / 3 |
| `stability`, `war_support` | country `variables.llmb_stab`, `llmb_ws` (percent, from the bridge mod), divided by 100 | 82.6% and 0% |
| `national_focus` | country `focus.current` | `SWE_defense_act` |
| `research` | country `technology.slots`: one key per busy technology, plus `empty` entries | 1 busy (`infantry_weapons2`), 2 free |
| `production` | country `production.military_lines`; equipment name from `equipments` via `equipment_variant_index` | two lines, 1 factory each |
| `divisions` | country `units.division`, grouped by `division_template_id` -> `division_templates` name | 12 |
| `ideology` | `politics.ruling_party` | `democratic` |

Things that are not what they look like:

* **`stability` and `war_support` in the country block are base values**
  (`stability=0.47` where the game showed 82.6%). Modifiers are applied at read
  time and are not stored. The adapter therefore reads the mod's computed
  variables and, **without the mod, reports both unknown** rather than the base.
  The mod writes them on its telemetry tick, so they can lag the save by up to a
  day.
* `manpower={ ratio=... }` in the country block is the recruitment ratio, not the
  pool. The pool is per state.
* `llmb_civ` (21 in the checked save) is the game's `num_of_civilian_factories`,
  which includes factories gained through trade, not the 9 owned. Factories are
  counted from the states.
* A production line's `efficiency` is the mean of the first `active_factories`
  entries of its `factory_efficiencies` (percent), and `output_per_day` is the
  line's `speed` over its unit `cost`. Both are derived, not stored under those
  names; the efficiencies (53, 50) look right but only the factory counts were
  checked against the game.
* `days_remaining` for research and focus is not stored (only points so far), so
  it stays unset.

### Still unknown (reported in `unknown_fields`, never defaulted)

`construction` (the country block holds no queue; `country_reports.construction`
is a daily count of factories on construction, not the items), `completed_focuses`
(not in the country block), `wars`, `fronts`, the hybrid-control fields
(`delegated_armies`, `ai_directives`, `posture`, `theater_postures`), and now
`fuel` and `convoys`, which the brief used to print as `0`. Division `location`
and `strength` are not read either: the save gives a province id and raw
strength figures, not the units `DivisionGroup` documents.

```bash
hoi4-harness save-inspect                   # newest save in HOI4_SAVE_DIR
hoi4-harness save-inspect path/to/save.hoi4
```

prints the top-level entries by size and the player country's keys with a sample
of each value. Its size table splits on any key at column 0, so on a real save it
lists thousands of numeric entries: those are artefacts of the heuristic, not
blocks.

Constraints: text saves only; a save is a snapshot, so pair it with a short
autosave interval; read-only, so compose it with a writer.

## screen — capture done, vision TODO

Grabs the window or a named region with `mss`. `REGIONS` names the parts of the UI
worth cropping (top bar, alert cluster, outliner) in 1920x1080 reference
coordinates.

`read_state()` needs a vision model call: send the PNG through the same
provider-neutral LLM layer with a schema matching `GameState`, and put anything
the model will not commit to in `unknown_fields`. This is the only adapter that
works on ironman and the only one that sees alerts and the map — and the most
expensive per observation, so it is for what numbers cannot express, not for
routine ticks.

## input_driver — mechanics and verification done, click paths recorded per install

The write side. Keyboard input is sent as **hardware scan codes** through
`SendInput` ([`keys.py`](../src/hoi4_harness/adapters/keys.py), Windows only,
a clear error elsewhere): HOI4 1.19 never sees the key events pyautogui posts
(`pyautogui.press('space')` with the game focused did nothing; a scan-code
keystroke toggled pause every time). Mouse clicks still go through pyautogui,
which works. Text is typed per character (shift for capitals and `_`); a
character with no scan code (`ö`) cannot be typed, and the whole string is
refused before any key is sent. Hotkeys (`HOTKEYS`) are preferred over coordinates because they
survive resolution and UI-scale changes; coordinates live in a calibration dict,
not in code — `hoi4-harness calibrate` writes it (with the screen resolution it
was captured at), and loading a calibration from another resolution is refused
rather than applied. `dry_run=True` logs intended input without sending it, which is how
the tests and the default CLI run.

Input is gated on window focus. Before anything is sent, the adapter checks that
the focused window's title contains `window_title` -- a case-insensitive
substring, because the real title carries a suffix (`Hearts of Iron IV (OpenGL)`)
and an exact match would never fire. If the game is not focused, the action comes
back as a failed `ActionResult` with the name of the window that *is* focused,
which the agent can read and act on like any other rejection.

A platform with no implemented check reports **unsupported** and refuses, rather
than passing. A guard that always returns true is worse than no guard, because it
gets trusted. `--no-window-guard` is the deliberate opt-out.

### Hotkeys (1.19.5)

`HOTKEYS` comes from `interface/topbar.gui` (`topbar_shortcuts()` reads it, for
re-deriving on another version or under a mod) and was tried in the game:

| Panel | Key | Source |
|---|---|---|
| national focus | `alt+q` | `national_focus_shortcut` |
| research | `w` | keybind `research_open` |
| construction | `t` | `construction_button` |
| production | `y` | `production_button` |
| decisions | `shift+q` | `decisionview_button` |
| political / advisors | `q` | `player_flag` |
| international market | `e` | |
| trade | `r` | `trade_button` |
| pause | `space` | `pause_button_date` |
| speed | `kp_plus` / `kp_minus` | `button_speedup` / `button_speeddown` |

There is no diplomacy panel hotkey. A script's `press` step takes either a key or
chord (`ctrl+f`) or one of these names (`national_focus`).

**Escape is never sent by a script.** With no panel open it opens the game menu,
which then swallows every following key, and most of these panels close
themselves when the action completes. A script that presses it is refused at load.

### The clock: a toggle, so look before pressing

HOI4 has one pause key, `space`, and it toggles. Pressed blind, it is right only
when the game happens to be in the state the harness assumes, and HOI4 loads
paused, so a run that started on a paused game used to run while the model
decided and stand still while the harness waited. `pause()` and `resume()` (and
`set_game_speed(0)`, which goes through `pause()`) therefore read the game first,
through the reader the composite attached:

- A date that moves proves the game is running. A date that stands still for
  `clock_probe_seconds` (default 10, `HOI4_CLOCK_PROBE_SECONDS`) is taken as
  paused. The readers see one date per in-game day, so this has to be longer than
  a day takes at the speed you run; at speed 5 a couple of seconds is plenty.
- The key is pressed only when the game is in the other state, and the date is
  watched again after every press. If the press did not take (someone else
  pressed it, say), it is pressed once more.
- If the date still does not do what it should, the clock cannot be seen from
  here, and the driver raises `ClockUnobservable` instead of toggling blind. A
  reader whose date only moves when the game autosaves ends up here.

The cost is the stop check: every pause watches a still date for
`clock_probe_seconds`, so each turn takes that much longer.
`speed` in `resume(speed)` is still not applied; the game runs at whatever speed
it was left at. For the same reason `set_game_speed` with a speed above 0 is
refused rather than pressing `+` and reporting a speed nobody checked: set the
speed in the game before the run.

### UI scripts: act, then prove it

A blind click is unverifiable, and an unverified action reported as success is
the worst failure mode in this whole design. So a scripted action has two halves,
kept apart on purpose ([`ui_scripts.py`](../src/hoi4_harness/adapters/ui_scripts.py)):

- **The click path** is data. It depends on the game version, the UI scale and
  your layout, so — like coordinates — it lives in a file you record against
  your own game, `<run-dir>/ui_scripts.json` (or `HOI4_UI_SCRIPTS`). Steps are
  `press`, `click` (a calibrated target), `type`, `wait` and `repeat` (the step
  before it, N times in all), and may carry `{placeholders}` filled from the
  action's arguments:

  ```json
  {"schema": 1, "scripts": {"set_national_focus": [
    {"press": "national_focus"}, {"press": "ctrl+f"}, {"type": "{focus_name}"},
    {"press": "shift+enter"}]}}
  ```

  Some placeholders are derived from the arguments before any input is sent
  ([`ui_resolve.py`](../src/hoi4_harness/adapters/ui_resolve.py)), and the action
  is refused, with nothing sent, when one cannot be:

  | Placeholder | From | Refused when |
  |---|---|---|
  | `{focus_name}` | the focus id's English name (`localisation/english`), as its longest ASCII-only run of words | no name, or nothing typable (`[...]` scripted loc, `ö`) |
  | `{state_name}` | the same, for the state | the same |
  | `{tech_folder}`, click target `research.tech_point` | the tech's sub-tree and `position` (see start_research below) | the point is off the screen or on the tab strip (the tree is not scrolled), the sub-tree has no box in the interface file, its box is one the rule refuses (a `UP` box with no `size`, or a percent wide at an absolute `x`), or two roots draw it in different places |

  These need the game's files: pass `--game-dir`.

- **The verification** is code, because it does not depend on the UI: a started
  focus is running, a researched technology is in a slot, a queued building
  lengthens the queue by `count`, a production line has the factories asked for,
  and a hired advisor costs political power (weak, since no adapter reports
  advisors, and said so). The hybrid actions verify the same way: an army is (or
  is no longer) under AI control, the posture — global or on a theater — is the
  one asked for, a directive stands for exactly the country named, and
  standing down leaves no directive and no posture.

After the steps, the driver re-reads the game through the reader it is composed
with and polls until the verification passes or a timeout expires. The result is
one of:

| Outcome | When |
|---|---|
| ok, "done and verified" | the consequence showed up |
| `rejected`, "input sent but the game did not change: …" | it did not — the UI was not where the script expected |
| `unverified` | the reader still cannot see the field that would prove it when the timeout expires (the log-tail bridge does not emit the focus yet; posture and directives turn known when their event line lands, which polling waits for); treat it as not done |
| `not_executed` | dry run: the path is logged, nothing is sent |

A script is refused before any input is sent when a click target has no
calibrated coordinate, and a scripts file naming an action with no verification
(anything outside the five peacetime actions and the four hybrid ones) is
refused at load time.

Through the log-tail reader, posture is observable — the mod emits an event when
it changes, and the reader tracks it from the first one. So are standing
directives: each generated per-target branch logs its own literal line
(`kind=14|detail=protect POL`, no variable to expand), and the reader rebuilds
the list from those and resets it on stand-down. Directives raised before the
harness started reading are missing until the next stand-down; a raise it saw
never is. Delegated armies are not reported by the mod, so delegation comes
back `unverified` there — the honest answer until the mod emits it. An action
with no recorded script is refused, as before.

To set one up on 1.19.5: copy [`profiles/ui_scripts.1.19.json`](../profiles/ui_scripts.1.19.json)
to `<run-dir>/ui_scripts.json`, then run `hoi4-harness calibrate`, which walks
exactly the targets it clicks (`calibration.DEFAULT_TARGETS`, kept in step by a
test). [`ui_scripts.example.json`](../profiles/ui_scripts.example.json) stays as a
template whose paths are guesses (for the hybrid actions), to be checked step by
step. `calibration.json`
in the run directory (or `HOI4_CALIBRATION`) is loaded automatically.

`hoi4-harness check-scripts` is the pre-flight: the scripts parse, every
`{placeholder}` is one of the action's real arguments (a typo is refused when the
file loads, not on the first live attempt), and every concrete click target has
a calibrated coordinate. Templated targets are listed with the values calibrated
so far.

### The five peacetime click paths (1.19.5)

What was run in the live game is marked; the click targets are recorded per
install.

- **set_national_focus** (keys verified end to end): `alt+q` opens the focus
  tree, `ctrl+f` selects its search bar, the focus's *localised name* is typed
  (the search matches names and descriptions; "Defense Act" showed "1/3 Found"
  and scrolled the tree to it), and `shift+enter` starts the current found focus
  and closes the window by itself. Also in the game's help: `ctrl+tab` /
  `ctrl+shift+tab` cycle the found focuses, `alt+enter` starts and keeps the window
  open. No click targets.
- **queue_construction**: `t`, click the building's icon
  (`construction.building.<building>`), then the **map search** (`ctrl+f` when the
  focus tree is not open; also the magnifier at the bottom right) takes the state
  name; clicking its first row (`map.search.first_result`) centres and zooms the
  camera on that state, so a click on the map centre (`map.centre`) lands in it,
  repeated `count` times. State names are `STATE_<n>` localisation.
- **start_research**: `w` opens Research with three slots; clicking a free one
  (`research.free_slot`) opens the tech tree, folder tabs across the top
  (`research.tab.<folder>`) and technologies on a grid with year columns. There is
  no search box, so the point is computed. A tech's `position = { x y }` in
  `common/technologies` is *not* a cell of the tab: it is relative to the tech's
  sub-tree (`y` the year column, `x` the row). A sub-tree is the chain hanging off
  one root technology (nothing in the folder leads to it) through
  `path = { leads_to_tech }`, and `interface/countrytechtreeview.gui` places each
  sub-tree as a `gridboxtype` named `<root>_tree` in the folder's container (so
  `support_weapons2` and `infantry_weapons2`, both `x = 0 y = @1938`, sit in
  different rows). In GUI units the tech's slot centre is
  `box.position + slotsize * (cell + 1/2)` with `cell = (y, x)`, and the screen is
  that times `height / 864` (1.25 at 1080p) from a content origin of (9, 103) GUI
  units, i.e. (11, 129) px. Wide equipment boxes and small icons on one cell share
  a centre. That reproduces 18 centres measured in the live infantry tab to within
  4 px (`tests/test_tech_tree_layout.py`). Nothing needs calibrating; optional
  overrides are `research.tree.origin` (the screen point of the content's GUI
  (0, 0), if a UI scale or layout moves it) and an exact `research.tech.<id>`.
  The other box formats are the same rule turned, read off the labels the interface
  file itself draws (subtitles and year numbers): `UPPER_LEFT` swaps the axes
  (`x = box.x + w*(row + 1/2)`, `y = box.y + h*(column + 1/2)`), and `UP` runs the
  years down with the box centred on its `size` width, so row 0 is the middle lane
  and negative rows go left (`x = box.x + width/2 + w*row`). A percent `x` or `size`
  is a share of the screen width less the folder's margins. A folder with a different
  left margin (the air tabs have 0) moves its content by the difference.
  **UNVERIFIED: the rotated tabs** (`armour_folder`, `nsb_armour_folder`,
  `air_techs_folder`, `bba_air_techs_folder`, `industry_folder`, `electronics_folder`).
  Without the game, they are checked only against themselves
  (`tests/test_tech_tree_layout.py`): no two techs of a folder share a point, chains
  only run down, and everything is inside the folder's content box. The points to
  measure are in [live-session.md](live-session.md) step 6b.
  Refused rather than guessed: techs with no `<root>_tree` box (mods, and the
  `mtgnaval*` folders), a `UP` box with no `size`, a percent-wide `UP` box at an
  absolute `x` (all of artillery), techs two of whose roots draw them in different
  places, and points off the screen (this driver does not scroll the tree; at
  1920x1080 that is most of every tab's lower years). Only the infantry tab has been
  measured live.
  picked with `enter` on its detail window (the Research button's shortcut).
- **hire_advisor**: `q` opens Political; advisor slots and candidates are icons
  (`politics.slot.<slot>`, `politics.candidate.<advisor_id>`), calibrated per name.
- **set_production**: a draft; `y` and a guess at the click path. Not walked in
  the game.

What proves each one: the running focus and the research slots when the reader
reports them (the mod's `LLMB|v2|focus` and `research` probe lines), else
**unverified**; a queued building only when it completes or when the reader shows
the queue (**unverified** through the log reader); an advisor by the political
power it costs.

## console channel -- effects through `run` instead of the UI (#83)

The input driver acts as a player does and pays for it in calibration. For six
actions the game has an effect that does the same thing, and the console can run
it with the same few keys every time, whatever panel is open and wherever the
camera is. `--adapter logtail+input` routes each action to a **channel**, `ui`
or `console`, and every result records which one it used in `changed.channel`.

| Action | Default | What is written |
| --- | --- | --- |
| `queue_construction` | console | `add_building_construction`, one level per block, in the state |
| `set_production` | console | `add_equipment_production` with `requested_factories` |
| `hire_advisor` | console | `activate_advisor` and `add_political_power = -cost` |
| `set_ai_directive`, `set_ai_posture` (incl. theater), `clear_ai_directives` | console | the mod's own scripted effects (below) |
| `set_national_focus`, `start_research` | ui | none: their only effects (`complete_national_focus`, `set_technology`) skip the time and cost, which is a different game |
| `delegate_army_to_ai` | ui | the mod has no delegation effect |

Change a channel with `action_channels` in a profile (`{"queue_construction":
"ui"}`); putting an action on the console that has no faithful effect is an
error, not a silent fallback. A console action whose preconditions fail is
refused, and does not fall back to the UI.

### What gets sent

`run` reads a file of *console commands* (`console_commands_documentation.md`:
"Runs the specified file with list of commands"), one per line, so the file is
not a bare effects block. Two commands carry the work:

* `eval_effect <effect>`, "Runs the inlined effect on a selected scope", for
  construction, production and advisors. Each line names its own scope
  (`eval_effect SWE = { ... }`) rather than relying on the selected one.
* `effect <tag> <scripted_effect_name>`, "Runs a scripted effect on selected
  scope", for the hybrid actions: `llm_bridge_select_<TAG>` then
  `llm_bridge_raise_<directive>` (or `llm_bridge_raise_theater_<posture>`),
  `llm_bridge_set_posture_<defensive|offensive>`, `llm_bridge_clear_directives`.
  These are what the mod's decisions call, so the mod logs its usual telemetry
  events and verification is the one the UI path uses. `console_hybrid_via =
  "decision"` writes `activate_decision = <the decision>` instead.

Example file for a construction, a production line and an advisor:

```
eval_effect SWE = { 1 = { if = { limit = { is_owned_by = SWE free_building_slots = { building = arms_factory size > 0 } } add_building_construction = { type = arms_factory level = 1 instant_build = no } } } }
eval_effect SWE = { add_equipment_production = { equipment = { type = infantry_equipment_1 } requested_factories = 3 } }
eval_effect SWE = { if = { limit = { has_political_power > 24.99 } activate_advisor = SWE_cheap_one add_political_power = -25 } }
```

The keys, always: `grave` (scan code 0x29, the key left of `1`, the console on
every layout), type `run <filename>`, `enter`, `grave`. They go through the same
scan-code layer, focus guard and dry-run switch as every other input. A dry run
writes the file under `<run_dir>/console/` and logs the keys without sending;
a live run writes the file where `run` reads it and keeps a copy there too.
Several actions in one `apply_batch` share one file and one `run`.

Settings (profile key / environment): `console_dir` / `HOI4_CONSOLE_DIR` (where
`run` reads; default the game's user directory), `console_filename` /
`HOI4_CONSOLE_FILE` (default `llmb_turn.txt`), `console_key` / `HOI4_CONSOLE_KEY`
(a key name or a raw scan code such as `0x29`), `console_effect_command` /
`HOI4_CONSOLE_EFFECT_COMMAND` (default `eval_effect`), `console_hybrid_via`,
`console_guards`.

### Preconditions: checked before anything is written

Effects skip the game's own checks, so the channel makes them first. A refused
action writes no file and sends no input; the result says which check failed.

| Check | Source | If it cannot be answered |
| --- | --- | --- |
| the player's country is known and exists | telemetry / `country` | refuse |
| state exists (a name, key or id; `capital` is `capital_scope`) | identifier index | refuse |
| equipment exists and is buildable (archetypes are not) | `common/units/equipment` | refuse |
| no line for that equipment yet; `factories >= 1`; factories <= free military factories | reader (`production`, `military_factories`) | refuse |
| advisor exists, one unambiguous base cost (pass `slot` if it has several) | `common/characters` | refuse |
| political power >= that cost | reader (`political_power`) | refuse |
| directive/theater target is a real tag the mod has plumbing for | index, `modgen.DEFAULT_TAGS` | refuse |
| a province building (`fortification`), or a global `balanced` posture | no effect exists | refuse |

Not observable by any reader today, so **unchecked** (listed in
`changed.unchecked`, never guessed): state ownership, a free building slot, an
advisor's `allowed`/`available` conditions, and cost modifiers (the base cost is
charged). Where script can test it, the effect carries its own `limit`
(`is_owned_by`, `free_building_slots`, `has_political_power`), so the game itself
declines; verification then reports the change did not happen.
`console_guards = false` drops those limits.

A directive's `weight` is not carried: the mod's blocks have fixed values, as on
the UI path. `set_production` with 0 factories (remove a line) and changing an
existing line are refused: `add_equipment_production` only creates one.

### `add_ai_strategy`: evaluated, not used

The effects documentation says only "Adds strategy entry to country AI". Vanilla
use gives the shape (`add_ai_strategy = { type = protect id = "POL" value = 150 }`),
and it takes any tag, which the per-tag generated blocks do not. It is not used
because it is additive and not removable by a flag (the only undo is a negative
value), it writes no telemetry line (verification would need a hand-written
`log`), and it would split directive state between two mechanisms that
`clear_ai_directives` has to undo. A tag outside the generated list is refused
with the `mod-directives --tags` command that adds it.

### The tilted camera: a homography (measured, 4 points)

Zoomed in, the flat model is wrong. Measured live (1.19.5, 1920x1080, after a map
search for Sodermanland, zoomed in) the markers of four victory-point cities sat at

| city (province) | map pixel (`unitstacks.txt` type 38) | screen |
|---|---|---|
| Stockholm (6050) | (3085.48, 324.16) | (1488, 579) |
| Uppsala (9149) | (3085.52, 316.00) | (1441.5, 418.5) |
| Gavle (11091) | (3069.84, 300.00) | (1047, 171) |
| Falun (156) | (3041.84, 296.00) | (483, 108) |

The y scale held at 15.5-17 screen px per map px but the x scale grew from about 20
at the top of the screen to about 28 lower down: the camera is tilted. A flat
`Camera` fitted to these four misses by 85, 49, 64 and 68 px. The map is a plane,
so the right model is a 3x3 homography (8 degrees of freedom):

```
sx = (h1 x + h2 y + h3) / (h7 x + h8 y + 1)        sy = (h4 x + h5 y + h6) / (h7 x + h8 y + 1)
```

`fit_homography(pairs, viewport)` (in `mapcamera.py`) solves it by least squares
over >= 4 `(map pixel, screen point)` pairs, normalising both coordinate sets
first (centroid to origin, mean radius sqrt 2) so the 8x8 normal equations are
well conditioned. It refuses collinear or repeated points, fewer than four pairs,
a fit with a point behind the camera, and a worst residual over `tolerance`
(default 8 px). `PerspectiveCamera` has `to_screen`, `to_map` (the inverse) and
works with `screen_point`, which refuses off-screen targets as before.
`fit_camera(pairs, viewport)` chooses per fit: a flat camera that explains three
or more pairs within 4 px wins (fewer parameters extrapolate better, and it is
right when the camera is not tilted); otherwise the homography. The result says
which (`fit.model`), the residuals, and the flat model's residuals.

**What the four live points prove: nothing yet.** Four pairs fix a homography
exactly, so the residual is zero by construction. Fitted:

```
 6.60813      -3.12444     -19019.1
 0.181391      3.41019      -1526.09
 8.34897e-05  -0.00313882       1          conditioning 1.67 (normalised), residuals 0 px
```

Only the flat model's failure is evidence (it misses by 49-85 px). That a
homography describes the camera is untested until a held-out city lands where it
is predicted (below, and `docs/live-session.md` step 11). The markers' visual
anchor may sit a few px off the stack point, and the screen readings are +-3 px;
perturbing the four readings by that much moves a prediction by about 100 px for
a city near the cluster and by thousands for one far away, because four
neighbouring points extrapolate badly. Treat a miss of tens of px for a nearby
city as the expected noise, not as a refutation.

The map-side anchor is `map/unitstacks.txt` (`MapLayer.anchor(province)`, type 38,
the victory-point marker), which is better than a centroid for a city. Rows are
`province;type;x;height;z;rotation;scale`; image y = 2048 - z. Type 38 is the VP
marker anchor (every province has one); 0 is the centre unit-stack slot, 1-8 and
11-18 further slots, 9/10 the flank slots, 21-37 copies of those. Apart from 38
these readings come from the file's shape, not from the game; see `STACK_TYPES`.

Predicted screen positions at that same camera (`scripts/predict_markers.py`;
the viewport is 1920x1080 minus 40 px top and 120 px bottom). The window shows
map x about 3016-3119, y about 286-339, so almost nothing else is on screen:

| city (province) | map pixel | predicted screen | on screen? |
|---|---|---|---|
| Mariehamn (9228) | (3118.4, 307.0) | (2118, 291) | no, right of the screen |
| Sundsvall (130) | (3077.12, 245.0) | (1126, -271) | no, above |
| Ostersund (11148) | (3048.84, 215.0) | (787, -414) | no, above |
| Karlstad (11114) | (3009.84, 321.0) | (-544, 470) | no, left |
| Linkoping (9393) | (3048.64, 352.0) | (180, 1519) | no, below |
| Norrkoping (11380) | (3064.52, 351.0) | (876, 1471) | no, below |
| Goteborg (383) | (2993.84, 367.0) | (-3899, 2740) | no, far left and below |

Vasteras and Orebro have no `VICTORY_POINTS_<id>` entry in 1.19.5 localisation, so
they have no VP marker to predict. Everything above is outside the view, so it
cannot be checked from this one screenshot. Pan or zoom out one step, take the
points from the new view, and use the procedure below, which holds cities out.

### UNVERIFIED: settle these in a live game

Nothing below has been run against the real game; each is a configuration value,
not a code change. `docs/live-session.md` step 7 has the exact checks.

1. **Which folder `run` reads.** Default: the game's user directory (the folder
   with `logs/` and `mod/`). Alternatives: the install folder, a `script/` folder.
   Set `console_dir` to what the check shows.
2. **That `eval_effect` takes its effect on the same line**, and that a scope
   name inside it (`SWE = { ... }`, `1 = { ... }`) is accepted. The docs give no
   argument syntax. If it does not, `effect <tag> <name>` is documented but takes
   a scripted-effect name, not inline script: only the hybrid actions would work,
   and the others would need scripted effects added to the mod.
3. **`-debug`.** Whether `run` and the console need the game started with `-debug`.
4. **Ironman.** The console is expected to be off in ironman, and its use may mark
   the save. Play non-ironman.
5. **Whether `add_building_construction` goes through the construction queue**
   (uses civilian factories, respects slots) or adds levels directly, and whether
   `level = 1` blocks queue one item each, as the verification counts.
6. **Whether `add_equipment_production` without `amount`** makes an unlimited
   line like the UI's.
7. **Whether an open console is closed by `grave`**, and whether the console
   keeps text between uses (a stale line would prefix the `run`).
8. **The rotated tech tabs' click points** (armour, air, industry, electronics),
   which are computed from the interface file but not measured:
   `docs/live-session.md` step 6b.

## map layer -- province coordinates for unit orders (#85)

Unit orders are clicks on the map, so the harness needs to know where a state or
province is. `hoi4_harness/mapdata.py` reads it from the game's own files and
`hoi4_harness/mapcamera.py` turns a map pixel into a screen point.

```
hoi4-harness map --game-dir PATH --state 141
hoi4-harness map --game-dir PATH --province 6050
```

prints the pixel count, bounding box, centroid, a **click point** (a pixel
inside the target) and, for a province, its terrain and neighbours (`~` marks a
strait with no shared border).

### What it reads

- `map/provinces.bmp`, 24-bit uncompressed, one flat colour per province. A small
  pure-Python reader handles bottom-up and top-down files and the 4-byte row
  padding. Coordinates are image coordinates: **y grows downward from the top of
  the picture**, the same as the screen. The BMP's storage order never leaks out.
- `map/definition.csv`: colour to province id, land/sea/lake, terrain, coastal.
- `map/adjacencies.csv`: straits that share no border. Rows with `-1` and the
  end marker are skipped. Shared pixel edges give the land and coastal borders
  (a corner touch does not count).
- `history/states`: each state's provinces and owner.

The playset is followed the way the identifier index follows it: a mod's file at
the same path wins, `replace_path` drops the folder, and `map/default.map` can
rename the three map files.

The scan is one pass and takes about 6 s for vanilla (5632x2048, 13 414
provinces). Its result is cached as JSON (about 1.5 MB, loads in well under a
second) in the user cache folder (`%LOCALAPPDATA%\hoi4-harness\map`, or
`$XDG_CACHE_HOME`; `HOI4_MAP_CACHE` or `--cache-dir` overrides, `--no-cache`
skips it). The key is the three files' names, sizes and mtimes plus the playset
name, so editing a map or adding a map mod rebuilds it.

### The click point

The centroid of a crescent, an archipelago or a state wrapped around a bay can
fall outside it, and a click there selects the sea. So the click point is the
pixel farthest from anything outside the target (a chamfer distance transform
over the target's bounding box), ties broken toward the centroid. It is checked
to map back to the state in the tests, for Stockholm's state and every Swedish
state. It is computed on demand, not cached.

### Map pixel to screen: a model, not a measurement

```
screen = origin + scale * (map pixel - anchor)        x and y both grow right/down
```

`Camera.from_references` fits `scale` and the offset from two measured points,
and refuses a pair that disagrees with the model (an axis flipped, or x and y
scales more than 3% apart). `Camera.centred_on` encodes "the map search centres
the camera on the target". `screen_point(layer, "state", id, camera)` refuses,
with `OffScreen`, a target outside the viewport or within 4 px of its edge
rather than clamping, because a clamped click lands on a UI panel or the wrong
province. `Viewport` takes the margins for the top bar and the bottom panel.

### UNVERIFIED: settle these in a live game

None of this was run against the game. What the model assumes, and could be
wrong about:

1. **The map is flat.** It is not, zoomed in (measured, above): HOI4 tilts the
   camera. The flat `Camera.from_references` fit refuses that case instead of
   averaging over it; use `fit_camera`, which falls back to the homography.
2. **One scale per zoom step**, and the zoom steps are discrete. Measure each
   step you intend to use; do not interpolate.
3. **The map wraps horizontally** in the game. The layer does not: a target near
   x=0 or x=5631 has its click point on one side only. Pan before clicking there.
4. **The viewport margins** (what the top bar, the bottom panel and any open
   window cover) are not known. They default to zero.
5. **That the map search centres the camera** on the state, and at what scale.

The check, at 1920x1080 and one zoom level, in a started game, with the harness
not sending input (the operator moves the mouse):

1. `hoi4-harness map --state N` for two states far apart (several hundred map
   pixels in both x and y) that are both visible, e.g. Stockholm's state 141 and
   a state about 500 px west and 300 px south of it.
2. Hover the mouse over each province in turn and read the province id from
   its tooltip (or the map search), then `map --province P` for its click
   point. Record (click point, mouse position) for both, with the mouse on the
   province's visible middle. The click point is inside the province, so the
   pair is known to be on it even if the centroid is not.
3. `Camera.from_references(pair1, pair2, Viewport(1920, 1080, ...))`. If it
   raises, that is finding 1 or an axis mix-up; if it fits, hover a third
   province and compare `camera.to_screen` to where it is. More than a few
   pixels off means the model needs a perspective term.
4. Open the map search, pick a state, and see whether the click point is at the
   viewport centre (item 5). If so `Camera.centred_on` needs only the scale.

## composite

Pairs a reader with a writer: `--adapter logtail+input`, `savegame+input` or
`screen+input`.

`advance()` blocks on the **in-game** date rather than returning after a single
read, and returns early on a critical event. Without that, a real bridge takes a
turn every few milliseconds and burns a budget in seconds. A wall-clock deadline
is the backstop, so a wedged game ends the turn instead of hanging the run, and
the game is left paused on every exit path including the timeout and an exception
from the reader.

Who owns the clock is a setting. With `clock_owner="harness"` (the default) the
composite unpauses, waits, and pauses again, counting the days from the date
before the unpause, since the input driver lets the game run while it watches
the date start to move. With `clock_owner="player"` it never
sends a clock instruction at all — it only watches the date move — and
`set_game_speed` is removed from the action list so the model cannot take the
clock either. That is the mode to use when a person is playing the same
campaign.

## headless -- the log in, the console and keyboard out

`--adapter headless` (or `--headless`) is a composite of `logtail` and a
keyboard-only input driver behind the channel router: no mouse, no screenshots,
no calibration. `adapters/headless.py` builds it. Two actions that exist only as
clicks (`start_research`, `delegate_army_to_ai`) are removed from the tool list.
The clock is the console's `gamespeed` and the space key, each proved by the date
(`HeadlessClock`), and each console run ends by asking the mod for fresh
telemetry so an action can be verified while the game is paused. What was
checked for a no-mouse path, the fake game that runs the whole stack with no
HOI4, and the settings are in [headless.md](headless.md).
