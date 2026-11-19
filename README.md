# hoi4-agent-harness

A skeleton harness for letting an LLM play **Hearts of Iron IV** — any LLM, through
one interface: Anthropic, OpenAI, anything OpenAI-compatible (Ollama, vLLM,
LM Studio, OpenRouter), Google Gemini, or a scripted stub that runs offline.

This is scaffolding, not a finished bot. The parts that are real are real and
tested; the parts that are stubs say so, in this README and in the file that
contains them.

```
git clone https://github.com/Xocas12/hoi4-agent-harness
cd hoi4-agent-harness && pip install -e ".[dev]"
hoi4-harness play --turns 20        # full offline run, no API key, no game needed
```

## The two problems this is built around

HOI4 is a real-time game with an enormous state space, and language models are
slow and priced per token. Those two facts kill the naive design — screenshot the
screen every second, ask a model what to do — before it starts. Two mechanisms
answer them.

**The harness owns the clock.** The game is pausable, so the loop pauses it,
reads state, decides, applies actions, then runs the game forward a fixed number
of in-game days and pauses again. The agent is never racing the game; from its
point of view HOI4 is turn-based. A critical event cuts the wait short, so a war
declaration is not sat on for six in-game days.

**Most moments are not decisions.** A deterministic reflex layer handles the
obvious and the urgent for free — empty construction queue, idle factories, pause
when something explodes. The model is woken only at decision points: a focus
finished, a research slot opened, a war started, a front broke, or the scheduled
review came round. That rule lives in one file, [`agent/policy.py`](src/hoi4_harness/agent/policy.py),
because it is the entire cost model of the project.

Everything else follows from those two:

| Lever | What it does |
|---|---|
| Delta briefs | One full situation report, then diffs until the diff gets big or N turns pass. A delta is a quarter the size of a brief. |
| Cached prefix | System prompt and tool schemas are byte-identical every call, so they bill at cache-read rates. Volatile text lives in the user message, never in `system`. |
| Fresh context each turn | Memory is a bounded journal the agent writes itself, plus a rolling digest — not a growing transcript. Old briefs contradict new ones and cost money to re-read. |
| 16 coarse actions | Not 200 fine ones. The tool block is on every request, and a long catalog confuses small models. |
| Hard budget ceilings | Calls, tokens and dollars. On exhaustion the loop drops to the reflex layer and keeps playing rather than stopping. |

Measured against the mock (`hoi4-harness measure`), first with a scripted planner
and then with `claude-opus-5-5`: every turn of a campaign is a scheduled review,
so the wake rate is the turn rate (about 4.3 a month, peace or war). The scripted
planner's estimate of ~2.2k input tokens a wake was **wrong on input** — a real
model sent 4.9k in peacetime (2.2x the estimate) and 7.1k in a scripted war
(3.2x), because it makes 1.4–1.7 calls a wake and each resends the prompt. The
full 1936→1939 campaign extrapolates to roughly 0.94M input tokens and $2.4 at
the priced rates; that is extrapolated from capped runs, not run to the end.
These are mock-world numbers: a real game's wartime brief and wake rate are still
unmeasured. Method, caveats and the tables are in
[docs/cost-and-timing.md](docs/cost-and-timing.md).

## Total customization

Everything the harness decides is a setting: precedence is defaults → environment
→ profile file → CLI flags. `hoi4-harness prompt` prints the exact system prompt a
run would use; `hoi4-harness doctor` prints the resolved configuration. After a
run, `hoi4-harness replay run.jsonl --turn 3` rebuilds the prompt that turn was
given and shows what another model calls in the same situation — nothing is
executed.

The layer worth knowing about is **guidance** — how much the harness tells the
model about playing HOI4. The mechanics half of the prompt is fixed (without it
the model cannot operate the tools); the strategy half is a swappable markdown
file:

| Pack | What it says |
|---|---|
| `none` | Nothing. Form your own view of the game. |
| `unconstrained` | Play to win by any in-game means: ahistorical, opportunistic, exploit-friendly, not scored on restraint. |
| `minimal` | Three mechanics facts, no strategy. |
| `doctrine` | Opinionated general advice. Default. |
| `historical` | Stay close to what the country actually did. |
| `coach` | A prescriptive checklist, for smaller models. |

