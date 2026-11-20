# Roadmap

Where the project is, what "finished" means, and what is open. Everything here is checked against
the issue tracker and the docs it links; nothing is claimed beyond what has run. Issues and
milestones: [github.com/Xocas12/hoi4-agent-harness/issues](https://github.com/Xocas12/hoi4-agent-harness/issues).
The live checklist is [live-session.md](live-session.md).

## Where it stands (2026-11-20)

The first plan was a ladder: a mod and telemetry, then input, then hybrid control, then wartime,
evaluation and harness quality, each milestone a claim the repo could not yet make. The offline half
of that is built and tested against the mock, the fake game, the fake army and the tactical
simulator. What changed since is the shape of control and what has been learned about the game's
own AI.

### Control is modular, not a ladder of levels

`operational_control` and `campaign_control` used to be levels (`llm` / `ai` / `game_ai`). They are
now presets of a **map**: each domain (focus, research, construction, production, politics,
diplomacy, army, navy, air) is run by exactly one of `llm`, `script`, `game` (the game's own AI)
or `off`, chosen independently, with the LLM steering layer (directives, postures, delegation) as
its own switch. The design, the hard constraint on what the game's AI can be handed (the whole
country under `human_ai`, or individual armies) and the contested-domain rule are in
[modular-control.md](modular-control.md). Built and tested on simulators only (#109).

### What the game's AI is, and is not

- **Expert AI is the base AI** (#108). It is detected from `dlc_load.json`, checked by
  `campaign-check`, and when it is on the directive weights go up so the model's steering is not
  drowned out by its country plans ([expert-ai.md](expert-ai.md)). Whether it changes behaviour
  under a directive in a real game is not verified.
- **It does not change how a front is fought.** That is engine code. The harness's answer is the
  **battle-plan planner** (#110, [tactics.md](tactics.md)). Its results are mostly negative and
  are reported that way:
  - the **learned plan chooser** (#111) did not learn to see what the coarse picture hides; where
    it beats the planner, the gain traces to its keep rule and candidate set, not to training;
  - the planner's **96 h axis patience** (#112) was tested against alternatives and kept; the gain
    that motivated the question is not the clock, and where it comes from inside the chooser is
    not isolated (follow-ups #116 and #117);
  - all of it is tuned against a simulator, and the plan executor's rules are assumptions about
    the engine (the gap list in tactics.md).

### Playing without the game's AI

- **Independent mode** (`--control llm`, #106): the model plays the country itself, with the mouse
  for what the console cannot do: **research and armies** (#107) and **production** (#114, editing
  a line instead of duplicating it). The click paths are drafts until walked in a real game
  (live-session.md sections 14 and 14b).
- **Play on after capitulation** (#105): a campaign no longer stops when the player's own country
  capitulates. It continues as a government in exile, wakes the model the turn it is seen, and
  records it; `campaign_stop_on_capitulation` restores the old stop.
- **A guidance inbox** (#106): `hoi4-harness guide` leaves a running campaign advice in
  `guidance_inbox.jsonl`; the model is woken for it and sees it in every brief until cleared. It is
  advice the model weighs, never executed by the harness ([campaign.md](campaign.md)).

### Verified in a real game (HOI4 1.19.5 and 1.20, two sittings)

- the mod loads without errors and its telemetry is real: daily state, the running focus and
  research, focus completion, and war and capitulation lines for the player's own wars (#1);
- directives and theater posture raised through decisions and read back in the brief (#7 and #9,
  the read-back half only; #7 itself is open);
- keys as hardware scan codes, and `set_national_focus` end to end (#3, one of five actions);
- the save adapter against a real 1.19.5 text save (#6), and playset indexing of a total
  conversion (#53);
- Anthropic passes the provider conformance suite (#22, one of the two providers it needs).

### Built and tested, not yet seen in a real game

- the `start_research`, `queue_construction`, `set_production` and `hire_advisor` click paths (#3);
- army delegation (#8) and the independent-mode research, army and production click paths;
- the headless console channel (#83) and rival mode (#31, #33, #91);
- every control map, the Expert AI directive weights, and the battle-plan executor.

### Measured on simulators only

- Cost with a real model on the mock (#14): the scripted estimate was 2.2x low on input in peace and
  3.2x in war ([cost-and-timing.md](cost-and-timing.md)).
- The hybrid experiment (#10, [results/10-hybrid-experiment.md](results/10-hybrid-experiment.md)):
  a result about the harness against a scripted world, not about HOI4.

## What "finished" means

Finished is a short list of things that have happened in a real game, not a list of features. The
project is finished when all four are true and written up:

1. **A live Germany overnight rerun completes.** The unattended campaign (`campaign-ger-world`)
   passes its pre-flight, plays a night against HOI4, and ends on its end date, its budget or a
   clean stop with a usable report: not a crash, not a silent hang.
2. **One live year of independent mode.** `--control llm` with the mouse plays one in-game year of
   its own country, with research, armies and production through the click paths, and the report
   says which actions were verified, unverified or rejected.
3. **#3, #7 and #8 are verified live.** The five peacetime actions change a real game and report
   failure rather than success when the UI is not as expected (#3); directing `protect FIN` and
   then `protect POL` visibly changes the AI's behaviour toward the right country (#7);
   delegating an army is visible in-game and in the next brief, and un-delegating puts it back (#8).
4. **One real-game comparison of LLM+AI against either alone.** The same scenario in the real game
   three ways: the model alone, the game's AI alone, and the model steering the AI, with the numbers
   reported. #10 asked this of the mock; nobody has asked it of the game.

Not on the list, on purpose: more scenarios, more models, a better learned controller. Those are
research around the harness, not the harness being finished.

## Open work, by milestone

From `gh issue list --state open`, 2026-11-20. The milestones v0.1.1 Robustness, v0.4 Wartime state
and v0.5 Evaluation worth trusting have nothing open.

### v0.2 Live bridge

*The harness plays a real game of HOI4, not a mock.*

- #3 Input driver: UI scripts with verification for the five peacetime actions (one of five
  verified live; "finished" item 3)
- #83 Headless action channel: run script effects through the console instead of walking the UI

### v0.2.5 Co-op play

*The model plays alongside a person, in the same campaign.*

- #31 Co-op: the LLM plays alongside a human, not instead of one
- #33 Action handover: the harness must not type over the player
- #91 Multiplayer: the harness plays one country while a person plays another

### v0.3 Hybrid control

*The model commands and the game's own AI executes.*

- #7 Directives must target the country the agent actually named ("finished" item 3)
- #8 Delegate armies to the AI through the real UI ("finished" item 3)

### v0.6 Harness quality

*The machinery is boring and trustworthy.*

- #22 Provider conformance tests against live keys: the opt-in suite passes on Anthropic; a second
  provider is what is left
- #23 Verify token accounting against a real invoice: pricing is audited; comparing a run with a
  real usage export is what is left

### No milestone

- #113 Independent mode: production by mouse (the code landed in #114; the live clause is open;
  "finished" item 2)
- #85 A learned micro controller under the LLM, good enough to beat competitive players (research;
  not on the "finished" list)
- #116 `evaluate()` leaks a planner's committed axis between games when given an instance
- #117 Isolate why the plan chooser beats the planner on France
- #24 This roadmap

## Non-goals

- Beating good human players. The question is whether an agent can play coherently for a decade of
  game time on a budget, not whether it can win.
- Multiplayer, in any form.
- Hooking the engine or reading process memory. The bridge stays at the level a player operates at:
  files the game writes, and input a person could send.
