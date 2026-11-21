# The designers: tanks, ships and planes

A player designs equipment on three screens, each a DLC feature (`dlc.FEATURES`): the tank designer (No Step
Back), the ship designer (Man the Guns) and the plane designer (By Blood Alone). A design starts from a chassis (a
tank chassis, a hull, an airframe), fills the chassis' module slots, may raise upgrade levels, and costs experience
of the matching branch. The harness reads all of that from the playset's files (`designers.py`), shows the
country's own designs from the newest text save, and builds a design as one guarded effect.

What exists (rows 39 to 41 of `docs/interaction.md`):

| Tool | What it does |
|---|---|
| `design_equipment kind base name [modules] [upgrades] [parent] [obsolete_older]` | Console/inbox, marked, own seat (domain `production`). `create_equipment_variant`, guarded and charged in one `if`. |
| `list_designs kind` | Query. Your designs of one kind from the newest save: name, type, version, modules by slot, upgrade levels, obsolete, parent. Needs the save observer (`observe_world`). |
| `describe_design_option kind [id]` | Query, from the files. The overview, or one chassis, module, module category or upgrade. |
| `steer_production kind=variant id=<chassis family>` | Rival mode (the game's AI holds production): `equipment_variant_production_factor`. |

## The effect

```
eval_effect GER = { if = { limit = {
    has_dlc = "No Step Back"                       # the designer's DLC (+ any extra DLC a module needs)
    has_tech = improved_medium_tank_chassis        # the chassis (OR = { } when several technologies unlock it)
    OR = { has_tech = artillery1 has_tech = antitank1 }   # each CHANGED module's unlock
    has_army_experience > 20                       # the price, tested
  }
  create_equipment_variant = { name = "Panzer III" type = medium_tank_chassis_2 [parent_version = N]
      obsolete = no [mark_older_equipment_obsolete = yes]
      modules = { turret_type_slot = ... main_armament_slot = ... } upgrades = { tank_nsb_armor_upgrade = 3 } }
  army_experience = -20                             # the price, taken in the same if
  log = "LLMB|v5|click|<nonce>" } }
```

On the typed console (keys transport) a DLC name with spaces cannot be written inside quotes (the console splits
quoted text on spaces, seen on 1.19.5), so the `has_dlc` tests and a design name with spaces are left to the inbox
transport: the harness still refuses a missing DLC before sending, and the result's `unchecked` says the game does
not test it there. The marker is logged inside the guard, so the line in the game's log proves the game let the design through
(`MARKED_ACTIONS`, `_verify_click`); without it the result says "Nothing changed". `create_equipment_variant`
itself charges nothing, which is why the harness charges: with no guard on experience the effect would be a free
design. Only the changed slots are written; a slot left out keeps the parent's module. `type` must be a chassis
type and not an archetype (`effects_documentation.md`). `parent_version` is the version number the save gives
(`list_designs`); without a parent the game uses the chassis' default design, whose modules the harness takes from
the save's own first design of the type when it has one, else from the chassis' `default_modules`.

## Checks before anything is sent

All harness-side, each refusing with the reason and sending nothing (`designers.plan_design`):

* the designer's DLC is active (`dlc.feature_refusal`), and any further DLC a module's block names (the land
  cruiser modules need Gotterdammerung as well as No Step Back);
* the chassis exists, is a type (not an archetype) and has an unlocking technology (`enable_equipments`);
* each module exists for that designer, is allowed in its slot (the chassis' `allowed_module_categories` for the
  slot, plus the categories another module of the design opens: a turret lists `allowed_module_categories = {
  main_armament_slot = { tank_medium_main_armament } }`), and has an unlocking technology
  (`enable_equipment_modules`). A module that nothing unlocks (34 in 1.20, for example `ship_radar_1`) is refused
  unless it is the chassis' own default for that slot: the harness cannot test what the files do not say;
* the final design fills every `required` slot, keeps every `module_count_limit` and `forbid_module_categories`;
* each upgrade belongs to the chassis, goes up and not down from the parent, stays within `max_level`, and the
  technologies of its `level_requirements` are in the guard (a requirement that is not a plain `has_tech` is
  refused);
* the design differs from the parent (an unchanged design is refused), and the defines are present.

## The experience cost rule

Per module slot, against the parent design (the chassis' default design when none is named):

| Change | Cost | Where |
|---|---|---|
| empty -> module | `EQUIPMENT_MODULE_ADD_XP_COST` 5 | `common/defines/00_defines.lua` line 671: "XP cost for adding a new equipment module in an empty slot when creating an equipment variant" |
| module -> empty | `EQUIPMENT_MODULE_REMOVE_XP_COST` 1 | line 674: "removing an equipment module and leaving the slot empty" |
| module -> related module | `EQUIPMENT_MODULE_CONVERT_XP_COST` 3 | line 673: "converting one equipment module to a related module". Related = the new module has `can_convert_from` naming the old module (`module = X`) or its category (`module_category = C`) |
| module -> unrelated module | `EQUIPMENT_MODULE_REPLACE_XP_COST` 6 | line 672: "replacing one equipment module with an unrelated module" |
| unchanged | 0 | |

Plus, per changed slot with a new module, that module's own `xp_cost` (`00_tank_modules.txt`: 93 modules,
`00_plane_modules.txt`: 61, `00_ship_modules.txt`: none). Plus the upgrades: a land upgrade with `linear_cost`
(`tank_nsb_engine_upgrade`, `tank_nsb_armor_upgrade`: `cost_by_level = 1`) costs level n times that for each level
n added; any other upgrade costs, per level added, `<KIND>_EQUIPMENT_BASE_COST + (total levels after) *
<KIND>_EQUIPMENT_RAMP_COST` (`cost = land|naval|air` in `upgrades/*.txt`; defines lines 1125 to 1130: "Cost in XP to
upgrade a piece of equipment one level is base + ( total levels * ramp )"). Plus `DESIGN_TEAM_CHANGE_XP_COST` (line
4471, 0 in vanilla: "Flat cost added to the XP cost of a new equipment design"). The localisation confirms the
screen shows per-change prices ("Costs N to add / replace / remove", `EQUIPMENT_DESIGNER_*_DESC`) and a total
("Design Cost", `EQUIPMENT_DESIGNER_ARMY_XP_COST`).

The guard is `has_<army|navy|air>_experience > cost` and the effect is `army_experience|navy_experience|
air_experience = -cost`, in the same `if`, so the game declines the whole design when it cannot pay.

## What is uncertain, and what the tool does about it

The result's `unchecked` list carries each of these; the cost is always reported as an upper bound
(`Plan.breakdown()["exact"]` is false).

1. **How a module's own `xp_cost` combines with the four defines.** The files do not say (the defines' comments
   speak of "adding a module" and "converting" and nothing of a module's `xp_cost`; the localisation has no
   tooltip for it). The harness charges the define PLUS the new module's `xp_cost`. That is at least what the screen
   asks under every reading that can be named (define only, module only, the larger of the two, the sum). It is
   not an upper bound for a reading that multiplies them, which nothing in the files suggests. The cost of a slot
   change is therefore never undercharged under those readings and may be overcharged by up to the module's
   `xp_cost` (5 to 10 for the dearest tank modules, 1 or 2 for guns). A removal is charged the define only.
