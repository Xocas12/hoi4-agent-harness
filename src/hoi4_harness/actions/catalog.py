"""The action catalog: the agent's entire vocabulary.

Design rules, learned the hard way from other game harnesses:

* **Few, coarse verbs.** One ``queue_construction`` beats twelve building-specific
  tools. Every extra tool is prompt tokens on every single request and one more
  thing for the model to confuse.
* **Declarative, not gestural.** Actions say what the world should look like
  ("5 factories on this line"), never "click here". Gestures are the adapter's
  problem.
* **Cheap to validate.** Enums over free text wherever the game has a fixed set,
  so a bad call is rejected locally instead of costing a round trip.
* **`note` is free.** Give the model somewhere to put reasoning that is not an
  action, or it will smuggle it into arguments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    category: str = "general"
    #: Irreversible or war-starting actions. The loop refuses these in dry-run
    #: mode and surfaces them for human confirmation otherwise.
    requires_confirmation: bool = False
    aliases: tuple[str, ...] = field(default=())


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


BUILDINGS = [
    "civilian_factory",
    "military_factory",
    "dockyard",
    "infrastructure",
    "air_base",
    "anti_air",
    "radar",
    "fortification",
    "synthetic_refinery",
]

PROVINCE_BUILDINGS = ["fortification", "coastal_fortification", "naval_base", "supply_hub"]
ARMY_ORDERS = ["hold", "front_line", "offensive", "garrison", "naval_invasion", "fall_back"]
AIR_MISSIONS = ["air_superiority", "close_air_support", "strategic_bombing", "port_strike",
                "naval_bombing", "logistical_strike", "interception"]
NAVAL_MISSIONS = ["patrol", "strike_force", "convoy_escort", "convoy_raiding", "naval_invasion_support",
                  "mine_laying", "mine_sweeping", "hold"]
DIPLOMACY = ["improve_relations", "guarantee", "justify_war_goal", "join_faction", "invite_to_faction",
             "non_aggression_pact", "lend_lease", "send_volunteers", "send_attache", "declare_war"]


#: Tools whose only executor is the simulator (``adapters/mock.py``): a live game has no route for
#: them (no effect does them faithfully, no UI script is recorded), so a live adapter never offers
#: them, and their descriptions say so. Kept in one place so the docs and a test can check it.
MOCK_ONLY = frozenset({"deploy_divisions", "set_trade", "set_army_order", "set_air_mission",
                       "set_naval_mission", "diplomacy"})
MOCK_ONLY_NOTE = (" Simulator only: no faithful effect does this and no UI path is recorded, so a "
                  "live game does not offer it unless a UI script for it has been recorded.")

ACTIONS: list[ActionSpec] = [
    ActionSpec(
        name="set_national_focus",
        category="politics",
        description=(
            "Start a national focus. Only one runs at a time; starting a new one is "
            "impossible until the current focus completes. A focus that needs a DLC which is not "
            "active is refused (\"needs the <DLC> DLC\")."
        ),
        parameters=_schema(
            {"focus_id": {"type": "string", "description": "Focus id, e.g. SWE_expand_the_army."}},
            ["focus_id"],
        ),
    ),
    ActionSpec(
        name="start_research",
        category="research",
        description="Put a technology into a free research slot.",
        parameters=_schema(
            {"technology": {"type": "string", "description": "Technology id."}},
            ["technology"],
        ),
    ),
    ActionSpec(
        name="queue_construction",
        category="economy",
        description="Add buildings to the construction queue in a named state.",
        parameters=_schema(
            {
                "building": {"type": "string", "enum": BUILDINGS},
                "state": {"type": "string", "description": "State name or id."},
                "count": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            ["building", "state"],
        ),
    ),
    ActionSpec(
        name="set_production",
        category="economy",
        description=(
            "Set how many military factories one equipment line holds. If a line for that "
            "equipment already EXISTS it is edited in place (factories up or down, 0 removes it); "
            "a new line is opened only when none exists. It never makes a second line for the "
            "same equipment. Total assigned may not exceed available factories. Editing an "
            "existing line is done with the mouse (the production screen), so it is only "
            "possible where that is set up; the result says when it is not."
        ),
        parameters=_schema(
            {
                "equipment": {"type": "string"},
                "factories": {"type": "integer", "minimum": 0, "maximum": 200},
            },
            ["equipment", "factories"],
        ),
    ),
    ActionSpec(
        name="change_production_equipment",
        category="economy",
        description=(
            "Change the equipment of a production line that exists (for example to a newer "
            "variant): the line keeps its factories and is NOT duplicated. Refused when "
            "to_equipment already has a line (edit that one with set_production). Done with the "
            "mouse on the production screen."
        ),
        parameters=_schema(
            {
                "from_equipment": {"type": "string", "description": "Equipment of the existing line."},
                "to_equipment": {"type": "string", "description": "Equipment it should make."},
            },
            ["from_equipment", "to_equipment"],
        ),
    ),
    ActionSpec(
        name="design_division_template",
        category="army",
        description=(
            "Create a division template, as the template designer does: a new name, battalions "
            "(each type in its own column, five deep) and up to five support companies, every "
            "type one your technologies have unlocked. Creating costs nothing. Editing a template "
            "that exists is refused: the designer charges army experience for it and no script "
            "charges that. The name is letters, digits, underscore and hyphen (no spaces). "
            "Training the divisions is done on the deployment screen, which no tool reaches."
        ),
        parameters=_schema(
            {
                "name": {"type": "string"},
                "battalions": {
                    "type": "array",
                    "items": _schema(
                        {
                            "type": {"type": "string"},
                            "count": {"type": "integer", "minimum": 1, "maximum": 25},
                        },
                        ["type", "count"],
                    ),
                },
                "support": {"type": "array", "items": {"type": "string"}},
            },
            ["name", "battalions"],
        ),
    ),
    ActionSpec(
        name="design_equipment",
        category="economy",
        description=(
            "Design a tank, ship or plane variant, as the designer screen does (tank: No Step "
            "Back, ship: Man the Guns, plane: By Blood Alone; refused without that DLC). kind, "
            "base (a chassis type such as medium_tank_chassis_2, ship_hull_light_3, "
            "small_plane_airframe_2), a name (letters, digits, space, underscore, hyphen), the "
            "modules to change as {slot: module id} (a slot you leave out keeps the parent's "
            "module; \"empty\" clears it) and upgrades as {upgrade: level} (levels only go up). "
            "parent is an existing design of the same chassis, by version number or name "
            "(list_designs); without it the design starts from the chassis' default design. Every "
            "module is checked against the slot (the chassis' categories, plus what a turret "
            "opens), the count limits and the technology that unlocks it, and the design must "
            "fill every required slot. It costs army experience (tanks), navy experience (ships) "
            "or air experience (planes): the add / replace / convert / remove price of each "
            "changed slot from the game's defines plus the new module's xp_cost, and the upgrade "
            "levels; the result states the cost and that it is an upper bound. Nothing is spent "
            "if the game's own test (the tech, the DLC, enough experience) fails. obsolete_older "
            "marks the older designs of the same family obsolete, so the game's AI builds the new "
            "one. describe_design_option reads the slots, modules, stats and costs; list_designs "
            "shows what you have."
        ),
        parameters=_schema(
            {
                "kind": {"type": "string", "enum": ["tank", "ship", "plane"]},
                "base": {"type": "string", "description": "Chassis type id."},
                "name": {"type": "string", "maxLength": 40},
                "modules": {"type": "object", "additionalProperties": {"type": "string"},
                            "description": "Slot id -> module id (or \"empty\")."},
                "upgrades": {"type": "object", "additionalProperties": {"type": "integer"},
                             "description": "Upgrade id -> target level."},
                "parent": {"type": "string", "description": "Version number or name of a design "
                           "of the same chassis."},
                "obsolete_older": {"type": "boolean"},
            },
            ["kind", "base", "name"],
        ),
    ),
    ActionSpec(
        name="deploy_divisions",
        category="army",
        description="Send divisions of a template into training/deployment.",
        parameters=_schema(
            {
                "template": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 100},
                "location": {"type": "string"},
            },
            ["template", "count"],
        ),
    ),
    ActionSpec(
        name="set_army_order",
        category="army",
        description="Give an army a standing order along a front or region.",
        requires_confirmation=True,
        parameters=_schema(
            {
                "army": {"type": "string"},
                "order": {"type": "string", "enum": ARMY_ORDERS},
                "target": {"type": "string", "description": "Front, region or country."},
            },
            ["army", "order"],
        ),
    ),
    ActionSpec(
        name="set_air_mission",
        category="air",
        description="Assign an air wing a mission over a strategic region.",
        parameters=_schema(
            {
                "wing": {"type": "string"},
                "region": {"type": "string"},
                "mission": {"type": "string", "enum": AIR_MISSIONS},
            },
            ["wing", "region", "mission"],
        ),
    ),
    ActionSpec(
        name="set_naval_mission",
        category="navy",
        description="Assign a fleet a mission in a naval region.",
        parameters=_schema(
            {
                "fleet": {"type": "string"},
                "region": {"type": "string"},
                "mission": {"type": "string", "enum": NAVAL_MISSIONS},
            },
            ["fleet", "region", "mission"],
        ),
    ),
]

ACTIONS += [
    ActionSpec(
        name="queue_province_building",
        category="economy",
        description=(
            "Queue a building that sits in one province: fortification (land fort), "
            "coastal_fortification, naval_base (a port province) or supply_hub. Province ids are "
            "on inspect_world view=state. It queues construction that your civilian factories "
            "then build, under the game's own slot rule for that province (what is already "
            "queued counts); count is the levels (1 to 10). Railways are not offered: they run "
            "between two provinces on a path the screen picks."
        ),
        parameters=_schema(
            {"building": {"type": "string", "enum": PROVINCE_BUILDINGS},
             "province": {"type": "integer", "minimum": 1, "description": "Province id."},
             "count": {"type": "integer", "minimum": 1, "maximum": 10}},
            ["building", "province"],
        ),
    ),
    ActionSpec(
        name="hire_advisor",
        category="politics",
        description=("Spend political power on an advisor, minister or designer. An advisor that needs a "
                     "DLC which is not active is refused (\"needs the <DLC> DLC\")."),
        parameters=_schema(
            {"slot": {"type": "string"}, "advisor_id": {"type": "string"}},
            ["advisor_id"],
        ),
    ),
    ActionSpec(
        name="take_decision",
        category="politics",
        description=(
            "Take a decision from the decisions view, as the player would: the game's own "
            "conditions and its political power cost are checked first. A targeted decision "
            "also needs a target (a country tag or state id from its targets). Missions are "
            "activated by the game and cannot be taken. A decision that needs a DLC which is not "
            "active is refused (\"needs the <DLC> DLC\"). Find ids with list_decisions."
        ),
        parameters=_schema(
            {"decision_id": {"type": "string"},
             "target": {"type": "string",
                        "description": "Country tag or state id, for a targeted decision."}},
            ["decision_id"],
        ),
    ),
    ActionSpec(
        name="click_gui_button",
        category="politics",
        description=(
            "Press a button in a custom window (a mod's or the game's scripted GUI), as the "
            "player would: the window's and the button's own conditions are checked by the game "
            "first, and the click runs in your country's scope. Find windows and buttons with "
            "list_gui_buttons; a button marked cannot_press says why it cannot be pressed."
        ),
        parameters=_schema(
            {"gui": {"type": "string", "description": "The window id (list_gui_buttons' window)."},
             "button": {"type": "string",
                        "description": "The button id, e.g. my_button_click."}},
            ["gui", "button"],
        ),
    ),
    ActionSpec(
        name="list_gui_buttons",
        category="query",
        description=(
            "Read-only: the buttons in custom windows your country can press, by window, with a "
            "label, when each can be pressed, what the game's own AI weighs it at, and why the "
            "harness cannot press the others. Window-switching buttons are left out unless you "
            "name a window. The result comes back on your next call, so do not send "
            "advance_time with it."
        ),
        parameters=_schema(
            {"gui": {"type": "string"}, "query": {"type": "string"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 80}},
            [],
        ),
    ),
    ActionSpec(
        name="set_front_order",
        category="army",
        description=(
            "Tell your armies what to do on the stretch of front at one state, and the game's AI "
            "carries it out: push (all-out attack, more divisions), attack (a steady offensive), "
            "probe (careful, small attacks), hold (no attacks, more defenders), reserve (keep a "
            "tenth of the army stationed in that state, behind the line), invade (aim the naval "
            "invasion planner at that coastal state), clear (drop the order). "
            "push and attack also aim the AI's concentrated offensives at that state. It stands "
            "until changed. Pick states from the Forces and map section or "
            "inspect_world view=borders; an order on an enemy state facing your line works best."
        ),
        parameters=_schema(
            {"state": {"type": "string", "description": "State id (or name)."},
             "order": {"type": "string",
                       "enum": ["push", "attack", "probe", "hold", "reserve", "invade", "clear"]}},
            ["state", "order"],
        ),
    ),
    ActionSpec(
        name="set_region_order",
        category="army",
        description=(
            "Tell your navy or air force where to fight, by strategic region (sea or land area), "
            "and the game's AI carries it out: dominate (contest the sea region), raid_convoys "
            "(hunt convoys there), avoid (keep the fleet out), home_base (base the strike force "
            "there), air_superiority (send fighters to win the air over the region), clear. It "
            "stands until changed. Regions are named in the brief and inspect_world."
        ),
        parameters=_schema(
            {"region": {"type": "string", "description": "Strategic region id or name."},
             "order": {"type": "string", "enum": ["dominate", "raid_convoys", "avoid",
                                                  "home_base", "air_superiority", "clear"]}},
            ["region", "order"],
        ),
    ),
    ActionSpec(
        name="inspect_world",
        category="query",
        description=(
            "Read-only: everything a player of your country sees, from the newest save. "
            "view=divisions lists every one of your divisions, worst first, with organisation, "
            "strength, supply, days out of supply, entrenchment, equipment fill and whether it is "
            "encircled or in a battle; view=battles lists the battles you are in and the ones you "
            "can see (sides, divisions, tactics, damage, an estimate of who is winning); "
            "view=fronts is the report per enemy front: how the line moved since the last save and "
            "over about 90 days, the battles over the period (won and lost by side, piecemeal "
            "attacks), the force in contact, and why a front is stuck and what addresses it; "
            "view=state id=<state id> shows one "
            "state, both sides; view=country id=<TAG> shows what your intelligence knows of a country "
            "(army, factories, production: exact, estimated or unknown by your intel); view=borders "
            "lists the states across your borders. The other panels: view=faction (members, leader, "
            "goals, rules; id=TAG for another country's), intel (agency upgrades, operatives and "
            "missions, network strength per country, your intel levels), projects (special projects, "
            "facilities, scientists), navy and air (your fleets and wings, or id=TAG as your intel "
            "allows), politics (parties, laws, spirits, advisors), economy (fuel, manpower, "
            "stockpile, trade, resource income, equipment against need), diplomacy (wars with their "
            "score, guarantees, puppets, lend-lease, world tension, volunteers), "
            "research (slots, doctrines, decisions, focus), occupation (resistance, victory points), "
            "supply (hubs, railways, trucks, divisions short of supply), commanders (generals, "
            "marshals and admirals with skills and traits). A panel whose feature the active DLCs "
            "leave out (projects, the MIOs in economy) says it does not exist and which DLC it "
            "needs. The result comes back on your next call."
        ),
        parameters=_schema(
            {"view": {"type": "string", "enum": [
                "divisions", "battles", "fronts", "state", "country", "borders", "faction", "intel",
                "projects",
                "navy",
                "air", "politics", "economy", "diplomacy", "research", "occupation", "supply",
                "commanders"]},
             "id": {"type": "string", "description": "State id for view=state; a country tag for "
                    "country, faction, navy, air, politics, economy and diplomacy."},
             "limit": {"type": "integer", "minimum": 1, "maximum": 120}},
            ["view"],
        ),
    ),
    ActionSpec(
        name="list_decisions",
        category="query",
        description=(
            "Read-only: the decisions your country can have in this playset, with category, "
            "cost and duration. Filter by category id or a word in the id or name; "
            "nothing happens in the game. The result comes back on your next call, so do "
            "not send advance_time with it."
        ),
        parameters=_schema(
            {"category": {"type": "string"}, "query": {"type": "string"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 60}},
            [],
        ),
    ),
    ActionSpec(
        name="list_designs",
        category="query",
        description=(
            "Read-only: your own tank, ship or plane designs from the newest save: name, "
            "chassis type, version (the number design_equipment takes as parent), modules by "
            "slot, upgrade levels, whether the design is obsolete and which one it was made "
            "from. kind=tank|ship|plane. Says it does not exist when the designer's DLC is off. "
            "The result comes back on your next call."
        ),
        parameters=_schema({"kind": {"type": "string", "enum": ["tank", "ship", "plane"]}}, ["kind"]),
    ),
    ActionSpec(
        name="describe_design_option",
        category="query",
        description=(
            "Read-only: what a designer offers, from the game's files. kind=tank|ship|plane "
            "alone: the chassis (year, unlocking technology, slots), the module categories and "
            "the upgrades. With id: one chassis (slots with their categories and defaults, "
            "count limits, base stats, upgrades), one module (stats, resources, xp_cost, what it "
            "converts from, the technology that unlocks it, the chassis it fits), one module "
            "category (its modules) or one upgrade (levels, cost, technologies). Nothing happens "
            "in the game. The result comes back on your next call."
        ),
        parameters=_schema(
            {"kind": {"type": "string", "enum": ["tank", "ship", "plane"]},
             "id": {"type": "string", "description": "A chassis, module, module category or upgrade."}},
            ["kind"]),
    ),
    ActionSpec(
        name="describe_action",
        category="query",
        description=(
            "Read-only: everything the game files say about one id -- a decision, a focus of "
            "your tree, a technology or idea this playset adds -- including its conditions, cost "
            "and whether the harness can do it. Nothing happens in the game."
        ),
        parameters=_schema({"id": {"type": "string"}}, ["id"]),
    ),
    ActionSpec(
        name="diplomacy",
        category="diplomacy",
        description=(
            "One diplomatic action toward one country. 'declare_war' and "
            "'justify_war_goal' are irreversible and gated behind confirmation."
        ),
        requires_confirmation=True,
        parameters=_schema(
            {
                "action": {"type": "string", "enum": DIPLOMACY},
                "target": {"type": "string", "description": "Country tag, e.g. FRA."},
                "detail": {"type": "string", "description": "State claimed, equipment lend-leased."},
            },
            ["action", "target"],
        ),
    ),
    ActionSpec(
        name="set_trade",
        category="economy",
        description="Trade civilian factories for a resource from another country.",
        parameters=_schema(
            {
                "resource": {
                    "type": "string",
                    "enum": ["oil", "rubber", "steel", "aluminium", "tungsten", "chromium"],
                },
                "from_country": {"type": "string"},
                "factories": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            ["resource", "from_country", "factories"],
        ),
    ),
    ActionSpec(
        name="set_game_speed",
        category="clock",
        description="Set game speed. 0 pauses. The harness normally owns this.",
        parameters=_schema(
            {"speed": {"type": "integer", "minimum": 0, "maximum": 5}}, ["speed"]
        ),
    ),
    ActionSpec(
        name="advance_time",
        category="clock",
        description=(
            "End the turn and run the game forward. Call this when there is nothing "
            "worth doing yet; it is the cheapest action available and the correct "
            "default in peacetime. Do not send it in the same answer as a read "
            "(inspect_world, list_decisions, describe_action, read_event, list_designs): the "
            "turn would end before you saw the result, so the harness skips advance_time and "
            "returns the read first, which costs a whole extra turn."
        ),
        parameters=_schema(
            {"days": {"type": "integer", "minimum": 1, "maximum": 90},
             "reason": {"type": "string"}},
            ["days"],
        ),
    ),
    ActionSpec(
        name="send_message",
        category="memory",
        description=(
            "Say something in the single-player chat: to the person playing (person), to every "
            "country's strategist and the person (global), or to one LLM country (its tag). Free, "
            "and nothing happens in the game: talk binds nobody, and what you do is what counts. "
            "Answer a message addressed to you here."
        ),
        parameters=_schema(
            {"to": {"type": "string", "description": "person, global, or a country tag."},
             "text": {"type": "string", "maxLength": 800}},
            ["to", "text"],
        ),
    ),
    ActionSpec(
        name="note",
        category="memory",
        description=(
            "Write a line to the campaign journal. Free, never fails, and carried "
            "into later turns. Use it for intent that outlives this turn: what you "
            "are building toward and what would change your mind."
        ),
        parameters=_schema({"text": {"type": "string"}}, ["text"]),
    ),
]

#: Read-only tools the harness answers from the playset's files; the game is not touched.
QUERY_ACTIONS = frozenset({"list_decisions", "describe_action", "list_gui_buttons", "inspect_world",
                           "read_event", "list_designs", "describe_design_option"})
#: The queries answered from the playset's files (mod context); inspect_world reads the save.
PLAYSET_QUERIES = frozenset({"list_decisions", "describe_action", "list_gui_buttons"})
#: ``read_event`` is answered from the screen (adapters/event_choice.py) and the playset's events.

BY_NAME: dict[str, ActionSpec] = {spec.name: spec for spec in ACTIONS}


def get(name: str) -> ActionSpec | None:
    return BY_NAME.get(name)


def names() -> list[str]:
    return [spec.name for spec in ACTIONS]

# --- hybrid control ---------------------------------------------------------
# The game already ships a competent operational AI: it moves divisions along a
# front, reinforces, and reacts to breakthroughs faster and cheaper than any
# model will. These actions let the agent hand that layer over and command at
# the level it is actually good at -- who to fight, where it matters, what
# posture to hold -- instead of pretending to be a corps commander.

AI_DIRECTIVES = ["invade", "protect", "contain", "befriend", "antagonize", "ignore",
                 "prepare_war", "declare_war", "volunteers", "lend_lease", "ally",
                 "spy_on", "sabotage", "steal_tech"]

ACTIONS += [
    ActionSpec(
        name="delegate_army_to_ai",
        category="strategy",
        description=(
            "Hand an army over to the game's own AI, or take it back. A delegated "
            "army executes fronts, reinforcement and local decisions on its own; "
            "you keep setting its posture and objectives."
        ),
        parameters=_schema(
            {
                "army": {"type": "string", "description": "Army name, or 'all'."},
                "delegate": {"type": "boolean"},
            },
            ["army", "delegate"],
        ),
    ),
    ActionSpec(
        name="set_ai_posture",
        category="strategy",
        description=(
            "Set how aggressively delegated forces behave. Defensive holds and "
            "reinforces; offensive presses attacks; balanced is the game default. "
            "With a theater (the country a front faces), only the fronts against "
            "that country change and it overrides the global posture there: hold "
            "against one enemy while pressing against another."
        ),
        parameters=_schema(
            {
                "posture": {"type": "string", "enum": ["defensive", "balanced", "offensive"]},
                "theater": {
                    "type": "string",
                    "description": (
                        "Optional: the country tag a front faces, e.g. SOV. "
                        "Omit for every front."
                    ),
                },
            },
            ["posture"],
        ),
    ),
    ActionSpec(
        name="set_ai_directive",
        category="strategy",
        description=(
            "Give the AI a standing strategic intent toward a country: who to "
            "invade, who to protect, who to contain, who to court. Weight is "
            "relative priority, not a promise. Diplomacy is the AI's to carry out, with its "
            "own acceptance and timing: prepare_war (justify and mass troops), declare_war "
            "(declare when it can), volunteers (send volunteers to them), lend_lease (send "
            "them lend-lease), ally (seek an alliance). Intelligence: spy_on (build an "
            "intel network there), sabotage (sabotage its industry), steal_tech."
        ),
        requires_confirmation=True,
        parameters=_schema(
            {
                "directive": {"type": "string", "enum": AI_DIRECTIVES},
                "target": {"type": "string", "description": "Country tag, e.g. GER."},
                "weight": {"type": "integer", "minimum": 0, "maximum": 500},
            },
            ["directive", "target"],
        ),
    ),
    ActionSpec(
        name="clear_ai_directives",
        category="strategy",
        description="Drop every standing directive and return the AI to default behaviour.",
        parameters=_schema({}, []),
    ),
]

BY_NAME = {spec.name: spec for spec in ACTIONS}

# --- division orders (#85) --------------------------------------------------
# By click, because no script reaches a division's order (docs/unit-orders.md).
# States are the brief's coordinates (the front picture counts divisions per
# state); a province id narrows an order to one stack or one target.

_STATE_ID = {"type": "integer", "minimum": 1}

ACTIONS += [
    ActionSpec(
        name="move_division",
        category="army",
        description=(
            "March the player's divisions from one state to another over land, "
            "by selecting them on the map and right-clicking the destination. "
            "Only where the front picture shows your divisions; a destination "
            "with enemy divisions is an attack_province. It takes days and "
            "cannot be confirmed at once: the brief's Order lines report it."
        ),
        parameters=_schema(
            {
                "from_state": {**_STATE_ID, "description": "State holding the divisions (front picture)."},
                "to_state": {**_STATE_ID, "description": "State to march to."},
                "from_province": {"type": "integer", "minimum": 1, "description": (
                    "Optional: click this province's stack instead of boxing the whole state.")},
                "to_province": {"type": "integer", "minimum": 1, "description": (
                    "Optional: a province of to_state; default is its middle.")},
                "via": {"type": "array", "maxItems": 4, "items": _STATE_ID, "description": (
                    "Optional waypoint states, visited in order before to_state is reached.")},
            },
            ["from_state", "to_state"],
        ),
    ),
    ActionSpec(
        name="attack_province",
        category="army",
        description=(
            "Attack an enemy-held, adjacent state from the state your divisions "
            "stand in. Same click path as move_division; refused when the target "
            "does not border the attacker or shows nothing hostile."
        ),
        parameters=_schema(
            {
                "from_state": {**_STATE_ID, "description": "State holding the attackers (front picture)."},
                "to_state": {**_STATE_ID, "description": "Adjacent enemy-held state to attack."},
                "from_province": {"type": "integer", "minimum": 1, "description": (
                    "Optional: attack from this province's stack only.")},
                "to_province": {"type": "integer", "minimum": 1, "description": (
                    "Optional: the province of to_state to attack; it must border the attacker.")},
            },
            ["from_state", "to_state"],
        ),
    ),
]


# --- battle plans (#85, stage 4) ----------------------------------------------
# NOT live-verified: the click path is drawn from interface/battleplantools.gui and
# recorded points (adapters/battle_plans.py, docs/unit-orders.md section 7).

ACTIONS += [
    ActionSpec(
        name="draw_front_line",
        category="army",
        description=(
            "Draw a front line with the battle-plan tool: the player's own states, in "
            "order, that the army holds against one enemy. Needs a recorded army point "
            "(army.<name>); refused when a state is not the player's in the front "
            "picture, the states do not form a chain, or the enemy borders none of them. "
            "Unverified in the game: the result never says the line exists."
        ),
        parameters=_schema(
            {
                "army": {"type": "string", "description": "A recorded army name (army.<name> point)."},
                "enemy": {"type": "string", "description": "Tag of the country the line faces."},
                "states": {"type": "array", "minItems": 1, "maxItems": 8, "items": _STATE_ID,
                           "description": "Own states along the line, in order."},
            },
            ["army", "enemy", "states"],
        ),
    ),
    ActionSpec(
        name="draw_offensive",
        category="army",
        description=(
            "Draw an offensive arrow with the battle-plan tool, from an own state across "
            "the border into an enemy one (optionally through waypoint states). Same "
            "refusals as attack_province, and the army needs a recorded point. Unverified "
            "in the game."
        ),
        parameters=_schema(
            {
                "army": {"type": "string", "description": "A recorded army name (army.<name> point)."},
                "from_state": {**_STATE_ID, "description": "Own state the arrow starts in."},
                "target_state": {**_STATE_ID, "description": "Hostile state the arrow ends in."},
                "via": {"type": "array", "maxItems": 4, "items": _STATE_ID,
                        "description": "Optional waypoint states."},
            },
            ["army", "from_state", "target_state"],
        ),
    ),
    ActionSpec(
        name="assign_battle_plan",
        category="army",
        description=(
            "Assign an army to the battle plan just drawn: select it by its recorded point, "
            "press the assign-units button (a recorded point: the interface file gives it no "
            "shortcut). Unverified in the game."
        ),
        parameters=_schema(
            {"army": {"type": "string", "description": "A recorded army name (army.<name> point)."}},
            ["army"],
        ),
    ),
]

BY_NAME = {spec.name: spec for spec in ACTIONS}


# --- politics the effects can do faithfully, and steering where only steering is -------------
# Each is guarded by the game's own conditions and charged what the screen charges (docs/interaction.md).

DIPLOMACY_ACTS = [*DIPLOMACY, "military_access", "leave_faction"]
STEER_PRODUCTION_KINDS = ["equipment", "units", "building", "garrison", "min_factories", "variant",
                          "military_share", "dockyard_share", "air_balance", "surplus"]
STEER_INTEL_KINDS = ["branch", "factories", "mission", "spymaster"]
STEER_SPENDING_KINDS = ["land_xp", "air_xp", "navy_xp", "pp", "pp_amount"]
STEER_MARKET_KINDS = ["buy", "buy_from", "sell", "trade_desire", "spend"]
STEER_ARMY_KINDS = ["template", "roles", "divisions", "build", "fuel", "trucks", "trains"]
STEER_LEVELS = ["less", "more", "much_more", "clear"]

ACTIONS += [
    ActionSpec(
        name="change_law",
        category="politics",
        description=(
            "Change a law (economy, trade or conscription) to the one named, as the politics "
            "screen does: the law's own allowed, visible and available conditions are checked, "
            "the law you replace must be allowed to go, and its political power cost is charged. "
            "Refused with the reason when the law cannot be taken now, or needs a DLC that is not "
            "active. Find laws and their costs "
            "with inspect_world view=politics or describe_action."
        ),
        parameters=_schema(
            {"law": {"type": "string", "description": "The law's idea id, e.g. war_economy."}},
            ["law"],
        ),
    ),
    ActionSpec(
        name="fire_advisor",
        category="politics",
        description=(
            "Dismiss an advisor you hired, as the politics screen does: only an advisor who is "
            "hired and whose role can be fired (the files' can_be_fired) goes. It costs nothing, "
            "and the slot is free for hire_advisor."
        ),
        parameters=_schema(
            {"advisor_id": {"type": "string", "description": "The character id or idea token."},
             "slot": {"type": "string", "description": "Only when the character has several roles."}},
            ["advisor_id"],
        ),
    ),
    ActionSpec(
        name="set_occupation_law",
        category="politics",
        description=(
            "Set the occupation law you apply to occupied land: the law's own visible and "
            "available conditions are checked. With occupied (a country tag) it sets the "
            "override for that country's land only; with state (a state id you control and do not "
            "own) the override for that one state; with neither, the default for all. Laws that "
            "depend on which country is occupied (some DLC laws) are refused with the reason, and "
            "so is a law that needs a DLC which is not active (\"needs the <DLC> DLC\")."
        ),
        parameters=_schema(
            {"law": {"type": "string", "description": "Occupation law id, e.g. martial_law_occupation."},
             "occupied": {"type": "string",
                          "description": "Optional: the occupied country's tag (an override)."},
             "state": {"type": "string",
                       "description": "Optional: one occupied state's id (an override)."}},
            ["law"],
        ),
    ),
    ActionSpec(
        name="diplomacy_act",
        category="politics",
        description=(
            "A diplomatic act that needs nobody's answer, done as the diplomacy screen does it: "
            "guarantee (guarantee a country's independence) and leave_faction (a member, not the "
            "leader, leaves). Everything that needs the other side to accept (military access, "
            "faction invitations, non-aggression pacts, lend-lease, volunteers) or takes time "
            "(justify_war_goal, declare_war, improve_relations) is refused with the reason and the "
            "faithful route: the AI steers it with set_ai_directive (prepare_war, declare_war, "
            "volunteers, lend_lease, ally, befriend), or the diplomacy action where the mouse plays."
        ),
        parameters=_schema(
            {"action": {"type": "string", "enum": DIPLOMACY_ACTS},
             "target": {"type": "string", "description": "Country tag; not needed for leave_faction."}},
            ["action"],
        ),
    ),
    ActionSpec(
        name="steer_production",
        category="steer",
        description=(
            "Tell the game's AI what to build, as a standing preference it weighs against its own "
            "plan: kind=equipment (id: an equipment category such as fighter, armor, infantry: "
            "its share of the factories), units (id: a ship or plane type: how much of it against "
            "the others), building (id: industrial_complex, arms_factory, dockyard ...: what it "
            "builds next), garrison (no id: how many divisions it keeps garrisoning occupied "
            "land), min_factories (id: an equipment category: the fewest factories it puts on it, "
            "more = 3, much_more = 10), military_share (no id: military factories against civilian "
            "ones), dockyard_share (no id: dockyards against military factories), air_balance (no "
            "id: how much of its factory share aircraft get), surplus (no id: how big a stockpile "
            "surplus it keeps before shifting factories off), variant (id: a chassis family such as "
            "medium_tank_chassis or small_plane_airframe: how much of that family the AI builds, "
            "so it favours the designs you made; equipment_variant_production_factor). Level "
            "less/more/much_more or clear "
            "(drop it); a kind has only the levels it makes sense at, and the result says which. "
            "It stands until changed."
        ),
        parameters=_schema(
            {"kind": {"type": "string", "enum": STEER_PRODUCTION_KINDS},
             "id": {"type": "string", "description": "The category, type or building; omit for garrison."},
             "level": {"type": "string", "enum": STEER_LEVELS}},
            ["kind", "level"],
        ),
    ),
    ActionSpec(
        name="steer_intelligence",
        category="steer",
        description=(
            "Tell the game's AI how to run the intelligence agency, as a standing preference: "
            "kind=branch (id: branch_intelligence, branch_defense, branch_operation, "
            "branch_operative or branch_crypto: which upgrades it wants next), factories (no id: "
            "how many factories it spends on the agency), mission (id: build_intel_network, "
            "counter_intelligence, quiet_network, root_out_resistance or control_trade: what its "
            "operatives do when no country is named), spymaster (no id: how much it wants to "
            "become spymaster when it leads a faction). Against one country use set_ai_directive "
            "spy_on, sabotage or steal_tech. Level less/more/much_more or clear."
        ),
        parameters=_schema(
            {"kind": {"type": "string", "enum": STEER_INTEL_KINDS},
             "id": {"type": "string", "description": "The branch or mission; omit for factories."},
             "level": {"type": "string", "enum": STEER_LEVELS}},
            ["kind", "level"],
        ),
    ),
    ActionSpec(
        name="steer_navy",
        category="steer",
        description=(
            "Tell the game's AI where its navy should fight, as a standing preference: "
            "kind=dominance (id: a sea region id: how much sea control it seeks there, "
            "less/more/much_more) or raiding (id: a country tag: how hard its raiders hunt that "
            "country's convoys). It does not move a ship: the AI still needs the fleet. Level "
            "clear drops it. For orders by sea region (dominate, raid convoys in a region, avoid, "
            "home base) use set_region_order; use this for raiding a country's convoys anywhere, "
            "or a graded dominance weight."
        ),
        parameters=_schema(
            {"kind": {"type": "string", "enum": ["dominance", "raiding"]},
             "id": {"type": "string", "description": "Sea region id, or a country tag for raiding."},
             "level": {"type": "string", "enum": STEER_LEVELS}},
            ["kind", "id", "level"],
        ),
    ),
    ActionSpec(
        name="steer_air",
        category="steer",
        description=(
            "Tell the game's AI which strategic region's air war to weigh most (more / much_more, "
            "or clear to drop it): its wings are sent where the weight is. It does not fly a "
            "wing: the AI still needs the planes and the bases. set_region_order "
            "order=air_superiority is the plain standing order for the same thing; use this one "
            "for a graded weight."
        ),
        parameters=_schema(
            {"region": {"type": "string", "description": "Strategic region id."},
             "level": {"type": "string", "enum": STEER_LEVELS}},
            ["region", "level"],
        ),
    ),
    ActionSpec(
        name="steer_spending",
        category="steer",
        description=(
            "Tell the game's AI what to spend experience and political power on, as a standing "
            "preference: kind=land_xp (id: division_template, unlock_doctrine, equipment_variant "
            "or army_spirit), air_xp (id: unlock_doctrine, equipment_variant or air_spirit), "
            "navy_xp (id: unlock_doctrine, equipment_variant or navy_spirit), pp (id: admiral, "
            "army_chief, navy_chief, become_faction_leader, decision, guarantee, "
            "industrial_concern, political_advisor, relation or supportive_scientist: what it "
            "spends political power on), pp_amount (id: idea or decision: how much it will put "
            "in). The AI still pays what the game charges and needs the points. Level "
            "less/more/much_more or clear."
        ),
        parameters=_schema(
            {"kind": {"type": "string", "enum": STEER_SPENDING_KINDS},
             "id": {"type": "string"},
             "level": {"type": "string", "enum": STEER_LEVELS}},
            ["kind", "id", "level"],
        ),
    ),
    ActionSpec(
        name="steer_market",
        category="steer",
        description=(
            "Tell the game's AI how to use the international equipment market, as a standing "
            "preference: kind=buy (id: an equipment archetype such as infantry_equipment or "
            "light_tank_chassis: how much it wants to buy it), sell (id: an equipment archetype: how "
            "much of its surplus it puts up for sale), buy_from (id: a country tag: whom "
            "it prefers to buy from), trade_desire (id: a country tag: how much it wants to trade "
            "with that country), spend (no id: how many civilian factories it spends on "
            "purchases). The other side's AI still accepts or refuses and the price is the "
            "market's. Level less/more/much_more or clear (a kind has only the levels it makes "
            "sense at)."
        ),
        parameters=_schema(
            {"kind": {"type": "string", "enum": STEER_MARKET_KINDS},
             "id": {"type": "string", "description": "The archetype or country tag; omit for spend."},
             "level": {"type": "string", "enum": STEER_LEVELS}},
            ["kind", "level"],
        ),
    ),
    ActionSpec(
        name="steer_army",
        category="steer",
        description=(
            "Tell the game's AI what divisions to build and how to supply them, as a standing "
            "preference: kind=template (id: infantry, cavalry, motorized, mechanized, light_armor, "
            "medium_armor, heavy_armor, modern_armor, paratroopers, mountaineers or marines: which "
            "templates it favours), roles (id: infantry, armor, garrison, mountaineers, "
            "paratroopers, marines, motorized or militias: how many divisions of that role it "
            "wants), divisions (no id: how many divisions in all), build (no id: how hard it "
            "builds them), fuel (no id: more = it rebuilds fuel to 90 percent before attacking "
            "again, much_more = it also stands offensives down below 40 percent), trucks and "
            "trains (no id: the fewest it wants). The AI still needs the factories, manpower "
            "and time. Level less/more/much_more or clear (a kind has only the levels it makes "
            "sense at)."
        ),
        parameters=_schema(
            {"kind": {"type": "string", "enum": STEER_ARMY_KINDS},
             "id": {"type": "string", "description": "The template type or role; omit for the rest."},
             "level": {"type": "string", "enum": STEER_LEVELS}},
            ["kind", "level"],
        ),
    ),
    ActionSpec(
        name="steer_research",
        category="steer",
        description=(
            "Tell the game's AI to research one technology first, when it next has a free slot "
            "(level=first), or drop that preference (clear). It costs what research costs and "
            "takes what it takes: nothing is skipped."
        ),
        parameters=_schema(
            {"technology": {"type": "string", "description": "Technology id."},
             "level": {"type": "string", "enum": ["first", "clear"]}},
            ["technology", "level"],
        ),
    ),
]

BY_NAME = {spec.name: spec for spec in ACTIONS}


# --- events: read the open window and choose its option (docs/events.md) -----------------------

ACTIONS += [
    ActionSpec(
        name="read_event",
        category="query",
        description=(
            "Read-only: the event window that is open on the screen in your seat. Returns its "
            "id, title, description and every option with what it does (effects) and when it is "
            "shown, how sure the match is, and how long it has been open. With id=<event id> it "
            "reads that event from the playset's files instead (no screen, no window needed). "
            "The brief already carries an open event; call this for the full text."
        ),
        parameters=_schema(
            {"id": {"type": "string", "description": "An event id, e.g. germany.75 (optional)."}},
            [],
        ),
    ),
    ActionSpec(
        name="choose_event_option",
        category="events",
        description=(
            "Answer the open event window by clicking one of its options, as a person does. "
            "option is the number shown by read_event / the brief (1, 2, ...) or the option's "
            "key. Refused, with nothing clicked, when no window is open, when the window could "
            "not be matched to an event, or when that option is not shown to your country. The "
            "result says whether the window closed. Choose before other mouse tools: with an "
            "event open, any other mouse tool answers it blind first (option 1)."
        ),
        parameters=_schema(
            {"option": {"type": "string", "description": "Option number (\"1\") or its key."}},
            ["option"],
        ),
    ),
]

BY_NAME = {spec.name: spec for spec in ACTIONS}


def _mark_mock_only() -> None:
    """Append the simulator-only note to the tools whose only executor is the mock."""
    for index, spec in enumerate(ACTIONS):
        if spec.name in MOCK_ONLY and MOCK_ONLY_NOTE not in spec.description:
            ACTIONS[index] = ActionSpec(
                name=spec.name, description=spec.description + MOCK_ONLY_NOTE,
                parameters=spec.parameters, category=spec.category,
                requires_confirmation=spec.requires_confirmation, aliases=spec.aliases)
    BY_NAME.clear()
    BY_NAME.update({spec.name: spec for spec in ACTIONS})


_mark_mock_only()
