# Expert AI as the base AI

Everything the game's own AI does for the harness is whatever AI the game runs:
- the whole country under `human_ai`;
- an army handed to the AI;
- every opponent.

Vanilla HOI4's AI is weak at templates, production, research and country plans. Expert AI
(Steam Workshop [741805475](https://steamcommunity.com/sharedfiles/filedetails/?id=741805475),
source at [github.com/Kondikka/expertai](https://github.com/Kondikka/expertai)) rewrites most of
that layer by script:
- construction, political power, trade, lend-lease and experience are spent by its own scripted
  effects;
- country plans are written per country (Germany's Barbarossa, the Pacific war, and so on);
- templates are reworked;
- the province map is shipped as script data so its scripts can reason about geography.

It does not change how the engine fights a front. That layer is engine code that no mod
reaches; the battle-plan planner is the harness's answer to it (docs/tactics.md).

The harness cannot bundle Expert AI: its repository has no licence. Subscribe to it on the
Workshop and enable it in the launcher's playset, with `llm_bridge` loaded last.

## What the harness does with it

`base_ai` in the profile is `auto` (the default), `vanilla` or `expert_ai`. With `auto` the
campaign reads the launcher's `dlc_load.json` at the start. If the playset can't be read, it
assumes vanilla.

| | vanilla | Expert AI |
|---|---|---|
| directive weights | 150 per strategy | 150 + 10,000 (`modgen.STRONG_BOOST`) |
| sent at the campaign start | nothing | `effect <TAG> llm_bridge_strong_directives` |
| logged | `base AI: vanilla (...)` | `base AI: expert_ai (...)`, `strong directive weights on` |
| `campaign_state.json` | `base_ai: vanilla` | `base_ai: expert_ai` |

**Why the weights change.** Expert AI's own country plans weigh 500 to 50,000 for the same
`ai_strategy` types the directives use. Germany's file, for example, has `antagonize POL 5000`
and `declare_war POL 500`. A directive at 150 would be noise next to those, so the model's
steering would do nothing.

The mod therefore has a `*_strong` twin of every directive and posture block, on only while
both the directive's flag and the global flag `llmb_strong_directives` are set. The twin adds
to the normal block; it doesn't replace it. Theater postures add 1,000, and the global postures
500/300 (defensive) and 1,000 (offensive). These are starting points, not measured values.

**Which countries it affects.** Expert AI's scripts run only where `is_ai = yes`:
- **Under `human_ai`** (the game-AI campaign) that includes the player's country, so Expert AI
  is the AI the model steers.
- **In the independent mode** it runs the opponents only. The strong weights aren't sent there,
  because nothing steers the game's AI.

## The checks (`hoi4-harness campaign-check`)

- **PASS** when Expert AI is enabled, or when the AI is vanilla.
- **FAIL** when `base_ai = expert_ai` and the playset doesn't enable it.
- **WARN** when:
  - more than one AI mod is enabled (they overwrite each other's AI files);
  - Expert AI is enabled but `base_ai = vanilla` (the directives are drowned out);
  - its `supported_version` differs from the install's at major.minor. Expert AI 5.0 says
    1.19.2.0, and this PC runs 1.20.0.0.
- **INFO** on the difficulty bonuses (below).

## Keep its difficulty bonuses off

Expert AI can give AI countries bonuses: static and dynamic modifiers, attrition reduction,
free convoys, reinforcements, granted resources and supply. Those are cheats, and a better
result bought with them says nothing about the AI.

They are off by default. The variable they key on, `EAI_difficulty_bonus`, is set only by its
in-game configuration menu (`common/scripted_guis/EAI_country_config_scripted_gui.txt`). Don't
open that menu during a run that will be compared with anything.

## Checked statically, not yet live

These were read from the installed Workshop copy, Expert AI 5.0:
- **Files:** no file path is shared with `llm_bridge` (the two descriptors aside).
- **Names:** every scripted effect, trigger and strategy name in `llm_bridge` is prefixed
  (`llm_bridge_` / `llmb_`), so nothing overrides anything.
- **`on_actions`:** both mods add blocks, and the game runs every block.
- **Load order:** Expert AI has no `replace_path`.

Still to check in the game:
1. Both mods load on 1.20 without errors in `error.log`.
2. Expert AI's plans run for the player's country under `human_ai`.
3. A strong directive visibly changes what Expert AI does (the directive-effect line, #11).
4. Its version mismatch doesn't break anything that matters.

The first live session with it is a pair of no-model runs (Germany 1936, vanilla against
Expert AI, same start) to measure what the base layer alone is worth.
