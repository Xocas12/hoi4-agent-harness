# Modular control: pick who runs each part of the country

Control used to be a ladder of levels: `operational_control` `llm` / `ai` / `game_ai`, and for a
campaign `campaign_control` `game_ai` / `llm`. It is now a **map**: each domain of the game is run
by exactly one controller, chosen independently, and the old levels are presets of the map. Any
part can be run by the model, by a deterministic script, by HOI4's own AI, or by nobody, and
"the model steers the game's AI" stays as its own layer.

**Status: built and tested on the simulators only** (the mock adapter, the fake game, the fake
army and the tactical simulator, `tests/test_modular_control*.py`). Nothing here has been run
against the live game, and no model was called to build it. What that leaves open is in
[Not verified](#not-verified).

## Domains and controllers

| Domain | Actions (`actions/catalog.py`) | Script controller (`auto/`) |
|---|---|---|
| `focus` | `set_national_focus` | follows a focus path, else a scored pick |
| `research` | `start_research` | fills idle slots from a priority list |
| `construction` | `queue_construction` | rule set: civilian factories, military ratio, infrastructure |
| `production` | `set_production`, `change_production_equipment`, `set_trade`, `design_division_template`, `deploy_divisions` | keeps lines full, in ratio |
| `politics` | `hire_advisor`, `take_decision` | runs a rule table you give it (empty by default) |
| `diplomacy` | `diplomacy` | none: `script` is refused (see below) |
| `army` | `set_army_order`, `move_division`, `attack_province` | a tactical controller through the bridge |
| `navy` | `set_naval_mission` | none |
| `air` | `set_air_mission` | none |

Always available to the model, whatever the map: `note`, `advance_time` and the read-only
`list_decisions` / `describe_action`. `set_game_speed` stays the harness's whenever the game's AI
runs the country.

| Controller | Meaning |
|---|---|
| `llm` | The model gets that domain's actions and is woken for its events. |
| `script` | A deterministic Python controller fills it. The model has no tools for the domain and is not woken for it; the brief carries one line per automated domain saying what the script did since the last wake. |
| `game` | HOI4's own AI. See the constraint below. |
| `off` | Nobody. The actions are not offered and nothing acts there. |
| `human` | A person playing alongside the model ([co-op.md](co-op.md)). Like `off` for the model, plus: the prompt and every brief say the person holds it, and a call into it is refused at queue time and again when a queued call runs, whatever else asks for it. Beside a `game` domain (`human_ai`) it is refused, because the AI would act in the person's domain too (`allow_contested` makes it a warning). `--human army,navy` is the short form. |

```toml
# profile (TOML): the top-level keys first, then the control table
army_controller = "scripted"
steer = "auto"

[control]
default = "off"            # every domain not named below
focus = "llm"
research = "script"
army = "script"
```

```json
{
  "control": {"focus": "llm", "research": "script", "construction": "script",
              "army": "script", "default": "off"},
  "army_controller": "scripted",
  "steer": "auto",
  "auto_options": {"research_list": "land_mass_assault",
                   "construction": {"state": "Svealand", "queue_target": 4}}
}
```

A domain the map does not name gets `default`, or `llm` when there is none.

On the command line:

```bash
hoi4-harness play --control focus=llm,research=script,army=script,default=off
hoi4-harness play --control-map profiles/mymap.json          # a file, inline JSON or pairs
hoi4-harness play --control-map "focus=llm,default=script" --steer off --army-controller learned
```

`--control` still takes `game_ai` / `llm` (the campaign's old switch); anything with an `=` is a
map. `--allow-contested` is described below. `hoi4-harness doctor` prints the resolved map.

## The hard constraint: what the game's AI can be handed

The game's AI can only be handed the **whole country** (the console's `human_ai`, "Toggles AI for
Human countries", console_commands_documentation.md:1048; docs/headless.md: "not army-scoped: it
hands the whole country to the AI, research included") or individual **armies**
(`delegate_army_to_ai`, issue #8, a draft click path, docs/campaign.md). So:

- `game` on any domain **but `army`** means `human_ai` is on. The AI then runs *every* domain.
- `army: game` without any other `game` domain is per-army delegation, and nothing else is the
  AI's.

A campaign sends `human_ai` **iff** some non-army domain is `game` (a campaign sets
`campaign_human_ai` from the map; `--no-human-ai` is for the old settings). Outside a campaign
nothing sends it, and the map says so as a warning: the game's AI must already be running the
country.

### The contested rule

With `human_ai` on, every domain *not* also set to `game` is **contested**: the AI may undo or
double what the model or a script does. The repo already knows which domains it does that to
(`control.UNDER_HUMAN_AI`, one doc line each):

| Domain under `human_ai` | Status | Source |
|---|---|---|
| focus, research | overwritten | the AI picks them: `agent/hybrid.py` `GAME_AI_OWNED`, docs/campaign.md |
| construction | overwritten | the AI builds; it would undo or double it (`GAME_AI_OWNED`) |
| production | overwritten | `set_production` opens a second line beside the one the AI keeps (docs/campaign.md) |
| diplomacy | overwritten | the AI runs it (docs/campaign.md, "Not offered") |
| army | overwritten | the AI runs every front and moves its own armies |
| politics | complements | "the AI takes few decisions and may leave advisor slots empty" (docs/campaign.md) |
| navy, air | unknown | nothing in the docs says what the AI does with fleets or wings |

The rule: a map that gives an **overwritten** domain to the model or a script while `human_ai` is
on is **refused** (`ControlError`, exit code 2 on the CLI) with the domain and the reason. A map
that does the same with an **unknown** domain is **warned**. **Complements** is allowed. `off`
is never contested. `allow_contested` (profile key, `--allow-contested`) downgrades the refusals
to warnings, for an experiment that wants to measure the clash.

Consequence worth knowing: `focus=llm, production=game` is refused, because `production=game`
means `human_ai`, and then the AI picks the focus itself. To keep a domain of your own next to a
`game` one, it has to be `politics` (or `off`).

## The steering layer

`set_ai_directive`, `set_ai_posture` (global or per theater), `clear_ai_directives` and
`delegate_army_to_ai` are not a domain; they are the `steer` switch, `llm` or `off`
(`auto`, the default, is `llm` when the game's AI owns something). It exists only while the game's
AI owns something to steer:

- with `human_ai` on, steering is the whole point of the preset `game_ai`;
- with delegated armies only (`army: game`, no `human_ai`) it is offered but **marked
  unverified**: nothing shows that `ai_strategy` affects a human country's delegated armies, and
  the map resolves with that warning;
- with the game's AI owning nothing, `steer = llm` is refused.

`delegate_army_to_ai` is offered to the model only for delegated armies with steering on. With
`army: game`, no `human_ai` and `steer = off`, nobody could hand the armies over, so the harness
does it once (`auto/army.py` `ArmyDelegate`, the AI-only arm of the hybrid experiment).

### Armies the model runs, and armies it hands a front

`army: llm` without `human_ai` is the model's own army control: it is offered `create_army`,
`assign_divisions`, `assign_army_front`, `draw_fallback_line`, `set_army_execution` and
`release_army` besides the older `move_division`, `attack_province` and the plan drawing actions,
when the profile sets `army_ui = true` and the camera record is good ([armies.md](armies.md)).
With `army: game` (no `human_ai`) and `steer = llm`, only `assign_army_front`, `set_army_execution`
and `release_army` are kept: the game has no per-army AI toggle, so handing an army a front against
a named enemy at a pace IS the delegation. `create_army` and `assign_divisions` are the model's
army management and stay out. That is the mode where the model owns the country and the game's AI
runs only the armies (#161 step 2). It is built and tested on the fake game only.

## Direct economy (#137, economy part)

`--control economy` is a named part of a map: the model runs the desk work of the country by hand,
as a player does, and nothing else.

| Domain | Controller | How it reaches the game |
|---|---|---|
| `research` | llm | `start_research` clicks the tech tree (mouse, game window in front) |
| `production` | llm | `set_production` edits an existing line by click and opens a new one by console; `change_production_equipment` switches a line by click (both need the recorded production points, `docs/live-session.md` section 14b) |
| `construction` | llm | `queue_construction` / `queue_province_building` through the console, checked against the save |
| `focus`, `politics`, `diplomacy` | llm | as in an independent campaign |
| `army`, `navy`, `air` | off | nobody; add `army=script` or `army=llm` after `economy` to give the armies to someone |

The game's AI owns nothing (`human_ai` is never sent, `steer` is off), so nothing it does can undo
or double an order, and what the model leaves empty stays empty. Overrides follow the word
(`economy,army=script`, or `{"base": "economy", "diplomacy": "off"}` in a profile); putting a
domain the model runs on `game` is refused as contested, like any map.

The start log says how each part is reached (`economy research: start_research (mouse)`) and warns
when a part the model owns has no tool in this run; the prompt says the same, so it never promises a
click path the run does not have (`hoi4_harness/economy.py`). Without the production points a
line that exists cannot be edited and the model is told so.

Built and tested on the fake game only (`tests/test_137_economy_control.py`). Not done here: the
army half of #137 (front lines, battle plans, division moves), research or production read as a
human reads the screen (the harness reads the save), and the live check.

## Sovereign mode (#161 step 2)

`--control sovereign` is the economy above plus the whole country's choices, with the game's AI
running **only the armies**. It is the full mode step 1's experiment (contested production under
`human_ai`, `docs/contested-production.md`) was meant to lead to: `human_ai` is never sent, so the
AI cannot undo or double a production line, a design or a pick.

| Domain | Controller | How |
|---|---|---|
| `focus`, `politics`, `diplomacy`, `research`, `production`, `construction` | llm | as in `economy` (`hoi4_harness/economy.py`) |
| `army` | game | per army, by a front: `assign_army_front(army, enemy)` draws the line on the border and sets the pace, and the AI carries the plan out (HOI4 has no per-army AI toggle, `docs/armies.md`) |
| `navy`, `air` | off | **policy: nobody orders fleets and wings.** They keep the game's default missions; the prompt, the start log and every brief say so, and there is no tool |
| `steer` | off | the model gives fronts, not directives (their effect on delegated armies is unverified) |

The model has `assign_army_front`, `set_army_execution`, `release_army`, `create_army` and
`assign_divisions` for the armies; `delegate_army_to_ai` (the unverified toggle) and the steering
tools are not offered, and the script that would delegate every army by the toggle is not built
for this mode. `resolve("sovereign,production=game")` is refused as contested, like any map.

**Starting.** The campaign setup (`campaign/setup.py`) turns `army_ui` and the mouse on, sends no
`human_ai`, and takes a text save at least every 7 game days, since a front is confirmed only by a
save that shows its plan. `begin()` then refuses to start (`CampaignStartError`, in the log as
`cannot start`) unless:

- the state line reads `ai=0` (`ai=1`, or no `ai=` field, is refused; the harness never toggles it);
- the five army tools are offered, with the adapter's reason for any that is not (no camera record,
  `army_ui` off, ...);
- every recorded point they need exists. The message names each one missing and the command:
  `army.create`, `plan.execute_careful`, `plan.execute_balanced`, `plan.execute_rush`, and
  `army.<name>` for each army the first save shows, as
  `hoi4-harness calibrate army.create plan.execute_careful ...` (`plan.assign_units` is optional).
  An army that appears later without a point is refused when it is ordered, with the same command.

**Wakes** (`agent/policy.py`, `sovereign.py`), at war only and on onset, so a gap the model chose to
leave does not wake it again every `campaign_wake_gap_days`:

- an army that has divisions and holds no front (a garrison or a fallback line is not a front);
- divisions the country has that are in no army;
- a front the front report reads as LOSING (`frontreport.py`, via `WorldObserver.front_readings`;
  needs `observe_world`), plus the focus and research-slot wakes of any independent campaign.

**The brief** says in two lines who runs what and how many armies hold a front, naming those that do
not; **the prompt** (`describe_map`) says how an army is handed over and that navy and air are
nobody's; the guidance pack `sovereign` (use `sovereign+strategy`) tells the model that production
is its job now. The profile is `profiles/sovereign-ger.json`.

One live detail the fake game could not settle and the code now assumes: a production edit leaves
the production screen up, and the army card, the map and the pace window are under it, so the army
click path presses the production hotkey first when the last production edit left it open
(`InputDriverAdapter.open_panel`; assumption P2 of `adapters/production_ui.py`).

Built and tested on the fake game only (`tests/test_161_sovereign.py`: a four-month campaign with a
research pick, a production edit, a building, fronts for every army, a raised division that wakes
the model and gets an army and a front, and no `human_ai` sent). Not done, and what the live run
needs, is in the comment on #161: the army click assumptions A1-A6 of `docs/armies.md`, a calibration
of the army cards and pace buttons, the front report over a real war, and a run of at least a game
year against `docs/results/` numbers.

## Presets: the old levels

`operational_control` and the campaign's `campaign_control` resolve to these maps, and a run
without `control` set behaves exactly as before: the same tool list (`tests/test_modular_control.py`
compares them action by action against `actions_for_mode`), the same prompt text (the
hand-written paragraphs in `agent/hybrid.describe` are kept for presets) and the same wake rules.

| Old setting | Map | Steer |
|---|---|---|
| `operational_control = llm`, or `campaign_control = llm` | every domain `llm` | off |
| `operational_control = ai` | every domain `llm`, `army = game`, `navy = off`, `air = off` | llm |
| `operational_control = game_ai`, or `campaign_control = game_ai` | every domain `game`, `politics = llm` | llm |

(`navy = off`, `air = off` in `ai`: that level only ever meant "the model does not order fleets and
wings".) `control = "llm"` (a bare preset name) is accepted as well.

A map you write differs from a preset in three ways: the prompt paragraph is generated from the
map (`agent/hybrid.describe_map`), the wake rules follow ownership, and the reflex layer is
replaced by the script controllers.

## Wakes and briefs follow ownership

With a map, a domain's wake rules fire only when the model owns the domain: "no national focus is
running" needs `focus = llm`, "research slot idle" needs `research = llm`, the empty-production
wake of an independent campaign needs `production = llm`. The front rules (a front losing ground,
going quiet, an encirclement, supply collapse, the capital threatened) need `army = llm` or the
steering. A war, an ally's capitulation and the player's own capitulation wake the model whatever
it owns, as they always did.

The model is told in the prompt which domains it owns, which are automated and by what, and which
the game's AI runs, and each brief carries a line per automated domain:

```
Automated research (research priority list): did start_research infantry_weapons1
Automated construction (construction rules): the queue is long enough
Automated army (scripted tactical controller): no map (set game_dir): the controller cannot be built
```

**No model at all.** A map with no `llm` domain and no steering needs no model
(`HarnessConfig.planner_active` is false): the loop never builds or calls a planner, in normal play
and in a campaign (`hoi4-harness play --control default=script,...` needs no provider and no
`--max-usd`). The scripts run every turn and the transcript has `auto` records and no `llm` ones.

## The script controllers

Each is a small class in `src/hoi4_harness/auto/` with
`decide(state, objective, memory) -> list[ActionCall]` and `feedback(call, result, state)`. A
script only sends actions of its own domain and only ones the adapter offers (the layer drops the
rest), through `HOI4Env.act_many(auto=True)`: same validation, playset check, confirmation gate and
per-turn cap as the model's calls. A choice the game refuses is left alone for 60 days. All of it
runs every turn, woken or not.

| Domain | What it does | Options (`auto_options`) |
|---|---|---|
| `research` | Walks a priority list (`auto/data/research.json`: `balanced`, `industry`, `land_mass_assault`, `land_superior_firepower`) and fills each idle slot with the first technology that is not already in a slot or started. The state does not say which technologies are researchable, so a refused one is skipped and the next tried. | `research_list`: a list name or a list of ids |
| `focus` | When no focus runs: the next unfinished focus of `focus_path`; else, with a playset (`game_dir`), the best keyword-scored focus whose prerequisites are done (industry and rearmament first); else nothing. No path ships: focus ids are per country and mod. | `focus_path` |
| `construction` | Keeps the queue at `queue_target` items in `state`: civilian factories while at peace and before `military_from`; from then, or at war, military factories until they are `military_ratio` of the civilian ones; infrastructure when a front reports low supply. Its `reflex` rule is the old reflex (an empty queue gets two civilian factories in the capital) and `Policy.reflex_actions` calls it. | `construction`: `state`, `queue_target`, `batch`, `military_from`, `military_ratio` |
| `production` | Hands idle military factories to the equipment furthest under its share (infantry 0.6, support 0.15, artillery 0.15, anti-air 0.1), the leftover to the biggest line. Its `reflex` rule is the old one (idle factories go to the biggest line). Over the console a line can only be opened, so topping one up is refused on a live headless run unless the production click points are recorded (`docs/live-session.md` section 14b), which edits the line instead. | `production.ratios` |
| `politics` | Runs a rule table (`when`: `min_pp`, `at_war`, `from`, `until`, `max_stability`; `do`: `hire_advisor` or `take_decision`; `once`). It ships **empty**: advisor and decision ids are per country and mod, the state does not list them, and the catalog has no law action. | `politics_rules` |
| `army` | A tactical controller from `tactics/registry.py` (`army_controller`: `scripted` by default, `learned`, `vanilla_like`) run through `tactics/bridge.py` `ControllerBridge` on the world the front picture stands for; its orders go out as `move_division` / `attack_province`, not repeated for `army_cooldown_days` (3). Needs the map (`game_dir`), the front picture and an adapter that offers the order tools; with any missing it does nothing and says why in the brief. | `army_cooldown_days` |
| `diplomacy` | **No script.** No rule is sound without knowing who the neighbours and rivals are, and a script that guessed would start or sour relations by accident, so `diplomacy = script` is refused. Use `llm`, `game` or `off`. | |
| `navy`, `air` | No script either. | |

**Reflexes became the script controllers.** The old deterministic layer (`Policy.reflex_actions`)
now calls `ConstructionScript.reflex` and `ProductionScript.reflex`, the same two rules to the
letter (`tests/test_modular_control.py` pins it). With a map, the reflex layer is off and the
scripts take its place for the domains you gave them.

### The tactical controller registry

`src/hoi4_harness/tactics/registry.py` is a name to class table, one line per controller:

```python
REGISTRY = {"scripted": ScriptedController, "learned": LearnedController,
            "vanilla_like": VanillaLikeController}
```

A new controller (the battle-plan front planner, say) is one more line there and then
`army_controller = "planner"` selects it; `ControlMap` validation and `hoi4-harness doctor` pick it
up from `registry.names()`. A controller is any class with `name` and `act(view) -> list[Order]`.

## Campaigns

A campaign accepts any map. `campaign.setup.apply_defaults` reads it: `human_ai` is sent iff a
non-army domain is `game`, and `campaign_control` follows (`game_ai` with `human_ai`, `llm` — the
independent kind, with the mouse tools — without). The safety checks stay: an independent-kind
campaign refuses to start when the state line already reads `ai=1`, and says which domains the map
gives to the model or a script. A contested map never gets that far. See
[campaign.md](campaign.md).

## Examples

```bash
# the model plays only the focus; scripts do research and the army; nothing else is run
hoi4-harness play --campaign --profile profiles/campaign-fake.json     --control focus=llm,research=script,army=script,default=off

# the game's AI runs the country; the model steers it and takes decisions (the old game_ai)
hoi4-harness play --campaign --profile profiles/campaign-ger-world.json     --control default=game,politics=llm

# a no-model run: scripts everywhere they exist, nothing else
hoi4-harness play --adapter mock --no-llm \
    --control default=script,diplomacy=off,navy=off,air=off,army=off

# armies go to the game's AI one by one; the model plays the rest and steers (steer unverified)
hoi4-harness play --campaign --profile profiles/campaign-ger-world.json     --control army=game,default=llm
```

## Not verified

- Everything above was run on the simulators only. The map, the contested rule and the tool and
  prompt narrowing are unit-tested; the script controllers are tested on plain states, the mock,
  the fake game with the mouse path and the fake army.
- The contested rule rests on the repo's own docs about what `human_ai` does (cited in the table);
  none of it was checked against the live game, and the "complements" and "unknown" entries are
  exactly where a live run could prove the table wrong.
- Whether `ai_strategy` affects a human country's delegated armies (steering with `army: game`
  only).
- The shipped research ids and equipment ids are common vanilla ones, not checked against a
  playset; the production top-up of an existing line is refused over the console; the army
  script's orders inherit every caveat of [unit-orders.md](unit-orders.md) and the sim-to-real gap
  of [tactics.md](tactics.md).
- Whether any mix plays better than the presets: no run was made, with or without a model.
