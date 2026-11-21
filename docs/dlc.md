# DLC: which ones are on, and what the harness offers because of it

HOI4 gates a great deal of content on `has_dlc = "Name"`: 1,790 times in `common/national_focus`
alone, and again in decisions, events, ideas and laws, characters, scripted GUIs, on_actions,
technologies, MIOs and special projects. A harness that does not know which DLCs the player has
offers things that do not exist in that game: the Swedish tree of Arms Against Tyranny to a
player without it, a decision whose `allowed` needs No Step Back, an event option the game
will never draw. This page is what the harness does about it, and the file evidence for every
claim. Where the game gates by something other than `has_dlc`, it says so.

## The active set

```bash
hoi4-harness dlc                          # installed / disabled / active, and what each turns on
hoi4-harness dlc --dlc "No Step Back"     # what if only this one were on
hoi4-harness dlc --no-dlc
```

In order of precedence:

1. **An explicit list**: `dlcs` in a profile (`["Arms Against Tyranny", "No Step Back"]`, `[]` for
   none), or `--dlc NAME` (repeatable) / `--no-dlc` on any command. It is the active set as given;
   the install is not read. A name the game files never test is kept with a note (a mod may test
   its own).
2. **The install minus the launcher's disabled list.** Installed: every
   `<game dir>/dlc/<folder>/<file>.dlc`, key `name` (that is the string `has_dlc` is tested with;
   `"Gotterdammerung"`, `"No Compromise, No Surrender"`). A folder with no `.dlc` file is not
   installed (`dlc023_man_the_guns` on the machine this was written on is an empty folder: Man the
   Guns is not owned there). Disabled: `disabled_dlcs` in `<user dir>/dlc_load.json`, found the
   way the saves are (`--user-dir` / `user_dir` to say). The launcher's spelling of an entry was
   not observable (the file on that machine lists none), so an entry matches leniently: the
   `dlc/<folder>/<file>.dlc` path, the folder, the file or its stem, the `pops_id`
   (`hoi4_dlc028_la_resistance`) or the name, with either slash, also inside `{"path": ...}`.
3. **Unknown**: the game dir has no `dlc/` folder at all (a fixture, a partial copy). Nothing is
   filtered, every `has_dlc` stays "unknown", and the brief says nothing about DLC: exactly how
   the harness behaved before this existed.

Cosmetic packs (portraits, unit packs, wallpapers) count as active DLCs too, since the game
reports them, but no `has_dlc` test in 1.20 names one; the brief therefore says "N active (M of
the 17 that add content)".

## How `has_dlc` is evaluated

`modcontext/script.py`: `dlc_truth(nodes, dlcs)` for `has_dlc` alone, `tag_truth(nodes, tag, dlcs)`
for tag tests plus DLC, `holds(...)` for scoring. Three-valued: True, False, or None when anything
else (a flag, a war, a date) could decide it, so an unknown stays unknown and never rules content
out by guess. `AND`, `OR` and `NOT` are handled; `NOT` with several entries is a NOR, as in the
game. A `has_dlc` that holds says nothing about *which country* may have the thing, so it is
skipped, not counted as a tag match. Without a known set (`dlcs` is None) nothing changes.

Content the game evaluates itself keeps its `has_dlc` when the harness copies a block into a
console guard (laws, advisors, decisions): the harness only refuses early, in the DLC's name,
when the DLC tests alone rule the item out.

## What is filtered

