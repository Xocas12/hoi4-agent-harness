# Hybrid control: the game's AI runs operations, the model runs strategy

## The premise

HOI4 ships a competent operational commander. It assigns divisions to fronts,
plugs holes, pulls reserves, retreats when a pocket is forming, and does all of
it within a tick, for free, at full map resolution.

A language model doing that same job is slower, more expensive, and worse. It
sees a text summary of a map, it answers in tens of seconds, and by the time it
has decided which division to move the encirclement has closed. Asking it to
micromanage fronts is asking it to lose at the one thing the built-in AI is
already good at.

What a model is good at is the layer above — the decisions that take weeks, have
no local answer, and are exactly what the native AI is worst at.

## The split

| Layer | Owner | Examples |
|---|---|---|
| Grand strategy | LLM | who to invade and when, whom to guarantee, which faction to join, when to sue for peace |
| Force design | LLM | doctrine, research direction, division templates, the industry-to-army ratio |
| Priorities | LLM | which theater matters this quarter, what to build, what posture to hold |
| Operations | game AI | front assignment, movement, reinforcement, local reserves |
| Tactics | game AI | when to attack a province, when to fall back, garrisoning |

## The seam

Four actions, and a mod that turns them into something the vanilla AI already
understands:

```
delegate_army_to_ai {army, delegate}          hand an army over, or take it back
set_ai_posture      {posture, theater?}       defensive | balanced | offensive
set_ai_directive    {directive, target, weight}  invade | protect | contain | befriend | antagonize | ignore
clear_ai_directives {}                        stand everything down
```

Underneath, the harness raises a country flag; an `ai_strategy` block in
[the mod](../mod/llm_bridge/common/ai_strategy/llm_bridge_directives.txt) keys on
that flag and starts biasing the AI's own scoring. No engine hooks, no fighting
the AI for control of a unit — the model sets intent and the AI executes it with
its own machinery.

### Theaters

A theater is a **front, by name** — the cheapest definition that can express the
most common decision a player makes, "hold in the east, press in the west", and
one the brief already prints. `set_ai_posture` with a `theater` overrides the
global posture on that front only; without one it sets the posture for every
front that has no posture of its own. An unknown front name is rejected with the
list of real ones. The brief shows both layers
(`AI control: posture defensive | theaters: west offensive`) and the delta
reports a theater changing posture on its own line. `clear_ai_directives` drops
theater postures along with everything else.

On the mod side this is still global: the posture flags in
`llm_bridge_directives.txt` have no notion of a front. Scoping them needs a front
identity the game script can key on (a strategic region or a state the front
runs through), and choosing that is a live-game job, like the rest of #1.

## Turning it on

```bash
HOI4_OPERATIONAL_CONTROL=ai hoi4-harness play --guidance hybrid --live --allow-all
```

Setting `operational_control` changes three things at once, and deliberately so:

1. The direct-command actions (`set_army_order`, `set_air_mission`,
   `set_naval_mission`) disappear from the tool list, even if an explicit
   whitelist named them.
2. The directive actions appear.
3. The system prompt gains a paragraph saying which layer the model is.

Offering both vocabularies at the same time is the failure case: the model gives
an army a direct order *and* a standing directive, they disagree, and the
resulting behaviour is attributable to neither.

## What is actually built

Working and tested: the action vocabulary, the control-mode switch, the prompt
layer, the `hybrid` guidance pack, state rendering for delegation and standing
directives, and mock-adapter support so the whole path runs offline.

Not built: the mod-side plumbing that reads a directive's target dynamically
(the `ai_strategy` blocks currently hardcode example tags), army-level delegation
through the real UI, and theater-scoped posture inside the mod (the harness side
and the mock are built; see [Theaters](#theaters)).

Not answered: **whether hybrid actually plays better.** That is the interesting
question and it needs three runs of the same scenario — model-only, AI-only,
hybrid — scored the same way. Until that exists this is a hypothesis with an
implementation, and the repo should say so.

## Why this is the more promising direction

Two reasons beyond raw skill.

**Cost.** Operations are the part of the game that generates constant, urgent,
low-value decisions. Handing them to the AI removes the traffic that would
otherwise force the wake rule wide open during wartime — which is precisely when
the token bill would explode. See [cost-and-timing.md](cost-and-timing.md).

**Attribution.** When the model only makes strategic decisions, a run's outcome
is evidence about strategic decision-making. When it is also moving divisions,
a loss could be either, and you have learned nothing.
