"""Core data types passed between the adapter, the environment and the agent.

These are plain dataclasses on purpose: every one of them has to survive being
JSON-encoded into a prompt, appended to a transcript, and reloaded by the eval
runner.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

UNKNOWN = "unknown"


@dataclass
class ProductionLine:
    equipment: str
    factories: int
    efficiency: float = 0.0
    output_per_day: float = 0.0


@dataclass
class ResearchSlot:
    index: int
    technology: str | None = None
    days_remaining: int | None = None


@dataclass
class PowerBalance:
    """One balance of power bar, as the mod logs it (``bop`` line): the bar's id, its
    value from -1 (all the way to the left side) to 1 (right), and its weekly change."""

    id: str
    value: float
    weekly: float | None = None


@dataclass
class ConstructionItem:
    building: str
    state: str
    progress: float = 0.0
    days_remaining: int | None = None


@dataclass
class DivisionGroup:
    template: str
    count: int
    location: str = "unassigned"
    strength: float = 1.0


@dataclass
class Front:
    """One contiguous fighting line, from the agent's point of view.

    Derived from army and combat data, never from province lists: a province
    list is enormous and a war has several fronts at once, which is the one
    place a brief's size can run away. Everything past ``pressure`` is optional
    because an adapter that cannot see it must say nothing rather than a
    reassuring default -- ``supply=None`` is "not observed", not "fine".
    """

    name: str
    enemy: str
    divisions_friendly: int = 0
    divisions_enemy: int = 0
    stance: str = "hold"          # hold | offensive | retreat
    pressure: str = "stable"      # stable | advancing | losing_ground
    #: Worst-supplied sector on this front, as a fraction of demand met (0-1).
    supply: float | None = None
    #: Friendly divisions at risk of being cut off. 0 when no pocket is forming.
    pocket_divisions: int = 0
    #: The capital or a victory-point cluster lies behind this front, in reach.
    threatens_capital: bool = False


@dataclass
class StateForces:
    """Divisions in one state, from the mod's front picture (``div`` lines).

    The game has no per-division location a script can read (docs/unit-orders.md),
    so the finest grain is a state: how many of the player's divisions and how
    many of an enemy's stand in it, and how many of each are below half strength.
    """

    state: int
    controller: str | None = None
    own: int = 0
    own_weak: int = 0
    foe: int = 0
    foe_weak: int = 0
    #: Display name, filled in by a reader that has the playset's localisation.
    name: str | None = None


@dataclass
class War:
    against: str
    since: str | None = None
    war_score: float = 0.0
    allies: list[str] = field(default_factory=list)
    #: What a reader that sees the enemy can add; None is "not observed".
    enemy_divisions: int | None = None
    #: Whether the enemy's controlled territory touches the player's.
    borders: bool | None = None
    #: How close the enemy is to surrendering, 0-1.
    surrender_progress: float | None = None


@dataclass
class CampaignStats:
    """The conquest numbers of a campaign run, from the mod's weekly ``camp`` line
    (schema v5). Every field is None when the line did not carry it (a token the game
    did not expand), never a zero."""

    date: str | None = None
    owned_states: int | None = None
    controlled_states: int | None = None
    divisions: int | None = None
    #: Countries at war with the player, and how many of those have capitulated.
    enemies: int | None = None
    capitulated: int | None = None
    in_faction: bool | None = None
    faction_leader: bool | None = None
    faction_members: int | None = None
    #: Countries that exist (puppets included), those that are nobody's subject, and
    #: every state in the world (the denominator of the player's share).
    countries_existing: int | None = None
    countries_independent: int | None = None
    world_states: int | None = None

    @property
    def state_share(self) -> float | None:
        """The player's share of the world's states, 0-1."""
        if self.owned_states is None or not self.world_states:
            return None
        return self.owned_states / self.world_states


@dataclass
class WarChange:
    """One dated change to a war the mock is scripted to fight.

    A start describes where a campaign begins; a timeline describes how its
    wars move on their own -- fronts opening, an enemy capitulating, an ally
    falling, the enemy reinforcing a sector. It is how the mock produces a
    1939-1941 war for measuring brief size and wake rate (#14) without a game.
    The *agent's* effect on that war stays the mock's crude front rule; the
    timeline only moves what the agent does not control.
    """

    date: str
    open_wars: list[War] = field(default_factory=list)
    #: Tags whose war with this country ends (a capitulation or a peace).
    end_wars: list[str] = field(default_factory=list)
    open_fronts: list[Front] = field(default_factory=list)
    close_fronts: list[str] = field(default_factory=list)
    #: Front name -> the enemy's new division count there.
    enemy_divisions: dict[str, int] = field(default_factory=dict)
    #: Allies that capitulate: removed from every war, with an event.
    allies_fall: list[str] = field(default_factory=list)
    #: Front name -> supply forced to this fraction (weather, a cut rail line).
    supply: dict[str, float] = field(default_factory=dict)
    #: Divisions that arrive at "home" (mobilisation, an ally's expeditionary force).
    reinforcements: int = 0
    #: (kind, text, severity) to emit on the day, if any.
    event: tuple[str, str, str] | None = None


@dataclass
class ScenarioStart:
    """A described opening position, for scenarios that do not begin neutral.

    A scenario carries one of these and hands it to an adapter that can begin
    from a description (the mock does); ``None`` fields mean "whatever this
    adapter's own default start is", so a start only has to state what makes it
    different from that. Scalar fields replace, dict fields merge over the
    adapter's defaults, list fields replace wholesale. Adapters that cannot
    begin from a description ignore the idea entirely -- a real game starts
    where its save or its bookmark says.
    """

    stability: float | None = None
    war_support: float | None = None
    political_power: float | None = None
    manpower: int | None = None
    civilian_factories: int | None = None
    military_factories: int | None = None
    dockyards: int | None = None
    fuel: float | None = None
    convoys: int | None = None
    #: Merged over the adapter's own defaults; pass 0 to starve a resource.
    resources: dict[str, int] | None = None
    #: Merged over the adapter's own defaults.
    stockpiles: dict[str, int] | None = None
    research: list[ResearchSlot] | None = None
    production: list[ProductionLine] | None = None
    construction: list[ConstructionItem] | None = None
    divisions: list[DivisionGroup] | None = None
    fronts: list[Front] | None = None
    wars: list[War] | None = None
    #: Extra scripted history, date -> (kind, text, severity), merged over the
    #: adapter's own script. This is how a scenario fixes its own war date.
    events: dict[str, tuple[str, str, str]] | None = None
    #: How the wars move on their own after the start, in date order.
    timeline: list[WarChange] | None = None


@dataclass
class GameEvent:
    """Something that happened since the last observation.

    ``severity`` is what the policy layer triages on: only ``critical`` events
    are allowed to interrupt the schedule and wake the planner early.
    """

    kind: str                     # focus_complete | research_done | war_declared | front_broken | ...
    text: str
    severity: str = "info"        # info | notable | critical
    date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GameState:
    """Normalised snapshot of everything the harness can currently see.

    An adapter that cannot see a field leaves it at its default and appends the
    field name to ``unknown_fields``; the renderer then prints "unknown" instead
    of a confident zero. Feeding an agent a fabricated 0 is worse than feeding
    it a gap.
    """

    date: str = "1936-01-01"
    #: The in-game hour (0-23) of the line ``date`` came from; None where the source has none.
    #: Several state lines share a date when the telemetry cadence is below a day.
    hour: int | None = None
    country: str = "___"
    country_name: str = "Unknown"
    speed: int = 0                      # 0 = paused
    #: Complete days of telemetry the reader has seen, for readers that see the
    #: game one tick at a time (the log tail). None for readers that see it whole.
    #: Verification uses it to tell "unchanged" from "not looked at again yet".
    telemetry_ticks: int | None = None
    political_power: float = 0.0
    stability: float = 0.0
    war_support: float = 0.0
    manpower: int = 0
    civilian_factories: int = 0
    #: Civilian factories on loan through trade, counted in the game's own
    #: civilian figure but gone when the trade ends. None when unseen.
    trade_civilian_factories: int | None = None
    military_factories: int = 0
    dockyards: int = 0
    fuel: float = 0.0
    convoys: int = 0
    national_focus: str | None = None
    focus_days_remaining: int | None = None
    #: Focuses finished so far, when the adapter can see them. It is what lets
    #: the brief offer the focuses available *now* rather than the whole tree.
    completed_focuses: list[str] = field(default_factory=list)
    ideology: str = "neutrality"
    research: list[ResearchSlot] = field(default_factory=list)
    production: list[ProductionLine] = field(default_factory=list)
    construction: list[ConstructionItem] = field(default_factory=list)
    stockpiles: dict[str, int] = field(default_factory=dict)
    #: Daily resource income, in the shape of the game's resource bar. Adapters
    #: that cannot see resources leave it empty.
    resources: dict[str, int] = field(default_factory=dict)
    divisions: list[DivisionGroup] = field(default_factory=list)
    fronts: list[Front] = field(default_factory=list)
    wars: list[War] = field(default_factory=list)
    faction: str | None = None
    #: Balance of power bars active for the country (schema v5 ``bop`` lines); [] is none.
    power_balances: list[PowerBalance] = field(default_factory=list)
    #: Whether the game's AI runs this country (the state line's ``ai=``, schema v5):
    #: None is "not observed". Campaign mode checks it after the console's ``human_ai``.
    ai_controlled: bool | None = None
    #: The weekly conquest numbers of a campaign run; None until the first line.
    campaign: CampaignStats | None = None
    # --- hybrid control: what the native AI has been told to do ---------
    delegated_armies: list[str] = field(default_factory=list)
    ai_directives: list[str] = field(default_factory=list)
    posture: str | None = None
    #: Posture scoped to one theater, overriding ``posture`` there. A theater is
    #: the country a front faces, by tag: it is what the engine's own
    #: ``front_unit_request`` strategy scopes on (``tag = KHM``), so the mod can
    #: express it and the brief already names each front's enemy.
    theater_postures: dict[str, str] = field(default_factory=dict)
    #: Divisions per state along the front, as of ``state_forces_date``. None is
    #: "not observed" (a reader without the mod's front lines); [] is "observed,
    #: and no division of either side stands in a contested state".
    state_forces: list[StateForces] | None = None
    state_forces_date: str | None = None
    #: Where the division orders given earlier stand (adapters/unit_orders.py).
    order_status: list[str] = field(default_factory=list)
    events: list[GameEvent] = field(default_factory=list)
    unknown_fields: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        d = asdict(self)
        if not include_raw:
            d.pop("raw", None)
        return d

    def known(self, name: str) -> bool:
        return name not in self.unknown_fields


@dataclass
class Observation:
    """What the agent sees on a turn: a snapshot plus prompt-ready framing."""

    turn: int
    state: GameState
    brief: str                                   # rendered markdown, prompt-ready
    is_delta: bool = False
    legal_actions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn": self.turn,
            "state": self.state.to_dict(),
            "brief": self.brief,
            "is_delta": self.is_delta,
            "legal_actions": self.legal_actions,
            "notes": self.notes,
        }


@dataclass
class ActionCall:
    """A single action the agent wants to take, before validation."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None
    rationale: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ActionResult:
    ok: bool
    message: str
    action: str = ""
    call_id: str | None = None
    changed: dict[str, Any] = field(default_factory=dict)
    # invalid_action | invalid_args | rejected | unsupported | budget | not_executed
    error_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_tool_content(self) -> str:
        """Compact JSON for a tool-result block. Terse on purpose: this text is
        re-sent on every subsequent request in the turn."""
        payload: dict[str, Any] = {"ok": self.ok, "message": self.message}
        if self.changed:
            payload["changed"] = self.changed
        if self.error_kind:
            payload["error"] = self.error_kind
        return json.dumps(payload, sort_keys=True)
