"""Generate the mod's per-target directive plumbing (#7).

``set_ai_directive`` names a country, but an ``ai_strategy`` block names its
target as a literal (``id = "GER"``): Paradox script has no variable-driven
``id`` that is known to work in this scope. The hand-written blocks therefore
hardcoded example targets, and "protect Finland" biased the AI toward whatever
tag the file happened to contain -- the hybrid layer existing without working.

So the certain-but-verbose route: one ``ai_strategy`` block per (directive,
target), each gated on its own country flag, generated from a template. Raising
a directive is two stable clicks rather than one decision per pair:

1. ``Target: POL`` -- a decision that makes POL the selected target;
2. ``Directive: protect`` -- a decision that raises ``protect`` against
   whichever target is selected, by setting ``llmb_protect_POL``.

That keeps the decision list at (targets + directives) entries instead of their
product, and keeps the harness side untouched: ``set_ai_directive`` in the
catalog does not know any of this exists.

Theater postures (#9) ride the same plumbing. A theater is the country a front
faces, and ``front_unit_request`` accepts a literal ``tag`` (vanilla's CHI.txt
writes ``tag = KHM``), so each (posture, tag) gets a block gated on
``llmb_posture_<posture>_<TAG>``. The selected target is reused: ``Target: GER``
then ``Theater posture: offensive``. Balanced is the absence of both flags.

The tag list defaults to the countries a 1936 campaign most plausibly names;
regenerate with ``hoi4-harness mod-directives --tags ...`` for a playset with
others (a total conversion, for one). Output is deterministic, and a test fails
when the committed files drift from what this module would write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .actions.catalog import AI_DIRECTIVES
from .adapters.logtail import SCHEMA_VERSION
from .cadence import DAILY_HOURS, SUBDAILY_HOURS, SUPPORTED_HOURS

if TYPE_CHECKING:
    from .identifiers import IdentifierIndex

#: The countries a directive can name: the ones a 1936 campaign most plausibly names
#: first, then the rest of the world, so a world-conquest run can target anyone.
DEFAULT_TAGS = (
    "GER", "ENG", "FRA", "ITA", "SOV", "USA", "JAP", "CHI", "PRC", "MAN",
    "POL", "CZE", "AUS", "HUN", "ROM", "YUG", "GRE", "BUL", "ALB", "TUR",
    "SPR", "POR", "BEL", "HOL", "LUX", "DEN", "NOR", "SWE", "FIN", "EST",
    "LAT", "LIT", "SWI", "IRE", "CAN", "AST", "NZL", "SAF", "RAJ", "SIA",
    "PHI", "MEX", "BRA", "ARG", "PER", "IRQ", "SAU", "EGY", "ETH",
    # The rest of the 1936 world: every other tag that owns a state in the base game's
    # history/states at the 1936 start (a conquest campaign names any of them).
    "AFA", "AFG", "BHU", "BOL", "BRM", "CHL", "COL", "COS", "CUB", "DOM", "ECU", "ELS",
    "GUA", "GXC", "HAI", "HON", "ICE", "INS", "JOR", "KUW", "LEB", "LIB", "MAL", "MEN",
    "MON", "NEP", "NIC", "OMA", "PAL", "PAN", "PAR", "PRU", "SHX", "SIK", "SLO", "SYR",
    "TAN", "TIB", "URG", "VEN", "XSM", "YEM", "YUN",
)

#: Directive -> the ai_strategy entries it raises, as (type, value).
#: VERIFY against common/ai_strategy in your install: an unrecognised type is
#: ignored silently, so a wrong name here is a directive that does nothing --
#: which the brief's directive-effect line (#11) is there to reveal.
STRATEGIES: dict[str, tuple[tuple[str, int], ...]] = {
    "invade": (("conquer", 150), ("invade", 150)),
    "protect": (("protect", 150),),
    "contain": (("contain", 150),),
    "befriend": (("befriend", 150),),
    "antagonize": (("antagonize", 150),),
    "ignore": (("ignore", 150),),
    # Diplomacy the AI carries out itself, with its own acceptance and timing (the effects for
    # these would force the other side's answer, or skip the justification time).
    "prepare_war": (("prepare_for_war", 150),),
    "declare_war": (("declare_war", 150),),
    "volunteers": (("send_volunteers_desire", 200),),
    "lend_lease": (("send_lend_lease_desire", 200),),
    "ally": (("alliance", 150),),
    # Intelligence against one country: operative_mission / operative_operation take the target
    # as mission_target / operation_target (ai_strategy/_documentation.md). A "kind:name" type
    # is written by _strategy_body.
    "spy_on": (("operative_mission:build_intel_network", 800),),
    "sabotage": (("operative_operation:operation_targeted_sabotage_industry", 800),),
    "steal_tech": (("operative_operation:operation_steal_tech_civilian", 800),),
}

#: The extra weight each directive gets while the global flag ``llmb_strong_directives`` is
#: set (``effect <TAG> llm_bridge_strong_directives``, sent by the harness when the base AI is
#: Expert AI: docs/expert-ai.md). Expert AI's own country plans weigh 500 to 5000 for the same
#: types (``antagonize POL 5000`` in its GER_EAI.txt), so a 150 directive would be noise next to
#: them. The strong block adds to the normal one; it does not replace it. A starting point,
#: not a measured value: unverified against the live game.
STRONG_BOOST = 10000
STRONG_FLAG = "llmb_strong_directives"
#: The same for a theater posture's front_unit_request.
STRONG_POSTURE_BOOST = 1000

#: Event codes the generated effects emit; mirrored in adapters/logtail.py.
EVENT_TARGET_SELECTED = 13
EVENT_DIRECTIVE_RAISED = 14
EVENT_THEATER_POSTURE = 15
#: The telemetry cadence was set (cadence.py); detail is the hours between two ticks.
EVENT_TELEMETRY_CADENCE = 20

#: Theater posture -> the front_unit_request value it raises against the tag.
#: Mirrors the global posture blocks in llm_bridge_directives.txt, which use 50
#: and 100 and are additive with these. "balanced" sets neither flag.
THEATER_POSTURES: dict[str, int] = {"defensive": 50, "offensive": 100}
THEATER_POSTURE_CHOICES = (*THEATER_POSTURES, "balanced")

HEADER = (
    "# GENERATED by `hoi4-harness mod-directives` from src/hoi4_harness/modgen.py.\n"
    "# Do not edit by hand: change the generator and regenerate.\n"
)


@dataclass(frozen=True)
class GeneratedFile:
    path: str          # relative to the mod root (mod/llm_bridge)
    text: str
    bom: bool = False  # HOI4 reads localisation only as UTF-8 with a BOM


def generate(tags: tuple[str, ...] | list[str] = DEFAULT_TAGS) -> list[GeneratedFile]:
    from . import front_orders, steering

    tags = tuple(dict.fromkeys(tag.strip().upper() for tag in tags if tag.strip()))
    for tag in tags:
        if not (len(tag) == 3 and tag.isalnum()):
            raise ValueError(f"{tag!r} is not a country tag (three letters or digits)")
    missing = set(AI_DIRECTIVES) - set(STRATEGIES)
    if missing:
        raise ValueError(f"no ai_strategy mapping for directive(s): {', '.join(sorted(missing))}")
    return [
        GeneratedFile("common/ai_strategy/llm_bridge_targets.txt", _strategies(tags)),
        GeneratedFile("common/scripted_effects/llm_bridge_targets.txt", _effects(tags)),
        GeneratedFile("common/decisions/llm_bridge_target_decisions.txt", _decisions(tags)),
        GeneratedFile("common/scripted_effects/llm_bridge_cadence.txt", _cadence_effects()),
        GeneratedFile(front_orders.FILE, front_orders.strategies()),
        GeneratedFile(steering.FILE, steering.strategies()),
        GeneratedFile("events/llm_bridge_cadence_events.txt", _cadence_events()),
        # The empty inbox: the game must know both events at load. The harness rewrites the
        # INSTALLED copies while it runs (inbox.py); this repo copy stays empty.
        *(GeneratedFile(rel, text) for rel, text in _empty_inbox().items()),
        GeneratedFile(
            "localisation/english/llm_bridge_targets_l_english.yml", _localisation(tags), bom=True
        ),
    ]


def write(mod_dir: Path, tags=DEFAULT_TAGS) -> list[Path]:
    written = []
    for item in generate(tags):
        target = mod_dir / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item.text, encoding="utf-8-sig" if item.bom else "utf-8", newline="\n")
        written.append(target)
    return written


def drift(mod_dir: Path, tags=DEFAULT_TAGS) -> list[str]:
    """Generated files whose committed copy differs from what would be written."""
    stale = []
    for item in generate(tags):
        target = mod_dir / item.path
        # Bytes, not text: utf-8-sig reads a file with or without its BOM, and
        # the game ignores localisation that has lost it.
        wanted = item.text.encode("utf-8-sig" if item.bom else "utf-8")
        current = target.read_bytes().replace(b"\r\n", b"\n") if target.exists() else None
        if current != wanted:
            stale.append(item.path)
    return stale


# --- the four files ----------------------------------------------------------


def _strategies(tags: tuple[str, ...]) -> str:
    out = [
        HEADER,
        "# One block per (directive, target), each gated on its own flag. The",
        "# harness-side vocabulary is set_ai_directive {directive, target}.",
        "",
    ]
    for posture, value in THEATER_POSTURES.items():
        for tag in tags:
            flag = f"llmb_posture_{posture}_{tag}"
            out += [
                f"{flag} = {{",
                f"	enable = {{ has_country_flag = {flag} }}",
                f"	abort = {{ NOT = {{ has_country_flag = {flag} }} }}",
                f"	ai_strategy = {{ type = front_unit_request tag = {tag} value = {value} }}",
                "}",
                "",
                *_strong_block(flag, [f"type = front_unit_request tag = {tag} "
                                      f"value = {STRONG_POSTURE_BOOST}"]),
            ]
    for directive in AI_DIRECTIVES:
        for tag in tags:
            flag = f"llmb_{directive}_{tag}"
            out.append(f"{flag} = {{")
            out.append(f"\tenable = {{ has_country_flag = {flag} }}")
            out.append(f"\tabort = {{ NOT = {{ has_country_flag = {flag} }} }}")
            for kind, value in STRATEGIES[directive]:
                out.append(f"\tai_strategy = {{ {_strategy_body(kind, tag, value)} }}")
            out.append("}")
            out.append("")
            out += _strong_block(flag, [_strategy_body(kind, tag, STRONG_BOOST)
                                        for kind, _ in STRATEGIES[directive]])
    return "\n".join(out)


def _strategy_body(kind: str, tag: str, value: int) -> str:
    """The inside of one ``ai_strategy`` block for ``kind`` against ``tag``."""
    if kind.startswith("operative_mission:"):
        return (f"type = operative_mission mission = {kind.split(':', 1)[1]} value = {value} "
                f"mission_target = {tag}")
    if kind.startswith("operative_operation:"):
        return (f"type = operative_operation operation = {kind.split(':', 1)[1]} value = {value} "
                f"operation_target = {tag}")
    return f'type = {kind} id = "{tag}" value = {value}'


def _strong_block(flag: str, strategies: list[str]) -> list[str]:
    """The boost a directive gets while ``llmb_strong_directives`` is set (Expert AI as the base
    AI): on only while both the directive's own flag and the global flag are set. The abort is
    an OR of two NOTs because ``NOT = { a b }`` is a NOR in this script language."""
    return [
        f"{flag}_strong = {{",
        f"\tenable = {{ has_country_flag = {flag} has_global_flag = {STRONG_FLAG} }}",
        f"\tabort = {{ OR = {{ NOT = {{ has_country_flag = {flag} }} "
        f"NOT = {{ has_global_flag = {STRONG_FLAG} }} }} }}",
        *(f"\tai_strategy = {{ {s} }}" for s in strategies),
        "}",
        "",
    ]


def _effects(tags: tuple[str, ...]) -> str:
    out = [HEADER, "llm_bridge_clear_target = {"]
    out += [f"\tclr_country_flag = llmb_target_{tag}" for tag in tags]
    out += ["}", ""]
    for tag in tags:
        out += [
            f"llm_bridge_select_{tag} = {{",
            "\tllm_bridge_clear_target = yes",
            f"\tset_country_flag = llmb_target_{tag}",
            # A literal log line, like the raise effects below: llmb_event_detail
            # is never set here, so llm_bridge_emit_event printed detail=0.
            f'\tlog = "LLMB|v{SCHEMA_VERSION}|evt|tag=[ROOT.GetTag]|kind={EVENT_TARGET_SELECTED}'
            f'|detail={tag}"',
            "}",
            "",
        ]
    for directive in AI_DIRECTIVES:
        out.append(f"llm_bridge_raise_{directive} = {{")
        for tag in tags:
            # Each branch logs its own literal line, so the directive and its
            # target reach the harness without a variable that has to expand:
            # the log reader rebuilds the standing directives from these, which
            # is what lets set_ai_directive be verified rather than assumed.
            out.append(
                f"\tif = {{ limit = {{ has_country_flag = llmb_target_{tag} }} "
                f"set_country_flag = llmb_{directive}_{tag} "
                f'log = "LLMB|v{SCHEMA_VERSION}|evt|tag=[ROOT.GetTag]|kind={EVENT_DIRECTIVE_RAISED}'
                f'|detail={directive} {tag}" }}'
            )
        out += ["}", ""]
    out.append("llm_bridge_clear_theater_postures = {")
    for posture in THEATER_POSTURES:
        out += [f"\tclr_country_flag = llmb_posture_{posture}_{tag}" for tag in tags]
    out += ["}", ""]
    for posture in THEATER_POSTURE_CHOICES:
        out.append(f"llm_bridge_raise_theater_{posture} = {{")
        for tag in tags:
            # Clear this tag's two flags, then set the one asked for. One literal
            # log line per branch, as for directives, so the log reader learns
            # "<posture> <TAG>" without a variable that has to expand.
            sets = [f"clr_country_flag = llmb_posture_{p}_{tag}" for p in THEATER_POSTURES]
            if posture in THEATER_POSTURES:
                sets.append(f"set_country_flag = llmb_posture_{posture}_{tag}")
            out.append(
                f"\tif = {{ limit = {{ has_country_flag = llmb_target_{tag} }} "
                + " ".join(sets)
                + f' log = "LLMB|v{SCHEMA_VERSION}|evt|tag=[ROOT.GetTag]|kind={EVENT_THEATER_POSTURE}'
                f'|detail={posture} {tag}" }}'
            )
        out += ["}", ""]
    out.append("llm_bridge_clear_target_directives = {")
    for directive in AI_DIRECTIVES:
        out += [f"\tclr_country_flag = llmb_{directive}_{tag}" for tag in tags]
    out += ["\tllm_bridge_clear_theater_postures = yes", "\tllm_bridge_clear_target = yes", "}", ""]
    return "\n".join(out)


def _decision(name: str, effect: str) -> list[str]:
    return [
        f"\t{name} = {{",
        "\t\ticon = generic_political_actions",
        "\t\tcost = 0",
        "\t\tfire_only_once = no",
        "\t\tdays_re_enable = 0",
        "\t\tavailable = { always = yes }",
        "\t\tcomplete_effect = {",
        f"\t\t\t{effect} = yes",
        "\t\t}",
        "\t\tai_will_do = { factor = 0 }",
        "\t}",
        "",
    ]


def _decisions(tags: tuple[str, ...]) -> str:
    out = [
        HEADER,
        "# Pick a target, then a directive or theater posture: targets + choices rather",
        "# than their product. Instant, free and idempotent, like the rest of the",
        "# bridge's decisions, so a mis-click costs nothing.",
        "",
        "llm_bridge_target_category = {",
        "",
    ]
    for tag in tags:
        out += _decision(f"llmb_target_{tag}", f"llm_bridge_select_{tag}")
    for directive in AI_DIRECTIVES:
        out += _decision(f"llmb_directive_{directive}", f"llm_bridge_raise_{directive}")
    for posture in THEATER_POSTURE_CHOICES:
        out += _decision(f"llmb_theater_{posture}", f"llm_bridge_raise_theater_{posture}")
    out.append("}")
    out.append("")
    return "\n".join(out)


def _localisation(tags: tuple[str, ...]) -> str:
    lines = [
        "l_english:",
        ' llm_bridge_target_category:0 "LLM Bridge: targets"',
        ' llm_bridge_target_category_desc:0 "Choose a country, then a directive to raise against it."',
    ]
    for tag in tags:
        lines.append(f' llmb_target_{tag}:0 "Target: {tag}"')
        lines.append(f' llmb_target_{tag}_desc:0 "Make {tag} the target of the next directive."')
    for directive in AI_DIRECTIVES:
        lines.append(f' llmb_directive_{directive}:0 "Directive: {directive}"')
        lines.append(
            f' llmb_directive_{directive}_desc:0 "Tell the AI to {directive} the selected target."'
        )
    for posture in THEATER_POSTURE_CHOICES:
        lines.append(f' llmb_theater_{posture}:0 "Theater posture: {posture}"')
        lines.append(
            f' llmb_theater_{posture}_desc:0 "Hold the {posture} posture on fronts against '
            'the selected target."'
        )
    return "\n".join(lines) + "\n"


# --- the telemetry cadence (cadence.py) ----------------------------------------


def _cadence_effects() -> str:
    """The telemetry tick and the effects that set how often it runs.

    ``llm_bridge_daily_tick`` is what ``on_daily`` calls for the player: once a day it either
    prints (the default, and every N days when slower) or leaves the printing to the event
    chain (below 24 hours) and only checks that the chain is alive. The chain is one hidden
    event per supported hour count, because ``hours`` in ``country_event`` is a literal (no
    vanilla file passes a variable, and the documentation shows no variable form). Per value N
    the country keeps ``llmb_tick_inflight_N``, the number of events of that chain waiting to
    fire, and the rules that make it idempotent are these three:

    * a chain is started only while ``inflight_N < 1``, so setting the same cadence twice, or
      A -> B -> A before A's event has fired, never leaves two events of one chain waiting;
    * an event that fires re-schedules itself only while ``llmb_tick_hours`` still equals its N,
      so changing the cadence ends the old chain at its next firing with nothing to cancel;
    * ``inflight`` and ``llmb_tick_hours`` are country variables, so both survive a save and
      load with the scheduled event itself.

    The daily tick restarts a chain whose event was lost (a country that stopped existing): every
    firing sets ``llmb_tick_alive`` and the tick clears it, so a day without a firing, which a
    live chain never has, resets the stuck counter and starts the chain again. That takes one
    to two days after the loss.
    """
    gate = "llm_bridge_is_harness = yes"
    out = [
        HEADER,
        "# Telemetry cadence. llmb_tick_hours: 0 or 24 = once a day (the default), 1..12 = the",
        "# event chain, a multiple of 24 = every N days. Set with `effect <TAG> llm_bridge_",
        "# telemetry_every_<H>h` (hoi4-harness: --telemetry-hours). See docs/mod-bridge.md.",
        "",
        "# One tick: the state line (with its army, war and probe lines) and the front picture.",
        "llm_bridge_emit_tick = {",
        "\tllm_bridge_emit_state = yes",
        "\tllm_bridge_emit_front = yes",
        "}",
        "",
        "# on_daily, in the player's scope. The three limits are mutually exclusive.",
        "llm_bridge_daily_tick = {",
        "\tif = {",
        "\t\tlimit = { check_variable = { llmb_tick_hours > 24 } }",
        "\t\tsubtract_from_variable = { llmb_tick_left = 1 }",
        "\t\tif = {",
        "\t\t\tlimit = { check_variable = { llmb_tick_left < 1 } }",
        "\t\t\tset_variable = { llmb_tick_left = llmb_tick_days }",
        "\t\t\tllm_bridge_emit_tick = yes",
        "\t\t}",
        "\t}",
        "\tif = {",
        f"\t\tlimit = {{ check_variable = {{ llmb_tick_hours > 0 }} "
        f"check_variable = {{ llmb_tick_hours < {DAILY_HOURS} }} }}",
        "\t\t# The event chain prints. Every firing sets llmb_tick_alive; a day with none means",
        "\t\t# the event was lost (the country stopped existing), so forget the stuck counter and",
        "\t\t# start the chain again. A live chain fires at least twice a day: never doubled.",
        "\t\tif = {",
        "\t\t\tlimit = { check_variable = { llmb_tick_alive < 1 } }",
    ]
    for hours in SUBDAILY_HOURS:
        out += [
            "\t\t\tif = {",
            f"\t\t\t\tlimit = {{ check_variable = {{ llmb_tick_hours = {hours} }} }}",
            f"\t\t\t\tset_variable = {{ llmb_tick_inflight_{hours} = 0 }}",
            f"\t\t\t\tllm_bridge_tick_schedule_{hours} = yes",
            "\t\t\t}",
        ]
    out += [
        "\t\t}",
        "\t\tset_variable = { llmb_tick_alive = 0 }",
        "\t}",
        "\tif = {",
        f"\t\tlimit = {{ OR = {{ check_variable = {{ llmb_tick_hours < 1 }} "
        f"check_variable = {{ llmb_tick_hours = {DAILY_HOURS} }} }} }}",
        "\t\tllm_bridge_emit_tick = yes",
        "\t}",
        "}",
        "",
    ]
    for hours in SUBDAILY_HOURS:
        out += [
            f"llm_bridge_tick_schedule_{hours} = {{",
            f"\tadd_to_variable = {{ llmb_tick_inflight_{hours} = 1 }}",
            f"\tcountry_event = {{ id = llmb_tick.{hours} hours = {hours} }}",
            "}",
            "",
            f"llm_bridge_tick_fire_{hours} = {{",
            f"\tsubtract_from_variable = {{ llmb_tick_inflight_{hours} = 1 }}",
            "\tif = {",
            f"\t\tlimit = {{ check_variable = {{ llmb_tick_hours = {hours} }} }}",
            "\t\tset_variable = { llmb_tick_alive = 1 }",
            f"\t\tif = {{ limit = {{ {gate} }} llm_bridge_emit_tick = yes }}",
            f"\t\tllm_bridge_tick_schedule_{hours} = yes",
            "\t}",
            "}",
            "",
        ]
    for hours in SUPPORTED_HOURS:
        out.append(f"llm_bridge_telemetry_every_{hours}h = {{")
        out.append(f"\tset_variable = {{ llmb_tick_hours = {hours} }}")
        if hours in SUBDAILY_HOURS:
            out += [
                "\tif = {",
                f"\t\tlimit = {{ check_variable = {{ llmb_tick_inflight_{hours} < 1 }} }}",
                f"\t\tllm_bridge_tick_schedule_{hours} = yes",
                "\t}",
                "\tset_variable = { llmb_tick_alive = 1 }",
            ]
        else:
            out += [
                f"\tset_variable = {{ llmb_tick_days = {max(hours // 24, 1)} }}",
                "\tset_variable = { llmb_tick_left = 1 }",
            ]
        out += [
            f"\tset_variable = {{ llmb_event_kind = {EVENT_TELEMETRY_CADENCE} }}",
            f"\tset_variable = {{ llmb_event_detail = {hours} }}",
            "\tllm_bridge_emit_event = yes",
            "\tset_variable = { llmb_event_detail = 0 }",
            "}",
            "",
        ]
    return "\n".join(out)


def _empty_inbox() -> dict[str, str]:
    from .inbox import render

    return render([])


def _cadence_events() -> str:
    out = [
        HEADER,
        "# The hidden events of the sub-daily telemetry chain: one per supported hour count,",
        "# because the delay of country_event is a literal. See llm_bridge_cadence.txt.",
        "",
        "add_namespace = llmb_tick",
        "",
    ]
    for hours in SUBDAILY_HOURS:
        out += [
            "country_event = {",
            f"\tid = llmb_tick.{hours}",
            "\thidden = yes",
            "\tis_triggered_only = yes",
            f"\timmediate = {{ llm_bridge_tick_fire_{hours} = yes }}",
            "}",
            "",
        ]
    return "\n".join(out)


# --- the focus / research probe (#2) -----------------------------------------

PROBE_PATH = "common/scripted_effects/llm_bridge_probe.txt"
_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")


def generate_probe(index: IdentifierIndex) -> GeneratedFile:
    """The daily probe that says which focus and which technologies are running.

    1.19.5.0 has no variable or loc object for the current focus, and no
    on_action for a finished one, but it does have triggers that take an id:
    ``focus_progress``, ``has_completed_focus``, ``has_focus_tree`` and
    ``is_researching_technology``. So the mod asks, once a day, about every id
    the install defines and logs the ones that answer yes; the log reader turns
    a focus or technology that stops answering into the completion event.

    Focuses are gated on the country's focus tree so only its own are tested;
    shared focuses are gated on any tree that pulls them in. Output is sorted,
    so it is deterministic for a given playset.
    """
    def ok(ident: str) -> bool:
        return bool(_ID.match(ident))

    def focus_block(ident: str, indent: str) -> str:
        return (f"{indent}if = {{ limit = {{ focus_progress = {{ focus = {ident} progress > 0 }} "
                f"NOT = {{ has_completed_focus = {ident} }} }} "
                f'log = "LLMB|v{SCHEMA_VERSION}|focus|tag=[THIS.GetTag]|id={ident}" }}')

    out = [
        HEADER.replace("hoi4-harness mod-directives", "hoi4-harness mod-probe"),
        "# Daily probe: one `focus` line for the focus being worked on, one `bop` line",
        "# per active balance of power bar and one `research` line per technology",
        "# being researched. Called from",
        "# llm_bridge_emit_state, right after the state line.",
        "",
        "llm_bridge_emit_probe = {",
    ]
    trees = sorted((t for t in index.trees if t.id and ok(t.id)), key=lambda t: t.id)
    for tree in trees:
        ids = sorted(f for f in tree.focuses if ok(f))
        if not ids:
            continue
        out.append(f"\tif = {{ limit = {{ has_focus_tree = {tree.id} }}")
        out += [focus_block(f, "\t\t") for f in ids]
        out.append("\t}")
    shared_users = sorted(t.id for t in trees if t.shared)
    shared = sorted(f for f in index.shared_focuses if ok(f))
    if shared and shared_users:
        users = " ".join(f"has_focus_tree = {t}" for t in shared_users)
        out.append(f"\tif = {{ limit = {{ OR = {{ {users} }} }}")
        out += [focus_block(f, "\t\t") for f in shared]
        out.append("\t}")
    # Balance of power (#135): the active bar's value and weekly change, x100 like stab/ws.
    # power_balance_value and power_balance_weekly are country-scope dynamic variables in
    # 1.20's dynamic_variables_documentation.md. UNVERIFIED in the live game.
    try:
        bars = sorted(b for b in index.mod_context().balances() if ok(b))
    except Exception:  # noqa: BLE001 - a playset without readable bop files has no bars
        bars = []
    for bar in bars:
        out.append(
            f"	if = {{ limit = {{ has_power_balance = {{ id = {bar} }} }} "
            "set_variable = { llmb_bop = power_balance_value } "
            "multiply_variable = { llmb_bop = 100 } "
            "set_variable = { llmb_bop_w = power_balance_weekly } "
            "multiply_variable = { llmb_bop_w = 100 } "
            f'log = "LLMB|v{SCHEMA_VERSION}|bop|tag=[THIS.GetTag]|id={bar}|value=[?llmb_bop]'
            f'|weekly=[?llmb_bop_w]" }}'
        )
    for tech in sorted(t for t in index.technologies if ok(t)):
        out.append(
            f"\tif = {{ limit = {{ is_researching_technology = {tech} }} "
            f'log = "LLMB|v{SCHEMA_VERSION}|research|tag=[THIS.GetTag]|id={tech}" }}'
        )
    out += ["}", ""]
    return GeneratedFile(PROBE_PATH, "\n".join(out))


def generate_research(index: IdentifierIndex) -> GeneratedFile:
    """The research_tech blocks (steering.research_strategies), one per technology."""
    from . import steering

    return GeneratedFile(steering.RESEARCH_FILE, steering.research_strategies(index.technologies))


def generate_regions(index: IdentifierIndex) -> GeneratedFile:
    """The navy and air steers (steering.region_strategies): per strategic region and country."""
    from . import steering

    every, sea = steering.regions(index.playset)
    return GeneratedFile(steering.REGIONS_FILE,
                         steering.region_strategies(every, sea,
                                                         [x for x in index.tags if x in DEFAULT_TAGS]))


def generate_reserves(index: IdentifierIndex) -> GeneratedFile:
    """The reserve blocks (front_orders.reserves), one per state of the playset."""
    from . import front_orders

    ids = [s for s in index.states if str(s).isdigit()]
    return GeneratedFile(front_orders.RESERVES_FILE, front_orders.reserves(ids))


def write_probe(mod_dir: Path, index: IdentifierIndex) -> Path:
    """The playset-specific files: the focus/research/bop probe and the reserve blocks."""
    target = mod_dir / PROBE_PATH
    for item in (generate_probe(index), generate_reserves(index), generate_research(index),
                 generate_regions(index)):
        out = mod_dir / item.path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(item.text, encoding="utf-8", newline="\n")
    return target
