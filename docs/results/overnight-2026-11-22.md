# Overnight 2026-11-22: live verification, then a 1936-1948 campaign

Unattended (the person asleep). Game: HOI4 1.20, vanilla + llm_bridge (scripts/live_setup.py), launched with -debug_mode. Model: Claude Haiku through the Claude Code CLI (`claude -p`, no API key). Every finding below is from the live game unless marked otherwise.

## Phase 1: verification (Spain 1936)

### Run 1 (00:38-00:48): the game crashed at the first weekly tick

Launched hoi4.exe -gdpr-compliant -debug_mode (native.launch_game), menus by mouse (Single Player, New Game, 1936, Spain, Start), `human_ai` and `gamespeed 4` through the console channel. Findings:

1. **No harness country, no inbox.** After `human_ai` the AI owns Spain, so `llm_bridge_is_harness` was false for every country: the daily hook (telemetry and the inbox dispatcher) fired for nobody. One state line, then silence. Marking Spain (`effect SPR llm_bridge_mark_harness`) brought the daily ticks back. In the real rival setup a person plays, and is_ai = no makes their country a harness country; a no-human run must mark a country.
2. **Telemetry verified live:** state, army, focus, research, divend, camp, the weekly intel line, and the new observe lines (res, tension, equip, garrison) with sensible values (Spain: steel 108 produced, 54 exported; infantry equipment 11,889 of 12,490 needed; world tension 4.9).
3. **Intel is already a percentage.** `civilian_intel` and friends read 40/5/30/5, so the mod's x100 gave 4000 and the reader clamped it to 100%: every country would have shown as fully known. Fixed (no multiply).
4. **Crash.** Access violation with a deep recursive stack at 1936.03.29.01, right after the weekly observe effect: its peace-conference block raised "Peace conference trigger is called when there is no active peace conference" three times, then the faction-influence and volunteer blocks (nested every_country with has_volunteers_amount_from on PREV/ROOT). Those blocks are removed; resources, tension, equipment and garrison stay. Some observe lines were logged twice (unexplained).
5. **138 load errors** in `llm_bridge_steer_regions.txt`: `equipment_market_for_sale_factor` takes the game's own category tokens (`armor`, `train`, as CZE.txt and the documentation use), not archetype names. The `sell` market steer is dropped.
6. **The inbox was never acknowledged.** The probe batch sat posted while Spain was marked and its daily hook ran for three days before the crash. Suspected: the writer's tmp + atomic rename is a rename the game's file watcher ignores. The writer now writes in place. To be retested.
7. Focus: VS Code came to the front during the run; the console path's focus guard kept keys out of it.
8. Weekly usage: the Claude Code banner read 77% of the weekly limit at 00:43, so model calls tonight stay lean (Haiku, sparse wakes).

### Run 2 (00:55-): the inbox does not reload; everything else through the console

1. **The inbox does not work live.** With Spain marked, daily ticks flowing (57 state lines) and the writer now writing in place, the probe batch was never acknowledged in 150 s (about 50 in-game days). 1.20's documented console reloads cover the interface, technologies, OOBs and textures, not events or scripted effects, and the game logged no reload of the file. The inbox stays in the code, opt-in (`--transport inbox`); `native` now defaults to the console path (keys) acting in the person's pauses, which is what rival mode verified on 2026-11-21. A new `idle` policy acts whenever the keyboard is idle (unattended runs).
2. **The brief works on live data** (save of 1936-03-01 plus the log): Spain's 17 divisions by state with terrain, org, manpower, dug-in and what each faces; industry; resources; the equipment gap; intel-gated neighbours (civilian intel 40% gave estimated factories, army intel 5% gave only the divisions seen in contact); DLC line.
3. **Bugs seen in the brief:** accented names garbled (the save is not UTF-8: needs a Windows-1252 fallback); world tension shown 0% while the log reads 4.9; the politics view's political power 21.4 against 209 in the log.
4. **Actions confirmed live through the console** (a marker logged inside the guard): `diplomacy_act guarantee POR` ("SPR now guarantees POR"), `set_front_order hold`, `set_region_order dominate` (region 68), `steer_production`. `change_law` refused for 150 PP with 93 held (correct). `take_decision` on a civil-war decision not yet available: unverified, as designed. `design_equipment` refused a design with no main armament (correct), and is not offered when the game's AI owns production.
5. **Every guard that copies a law's or decision's `has_dlc = "Death or Dishonor"` failed on the console** (it splits quoted text on spaces). Fixed: the console channel folds `has_dlc` to `always = yes/no` from the playset's active DLCs before writing.
6. **Region names:** "Western Mediterranean" did not match "Western Mediterranean Sea"; a unique partial name now matches. The LLM has no tool that lists strategic regions yet.
7. **Late countries work live:** `native --rival SPR --breakaway --rival-act-when idle` read "D01 broke away from SPR (democratic)" from the mod's dyn line, started on D01 (the Republic, the AI's Spain having gone the other way), marked it and saw it report.

### Run 2b (01:10-01:35): the LLM plays the Republic (D01) in the civil war

Haiku through `claude -p`, rival mode on the breakaway, `idle` policy (orders typed whenever the keyboard is idle).

- **It plays with the new tools.** Every turn from 1937-03 on gave 3-4 `set_front_order` orders (push/hold by state), plus `steer_production`; windows ran them ("4 ran"). A second breakaway, D03, appeared and declared war on D01; the wake on war declaration fired.
- **Mistake: invented ids.** `steer_research` with `synth_oil`, `synthetic_oil`, `synthetic_oil_production`, rejected each time with "nearest real ones: synth_oil_experiments", and the model never used the suggestion. Fixes: a unique prefix now resolves (`synth_oil` -> `synth_oil_experiments`); the rival guidance says to take the suggested id and never resend a rejected guess.
- **Gap: no directives at dynamic tags.** "the mod has no directive plumbing for D03": the directive blocks were generated for 1936's tags only. D01-D20 added.
- **Bug: battles showed the rival's own side as "?"** and read the winning verdict from the wrong side: the participant parser required an all-letter tag, so D01 was dropped. Fixed.
- **Mistake: long notes lost.** The model's plan notes ran past 500 characters and were rejected outright. Now trimmed to 600.
- **Brief observations:** 45 divisions, 28 battles with tactics and an estimated bar, the civil war front by state, industry; accented names garbled until the Windows-1252 fallback (fixed).
- Judgement note: the model set a defensive posture while holding `invade SPR`, then steered with front orders; front orders are the better fit here and the guidance should say that posture and front orders combine (posture = the whole army's stance, front orders = where).

## Phase 2: campaign (Germany 1936-1948)

## Mistakes and fixes