```bash
hoi4-harness play --guidance none                     # benchmark the model, not my doctrine
hoi4-harness play --guidance ./my-doctrine.md         # or your own file
hoi4-harness play --system-prompt ./whole-thing.txt   # or replace the prompt entirely
hoi4-harness play --profile profiles/unleashed.toml --live   # off the leash
```

Point it at an install and a playset (`--game-dir`, `--mod`, or
`HOI4_GAME_DIR` / `HOI4_MOD_DIRS`) and invented focus, technology, state and
country ids are refused with the nearest real ones, the brief lists the focuses
available now, and a total conversion is recorded as such — see
[docs/playsets.md](docs/playsets.md).

Playing alongside the model rather than watching it: `--advisor` (it recommends,
you act), `--player-clock` (it never touches the clock) and `--handover` (its
actions queue until you hand it the keyboard, and are re-checked before they
run) — see [docs/co-op.md](docs/co-op.md).

Also configurable: which actions exist at all (`--actions`, `--without`), whether
the reflex layer runs (`--no-reflex` hands every decision to the model), whether
the model is woken at all (`--no-llm` plays on reflexes alone — the baseline
every score is reported against), whether irreversible actions are gated and who
answers for them (`--allow-all`, `--confirm`), the wake rules, the pacing, the
budget ceilings, and the game
start. Three worked profiles ship in [profiles/](profiles/). Full list:
[docs/customization.md](docs/customization.md) and
[docs/guidance.md](docs/guidance.md).

The agent's in-game conduct is not policed. The only structural limits are
schema validation (a malformed call is a bug, not a strategy), the confirmation
gate you can switch off, and the budget.

## Two layers of control

The game already ships a competent operational commander: it assigns divisions
to fronts, plugs holes, pulls reserves, and does it within a tick at full map
resolution. A model doing that job is slower, dearer and worse. So the harness
supports a **hybrid** mode where the native AI runs operations and the model
commands the layer above it — who to invade, what to build toward, which theater
matters, what posture to hold:

```bash
HOI4_OPERATIONAL_CONTROL=ai hoi4-harness play --guidance hybrid --live --allow-all
```

Setting that mode removes the direct-command actions from the tool list (even if
a whitelist named them), adds `set_ai_directive` / `set_ai_posture` /
`delegate_army_to_ai`, and tells the model in the system prompt which layer it
is. Offering both vocabularies at once is the failure case — a direct order and a
standing directive that disagree produce behaviour attributable to neither.

Whether hybrid actually *plays better* is only partly answered. The first
experiment ran the same scenarios three ways (model-only, AI-only, hybrid) on the
**mock**, 8 seeds each: hybrid won `winter_crisis` and tied AI-only on
`defensive_war`, which all three arms failed. That says something about the
harness against a scripted world, not about HOI4. In the real game it is not
known that the AI changes its behaviour under a directive at all: directives and
per-theater posture are raised and read back (verified live), but the AI's
response to them has not been observed. See
[docs/results/10-hybrid-experiment.md](docs/results/10-hybrid-experiment.md) and
[docs/hybrid-control.md](docs/hybrid-control.md).

## The mod

[`mod/llm_bridge/`](mod/llm_bridge/) is a small HOI4 mod that gives the harness a
real bridge. The game prints a structured state line into `game.log` once a day, on the
player's own tick, and the harness tails it — exact numbers, live, for the cost of
a file read, which is cheaper than parsing a save and cheaper than a vision call.
It also carries the decisions and `ai_strategy` blocks the hybrid layer pushes
intent through.

It has been run in a live HOI4 1.19.5.0 game (Sweden, 1936–37; the mod declares
`supported_version 1.19.*`). It loads with nothing in `error.log`, including the
2.4 MB generated probe. Telemetry schema v3 reports real values for political
power, stability, war support, factories, manpower, techs and construction
counters, the running focus and the technologies being researched, and war and
capitulation lines for the player's own wars only. A completed focus was reported
live (`focus complete: SWE_defense_act`). Not yet seen live: research completion
through the techs counter (tested, not observed) and the v3 `owned` field
(factory count), which was added after the session. Directives and per-theater
posture are raised through decisions and read back in the brief (live: `AI
control: ETH: offensive | GER: defensive`); whether the game's AI behaves
differently under one is **not verified**.

