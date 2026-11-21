"""Prompt construction.

The system prompt is assembled from three layers, in this order:

1. **Mechanics** (fixed) -- what the tools are, how a turn works, what the reports
   mean. Without this nothing functions, so it is not configurable.
2. **Guidance** (swappable) -- how to play, from nothing at all to a prescriptive
   checklist. See :mod:`hoi4_harness.guidance`.
3. **Extra** (optional) -- whatever the operator appends, or a complete
   replacement via ``system_prompt_path``.

The split is not cosmetic. How much coaching a model needs is the interesting
experimental variable in this project, and it belongs in a file you can swap,
not welded into the harness. The whole assembled prompt is still byte-stable
across a run, so prefix caching works regardless of which layers are in play.
"""

from __future__ import annotations

from pathlib import Path

from ..guidance import load as load_guidance

MECHANICS = """You are playing a single-player campaign of Hearts of Iron IV as the \
country named in each situation report. You act through tools; the harness \
executes them and reports back what actually happened.

How the game reaches you:
- You are woken only when something needs deciding. Between wakes the harness \
runs the clock and handles routine upkeep.
- A report may be a full situation brief or a list of changes since the last one. \
Treat a change list as an update to what you already knew.
- Anything marked "unknown" is not observable through the current bridge. Do not \
reason about it as if it were zero.

How to act:
- Take the smallest set of actions that moves the plan forward, then call \
advance_time. Doing nothing for a week is a legitimate and cheap move.
- One thing at a time gets built, researched, and focused on. Check the report \
before assuming a slot is free.
- Use `note` to record intent that should outlive this turn: what you are \
building toward, and what would change your mind. Your notes come back to you.
- If an action is rejected, the reason is in the result. Fix the argument or \
choose a different action; do not repeat the same call."""

ADVISOR = """Advisor mode. You are not playing this campaign; a person is. Every tool \
call you make is shown to them as a recommendation, and none of them are \
executed -- the harness cannot act, so the game will never change because you \
called something. That is expected, not an error. Use the tools exactly as you \
would if you were playing: concrete calls are the advice. `note` still writes \
your journal. Call advance_time to end the turn, as usual."""


HEADLESS = """This run is headless: the harness reads the game from the mod's log and from
text saves it asks the game to write, and acts through the console and the keyboard.
It has no mouse and no screen. Some actions need clicks or a view the harness does
not have, so they do not exist here:
{gone}.
You cannot start research or hand an army to the game's AI; an idle research slot
stays idle, so do not plan around it. set_production only opens a line for equipment
that has none yet and refuses when one exists or no military factory is free; it does
not change a running line. Everything else works as described.

Two things follow from reading a log. The game logs once per in-game day, so a
result marked "unverified" means the harness has not been able to see the change
yet, not that it failed; check the next report before repeating it (a repeated
queue_construction queues twice). Construction and production are checked against a
save the harness takes itself; when that save cannot be read the result says so, and
the brief shows production and the queue as of the last save it did read. A focus you
start appears in the log only after a day of progress."""


CAMPAIGN = """This is an unattended campaign, and you are its grand strategist. The game is running
headless: the harness reads it from the mod's log and acts through the game's console. The
game's own AI is running your country (the console's human_ai): it chooses the national focus
and the research, builds and produces, designs and deploys divisions and runs every front. You
cannot do any of that, and the tools for it are not offered, because the AI would undo or double
it. What you have is the layer above:
- set_ai_directive and clear_ai_directives say who the AI should act on (invade, protect,
  contain, befriend, antagonize, ignore). set_ai_posture sets how hard it presses, for every
  front or for the front facing one country (theater). take_decision takes a decision the AI does
  not; click_gui_button presses a button in a custom window (list_gui_buttons shows them, with
  how the game's AI weighs each); hire_advisor fills a slot it left empty.
- note keeps a plan across wakes. You are woken only for a scheduled review (weeks apart) or when
  a war starts or ends; the game runs between wakes without you.
- advance_time days sets how long the clock runs until your next scheduled review (7 to 90 days).
  Every wake spends from a fixed money budget, so sleep long when the plan is working.

A change you make reaches the game when the harness types it into the console, and the result
says whether the game took it. Directives and postures are standing orders: they stay until you
change them. The brief reports the conquest: states held and the share of the world, the wars
with each enemy's divisions and surrender progress, the independent countries left, and what the
run has spent."""


