# Observation: what an LLM country sees (#139)

A player of a country sees the map, every one of their divisions, the enemy units next to their lines, every panel the game opens (politics, faction, diplomacy, intelligence, research, production, navy, air, occupation, supply) and what their intelligence knows of other countries. The harness gives an LLM country the same, and nothing a person could not see.

## Sources

- **The newest text save of the campaign.** `live_setup prepare` sets `save_as_binary=no`; the game autosaves monthly, and any newer save is used. The observer picks the newest `HOI4txt` save dated no later than the game and no more than 62 days before it, and rereads it only when the file changes. It reads the country's own block, the blocks of its neighbours, its enemies, its faction partners and the person's country (up to 16), the states (owners, factories, manpower, resistance), the provinces (who holds them, victory points, supply hubs, railways) and a few shared blocks (factions, air bases, intelligence networks, doctrines, supply, characters). On a 60 MB 1936 save that is 3 to 4 s; on a 66 MB 1941 save with 17 countries about 7 s.
- **The mod's intel line**, weekly (`llm_bridge_emit_intel`): the country's civilian, army, navy and air intel against each enemy, neighbour and major, from the 1.20 dynamic variables `civilian_intel` and friends. UNVERIFIED live: the `ROOT.army_intel@THIS` form. Without a reading the observer assumes an "estimate" band and says so. The save holds the intel *sources* (static and dynamic pools), not the percentage the intel ledger shows, so the mod line is the only source of the levels.
- **The mod's daily telemetry** (`LLMB|v5|state`, `war`, `bop`, `camp`, `div`, `evt` lines): the top bar and what moves between saves. It stays the source for stability and war support (the save has only the base value).

## What is shown

- **Own country, in full:** every panel in the coverage table below.
- **Contact:** foreign divisions in a province that touches the country's units or territory are shown exactly, as the map shows them.
- **Public for every country:** ruling party, faction and its members, wars, guarantees, puppets, non-aggression pacts, military access, victory points and who holds a province.
- **Other countries, through intel.** Each branch has its own intel, as in the game's intel ledger:

  | Intel | Army, stockpile | Navy | Air | Civilian: factories, production, politics |
  |---|---|---|---|---|
  | 66% and up | exact count, templates, stockpile | exact ships by type, task forces with location and mission | exact planes by type, wings with base and mission | exact factories and lines; parties, leaders, laws, spirits, advisors |
  | 33% to 66% | rounded ("~140") | rounded ships by type | rounded planes by type | rounded factories; parties to the nearest 5, laws |
  | below 33% | "at least N seen", or unknown | unknown | unknown | unknown; the ruling party only |

  Estimates are rounded, not randomised, so two looks at one save agree.
- **Never shown for another country:** its agency, operatives, intelligence networks, special projects, research queue, decisions, doctrines.

## Tools

`inspect_world` (read-only), `view=` one of:

| View | `id` | Shows |
|---|---|---|
| `divisions` | | every own division: template, state, province, terrain, organisation, manpower, entrenchment, supply |
| `state` | state id | one state, both sides |
| `country` | TAG | the intel-gated picture of a country (army, factories, production) |
| `borders` | | the states across the border |
| `faction` | TAG (default own) | members, leader, manifest and goals, rules; slots and sharing for members |
| `intel` | | agency (upgrades, operatives, missions, pool, captured), intelligence network strength per country, intel levels per country |
| `projects` | | special projects (active with progress, completed), facilities, scientists, breakthrough points |
| `navy` | TAG | ships by type, task forces with location, mission, organisation |
| `air` | TAG | planes by type, wings with base state, mission, region |
| `politics` | TAG | parties and popularity, leaders, laws, national spirits, advisors, elections |
| `economy` | TAG | political power, command power, fuel, manpower, convoys, equipment stockpile and what divisions hold, industrial organisations, trade routes |
| `diplomacy` | TAG | wars with casualties and score parts, war goals, guarantees, puppets, lend-lease, access, opinions |
| `research` | | slots with points, researched count, doctrines and mastery, decisions, focus |
| `occupation` | | resisting states (resistance, target, compliance), own states held by others, victory points, occupation law |
| `supply` | | hubs, railway level, trucks and losses, divisions short of supply |
| `commanders` | | generals, marshals and admirals with skills and traits |

Rival and native runs turn the observer on (`observe_world`), and with a game folder also the playset tools (`mod_context`). The brief gets two to four more lines under "Forces and map": diplomacy (faction, wars, guarantees, subjects), intelligence (agency, operatives on missions, network strength) and fleet, air and stockpile.

## Coverage: every screen a human uses (HOI4 1.20)

