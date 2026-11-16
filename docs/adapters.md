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

A save is 60-100 MB, so `read_save()` builds a structural root index and
parses only the player's country, `states`, `division_templates` and
`equipments`. Strings (including escaped quotes) and comments are skipped
while counting braces. Country selection uses direct children rather than
indentation, including compact saves. Missing keys never trigger a whole-save
parse, and unfinished or ambiguous root blocks are withheld.

### What the 1.19.5 save contains, and where each field comes from

Checked against the live game at 23 April 1937 (Sweden, started from the 1936
bookmark; `tests/fixtures/swe_1937_04_23.hoi4` is a trimmed copy and
`tests/test_savegame_mapping.py` asserts each figure).
An additional initial SWE snapshot (1936-01-01) is reduced to 7,787 bytes in
`tests/fixtures/savegame/`; `tests/test_savegame_followup.py` checks the extra
naval lines, typed references, and unknown-data behavior against it.

| GameState field | Where | Checked against |
| --- | --- | --- |
| `political_power` | country `politics.political_power` | 891.41 (top bar ~886-891) |
| `manpower` | sum of `manpower_pool.available` over states the player **owns** | 32,980 (top bar "32.98K") |
| `civilian_factories`, `military_factories`, `dockyards` | sum of `industrial_complex`, `arms_factory`, `dockyard` `level` over owned states | 9 / 3 / 3 |
| `stability`, `war_support` | country `variables.llmb_stab`, `llmb_ws` (percent, from the bridge mod), divided by 100 | 82.6% and 0% |
| `national_focus` | country `focus.current` | `SWE_defense_act` |
| `research` | country `technology.slots`: one key per busy technology, plus `empty` entries | 1 busy (`infantry_weapons2`), 2 free |
| `production` | `production.military_lines` and `naval_lines`; typed equipment reference resolved through `equipments` | two military lines in the 1937 fixture; two military and four naval lines in the 1936 fixture |
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
  checked against the game. Missing inputs leave these metrics `None`, and
  naval efficiency/output remain unknown. Active assignments missing from a
  queued naval line remain `None`; requested counts are not substituted.
* Invalid or missing manpower pools/building levels keep the affected total
  unknown. Non-finite and fractional counts are rejected instead of becoming
  zero or being rounded down.
* Equipment and template references retain both `(id, type)`. Unresolved or
  duplicate references leave the corresponding collection unknown.
* `days_remaining` for research and focus is not stored (only points so far), so
  it stays unset.

### Still unknown (reported in `unknown_fields`, never defaulted)

`construction` (the country block holds no queue; `country_reports.construction`
is a daily count of factories on construction, not the items), `completed_focuses`
(not in the country block), `wars`, `fronts`, the hybrid-control fields
(`delegated_armies`, `ai_directives`, `posture`, `theater_postures`), and now
`fuel` and `convoys`, which the brief used to print as `0`. Division `location`
and `strength` are not read either: the save gives a province id and raw
strength figures, not the units `DivisionGroup` documents. They are explicitly
reported as `location="unknown"` and `strength=None`. Naval dockyards are kept
separate from military assignments in briefs and reflexes. Deltas aggregate
repeated equipment lines and report lost visibility as unknown.

```bash
hoi4-harness save-inspect                   # newest save in HOI4_SAVE_DIR
hoi4-harness save-inspect path/to/save.hoi4
```

prints the top-level entries by size and up to 100 country keys with sample
values. On the 1936 SWE save it identifies 77 distinct root keys and all 64
country keys; unindented nested keys are not counted as root entries.

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
  | `{tech_folder}`, click target `research.tech_point` | the tech's sub-tree and `position` (see start_research below) | the point is off the screen or on the tab strip (the tree is not scrolled), or the sub-tree has no `LEFT` box in the interface file |

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
  Not placed, and refused rather than guessed: sub-trees whose box is not
  `format = "LEFT"` (the armour, air, industry and electronics tabs are turned
  `UP`/`UPPER_LEFT`), boxes placed by percentages, techs with no `<root>_tree`
  box (mods), and only the infantry tab has been measured live. Then a tech is
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