CAMPAIGN_LLM = """This is an unattended campaign, and you play the country yourself. The game is
running headless: the harness reads it from the mod's log and acts through the game's console and
keyboard. The game's own AI is NOT running your country (human_ai is not on), so nothing fills a
slot for you: when the national focus ends, a research slot opens or a production line is empty,
it stays that way until you, or one of the harness's reflexes (an empty construction queue, idle
factories), fills it. You are woken when that happens, when a war starts or ends, on a scheduled
review (weeks apart), and when the person running the campaign leaves guidance.
- Your tool list is the whole of what you can do. The harness offers only what it can do
  without the mouse and can read back, so some of what a player does is not there (research may
  not be, and no tool moves a division). Do not plan around a tool you do not have.
- Armies: the game's AI is not moving your divisions, and no tool here does. They stand where they
  are; plan for a war you cannot attack in.
- note keeps a plan across wakes. advance_time days sets how long the clock runs until your next
  scheduled review (7 to 90 days). Every wake spends from a fixed money budget, so sleep long when
  nothing needs you.

A change you make reaches the game when the harness types it in, and the result says whether the
game took it ("unverified" means the harness could not see it yet, not that it failed: check the
next report before repeating it). The brief reports the conquest: states held and the share of the
world, the wars, the independent countries left, and what the run has spent."""


CAMPAIGN_MODULAR = """This is an unattended campaign. The game is running headless: the harness reads
it from the mod's log and acts through the game's console (and, where the run has it, the mouse).
Who runs what is in the paragraph above; this is how the run works.
- note keeps a plan across wakes. You are woken on a scheduled review (weeks apart), when a war
  starts or ends, and for the events of the domains you run; the game runs between wakes without
  you. advance_time days sets how long the clock runs until your next scheduled review (7 to 90
  days). Every wake spends from a fixed money budget, so sleep long when nothing needs you.
- A change you make reaches the game when the harness types it in, and the result says whether
  the game took it ("unverified" means the harness could not see it yet, not that it failed: check
  the next report before repeating it). The brief reports the conquest: states held and the share
  of the world, the wars, the independent countries left, and what the run has spent."""


def campaign_llm(hands=None) -> str:
    """The independent campaign's note. ``hands`` is what the run offers of the mouse tools
    (``HarnessConfig.campaign_hands``: ``offered``, ``armies``); with none offered it is
    exactly the keyboard-and-console text (``CAMPAIGN_LLM``)."""
    offered = set((hands or {}).get("offered") or ())
    production = (hands or {}).get("production") or {}
    if not offered and not production.get("edit") and not production.get("equipment"):
        return CAMPAIGN_LLM
    got = {"research": "start_research" in offered,
           "orders": bool(offered & {"move_division", "attack_province"}),
           "delegate": "delegate_army_to_ai" in offered}
    armies = (hands or {}).get("armies") or []
    lines = []
    if got["research"]:
        lines.append(
            "- Research: start_research puts a technology into a free research slot. The harness "
            "clicks the tech tree for you, so it runs only while the game window is in front; "
            "the result says whether the slot now shows the technology, and you are woken when a "
            "slot goes idle.")
    if got["orders"]:
        lines.append(
            "- Armies, by order: move_division and attack_province send the divisions standing in "
            "a state (from the front picture in the brief) to another state, by clicking the map. "
            "A march takes days, so the result is always 'unverified' and the brief's Order: "
            "lines say what became of it: do not repeat an order meanwhile. A refusal says why "
            "and nothing was clicked.")
    if got["delegate"]:
        named = (" Armies you can hand over: " + ", ".join(armies) + ".") if armies else ""
        lines.append(
            "- Armies, by delegation: delegate_army_to_ai hands an army to the game's own AI (it "
            "then runs that army on its front) or takes it back (delegate false). The harness "
            "cannot read the toggle back, so the result is always 'unverified': keep a record of "
            "what you handed over in your notes." + named)
    if production.get("edit"):
        lines.append(
            "- Production: set_production EDITS the line that already makes that equipment "
            "(factories up or down, 0 removes it) by clicking the production screen, and opens a "
            "new line only when none exists; it never makes a second line for the same "
            "equipment. It runs only while the game window is in front, and the result says "
            "whether the line now shows the number you asked for."
            + (" change_production_equipment switches an existing line to other equipment "
               "(for example a newer variant) and keeps its factories; it refuses when the "
               "other equipment already has a line." if production.get("equipment") else ""))
    if not got["orders"] and not got["delegate"]:
        lines.append("- Armies: no tool here moves a division, and the game's AI is not moving "
                     "them. They stand where they are; plan for a war you cannot attack in.")
    elif not got["orders"]:
        lines.append("- No tool here moves a division yourself: a division moves only when the "
                     "game's AI moves it, so an army you do not delegate stands where it is.")
    elif not got["delegate"]:
        lines.append("- Divisions you do not order stand where they are; the game's AI is not "
                     "moving them.")
    note_keeps = (
        "- Still not available: anything not in your tool list." if production.get("edit") else
        "- Still not available: changing a running production line (set_production only opens a "
        "line for equipment that has none), and anything not in your tool list.")
    return (CAMPAIGN_LLM.split("- Your tool list is the whole")[0]
            + "- Your tool list is the whole of what you can do. Some of it the harness does with "
              "the mouse; the rest through the console and the keyboard. Do not plan around a tool "
              "you do not have.\n"
            + "\n".join(lines) + "\n" + note_keeps + "\n"
            + "- note keeps a plan across wakes."
            + CAMPAIGN_LLM.split("- note keeps a plan across wakes.", 1)[1])


