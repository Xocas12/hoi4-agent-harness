# Roadmap

Seven milestones, ordered by what unblocks the most. Each one is a claim the repo
cannot currently make; the issues under it are what it would take to make it.
Tracked at [github.com/Xocas12/hoi4-agent-harness/milestones](https://github.com/Xocas12/hoi4-agent-harness/milestones).

## Status after the second live session (2026-11-16)

A second sitting with HOI4 1.19.5.0 (Sweden, 1936-37) moved v0.2 and v0.3 from
theory to partly verified. Nothing below is claimed beyond what ran; the live
checklist is [live-session.md](live-session.md).

**Verified live**

- #1 mod and telemetry: loads on 1.19.5 (`supported_version 1.19.*`) with no
  errors in `error.log`, including the 2.4 MB generated probe. Schema v3: one state
  line a day on the player's own tick, real values for political power, stability,
  war support, factories, manpower, techs and `built_*`; daily probe lines name the
  running focus and the technologies being researched; focus completion reported
  (`focus complete: SWE_defense_act`); war and capitulation lines for the
  player's own wars only.
- #7 directives and #9 theater posture: raised through decisions and read back
  (the brief showed `AI control: ETH: offensive | GER: defensive`). A theater is
  the country a front faces.
- #3 input: keys sent as hardware scan codes (pyautogui keys do not reach HOI4);
  real 1.19.5 hotkeys; no Escape in scripts; `set_national_focus` via focus search
  works end to end through the harness.
- #6 save adapter: reads a 1.19.5 text save (`save_as_binary=no`) and was checked
  against a real save. The construction queue is not in the save.
- #53 playsets: indexed The New Order (512 countries, 15,069 focuses, 897 techs)
  in ~4 s.
- #22 provider conformance: Anthropic passes live.

**Built and tested, not yet seen live**

- Research completion through the `techs` counter, and the schema v3 `owned` field
  (added after the session).
- #3 `start_research` (tech-tree points computed from `countrytechtreeview.gui`,
  within 4 px of 18 measured infantry-tab points), `queue_construction`,
  `set_production`, `hire_advisor`. Rotated tabs (armour, air, industry,
  electronics) are refused. Verification reports `unverified`, not `rejected`,
  until a fresh day of telemetry.
- #83 console channel: opt-in; open questions in [adapters.md](adapters.md).

**Measured on the mock only**

- #14 cost with `claude-opus-5-5` ([cost-and-timing.md](cost-and-timing.md)): the
  old estimate was low on input by 2.2x in peacetime and 3.2x in wartime.
- #10 hybrid experiment ([results/10-hybrid-experiment.md](results/10-hybrid-experiment.md),
  [hybrid-control.md](hybrid-control.md)): a result about the harness against a
  scripted world.

**Still open**

- Whether the game's AI changes behaviour under a directive: not verified.
- #22 needs a second provider; #23 needs a comparison with a Console invoice.

## v0.1.1 — Robustness

*Claim: the harness survives an overnight run.*

Found by auditing the code rather than the roadmap. None of these show up in a
30-turn mock run, and all of them show up in a six-hour campaign against a real
game. None needs a game or an API key to fix.

- A transient API error must degrade to the reflex layer, not end the run
- Resume from a transcript after a crash
- Wire up the triage model role or delete it
- Tests for the save parser and provider wire conversion
- Refuse to send input when the game is not the focused window
- Per-turn progress output; remove dead config

## v0.2 — Live bridge

*Claim: the harness plays a real game of HOI4, not a mock.*

The mod emits telemetry that a real game actually produces, the input driver can
perform the five actions a peacetime campaign needs, and `advance()` blocks on
the real clock. Until this lands everything else is theory.

- Verify every script token in the mod against a live game
- Focus / research / construction completion hooks in `on_actions`
- UI scripts with verification for focus, research, construction, production, advisor
- `CompositeAdapter.advance()` blocking on the in-game date
- A calibration command for screen coordinates

## v0.2.5 — Co-op play

*Claim: the model plays alongside a person, in the same campaign.*

The harness was designed as if the agent were the only player: it owns the clock
and the keyboard. Sharing a campaign with a human breaks both assumptions, and
the modes that come out of fixing them are the ones most people will actually
use.

- Advisor mode — you play, it recommends, it never acts (needs only the read path)
- Passive mode — never pause, resume, or set speed
- Action handover — queue actions, execute in a window the player grants
- Split portfolio — the model takes the economy, you keep the army (already
  expressible today with `--actions`, once the input driver can act)

Co-op runs are for playing, not for measuring: once both parties act, outcome
attribution is gone and the eval runner should refuse to score the transcript
rather than produce a number that means nothing.

## v0.3 — Hybrid control

*Claim: the model commands and the game's AI executes.*

The vocabulary and the mode switch exist; the plumbing behind them is example
code. This milestone makes a directive actually change AI behaviour, and then
asks the question the whole idea rests on.

- Per-target directives (generated blocks, one per directive and tag), verified in-game
- Army delegation through the real UI
- Theater-scoped posture
- **The experiment**: same scenario, three ways — model-only, AI-only, hybrid

## v0.4 — Wartime state

*Claim: the compression story survives a war.*

Everything about brief size and wake rate is measured in peacetime, where
nothing happens. War is where fronts, combats and encirclements arrive at once,
and where the reflex layer is least adequate.

- Front summarisation from army blocks, capped at a handful of lines
- Wartime wake rules: encirclement risk, supply collapse, capital threatened
- Measure brief size and wake rate across 1939-1941 and publish both

## v0.5 — Evaluation worth trusting

*Claim: a score from this harness means something.*

- A reflex-only baseline. If it scores close to the model, the benchmark is
  measuring the reflex layer
- More scenarios: a defensive war, a resource-starved start, a rearmament race
- The same scenario across models, reporting outcome *and* spend
- Variance: same model, same scenario, N seeds

## v0.6 — Harness quality

*Claim: the machinery is boring and trustworthy.*

- Structured outputs where the provider supports them
- A human-in-the-loop UI for `confirm_hook`
- Transcript replay: re-run a decision against a different model, no game needed
- Token accounting verified against real invoices
- Provider conformance tests against live keys

## Non-goals

- Beating good human players. The question is whether an agent can play
  coherently for a decade of game time on a budget, not whether it can win.
- Multiplayer, in any form.
- Hooking the engine or reading process memory. The bridge stays at the level a
  player operates at: files the game writes, and input a person could send.
