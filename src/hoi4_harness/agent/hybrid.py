"""Hybrid control: the game's AI runs operations, the model runs strategy.

The premise is that HOI4 already ships a competent operational commander. It
moves divisions along a front, plugs holes, reinforces and reacts within a tick,
for free. A language model doing the same job is slower, more expensive, and
worse -- it cannot see the map at the resolution the decision needs, and by the
time it answers the encirclement has closed.

What the model *is* good at is the layer above: who to fight and when, what to
build toward, which doctrine to commit to, when a front is a distraction and
when it is the war. That layer moves on a timescale of weeks, involves tradeoffs
with no local answer, and is exactly what the native AI is worst at.

So the split:

    LLM        who to invade, whom to guarantee, when to enter a war,
               doctrine and research direction, production and construction
               priorities, theater priorities, posture, when to sue for peace

    game AI    front assignment and movement, reinforcement, local reserves,
               tactical retreat and counterattack, garrisoning

The seam is a small directive vocabulary (``set_ai_directive``,
``set_ai_posture``, ``delegate_army_to_ai``) that the LLM Bridge mod turns into
``ai_strategy`` blocks the vanilla AI already understands.

This is groundwork, not a finished system. What is here: the vocabulary, the
control split, the prompt layer, and mock support so it can be exercised. What
is not: the mod-side plumbing that reads a directive's target dynamically, and
any evidence about whether hybrid actually plays better than either half alone.
That last one is the interesting question and it is issue-tracked, not answered.
"""

from __future__ import annotations

#: Actions that only make sense when the model is commanding units directly.
OPERATIONAL_ACTIONS = frozenset({
    "set_army_order", "set_air_mission", "set_naval_mission", "move_division", "attack_province",
    "draw_front_line", "draw_offensive", "assign_battle_plan",
})

#: Actions that only make sense when the native AI is executing.
DIRECTIVE_ACTIONS = frozenset(
    {"delegate_army_to_ai", "set_ai_posture", "set_ai_directive", "clear_ai_directives",
     "set_front_order", "set_region_order", "steer_production", "steer_intelligence", "steer_research",
     "steer_navy", "steer_air"}
)

#: What the game's AI does for itself once it runs the whole country (the console's
#: ``human_ai``, campaign mode): it picks the focus and the research, builds and
#: produces, designs templates, trades and moves its own armies. Offering any
#: of these to the model gets it undone the next day, or doubled (``set_production``
#: opens a second line beside the one the AI keeps), and a construction or production
#: order costs two 66 MB saves to verify. The clock is the harness's.
GAME_AI_OWNED = frozenset({
    "set_national_focus", "start_research", "queue_construction", "set_production",
    "change_production_equipment",
    "design_division_template", "deploy_divisions", "delegate_army_to_ai", "set_trade",
    "set_game_speed", "diplomacy",
})

MODES = ("llm", "ai", "game_ai")


def actions_for_mode(allowed: set[str], mode: str) -> set[str]:
    """Narrow the action set to the control mode.

    Offering both vocabularies at once is the failure case: the model gives an
    army a direct order *and* a standing directive, they disagree, and the
    resulting behaviour is attributable to neither.
    """
    if mode == "game_ai":
        return allowed - OPERATIONAL_ACTIONS - GAME_AI_OWNED
    if mode == "ai":
        return allowed - OPERATIONAL_ACTIONS
    return allowed - DIRECTIVE_ACTIONS


#: The mouse tools an independent campaign may have (adapters/hands.py); named here so the
#: prompt does not import the adapters.
_ORDERS = ("move_division", "attack_province")


def hands_summary(offered) -> dict:
    """The mouse tools of ``offered`` the prompt talks about."""
    offered = set(offered)
    return {"research": "start_research" in offered,
            "orders": bool(offered & set(_ORDERS)),
            "delegate": "delegate_army_to_ai" in offered}