RIVAL = """You are the grand strategist of {tag}, one country in a single-player game that a
person is also playing. The person plays a different country, and may well be your enemy. {tag}
is run by the game's own AI: it chooses the national focus and the research, builds and
produces, designs and deploys divisions and moves every army. You cannot do any of that, and the
tools for it are not offered. What you have is the layer above:
- set_ai_directive and clear_ai_directives say who the AI should act on (invade, protect,
  contain, befriend, antagonize, ignore). set_ai_posture sets how hard it presses, for every
  front or for the front facing one country (theater). take_decision takes a decision the AI does
  not; click_gui_button presses a button in a custom window (list_gui_buttons shows them, with
  how the game's AI weighs each); hire_advisor fills a slot it left empty, with {tag}'s
  political power. A balance of power line in the brief says where the bar stands and which
  decision category moves it.
- note keeps a plan across wakes. advance_time days says how long until your next scheduled
  review (7 to 90 days). You are also woken when a war starts or ends.

Three facts about how you play, none of them negotiable:
- {acting} Every action you take is QUEUED, and the result says so. It has not happened, and
  nothing in the next brief will show it until it has. It is checked again when it runs and
  reported as stale if the world moved. Plan for that delay, and do not repeat a call that is
  already queued.
- You do not own the clock. The person does: they pause, unpause and change the speed when they
  like. Game time passes only while they let it, so a long pause means no time and no wake-up.
- You know what {tag} can know, and nothing else. The brief is {tag}'s own numbers, its wars and
  the divisions the game reports for its enemies. You cannot see the person's plans, economy or
  orders, and you must not guess at them as if you could. If the person declares war on {tag},
  that shows up as a war; that is all you are told.

Directives and postures are standing orders: they stay until you change them. Steer, and let the
AI run the country."""

#: What the third fact says the actions' windows are, by ``rival_act_when``.
RIVAL_WINDOWS = {
    "paused": ("Your actions are typed into the game's console only while the person has the "
               "game paused and is away from the keyboard, so they run in the person's pauses, "
               "not on your schedule."),
    "handover": ("Your actions are typed into the game's console only when the person hands "
                 "you the keyboard on purpose, so they run when the person decides, not on "
                 "your schedule."),
    "never": ("Your actions are not run at all: each is shown to the person as advice, and "
              "they decide whether to do it themselves."),
}


def playset_note(name: str) -> str:
    """Said once, in the stable prefix, when the game is not the one the model
    remembers. Guidance packs that assert historical dates stay as written; this
    tells the model which of its own knowledge to distrust."""
    return (
        f"This campaign runs on the playset '{name}', not the base game. Countries, "
        "states, focus trees, technologies and the calendar of events may differ "
        "from what you remember about Hearts of Iron IV and from any history the "
        "guidance below assumes. Trust the situation reports and the ids they "
        "offer over your recollection; an invented id is rejected, with the nearest "
        "real ones, before it reaches the game."
    )


INSTRUCTION = (
    "The person who set up this run gave you a standing instruction. It holds for the whole run, "
    "not one turn: treat it as the strategic direction you are playing toward, and keep to it "
    "unless the game makes it impossible, in which case say so with `note` and follow its spirit. "
    "It does not override the tools' rules or what the game allows. "
    "Instruction: {text}"
)


