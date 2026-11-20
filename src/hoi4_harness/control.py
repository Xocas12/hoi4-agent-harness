"""Modular control: who runs each part of the country.

Control used to be a ladder of levels (``operational_control`` llm / ai / game_ai,
``campaign_control`` game_ai / llm). This module replaces the ladder with a map: each
*domain* of the game (the national focus, research, construction, ...) is run by exactly one
*controller*, picked independently:

    llm      the model: it gets that domain's actions and is woken for its events
    script   a deterministic Python controller (``hoi4_harness.auto``) fills it; the model has
             no tools for it and is not woken for it, the brief reports what the script did
    game     HOI4's own AI (see "The hard constraint" below)
    off      nobody: the domain is left alone and its actions are not offered

The steering layer (``set_ai_directive``, ``set_ai_posture``, ``clear_ai_directives``, and
``delegate_army_to_ai``) is its own switch, ``steer``: ``llm`` or ``off``, and it exists only
while the game's AI owns something for it to steer.

The old settings are PRESETS: ``operational_control = "llm" | "ai" | "game_ai"`` resolve to the
maps in ``PRESETS``, and a tool list, prompt and wake rule that are identical to what the
ladder produced (tests/test_modular_control.py pins that, action by action).

The hard constraint
-------------------
The game's AI can only be handed the WHOLE country (the console's ``human_ai``, "Toggles AI for
Human countries", console_commands_documentation.md:1048, which runs research, production,
construction, focuses and diplomacy as well as the fronts) or individual ARMIES
(``delegate_army_to_ai``, issue #8, a draft click path; docs/headless.md: ``human_ai`` is "not
army-scoped: it hands the whole country to the AI, research included"). So:

* ``game`` on any domain but ``army`` means ``human_ai`` is on, and then the AI runs every
  domain. A domain NOT also set to ``game`` is *contested*: the AI may undo or double what the
  model or a script does there. The repo already knows which domains it does (below).
* ``army: game`` without ``human_ai`` is per-army delegation, nothing else is the AI's.

The contested rule (``UNDER_HUMAN_AI``): with ``human_ai`` on, a map that gives a domain to the
model or a script is REFUSED where the repo's docs say the AI overwrites it (focus, research,
construction, production, diplomacy, army: agent/hybrid.py ``GAME_AI_OWNED``, docs/campaign.md
"Not offered", docs/rival.md), ALLOWED where they say it does not (politics: docs/campaign.md,
"the AI takes few decisions and may leave advisor slots empty"), and WARNED where nothing says
(navy, air). ``allow_contested`` turns the refusals into warnings, for an experiment that wants
to measure the clash.

Tested on the simulators only: nothing here has been run against the live game.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DOMAINS = ("focus", "research", "construction", "production", "politics", "diplomacy",
           "army", "navy", "air")
CONTROLLERS = ("llm", "script", "game", "off")
STEER_MODES = ("llm", "off")

#: The catalog actions of each domain (``actions/catalog.py``). ``production`` is everything
#: that makes equipment and divisions; ``army`` is what moves them.
DOMAIN_ACTIONS: dict[str, frozenset[str]] = {
    "focus": frozenset({"set_national_focus"}),
    "research": frozenset({"start_research"}),
    "construction": frozenset({"queue_construction"}),
    "production": frozenset({"set_production", "change_production_equipment", "set_trade",
                             "design_division_template", "deploy_divisions"}),
    "politics": frozenset({"hire_advisor", "take_decision"}),
    "diplomacy": frozenset({"diplomacy"}),
    "army": frozenset({"set_army_order", "move_division", "attack_province",
                       "draw_front_line", "draw_offensive", "assign_battle_plan"}),
    "navy": frozenset({"set_naval_mission"}),
    "air": frozenset({"set_air_mission"}),
}
#: The steering vocabulary of the game's AI.
STEER_ACTIONS = frozenset({"set_ai_directive", "set_ai_posture", "clear_ai_directives"})
DELEGATE_ACTION = "delegate_army_to_ai"
CLOCK_ACTION = "set_game_speed"
#: Never narrowed by control: notes, the clock handed back, the read-only playset queries.
ALWAYS = frozenset({"note", "advance_time", "list_decisions", "describe_action"})

#: Domains with a deterministic controller in ``hoi4_harness.auto``. Diplomacy has none: no
#: rule is sound without knowing who the country's neighbours and rivals are, and a script that
#: guessed would start or sour relations by accident. Navy and air have no script either.
SCRIPTABLE = frozenset({"focus", "research", "construction", "production", "politics", "army"})

OVERWRITTEN, COMPLEMENT, UNKNOWN = "overwritten", "complements", "unknown"
#: What the game's AI does to a domain once ``human_ai`` hands it the country, and the doc line
#: that says so. ``overwritten``: it picks or builds that itself, so an order from the model or a
#: script is undone or doubled. ``complements``: it leaves room for the player's action.
#: ``unknown``: nothing in the repo says.
UNDER_HUMAN_AI: dict[str, tuple[str, str]] = {
    "focus": (OVERWRITTEN, "the AI picks the national focus (docs/campaign.md, division of labour)"),
    "research": (OVERWRITTEN, "the AI picks the research (agent/hybrid.py GAME_AI_OWNED)"),
    "construction": (OVERWRITTEN, "the AI builds and would undo or double it (GAME_AI_OWNED)"),
    "production": (OVERWRITTEN, "set_production opens a second line beside the one the AI keeps "
                                "(docs/campaign.md)"),
    "diplomacy": (OVERWRITTEN, "the AI runs diplomacy (docs/campaign.md, Not offered)"),
    "army": (OVERWRITTEN, "the AI runs every front and moves its own armies (docs/campaign.md)"),
    "politics": (COMPLEMENT, "the AI takes few decisions and may leave advisor slots empty "
                             "(docs/campaign.md)"),
    "navy": (UNKNOWN, "nothing in the docs says what the AI does with fleets"),
    "air": (UNKNOWN, "nothing in the docs says what the AI does with air wings"),
}

#: Said of ``steer`` when only armies are delegated, because nothing has shown it.
STEER_DELEGATED_WARNING = ("steer with delegated armies only (no human_ai): unverified whether "
                           "ai_strategy affects a human country's delegated armies")


class ControlError(ValueError):
    """A control map that cannot be honoured."""


@dataclass(frozen=True)
class ControlMap:
    """A resolved control map: one controller per domain, and the steering switch."""

    domains: dict[str, str]
    steer: str = "off"
    #: Set when the map is one of the old settings (``llm`` / ``ai`` / ``game_ai``): the text,
    #: wake rules and reflex layer of that setting are kept exactly.
    preset: str | None = None
    #: The tactical controller the ``army`` script runs (``tactics.registry``).
    army_controller: str = "scripted"
    warnings: tuple[str, ...] = field(default=())

    # --- what the map means ------------------------------------------------------------

    @property
    def explicit(self) -> bool:
        """A map the operator wrote, as opposed to one of the old settings."""
        return self.preset is None

    @property
    def human_ai(self) -> bool:
        """The game's AI is handed the whole country: some domain but the army is ``game``."""
        return any(c == "game" for d, c in self.domains.items() if d != "army")

    @property
    def delegated(self) -> bool:
        """Armies are handed to the AI one by one (``army: game`` without ``human_ai``)."""
        return self.domains["army"] == "game" and not self.human_ai

    @property
    def ai_owns_something(self) -> bool:
        return self.human_ai or self.domains["army"] == "game"

    def owned_by(self, controller: str) -> list[str]:
        return [d for d in DOMAINS if self.domains[d] == controller]

    @property
    def uses_model(self) -> bool:
        """Whether any part of the map needs a model (an llm domain, or steering)."""
        return bool(self.owned_by("llm")) or self.steer == "llm"

    def controller(self, domain: str) -> str:
        return self.domains[domain]

    # --- the tool list -----------------------------------------------------------------

    def actions_for_model(self, allowed: set[str]) -> set[str]:
        """The adapter's actions narrowed to what the model owns."""
        out = set(allowed)
        for domain, names in DOMAIN_ACTIONS.items():
            if self.domains[domain] != "llm":
                out -= names
        if self.steer != "llm":
            out -= STEER_ACTIONS
        # Handing an army over is the model's call only where armies are the AI's one by one
        # and the model is steering; with human_ai the AI has them all already.
        if not (self.domains["army"] == "game" and not self.human_ai and self.steer == "llm"):
            out.discard(DELEGATE_ACTION)
        if self.human_ai:
            out.discard(CLOCK_ACTION)       # the clock is the harness's under human_ai
        return out

    def script_domains(self) -> list[str]:
        return self.owned_by("script")

    def describe_short(self) -> str:
        parts = [f"{d}={self.domains[d]}" for d in DOMAINS]
        return ", ".join(parts) + f", steer={self.steer}"


