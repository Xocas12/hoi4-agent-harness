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
})

#: Actions that only make sense when the native AI is executing.
DIRECTIVE_ACTIONS = frozenset(
    {"delegate_army_to_ai", "set_ai_posture", "set_ai_directive", "clear_ai_directives"}
)

#: What the game's AI does for itself once it runs the whole country (the console's
#: ``human_ai``, campaign mode): it picks the focus and the research, builds and
#: produces, designs templates, trades and moves its own armies. Offering any
#: of these to the model gets it undone the next day, or doubled (``set_production``
#: opens a second line beside the one the AI keeps), and a construction or production
#: order costs two 66 MB saves to verify. The clock is the harness's.
GAME_AI_OWNED = frozenset({
    "set_national_focus", "start_research", "queue_construction", "set_production",
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
            "and hire_advisor; you do not start research, queue construction or "
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
