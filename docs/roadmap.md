# Roadmap

Ordered by what unblocks the most. Each item is small enough to be one PR.

## 1. Make one real adapter complete

- [ ] `savegame`: finish `_to_state` against a real 1936 save — resources,
      production, construction, research, division counts.
- [ ] `input_driver`: UI scripts for the five actions that carry a peacetime
      campaign (focus, research, construction, production, advisor), each with a
      verify step.
- [ ] `composite.advance()`: block on the in-game date, return early on a
      critical event.

That trio is the difference between a harness and a demo.

## 2. Wartime state

The compression story holds in peacetime and is untested in war.

- [ ] Front summarisation: derive fronts from combat/army blocks, not province
      lists, and cap the brief at a handful of lines.
- [ ] Wartime wake rules: encirclement risk, supply collapse, capital threatened.
- [ ] Measure brief size and wake rate across 1939-1941 and report both.

## 3. Evaluation worth trusting

- [ ] More scenarios: a defensive war, a resource-starved start, a rearmament race.
- [ ] Run the same scenario across models and publish outcome *and* spend.
- [ ] A reflex-only baseline. If the reflex layer scores close to the model, the
      benchmark is measuring the reflex layer.
- [ ] Variance: same model, same scenario, N seeds.

## 4. Harness quality

- [ ] Structured outputs where the provider supports them, so a malformed tool
      call cannot happen rather than being caught.
- [ ] Human-in-the-loop confirmation for gated actions (`confirm_hook` exists and
      has no UI).
- [ ] Transcript replay: re-run a decision against a different model from a saved
      transcript, without touching the game.
- [ ] Token accounting per provider verified against real invoices.

## Non-goals

- Beating good human players. The interesting question is whether an agent can
  play coherently for a decade of game time on a budget, not whether it can win.
- Multiplayer, in any form. See [fair-play.md](fair-play.md).
- Modding the game or hooking the engine. The bridge stays at the level a player
  operates at.