#: The old levels as maps. ``navy`` and ``air`` are ``off`` in ``ai``: that level only ever meant
#: "the model does not order fleets and wings", not that the AI took them over.
PRESETS: dict[str, dict] = {
    "llm": {"domains": {d: "llm" for d in DOMAINS}, "steer": "off"},
    "ai": {"domains": {**{d: "llm" for d in DOMAINS}, "army": "game", "navy": "off", "air": "off"},
           "steer": "llm"},
    "game_ai": {"domains": {**{d: "game" for d in DOMAINS}, "politics": "llm"}, "steer": "llm"},
}


def preset(name: str, army_controller: str = "scripted") -> ControlMap:
    """The map the old ``operational_control`` value ``name`` stands for. An unknown name plays
    as ``llm``, as the old code did."""
    spec = PRESETS.get(name, PRESETS["llm"])
    return ControlMap(dict(spec["domains"]), spec["steer"],
                      preset=name if name in PRESETS else "llm", army_controller=army_controller)


def parse_spec(value) -> dict:
    """``"focus=llm,research=script"``, a dict, or a preset name, as a spec dict.

    Keys: the domains, ``default`` (the controller of every domain not named) and ``steer``.
    A bare preset name becomes ``{"preset": name}``."""
    if isinstance(value, str):
        text = value.strip()
        if "=" not in text:
            return {"preset": text}
        spec = {}
        for part in text.split(","):
            if not part.strip():
                continue
            key, sep, val = part.partition("=")
            if not sep:
                raise ControlError(f"control entry {part.strip()!r} is not domain=controller")
            spec[key.strip()] = val.strip()
        return spec
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    raise ControlError(f"control must be a preset name, a 'domain=controller,...' string or a "
                       f"table, not {type(value).__name__}")