```bash
export HOI4_LOG_PATH="$HOME/Documents/Paradox Interactive/Hearts of Iron IV/logs/game.log"
hoi4-harness play --adapter logtail+input --live
```

The script tokens in it (variables, `on_action` names, `ai_strategy` types) are
the part that moves between game versions, and Paradox script fails silently — CI
checks structure, but only the game can tell you a token is real. Details and a
verification checklist: [docs/mod-bridge.md](docs/mod-bridge.md).

## How it fits together

```
 adapter  ->  observation  ->  policy  ->  agent loop  ->  env  ->  adapter
 (read)       (brief/delta)    (wake?)     (LLM+tools)    (validate,
                                                           gate, apply)
```

- **[adapters/](src/hoi4_harness/adapters/)** — the bridge to a running game. `mock` (complete),
  `logtail` (complete, needs the mod), `savegame` (text saves: the player's numbers, research, production, divisions),
  `screen` (capture done, vision call stubbed), `input_driver` (write side: scan-code keys and the focus script verified live, the rest not yet),
  `composite` (pair a reader with a writer).
- **[actions/](src/hoi4_harness/actions/)** — the catalog, a dependency-free schema validator, and
  provider-neutral tool specs.
- **[observation/](src/hoi4_harness/observation/)** — full briefs, deltas, and the rule for choosing.
- **[agent/](src/hoi4_harness/agent/)** — policy, memory, budget, prompts, loop, and the provider layer.
- **[eval/](src/hoi4_harness/eval/)** — scenarios with checkable objectives, scored on outcome *and* cost, and
  reported against a recorded reflex-only baseline (`eval --no-llm --write-baseline`),
  across seeds (`--seeds N`, median and range). See [docs/evaluation.md](docs/evaluation.md).

## Status

| Piece | State |
|---|---|
| Agent loop, budget, memory, policy | Working, tested |
| Action catalog + validation | Working, tested (20 actions) |
| Observation briefs + deltas | Working, tested |
| Mock adapter | Working, tested, deterministic |
| Log-tail adapter (reads the mod's telemetry) | Working, tested, run against a live 1.19.5 log; `verify-bridge` checks tag consistency and date shapes, and the brief shows focus, research and theater postures |
| Hybrid control vocabulary + mode switch | Working, tested; directives and theater posture raised and read back live; the AI's response to them not verified; mock-only experiment (#10) |
| LLM Bridge mod | Verified live on 1.19.5.0: loads clean, schema v3 telemetry (daily, player's tick), focus completion seen; research completion and the `owned` field not yet seen live |
| Eval runner + scenarios | Working, tested (7 scenarios, multi-seed, cross-model) |
| Cost measurement (`measure`) | Working, tested; measured against the mock with `claude-opus-5-5` (#14): the scripted estimate was 2.2x low on input in peace, 3.2x in war |
| Wartime brief + wake rules | Working, tested against synthetic and scripted wars; measured on the mock with a real model; unmeasured in a real game war |
| Identifier index (playsets, #53) | Run on a real total conversion (The New Order: 512 countries, 15,069 focuses, 897 techs in ~4 s); near-match rejection, scenario playset declarations and a load-order warning when the bridge is not last |
| Per-target AI directives | Generated and CI-checked; raised and read back live; whether the AI acts on them not verified |
| Theater posture (#9) | A theater is the country a front faces; raised in the mod and read back live |
| Guidance packs + profiles | Working, tested (6 packs, 3 example profiles) |
| Anthropic provider | Conformance suite passes live (#22); token accounting audited in code, still to be compared with a Console invoice (#23) |
| OpenAI-compatible providers | Written, not yet run against a live key; a second provider is still needed for conformance (#22); suite in `tests/live/` |
| Gemini provider | Written; the SDK surface moves — check this first if it fails |
| Save-file adapter (#6) | Reads a 1.19.5 **text** save (needs `save_as_binary=no`): political power, stability and war support (via the mod's variables), manpower, owned factories, focus, research slots, production lines (factory counts) and divisions by template. The construction queue is not in the save. Verified against a real save |
| Console channel (#83) | Built and unit-tested, opt-in; not yet run live; open questions in [docs/adapters.md](docs/adapters.md) |
| Screen adapter | Capture works; vision→state is TODO |
| Input driver (#3) | Keys go out as hardware scan codes (pyautogui keys do not reach HOI4, verified live), with the real 1.19.5 hotkeys and no Escape in scripts. `set_national_focus` via focus search is verified live end to end through the harness. Tech-tree click points are computed from `countrytechtreeview.gui` (within 4 px of 18 measured infantry-tab points), but `start_research`, `queue_construction`, `set_production` and `hire_advisor` have **not** yet been run live through the harness; rotated tabs (armour, air, industry, electronics) are refused. A verification that cannot be settled returns `unverified`, not `rejected`, until a fresh day of telemetry. Tests cannot send real input |

Nothing that is a stub pretends otherwise at runtime: an adapter that cannot
perform an action refuses it, and a field it cannot read renders as `unknown`
rather than `0`.

## Using a model

```bash
export HOI4_LLM_PROVIDER=anthropic   ANTHROPIC_API_KEY=...
export HOI4_LLM_PROVIDER=openai      OPENAI_API_KEY=...
export HOI4_LLM_PROVIDER=openai      HOI4_LLM_BASE_URL=http://localhost:11434/v1   # Ollama
export HOI4_LLM_PROVIDER=google      GOOGLE_API_KEY=...

hoi4-harness play --provider anthropic --model claude-opus-5 --turns 40 --max-usd 2
hoi4-harness eval economy_ramp --provider openai --model gpt-5
hoi4-harness doctor
```

See [.env.example](.env.example) for every environment variable, and
[docs/customization.md](docs/customization.md) for the full settings table.

To check a provider still speaks the wire format the harness expects — tool
schema accepted, tool calls parsed back into valid actions, a second tool round
accepted, usage reported, prefix caching where promised — run the live
conformance suite. It is opt-in because it costs a few cents a provider:

```bash
HOI4_LIVE_TESTS=1 ANTHROPIC_API_KEY=... pytest tests/live -v -rs
HOI4_LIVE_ANTHROPIC_MODEL=claude-haiku-4-5 ...   # pick the model per provider
```

It also runs weekly in `.github/workflows/live-model.yml` for whichever keys
the repository has; providers without one skip. Anthropic passes it live; a
second provider has not been run, so the suite has not yet shown the wire format
is portable. Token accounting was audited in code and priced against a real run's
calls, but not compared with a Console invoice (#23).


## Running it against the real game

Everything that can be built without the game is built; the rest needs one
sitting with a real install. [docs/live-session.md](docs/live-session.md) is that
sitting, step by step: what to run, what to look for, and which issue each
output goes to.

## Where it goes next

Seven milestones, each a claim the repo cannot currently make (v0.2 and v0.3 are
partly made: see the status note in [docs/roadmap.md](docs/roadmap.md)), tracked as
[milestones](https://github.com/Xocas12/hoi4-agent-harness/milestones) with the
work broken out in [issue #24](https://github.com/Xocas12/hoi4-agent-harness/issues/24):

| | Claim |
|---|---|
| [v0.1.1](https://github.com/Xocas12/hoi4-agent-harness/milestone/6) | The harness survives an overnight run |
| [v0.2](https://github.com/Xocas12/hoi4-agent-harness/milestone/1) | The harness plays a real game, not a mock |
| [v0.2.5](https://github.com/Xocas12/hoi4-agent-harness/milestone/7) | The model plays alongside a person, in the same campaign |
| [v0.3](https://github.com/Xocas12/hoi4-agent-harness/milestone/2) | The model commands and the game's AI executes |
| [v0.4](https://github.com/Xocas12/hoi4-agent-harness/milestone/3) | The compression story survives a war |
| [v0.5](https://github.com/Xocas12/hoi4-agent-harness/milestone/4) | A score from this harness means something |
| [v0.6](https://github.com/Xocas12/hoi4-agent-harness/milestone/5) | The machinery is boring and trustworthy |

Two of those issues are the questions the project exists to answer: whether an
agent can play coherently for a decade of game time on a budget
([#14](https://github.com/Xocas12/hoi4-agent-harness/issues/14)), and whether
splitting command between a model and the game's own AI beats either alone
([#10](https://github.com/Xocas12/hoi4-agent-harness/issues/10)). Both have a
first answer on the mock only (measured cost with a real model, a hybrid
experiment); neither has been answered in the real game. Everything else is
plumbing in service of asking them properly.

## License

MIT.
