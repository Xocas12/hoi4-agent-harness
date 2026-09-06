# Cost and timing

Two objections come up immediately when you propose an LLM agent for HOI4: the
game runs in real time, and the state space is enormous. Both are real. Neither
is fatal, and the answers are structural rather than clever.

## 1. Real time is optional

HOI4 pauses, and it pauses instantly and completely. The loop therefore runs:

```
pause -> read state -> decide -> apply actions -> unpause N in-game days -> pause
```

The model never races the clock. Wall-clock latency stops mattering: a model that
takes forty seconds to think just means the game sits paused for forty seconds.
What *does* matter is in-game latency — how many game-days pass between a thing
happening and the agent noticing — and that is bounded two ways: a scheduled
review every `days_per_turn` (default 7), and an early return from `advance()`
the moment a critical event fires.

Speed 5 on a fast machine runs roughly a game-week in a couple of seconds, so a
1936–1939 campaign at weekly reviews is a few minutes of game time plus however
long the model takes across ~190 wakes.

## 2. Most game-days are not decisions

This is the part that decides the bill. Asking a model "what now?" every game-day
across 1936–1939 is ~1,300 calls to answer "keep building" nine hundred times.

The harness splits control:

- **Reflex** ([`agent/policy.py`](../src/hoi4_harness/agent/policy.py)) — deterministic Python. Refills an
  empty construction queue, reassigns idle military factories, and nothing that
  requires judgement. Free and instant.
- **Planner** — the model. Woken on: a critical event, no national focus running,
  an idle research slot, a front losing ground, or the scheduled review.

Widening that rule is how a run gets expensive. It is deliberately one small
function so the cost of a change is visible.

## 3. The state space is compressed before it is sent

A HOI4 save is tens of megabytes. Almost none of it is decision-relevant on any
given turn. Three reductions, in order:

1. **Normalise** — the adapter maps whatever it can see onto `GameState`, one flat
   struct of ~25 fields plus five short lists. Anything it cannot see is recorded
   in `unknown_fields` rather than defaulted to zero.
2. **Render** — `render_full` produces a ~300-token brief: numbers, not prose,
   empty sections omitted, long lists truncated to what matters, and the
   decision-shaped facts shouted (`NONE SELECTED`, `3 SLOT(S) FREE`, `QUEUE EMPTY`).
3. **Diff** — after the first brief, `render_delta` sends only what moved, with
   per-field thresholds so noise (manpower ticking up 120/day) never appears.
   A full brief returns when the diff grows past ~12 lines, every
   `full_brief_every` turns, or on any critical event.

## 4. The prompt is built for caching

Prefix caching only pays if the prefix is byte-identical. So:

- `system` is a frozen constant — role, doctrine, rules. No dates, no numbers.
- The tool block is generated deterministically from the catalog, filtered by
  adapter capability, in stable order.
- Everything volatile — brief, memory, wake reason — is in the last user message.

With the Anthropic provider both the system block and the tool block are marked
cacheable, so from the second call onward the ~1.7k-token prefix bills at
cache-read rates.

## 5. Memory instead of transcript

Each wake starts a fresh conversation. Continuity comes from two bounded things:

- a **journal** the agent writes itself through the `note` action (last 12 entries),
- a **digest** of recent turns (last 6 lines, auto-generated).

A growing transcript would be both more expensive and worse: old briefs
contradict the current one, and the model re-argues settled decisions.

## The arithmetic

Estimate, not measurement — the token counts below are design targets, and the
harness reports actuals in `spend` at the end of every run.

| Component | Tokens per wake |
|---|---|
| System prompt | ~500 (cached) |
| Tool schemas (16 actions) | ~1,200 (cached) |
| Memory block | ~150 |
| Brief (full ~300 / delta ~80) | ~120 average |
| Output (reasoning + tool calls) | ~200 |

≈2.2k input, ~90% of it cache-read after the first call, plus ~200 output. Over
~190 wakes for a 1936–1939 campaign: roughly **400k input / 40k output tokens**.

At frontier-model prices that lands around a couple of dollars per campaign; on a
mid-tier model it is cents; on a local model behind an OpenAI-compatible endpoint
it is electricity. Set `--max-usd` and the harness enforces it — on exhaustion the
run does not stop, it drops to the reflex layer and keeps playing for free.

## What would break this

Honest failure modes, since they decide whether the numbers above survive contact:

- **Wartime.** Fronts, combats, and encirclements are the state that does not
  compress well, and war is exactly when reflexes are least adequate. Expect the
  wake rate and brief size to rise together. Front summarisation is the open
  design problem in this repo.
- **Screen-based observation.** A vision call per observation is 1–2k image tokens
  and cannot be cached across turns. It is for what numbers cannot express, not
  for routine ticks.
- **A chatty model.** A model that narrates instead of acting can double output
  tokens. `max_tool_rounds_per_turn` caps the damage; the prompt's "take the
  smallest set of actions, then advance_time" is doing real work.