def build_system(
    guidance: str | Path | None = "doctrine",
    extra: str = "",
    system_prompt_path: str | Path | None = None,
    operational_control: str = "llm",
    advisor: bool = False,
    playset: str | None = None,
    headless: bool = False,
    headless_reader: str = "log+save",
    playset_context: str = "",
    campaign: bool = False,
    rival: str = "",
    rival_when: str = "paused",
    campaign_control: str = "game_ai",
    campaign_hands: dict | None = None,
    control_map=None,
    instruction: str = "",
) -> str:
    """Assemble the system prompt.

    ``system_prompt_path`` replaces everything, mechanics included -- total
    control, and entirely your problem if the model then cannot use the tools.
    """
    if system_prompt_path:
        return Path(system_prompt_path).read_text(encoding="utf-8").strip()

    from .hybrid import describe, describe_map

    independent = bool(campaign) and campaign_control == "llm" and not rival
    hands = (campaign_hands or {}).get("offered") if independent else None
    modular = control_map is not None and control_map.explicit
    parts = [MECHANICS,
             describe_map(control_map, hands) if modular
             else describe(operational_control, campaign=independent, hands=hands)]
    if advisor:
        # Sits with the mechanics, before any guidance: it corrects the fiction
        # that the harness executes what the model calls.
        parts.append(ADVISOR)
    if rival:
        parts.append(RIVAL.format(tag=rival, acting=RIVAL_WINDOWS.get(rival_when, RIVAL_WINDOWS["paused"])))
    elif campaign:
        # Instead of the headless note: it lists what the mouse-only actions cannot do, and
        # a campaign's tool list is narrower than that for a different reason.
        parts.append(CAMPAIGN_MODULAR if modular
                     else campaign_llm(campaign_hands) if independent else CAMPAIGN)
    elif headless:
        from ..adapters.headless import unavailable_actions

        parts.append(HEADLESS.format(gone=", ".join(unavailable_actions(headless_reader))))
    if playset and playset != "vanilla":
        parts.append(playset_note(playset))
    if playset_context:
        # Deterministic text (modcontext.prompt), so it stays in the cached prefix.
        parts.append(playset_context)
    text = load_guidance(guidance)
    if text:
        parts.append(text)
    if extra:
        parts.append(extra.strip())
    if instruction and instruction.strip():
        parts.append(INSTRUCTION.format(text=instruction.strip()))
    return "\n\n".join(parts)


def turn_prompt(
    brief: str,
    objective: str,
    memory_block: str,
    wake_reason: str,
    turn: int,
    actions_left: int,
) -> str:
    """The volatile half: everything that changes turn to turn."""
    parts = [f"Turn {turn}. You were woken because: {wake_reason}."]
    if objective:
        parts.append(f"Standing objective: {objective}")
    if memory_block:
        parts.append(memory_block)
    parts.append(brief)
    parts.append(
        f"Take up to {actions_left} actions, then call advance_time to hand the clock back."
    )
    return "\n\n".join(parts)


# Backwards-compatible default, used when nothing overrides it.
def system_for(config, index=None) -> str:
    """The system prompt a run with ``config`` (and ``index``) is given.

    The one place that maps a config onto ``build_system``: the loop, ``replay``
    and ``hoi4-harness prompt`` all call it, so the prompt they show is the
    prompt the run had.
    """
    context = ""
    if getattr(config, "mod_context", False) and index is not None:
        from ..modcontext import playset_section

        context = playset_section(
            index, config.country, max_tokens=config.mod_context_tokens,
            cache_dir=config.mod_context_cache_dir, offline=not config.mod_context_fetch,
            tools="list_decisions" not in set(config.disabled_actions))
    return build_system(
        guidance=config.guidance,
        extra=config.system_prompt_extra,
        system_prompt_path=config.system_prompt_path,
        operational_control=config.operational_control,
        advisor=config.advisor,
        playset=index.playset.name if index is not None else None,
        headless=config.adapter == "headless",
        headless_reader=config.headless_reader,
        playset_context=context,
        campaign=getattr(config, "campaign", False),
        rival=getattr(config, "rival", ""),
        rival_when=getattr(config, "rival_act_when", "paused"),
        campaign_control=getattr(config, "campaign_control", "game_ai"),
        campaign_hands=_hands_for(config),
        control_map=config.control_map() if hasattr(config, "control_map") else None,
        instruction=getattr(config, "instruction", ""),
    )


def _hands_for(config) -> dict | None:
    """What the prompt is told of the mouse tools: what the adapter found live at the start
    (``campaign_hands``); for a config no adapter has been built for (``hoi4-harness prompt``)
    the tools the mouse could offer, since nothing has been refused yet."""
    if not getattr(config, "hands_mouse", False):
        return None
    found = getattr(config, "campaign_hands", None)
    if found:
        return found
    return {"offered": ["start_research", "move_division", "attack_province",
                        "delegate_army_to_ai"], "armies": []}


SYSTEM = build_system("doctrine")