| Where | What the harness does | Code |
|---|---|---|
| Focus tree choice | The game scores each tree by its `country = { factor modifier = { add N .. } }`. A tree whose only score a `has_dlc` kills is dropped (Poland without Poland: United and Ready or No Step Back gets `generic_focus`); one beaten by a score only DLC raised is dropped (the No Compromise, No Surrender sea trees over the Waking the Tiger ones). Trees that merely score lower stay: scripts swap countries onto them (Free France). | `identifiers.trees_for` |
| Focuses | `allow_branch = { has_dlc .. }` False hides the focus and every focus that can then never be reached (a prerequisite group made only of hidden focuses). `available = { has_dlc .. }` False keeps it out of "available now". `set_national_focus` on either is refused: `Focus 'X' needs the No Step Back DLC, which is not active.` | `hidden_focuses`, `focus_needs`, `check` |
| Decisions | Not offered when the DLC tests in `allowed`, `visible` or `available`, or in the category's, rule it out. `take_decision` says why. | `surface.decision_for`, `check_take` |
| Events | An event whose own `trigger` is ruled out is never what a window is matched to and `read_event` says it is unavailable; an option whose `trigger` is ruled out is `shown: no`. | `events.py` |
| Laws, spirits, advisors | Left out (and `change_law`, `hire_advisor`, `set_occupation_law` refused in the DLC's name) when `allowed`, `visible` or `available` is ruled out. | `surface`, `policy_effects` |
| Custom windows | `guis_for` drops windows ruled out; a button whose window or own `visible`/`enabled` needs an inactive DLC cannot be pressed (`Window 'X' needs ..`). | `surface.guis_for`, `click_refusal` |
| Balance of power | Bars of an inactive DLC are not described in the brief (table below). | `surface.balance_lines` |
| Views | `inspect_world view=projects` and the MIO part of `view=economy` say the feature does not exist for the country, in the DLC's name, instead of showing empty data. | `world.query`, `surface.item_feature` |
| Brief | One line: `DLC: 12 active (9 of the 17 that add content); focuses, decisions, events, laws, advisors and windows that need an inactive DLC are filtered out.` | `identifiers.brief_lines` |
| Prompt | "This playset" names the active content DLCs and the whole features that are off. | `modcontext/prompt.py` |
| Catalog | Descriptions of `set_occupation_law`, `change_law`, `hire_advisor`, `take_decision`, `click_gui_button` and `inspect_world` say what is refused when a DLC is missing. | `actions/catalog.py` |

The index itself (`IdentifierIndex.trees`, `summary()`, its fingerprint) and the bridge mod's
generated files are *not* filtered: they list every id the game defines, which does not depend on
ownership (see "The mod" below).

## Feature to DLC

Two kinds of evidence, and they differ.

**Whole features the files tie to one DLC** (`dlc.FEATURES`; the harness refuses or hides them
as a whole):

| Feature | DLC | Evidence |
|---|---|---|
| Tank designer | No Step Back | `common/units/equipment/modules/00_tank_modules.txt` opens `equipment_modules = { limit = { has_dlc = "No Step Back" } ...`; `common/defines/00_defines.lua` `DEFAULT_MODULE_VARIANT_CREATION_XP_CUTOFF_LAND`: "the tank designer DLC feature must be active". A second module block needs Gotterdammerung as well as No Step Back (line 1634). |
| Ship designer | Man the Guns | `00_ship_modules.txt` opens `equipment_modules = { limit = { has_dlc = "Man the Guns" } ...` |
| Plane designer | By Blood Alone | `00_plane_modules.txt` opens `equipment_modules = { limit = { has_dlc = "By Blood Alone" } ...` |

**Features the files gate item by item**, so there is no single DLC. The harness evaluates each
item's own conditions. "Standing" is the number of items not ruled out by `has_dlc` in
`allowed`/`visible`/`available`, counted by `script.dlc_truth` with no DLC and with all 17 (tags
and other conditions are not counted, so these are upper bounds):

| Feature | Folder | Items | Standing with no DLC | With all DLC | Evidence of the gating |
|---|---|---|---|---|---|
| Special projects (view=projects) | `common/special_projects/projects` | 49 | 15 | 49 | `allowed = { has_dlc = "Gotterdammerung" }` (`sp_air_bouncing_bomb`, `sp_air_earthshaker_bomb`..), By Blood Alone, Man the Guns, No Step Back on others; `special_projects_documentation.md`: "Mainly Country-specific & DLC locks". The feature itself is not DLC-tied |
| MIOs (view=economy) | `common/military_industrial_organization/organizations` | 557 | 219 | 527 | `allowed` of the organizations: Arms Against Tyranny (131 tests), No Compromise, No Surrender (65), Gotterdammerung (62), Battle for the Bosporus (54); `_documentation.md`: MIOs replace design companies |
| Occupation laws | `common/occupation_laws` | 22 | 18 | 22 | Graveyard of Empires (4), Battle for the Bosporus, Gotterdammerung, No Compromise, No Surrender: per law, not the feature |
| Operations | `common/operations` | 39 | 38 | 39 | No Step Back, Man the Guns, By Blood Alone, Thunder at Our Gates: per operation |
| Intelligence agency, operatives | `common/intelligence_agencies` | | | | Only Belgium's agency name and logo vary with Gotterdammerung (`00_intelligence_agencies.txt:137-171`); the agency, its upgrades and operatives are not DLC-gated |
| Decisions | `common/decisions` | 615 | 558 | 615 | `has_dlc` in `allowed`/`available` of 89 files |
| Focus trees | `common/national_focus` | 68 | | | 55 of the 68 trees score by a `has_dlc` in their `country` block (matrix below) |
| Faction rules, goals | `common/factions` | | | | One goal needs La Resistance (`goals/faction_goals_short_term.txt:799`, `spy_master_goal_desc`); no file under `rules/` contains `has_dlc` |
| Governments in exile | `common/factions/rules/goverment_in_exile_rules.txt` | | | | No `has_dlc` anywhere in it: not DLC-tied |

So: operatives, the intelligence agency, occupation laws, MIOs, special projects, faction rules
and governments in exile are **not** La Resistance / Arms Against Tyranny / Gotterdammerung /
No Compromise, No Surrender features as a whole, whatever the marketing; what those DLCs add are
specific laws, organizations, projects and goals. (The task note guessed otherwise for several;
the files say what is above.)

### Balance of power

`common/bop/*.txt` carries no `has_dlc`; the bars always load. The DLC of a bar is the DLC the
script that sets it up is gated on (`dlc.BOP_DLC`):

| Bar | DLC | Evidence |
|---|---|---|
| `DEN_balance_of_power` | Arms Against Tyranny | `common/national_focus/denmark.txt` (tree needs it), `events/AAT_Denmark.txt` |
| `SWE_riksdag_political_balance` | Arms Against Tyranny | `sweden.txt` (tree needs it), `events/AAT_Sweden.txt` |
| `FIN_power_balance` | Arms Against Tyranny | `common/on_actions/09_aat_on_actions.txt` (23 `has_dlc` tests) |
| `ETH_centralization_balance` | By Blood Alone | `08_bba_on_actions.txt`, `ethiopia.txt` (tree needs it) |
| `ITA_power_balance` | By Blood Alone | `08_bba_on_actions.txt` (and `common/modifiers/BBA_power_balance_modifiers.txt`) |
| `SWI_centralization_bop` | By Blood Alone | `switzerland.txt` (tree needs it), `events/BBA_Switzerland.txt` |
| `PRC_communist_power_struggle` | No Compromise, No Surrender | `china_communist_sea.txt` (country block `has_dlc = "No Compromise, No Surrender"`), `events/SEA_Communist_China.txt` |
| `CZE_bop_army_readiness` | Peace For Our Time | `czechoslovakia_mu.txt` (country block `has_dlc = "Peace For Our Time"`) |
| `BRA_political_military_balance` | none found | defined, referenced nowhere else in 1.20 |

### Focus trees: the matrix

From `common/national_focus` (`country = { factor = 0 modifier = { add N tag = X has_dlc = ".." } }`),
read by `trees_for`. Examples (tree, tag, what it needs): `sweden_focus` SWE Arms Against
Tyranny; `polish_focus` POL Poland: United and Ready *or* No Step Back; `czech_focus` CZE Death or
Dishonor; `china_nationalist_focus` CHI Waking the Tiger, beaten by `china_nationalist_sea_focus`
(score 1000) with No Compromise, No Surrender; `indian_focus` RAJ Together for Victory without
Graveyard of Empires, `indian_focus_goe` with it; `austria_focus_tree` AUS Gotterdammerung.
`german_focus`, `french_focus`, `italian_focus`, `soviet_focus`, `japanese`, ... need none.
Without any DLC a country whose tree is tied to one gets `generic_focus` (the tree with
`default = yes`, `factor = 1`).

## The mod

`mod/llm_bridge` audit: every trigger and effect it uses, checked against `documentation/`
(`triggers_documentation.md`, `effects_documentation.md`, `dynamic_variables_documentation.md`).

- **Triggers and effects.** All the engine ones it uses are documented without any DLC
  restriction and none of its files contains `has_dlc`: `has_power_balance`, `power_balance_value`,
  `power_balance_weekly`, `has_collaboration`, `faction_influence_score/_ratio/_rank`,
  `faction_manifest_fulfillment`, `faction_power_projection`, `has_volunteers_amount_from`,
  `is_in_peace_conference`, `has_focus_tree`, `has_completed_focus`. The documentation does
  not mark DLC dependence in either direction, so this is "engine triggers that the game
  documents", not a proof they are safe without ownership. What makes them safe in practice is
  that the **definitions load regardless of ownership**: `common/bop` (and every focus, tech,
  tree, decision) is read whether or not the DLC is owned, so `has_power_balance = { id = X }`
  names a bar that exists and is simply false without the DLC; a `has_completed_focus` of a focus
  in an unowned tree is false. Nothing in the mod refers to an id the game does not define.
- **Risky, not verified live.** `has_collaboration`, the `faction_*` variables and
  `faction_manifest_fulfillment` read systems (collaboration governments, faction manifests) that
  came with 1.11/1.12 content; whether they evaluate to 0 or log an error in a game without the
  matching DLC could not be settled from the files. They are read inside
  `is_in_faction`/enemy-country guards in `llm_bridge_observe.txt`. Check the first telemetry
  of a no-DLC run for `error.log` lines naming them.
- **Generated files** (`llm_bridge_probe`, `llm_bridge_steer_regions`, `llm_bridge_research`...)
  are built from `index.trees`/technologies, which are not DLC-filtered, so the same mod
  works whatever is on.
- **`ai_strategy` blocks of an unusable type.** Verified where the documentation says so:
  `common/ai_strategy/_documentation.md:135` - "If a plane type is DLC locked its set ratio is
  ignored and 0 is used instead" (`unit_ratio` of an air type). Not verifiable from the files:
  `equipment_production_factor` for an equipment the player cannot build, `build_building` for a
  building whose `dlc_allowed` is off (`stronghold_network`, `mega_gun_emplacement`,
  `naval_headquarters`, the landmarks), `template_prio`/`role_ratio` for a template the DLC adds
  (`common/ai_templates/templates_GER.txt:989` and `templates_SOV.txt` zero a template's
  `upgrade_prio` themselves when Gotterdammerung is off). The harness's `steering` ids are
  generic archetypes and categories the base game has, and `steer_production building=` names
  none of the buildings with a `dlc_allowed` block (`common/buildings`: `stronghold_network`,
  `mega_gun_emplacement`, `naval_headquarters`, `commercial_nuclear_reactor`, the landmarks). A bridge block that matches nothing is the
  expected failure mode, but it is unconfirmed live.

## Tests

`tests/test_dlc.py`: detection from a fake game dir with `.dlc` files and a `dlc_load.json`, the
evaluator under `NOT`/`OR`/`AND`, and a matrix (all DLC, none, mixed, unknown) over a fixture
playset with DLC-gated focus trees, hidden branches, decisions, events, laws, spirits, a window,
balance of power bars, whole features and the projects view.