Ground truth: the installed game's `interface/*.gui` (one window per screen: `topbar`, `countrypoliticsview`, `countrydiplomacyview`, `factions/countryfactionview`, `countryintelligenceagencyview`, `countryintelledger`, `operationoverview`, `special_projects/*`, `countrytechnologyview`, `countrydoctrinetreeview`, `countryproductionlineview`, `military_industrial_organization/*`, `countryconstructionsview`, `countrytradeview`, `international_market/*`, `countrylogisticsview`, `countryarmyview`, `naviesview`, `strategicairview`, `airgroupview`, `countryoccupationview`, `waroverview`, `peaceconferencewindow`, `countrydecisionview`, `nationalfocusview`, `worldtensionwindowpopup`, `powerbalanceview`, `unitleaderwindow`), `documentation/*.md`, `common/` folders, and key paths checked by search in a real 1.20 text autosave (GER, 1 March 1936, 60 MB) and a 1941 text save (GER, 66 MB, with wars, a hired agency, special projects and doctrines). The wiki pages for these screens were not fetched: the game's own files and the saves answered every question the table asks.

Status: **have** (the harness read it before this work), **built** (read now, key path verified against a real save), **partial** (read, with the gap named), **not built** (the save holds it; left out), **not feasible** (the save does not hold it, or holds it empty; why is given).