2. **Upgrade levels.** "linear" is read as level n costing n times `cost_by_level`, the dearer of two readings (flat
   `cost_by_level` per level is the other). The base-plus-ramp formula's "total levels" is read as the design's
   levels after the change. Both are upper bounds. Licensed equipment pays more (`LICENSE_EQUIPMENT_UPGRADE_XP_FACTOR`,
   2): a design of your own is not licensed, and this is not applied.
3. **Cost modifiers.** `land_equipment_upgrade_xp_cost`, `naval_...`, `air_...` and `equipment_upgrade_xp_cost`
   (ideas, advisors) scale the screen's price ("Experience cost factor when changing an equipment"); script cannot
   read them. A raise above the slack in (1) would be undercharged; a reduction is overcharged.
4. **The parent's contents** come from the newest text save (a design made since is not in it), and "default
   design" from the save's frame or the chassis defaults.
5. **Role-dependent module rules** (`allow_equipment_type`, `forbid_equipment_type*`: a flamethrower for flame
   tanks, an anti-air gun against a main gun) and which equipment type a plane or an anti-air tank becomes are
   derived by the game; the harness does not test them, and a design the game rejects is declined by the game
   (no marker, nothing changed).
6. Whether `create_equipment_variant` itself re-validates module compatibility is untested in a live game; so is
   the effect as a whole (the fake game models it: `FakeGame._create_variant`). Duplicate names are allowed by the
   game and not tested.

If any of this proves wrong in a live game, the place to change is `designers.plan_design` and this file.

## Reading the country's designs

`designers.read_variants(text, tag)` reads the save's top-level `equipments = { <type> = { ... } }` entries that
carry `creator="TAG"` and a `modules` block: `name`, `version` (the number `parent_version` takes, counted within the
type), `parent_id`, `obsolete`, `is_frame` (the chassis' own first design), `modules`, and `upgrades` (written as
`upgrades={ upgrades={ { tank_nsb_engine_upgrade 2} ...} }`). 1941 Germany has 93 such entries. `World.variants` holds
them; `WorldObserver.variants(state)` hands them to `list_designs` and to the console channel (`ConsoleChannel.designs`,
set by `HOI4Env.attach_world`).

## Rival mode

The game's AI designs and builds for a rival country (`GAME_AI_OWNED` lists `design_equipment`, as it lists
`design_division_template`). What the LLM can do there is steer. `steer_production kind=variant id=<family>`
sets `equipment_variant_production_factor` (`common/ai_strategy/_documentation.md` lists the token; the syntax is the
one `CHI.txt` and `CZE.txt` use: `id = light_tank_chassis`, `value = -100` to `60`). The id is a chassis family
(archetype), not one design: a strategy cannot name a design, so the AI builds the newest non-obsolete design of the
family, which `design_equipment obsolete_older` arranges in a seat run. The levels are less -50, more 50, much_more
100. The blocks are in `llm_bridge_steer_regions.txt` (generated per playset; 846 lines were added for the vanilla
archetypes). `steer_spending kind=land_xp id=equipment_variant` (already there) steers how eager the AI is to spend
experience on designs.

## Not built

* Renaming, removing, setting a default, and toggling obsolete one design (row 42): `create_equipment_variant` only
  creates; `obsolete_older` is the one obsolete route.
* The stockpile conversion (row 43) and the nation-wide upgrade screen for non-modular equipment (row 38).
* Design teams (`design_team =`, military industrial organisations): not passed, so `DESIGN_TEAM_CHANGE_XP_COST`
  stays a flat term that is 0 in vanilla.
* `allow_without_tech`, `model`, `icon`, `role_icon_index`: not offered.