def describe(mode: str, campaign: bool = False, hands=None) -> str:
    """One paragraph for the prompt, so the model knows what it is not doing.

    ``campaign`` is the independent campaign (``campaign_control = "llm"``): the model
    plays the country itself, but a headless run has no way to move a unit, so "you
    command directly" would promise what the tool list cannot do. ``hands`` is the set of
    mouse tools the run offers (``start_research``, ``move_division``, ``attack_province``,
    ``delegate_army_to_ai``); empty or None is the keyboard-and-console run."""
    got = hands_summary(hands or ())
    if campaign and mode == "llm" and any(got.values()):
        if got["orders"] and got["delegate"]:
            armies = ("You can order your divisions yourself and hand a whole army to the "
                      "game's own AI")
        elif got["orders"]:
            armies = ("You can order your divisions yourself; the ones you do not order stand "
                      "where they are")
        elif got["delegate"]:
            armies = ("No tool here moves a division yourself: they move only when the game's own "
                      "AI moves them, and you can hand an army to it")
        else:
            armies = "No tool here moves a division, so your armies stand where they are"
        return (
            "You are playing the country yourself: the game's own AI is NOT running it, so "
            "the national focus, the research, construction and production are yours to "
            "fill, and they stay empty until you do. You have only the tools in your tool "
            "list" + (", including the ones the harness performs with the mouse: "
                      + ", ".join(n for n, on in (
                          ("research", got["research"]),
                          ("division orders", got["orders"]),
                          ("handing an army to the AI", got["delegate"])) if on)) + ". " + armies + "."
        )
    if campaign and mode == "llm":
        return (
            "You are playing the country yourself: the game's own AI is NOT running it, so "
            "the national focus, the research, construction and production are yours to "
            "fill, and they stay empty until you do. You have only the tools in your tool "
            "list. In this run none of them moves a unit, and nothing else does either, so "
            "your armies stand where they are."
        )
    if mode == "game_ai":
        return (
            "The game's own AI is running your country: it picks the national focus and "
            "the research, builds and produces, designs and deploys divisions and runs "
            "every front. You are the grand strategist above it. You steer it with "
            "set_ai_directive, set_ai_posture (globally or per theater), take_decision "
            "and hire_advisor, and you change laws (change_law, set_occupation_law); you steer what it "
            "builds and researches with steer_production, steer_intelligence and steer_research; "
            "you do not start research, queue construction or "
            "production, or order units, because the AI would undo or double it."
        )
    if mode == "ai":
        return (
            "Operations are delegated. The game's own AI runs fronts, movement, "
            "reinforcement and local reserves; you do not move units. Your job is "
            "intent: who to fight, what to build toward, which theaters matter, "
            "and what posture your forces hold. Express it with set_ai_directive, "
            "set_ai_posture and delegate_army_to_ai."
        )
    return (
        "You command directly. No layer beneath you is making operational "
        "decisions, so front assignment and army orders are yours."
    )


#: What each domain is, in the words of the prompt.
_DOMAIN_WORDS = {
    "focus": "the national focus",
    "research": "research",
    "construction": "construction",
    "production": "production, trade and division templates",
    "politics": "advisors and decisions",
    "diplomacy": "diplomacy",
    "army": "army orders",
    "navy": "fleet missions",
    "air": "air missions",
}
_SCRIPT_WORDS = {
    "focus": "follows a focus path", "research": "fills idle slots from a priority list",
    "construction": "keeps the queue busy by rule", "production": "keeps the lines full in ratio",
    "politics": "runs a rule table", "army": "moves divisions with a tactical controller",
}


def _words(domains) -> str:
    return ", ".join(_DOMAIN_WORDS[d] for d in domains)


def describe_map(cmap, hands=None) -> str:
    """The prompt paragraph for a control map the operator wrote (``control.py``): which parts
    of the country the model owns, which a script runs, which the game's AI runs, and whether it
    steers. The old settings keep ``describe``'s own text."""
    owned = cmap.owned_by("llm")
    parts = ["Control of this country is split by domain. Each part has exactly one controller, "
             "and nothing else touches it."]
    if owned:
        parts.append(f"You run: {_words(owned)}. Your tool list is the whole of what you can do, "
                     "and you are woken for these.")
    else:
        parts.append("You run no domain directly.")
    scripted = cmap.owned_by("script")
    if scripted:
        what = "; ".join(f"{d} {_SCRIPT_WORDS[d]}" for d in scripted)
        parts.append(
            f"Deterministic scripts run: {_words(scripted)} ({what}). You have no tools for them "
            "and are not woken for them; the brief says in one line each what the script did since "
            "your last wake. Plan around what they do, do not try to redo it.")
    game = cmap.owned_by("game")
    if cmap.human_ai:
        parts.append(
            "The game's own AI has been handed the whole country (human_ai), so it runs: "
            f"{_words(game)}, and it also acts in anything not listed as yours. Do not expect an "
            "order of yours in a domain it runs to stay as you left it.")
    elif game:
        parts.append("The game's own AI runs the armies handed to it (they stand under its "
                     "control, not yours).")
    off = cmap.owned_by("off")
    if off:
        parts.append(f"Nobody runs {_words(off)}: there is nothing to do there and no tool for it.")
    human = cmap.owned_by("human")
    if human:
        parts.append(
            f"A person is playing this country with you and holds: {_words(human)}. You have "
            "no tools there and must not try to do it by another route, and nothing there "
            "is yours to fix. Leave it alone even when you think you could do it better; "
            "advice goes in a note, and the person can leave you guidance that you weigh.")
    if cmap.steer == "llm":
        parts.append(
            "You steer the game's AI with set_ai_directive, clear_ai_directives and set_ai_posture "
            "(globally or per theater)"
            + (", and delegate_army_to_ai hands it an army or takes one back"
               if cmap.delegated else "")
            + ". Directives and postures are standing orders.")
    else:
        parts.append("You do not steer the game's AI: no directive or posture tools exist here.")
    return " ".join(parts)
