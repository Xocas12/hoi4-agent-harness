# Observation: what an LLM country sees (#139)

A player of a country sees the map, every one of their divisions, the enemy units next to their lines, and what their intelligence knows of other countries. The harness gives an LLM country the same, from two sources.

## Sources

- **The newest text save of the campaign.** `live_setup prepare` sets `save_as_binary=no`; the game autosaves monthly, and any newer save is used. The observer picks the newest `HOI4txt` save dated no later than the game and no more than 62 days before it, and rereads it only when the file changes. It reads the country's own block, the blocks of its neighbours, its enemies and the person's country (up to 16), the states (owners, factories) and the provinces that changed hands. On a 60 MB 1936 save that is about 4 s.
- **The mod's intel line**, weekly (`llm_bridge_emit_intel`): the country's civilian, army, navy and air intel against each enemy, neighbour and major, from the 1.20 dynamic variables `civilian_intel` and friends. UNVERIFIED live: the `ROOT.army_intel@THIS` form. Without a reading the observer assumes an "estimate" band and says so.

## What is shown

- **Own forces, all of them:** each division's template, province, state, terrain, organisation, manpower against need, entrenchment and supply. The brief groups them by state (states facing the enemy first); `inspect_world view=divisions` lists each one.
- **Own industry:** factories and production lines.
- **Contact:** foreign divisions in a province that touches the country's units or territory are shown exactly, as the map shows them.
- **Other countries, through intel:**

  | Intel | Army | Factories and production |
  |---|---|---|
  | 66% and up | exact count and templates | exact factories, every line with its factories |
  | 33% to 66% | rounded ("~140") | rounded factories, the main equipment types |
  | below 33% | "at least N seen", or unknown | unknown |

  Estimates are rounded, not randomised, so two looks at one save agree.

## Tools

`inspect_world` (read-only): `view=divisions`, `view=state id=<state>`, `view=country id=<TAG>`, `view=borders`.

Rival and native runs turn the observer on (`observe_world`), and with a game folder also the playset tools (`mod_context`).

## Not yet

Fleets and air wings, supply hubs and railways per state, weather, and the enemy's templates' stats. A save is up to a month old between autosaves; the daily log keeps the country's own numbers current in between.
