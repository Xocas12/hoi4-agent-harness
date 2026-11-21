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

ARMY_ORDERS = ["hold", "front_line", "offensive", "garrison", "naval_invasion", "fall_back"]
AIR_MISSIONS = ["air_superiority", "close_air_support", "strategic_bombing", "port_strike",
                "naval_bombing", "logistical_strike", "interception"]
NAVAL_MISSIONS = ["patrol", "strike_force", "convoy_escort", "convoy_raiding", "naval_invasion_support",
                  "mine_laying", "mine_sweeping", "hold"]
DIPLOMACY = ["improve_relations", "guarantee", "justify_war_goal", "join_faction", "invite_to_faction",
             "non_aggression_pact", "lend_lease", "send_volunteers", "send_attache", "declare_war"]


ACTIONS: list[ActionSpec] = [
    ActionSpec(
        name="set_national_focus",
        category="politics",
        description=(
            "Start a national focus. Only one runs at a time; starting a new one is "
            "impossible until the current focus completes."
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
        description="Create or edit a division template.",
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
        name="hire_advisor",
        category="politics",
        description="Spend political power on an advisor, minister or designer.",
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
            "activated by the game and cannot be taken. Find ids with list_decisions."
        ),
        parameters=_schema(
            {"decision_id": {"type": "string"},
             "target": {"type": "string",
                        "description": "Country tag or state id, for a targeted decision."}},
            ["decision_id"],
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
            "default in peacetime. Do not send it in the same answer as "
            "list_decisions or describe_action: the turn would end before you saw the "
            "result, so the harness skips advance_time and returns the read first."
        ),
        parameters=_schema(
            {"days": {"type": "integer", "minimum": 1, "maximum": 90},
             "reason": {"type": "string"}},
            ["days"],
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
        parameters=_schema({"text": {"type": "string", "maxLength": 500}}, ["text"]),
    ),
]

#: Read-only tools the harness answers from the playset's files; the game is not touched.
QUERY_ACTIONS = frozenset({"list_decisions", "describe_action"})

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

AI_DIRECTIVES = ["invade", "protect", "contain", "befriend", "antagonize", "ignore"]

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
            "relative priority, not a promise."
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