| # | Screen | What a human sees | Source | Status |
|---|---|---|---|---|
| 1 | Top bar | political power | save `politics.political_power`; state line `pp` | have |
| 2 | Top bar | stability, war support | state line `stab`, `ws` (computed; the save has only the base value) | have |
| 3 | Top bar | manpower | state line `mp`; save state `manpower_pool.available` | have |
| 4 | Top bar | factories (civilian, military, dockyard, trade-lent) | state line; save `states` buildings | have |
| 5 | Top bar | fuel | save `fuel_status`; `economy` adds maximum, gain, cost | have |
| 6 | Top bar | convoys, command power, nukes | save `convoys.equipment`, `command_power`, `nukes` | built (`economy`) |
| 7 | Top bar | resource income (steel, aluminium, oil, ...) | computed daily from state resources and trade; not saved | not feasible |
| 8 | Top bar | date, speed, alerts, events | state line, `evt` lines | have |
| 9 | Politics | laws (economy, trade, conscription) | save `politics.ideas`, told apart by token | built |
| 10 | Politics | national spirits | save `politics.ideas` | built |
| 11 | Politics | advisors hired | `politics.ideas` entries that are a character's `advisor.idea_token`; no hired advisor exists in the four saves checked, so this path is unverified on a real save (synthetic test only) | partial |
| 12 | Politics | party popularity, ruling party, party leaders | save `politics.parties`, `ruling_party`; `character_manager` | built |
| 13 | Politics | elections | save `elections_allowed`, `last_election`, `election_frequency` | built |
| 14 | Faction | members, leader | top-level `faction.members` (no leader field: the first member is read as the leader, which is right for GER, ENG, SOV and USA in the saves checked) | built |
| 15 | Faction | manifest, goals and their status, rules | `faction.goal_status`, `rule_status` | built |
| 16 | Faction | upgrade slots (spymaster perks), tech and manpower sharing | `faction.upgrades.slots`, `research`, `manpower`, `tech_sharing_group` | built |
| 17 | Faction | influence per member, initiative, faction level and perks (1.17) | top-level `faction_system.countries`, an array by country index with undocumented numeric keys | not feasible (encoding not decoded) |
| 18 | Diplomacy | guarantees, non-aggression pacts, military access, docking, air-base access, market access | `diplomacy.active_relations.<TAG>.guarantee` and siblings | built |
| 19 | Diplomacy | opinion and attitude | `cached_sum`, `attitude` | built |
| 20 | Diplomacy | wars and participants, casualties | `war_relation` (also the mod's `war` lines) | have, built |
| 21 | War overview | war score | `war_score_*_vs_*` holds the parts (provinces, equipment, air, ships, convoys, casualties); the single percentage is computed | partial |
| 22 | War overview | surrender progress | computed by the game | not feasible |
| 23 | Diplomacy | war goals held | `diplomacy.wargoals` | built |
| 24 | Diplomacy | puppets, autonomy level, overlord | `relation.puppet.autonomy_state`; autonomy progress is not read | partial |
| 25 | Diplomacy | lend-lease | `relation.lend_lease` (direction, since); amounts in `lend_lease_to_allies_history` not read | partial |
| 26 | Diplomacy | volunteers | no per-country record in the save beyond a permission flag | not feasible |
| 27 | Diplomacy | world tension | top-level `threat` lists events; the scaled total is computed | not feasible |
| 28 | Peace conference | the table, claims, points | top-level `peace_conference` is empty in every save checked, shape unseen | not feasible |
| 29 | Intelligence | agency name, upgrades by branch and level, upgrade in progress, counter-intelligence, slots | `intelligence_agency.upgrades`, `upgrade`, `defense`, `max_operative_count` | built |
| 30 | Intelligence | operatives: skill, traits, state, mission and target, recruitable pool, captured | `intelligence_agency.operative`, `recruitment`, `captured` | built |
| 31 | Intelligence | operations finished, decryption targets | `operations.finished`, `cryptology.targets` | built |
| 32 | Intelligence | intelligence network strength per country | top-level `strategic_operatives.strategic_operative.<country index>.intel_network[].strength` (index = position in the save's country list, GER 1) | built |
| 33 | Intel ledger | civilian, army, navy, air intel per country | the mod's weekly `intel` line; the save holds sources, not the percentage | have |
| 34 | Special projects | projects: active with phase and progress, completed | `program_status.project_pool.project.<id>` | built |
| 35 | Special projects | facilities and the scientist in each | `program_status.program` | built |
| 36 | Scientists | traits, specialisation levels | `characters` status + `character_manager` `scientist` | built |
| 37 | Special projects | breakthrough points | `program_status.breakthrough` | built |
| 38 | Research | slots, current technologies with points and design team | `technology.slots` + `technologies.<id>.research_points` | built |
| 39 | Research | days left per technology | not stored (points only) | not feasible |
| 40 | Research | researched technologies | `technology.technologies` (those with `level`) | built (count and latest) |
| 41 | Research | tech tree, what can be researched | `techtree.py`, playset tools | have |
| 42 | Doctrines | grand doctrine, sub-doctrine tracks, mastery | top-level `doctrine.countries[<country index>].folder[]` | built |
| 43 | Production | lines, factories, efficiency, output | `production.military_lines` | have |
| 44 | Production | industrial organisations (MIOs) | `production.industrial_organisations`: names; traits, funding and policies are not read | partial |
| 45 | Construction | queue | `general_lines` | have |
| 46 | Equipment | stockpile by type | `production.equipments.equipment` (id, amount) | built |
| 47 | Equipment | what divisions hold | each division's `equipment` | built |
| 48 | Equipment | deficit against need | need per template comes from unit definitions, not the save; stockpile against what the lines make is the closest | not feasible |
| 49 | International market | stockpile, price levels, requests | `equipment_market` is empty in every save checked | not feasible |
| 50 | Trade | routes and partners | `resources.delivery_routes`, relation `trade` | built |
| 51 | Logistics | supply per division | each division's `army_current_supply_ratio` | have |
| 52 | Logistics | supply hubs and railway levels | `provinces.<id>.buildings.supply_node`, `rail_way` | built |
| 53 | Logistics | trucks, trains, losses | `supply_system_2.countries.<tag>` | built |
| 54 | Logistics | supply flow per province (map mode) | not saved | not feasible |
| 55 | Army | divisions: template, location, organisation, manpower, entrenchment, supply | `units.division` | have |
| 56 | Army | commanders: rank, skill, attack, defence, planning, logistics, traits | `characters` status + `character_manager` rank block | built |
| 57 | Army | division templates: regiments and support | `division_templates` names read; the regiment layout is not | partial |
| 58 | Army | armies, army groups, theatres | `theatres` block not read | not built (readable; the front orders cover the orders side) |
| 59 | Army | fronts and front lines | the mod's `div`/`front` lines, `front_orders` | have |
| 60 | Navy | task forces: ships by type, location, mission, organisation | `units.fleet.task_force` | built |
| 61 | Navy | sea region name | task force `location` is a sea province id; the region is not named | partial |
| 62 | Navy | per-ship detail, losses | ships are averaged; losses are in `history` | partial |
| 63 | Air | wings: type, planes, base state, mission, region | `strategic_air.<tag>.air_wing_pool`; `executing_mission`, `strategic_region` | built |
| 64 | Air | air zones, air base capacity | `strategic_air.air_base` (state, capacity); not exposed | partial |
| 65 | Occupation | resistance, target, compliance per state | state `resistance`, `controller` | built |
| 66 | Occupation | occupation law | `occupation_status.default_law` | built |
| 67 | Occupation | garrison needed and present | computed | not feasible |
| 68 | Occupation | collaboration governments | `collaboration` empty in every save checked | not feasible |
| 69 | Map | victory points and who holds them | `provinces.<id>.victory_points`, `controller` | built |
| 70 | Map | state owner, controller, buildings | `states` | have |
| 71 | Decisions | active timed decisions, targeted decisions available | `decision_status` | built |
| 72 | Decisions | the decisions of the playset | `list_decisions` | have |
| 73 | Focus tree | current focus, progress, completed | `focus` (also the probe lines) | have, built |
| 74 | Balance of power | bars | the mod's `bop` lines | have |
| 75 | Weather | weather map | top-level `weather` (readable, 80,000 lines, not an input to a decision the strategist makes) | not built |

Counts, 75 rows: **have** 16, **have and built** 2 (rows 20 and 73), **built** 34, **partial** 9, **not built** 2 (rows 58 and 75), **not feasible** 12.

## Not read

The unread blocks and why: `faction_system` (undecoded keys), `peace_conference`, `equipment_market`, `collaboration` (empty in the saves checked), `theatres`, `weather`. Two paths are verified only on a synthetic save: hired advisors (no real save had one) and the naval mission numbers other than 0, 1 and 3 (the names for 2 and 4 to 7 follow the game's order and are not checked). A save is up to a month old between autosaves; the daily log keeps the country's own numbers current in between.