def resolve(value, *, steer: str | None = None, army_controller: str = "scripted",
            planner_enabled: bool = True, allow_contested: bool = False) -> ControlMap:
    """Turn a spec into a validated ``ControlMap``, or raise ``ControlError`` saying why not.

    ``value`` is a preset name, a ``domain=controller`` string or a dict (see ``parse_spec``);
    a domain the spec does not name gets its ``default`` (the model, if there is none).
    ``steer`` overrides the spec's; unset it is ``llm`` when the game's AI owns something and
    ``off`` otherwise."""
    spec = parse_spec(value)
    named = spec.pop("preset", None)
    if named is not None:
        if len(spec):
            raise ControlError("a preset name cannot be combined with domain entries")
        if named not in PRESETS:
            raise ControlError(f"unknown control preset {named!r}: one of {', '.join(PRESETS)}")
        out = preset(named, army_controller)
        _check_army_controller(out)
        return out
    default = spec.pop("default", "llm")
    spec_steer = spec.pop("steer", None)
    for key in spec:
        if key not in DOMAINS:
            raise ControlError(f"unknown control domain {key!r}: one of {', '.join(DOMAINS)}")
    domains = {d: spec.get(d, default) for d in DOMAINS}
    for domain, controller in domains.items():
        if controller not in CONTROLLERS:
            raise ControlError(f"{domain}: unknown controller {controller!r}: one of "
                               f"{', '.join(CONTROLLERS)}")
    wanted = steer if steer is not None else spec_steer
    if wanted is not None and wanted not in (*STEER_MODES, "auto"):
        raise ControlError(f"steer must be llm, off or auto, not {wanted!r}")
    for domain in DOMAINS:
        if domains[domain] == "script" and domain not in SCRIPTABLE:
            raise ControlError(
                f"{domain}: no deterministic controller exists for it (scriptable: "
                f"{', '.join(sorted(SCRIPTABLE))}); use llm, game or off")
    warnings: list[str] = []
    human_ai = any(c == "game" for d, c in domains.items() if d != "army")
    ai_owns = human_ai or domains["army"] == "game"
    if human_ai:
        for domain in DOMAINS:
            if domains[domain] in ("game", "off"):
                continue
            kind, why = UNDER_HUMAN_AI[domain]
            who = domains[domain]
            games = ", ".join(d for d, c in domains.items() if c == "game" and d != "army")
            if kind == OVERWRITTEN:
                message = (f"{domain}={who} is contested: with {games} on game the whole country "
                           f"is under the game's AI (human_ai) and {why}")
                if not allow_contested:
                    raise ControlError(
                        message + f". Give {domain} to the game too, or take the game off "
                        f"{games}; allow_contested=true downgrades this to a warning")
                warnings.append(message + " (allowed by allow_contested)")
            elif kind == UNKNOWN:
                warnings.append(f"{domain}={who} under human_ai: {why}; it may be undone or doubled")
    if wanted in (None, "auto"):
        steer_mode = "llm" if ai_owns else "off"
    else:
        steer_mode = wanted
    if steer_mode == "llm" and not ai_owns:
        raise ControlError("steer=llm needs the game's AI to own something: put a domain on game "
                           "(whole country, human_ai) or army=game (delegated armies)")
    if steer_mode == "llm" and not human_ai:
        warnings.append(STEER_DELEGATED_WARNING)
    if not planner_enabled:
        idle = [d for d, c in domains.items() if c == "llm"]
        if idle:
            warnings.append(f"no model is running, so nothing runs {', '.join(idle)} "
                            "(llm domains stay idle)")
    out = ControlMap(domains, steer_mode, preset=None, army_controller=army_controller,
                     warnings=tuple(warnings))
    _check_army_controller(out)
    return out


def _check_army_controller(cmap: ControlMap) -> None:
    if cmap.domains["army"] != "script":
        return
    from .tactics.registry import names

    if cmap.army_controller not in names():
        raise ControlError(f"army_controller {cmap.army_controller!r} is not registered: one of "
                           f"{', '.join(names())}")
