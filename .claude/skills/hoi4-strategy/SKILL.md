---
name: hoi4-strategy
description: How Hearts of Iron IV 1.20 works and what plays well now -- economy, laws, combat width, doctrines after the 1.17 rework, the 1.18-1.20 balance changes, balance of power, custom mod windows, the game AI's weak spots, and a do-not list. Use before steering or playing a HOI4 country with the harness (rival, campaign, mailbox player), or when judging a HOI4 strategy, so the play is not an outdated meta.
---

<!-- GENERATED from src/hoi4_harness/guidance/strategy.md by hoi4_harness.guidance.skill_text. Edit that file, then run: python -m hoi4_harness.guidance --write-skill -->

# Hearts of Iron IV 1.20: how the game works and what plays well now

Sources are marked. **[files]**: read from the 1.20 game files, so exact. **[notes]**: from patch notes for 1.17 to 1.20. **[community]**: the common view of experienced players. It is a sound default, not a law, and some of it is older than the latest balance patches. When the brief or a tool result contradicts this sheet, the game is right.

## What changed recently (do not play an old meta)

- **1.17 (Nov 2025) rebuilt doctrines [notes, files].** Each branch now has one grand doctrine plus subdoctrine tracks earned with mastery. Land grand doctrines: Mobile Warfare, Superior Firepower, Grand Battleplan, Mass Assault. The land tracks are infantry, combat support, armor and operations. Advice about the old doctrine trees and their old bonuses is out of date, even where the names survived. The same patch rebuilt factions (rules and goals set by the faction leader), capped divisions per naval invasion plan, and lowered naval dominance to 60%.
- **1.18 (Apr 2026) [notes].** War score from factions and sunk ships was cut, so winning means taking ground and victory points. The AI got better for Germany, Italy and the UK (Germany now takes Service by Requirement before Barbarossa). Strike forces can no longer be told to avoid combat.
- **1.19 (Jun 2026), with balance passes in Sep and Oct [notes].** It added a regimental support row in the division designer, Special Forces and Dispersed Operations doctrines, and nerfs to tank destroyers, SPGs and self-propelled AA (less HP and breakthrough). Line tanks gained HP at high reliability, and line artillery gained breakthrough. "Spam heavy tank destroyers" and "7/2 is dead" both date from before these changes.
- **1.20 (Nov 2026) [notes].** A small balance patch with Romania's new tree. The Soviet Desperate Measures spirit doubled to +50% mobilisation speed, the Stalin Line got land forts, and France starts 1936 with Stronghold Network. Expect a sturdier USSR in 1941 and a slightly harder France.

## Economy

- **Factories.** Civilian factories build; military factories produce equipment; dockyards build ships. A civilian factory takes about a year to repay its cost, so building civilian factories pays until roughly 1938. After that, every civilian built is a military one not built for the war that is coming [community].
- **Opener.** Civilians first in 1936 and 1937, military factories from 1938 (earlier if a war is close), and refineries or synthetic plants only if short of oil or rubber. Build infrastructure where a factory slot or supply needs it, not everywhere. Production efficiency builds up over time, so keep lines running steadily rather than switching them [community].
- **Economy laws [files].** Each step frees civilian factories and makes conversion cheaper:

  | Law | Consumer goods | Needs war support |
  |---|---|---|
  | Civilian Economy | 35% | |
  | Low Mobilisation | 30% | |
  | Partial Mobilisation | 25% | over 25% |
  | War Economy | 20% | over 50%, and fascist or communist, or at war with an enemy that has over 40% of your industry |
  | Total Mobilisation | 15% | at war and over 80%; costs 3% recruitable population |

  Every law change costs 150 political power. Take Partial early, and War Economy as soon as it is available. A democracy at peace cannot take War Economy.
- **Trade laws [files].**
  - Free Trade: +15% factory output, +15% construction speed, +10% research. 80% of resources go to export.
  - Export Focus: +10% output, +10% construction, +5% research, 50% exported.
  - Limited Exports: +5% output and construction, 25% exported.
  - Closed Economy: no bonus, nothing exported.

  Free Trade or Export Focus is the strong choice unless the war needs your own steel and oil.
- **Conscription [files].** Recruitable population:

  | Law | Recruitable |
  |---|---|
  | Volunteer Only | 1.5% |
  | Limited | 2.5% |
  | Extensive | 5% |
  | Service by Requirement | 10% |
  | All Adults Serve | 20% |
  | Scraping the Barrel | 25% |

  The higher laws cost factory output. Raise the law before manpower runs out, not after.
- **Resources.** Steel, aluminium, rubber, oil, tungsten and chromium. A shortage cuts the output of every line that uses the resource. Fuel runs out in long wars, and an army without fuel stops moving.
- **Political power.** Spend it in this order [community]:
  1. economy and trade laws;
  2. an advisor that adds factories or construction speed;
  3. research or army advisors.

  Keep some in reserve for a war goal or an urgent decision.

## Combat and the army

- **Combat width [files].** Plains, hills and desert 70; forest and jungle 60; mountain and marsh 50; urban 80. Each extra attack direction adds half the base again (35 on plains). Width over the limit costs 1% per point, up to -33%. Infantry, tank and motorised battalions are 2 wide, line artillery 3, support companies 0. Divisions of about 20 and 25-30 width fit most terrain well [community].
- **Organisation and strength.** Organisation decides who keeps fighting; strength (HP) decides who dies. A division at zero organisation retreats. Breakthrough protects an attacker the way defense protects a defender. Tanks win by breakthrough and soft attack; infantry holds by defense, entrenchment and terrain [community].
- **Templates [community, after 1.19].**
  - Holding infantry: about 20 width, with engineers, support artillery and anti-tank or anti-air as the enemy demands, plus heavy weapons in the new regimental row.
  - Assault infantry or armour: 25-35 width, with recon, engineers, logistics and a field hospital.
  - Garrison: the cheapest template, with military police.

  Two or three templates are enough. Many templates split the equipment and the AI handles them badly.
- **Air.** Air superiority first: without it every other mission is weaker, and enemy CAS and bombers punish your divisions. Fighters before CAS [community].
- **Supply.** Supply flows from the capital along railways to supply hubs, then by truck. A front far past the last hub and the trains attrits and stalls. Rail, hubs and trucks are what keep an offensive going, not more divisions [community].
- **Winter, terrain, rivers.** Attacking across rivers, into mountains, marshes or cities, or in Russian winter without preparation bleeds an army. Entrenched defenders in good terrain need far more strength to break [community].
- **Naval invasions.** They need naval superiority in the sea zones crossed (dominance 60% since 1.17) and run under a division cap per plan. The AI rarely invades well [notes].

## War, diplomacy and politics

- **War goals.** Justifying a war goal costs political power and time, and raises world tension (10 for a war with a claim or core, 14 without) [files]. Democracies usually need world tension high, or a threat, before they can justify. Focus trees often give claims or wars that skip justification.
- **Surrender.** A country surrenders as its victory points fall. Take the capital and the big cities, not just ground. Since 1.18 war score rewards taking and holding territory over sinking ships and joining late [notes].
- **Factions.** Joining one drags you into its wars. In a faction you get access to its research and intelligence, and since 1.17 the leader sets goals. A small country's best protection is a guarantee or a faction that can actually reach it [community].
- **Occupation.** Occupied states need garrisons. Rising resistance cuts their factories and resources, and compliance builds slowly under a gentle occupation law [community].
- **Balance of power [files].** Some countries (Italy, Sweden, Switzerland, Finland, Brazil, China and others, plus many mod countries) have a bar between two sides. Each band of the bar has its own modifier, and the outer bands often run effects or unlock focuses. Nothing on screen moves the bar directly. It moves through the decisions in its category, focuses and events. Read where it stands and the band you want, then push with those decisions. The extremes usually bring a cost with the bonus.
- **Custom windows (mods).** Mods put whole mechanics in custom windows: elections, factions, cabinets, economic programmes. Often they are the main way to play that country, and the game's AI presses them by its own weights. Expert AI's settings window changes global rules, and the harness refuses those clicks unless the run allows it.

## The game's AI: what it does well and badly

- It handles fronts, reinforcement and local moves faster than any turn-based planner. Steer it with who to fight (directives), how hard (postures) and when (timing a war), rather than trying to replace it.
- It rarely plans naval invasions well, lets supply lag behind long advances, and spreads divisions thin over long fronts. A defensive posture on a quiet front and an offensive one on the front that matters helps it [community].
- It takes decisions and focuses on its own schedule. It can miss ones that matter for the plan, so check the decision categories that matter (balance of power, war preparation) and take them yourself.

## Do not

1. Do not follow doctrine advice from before 1.17, or tank-destroyer and SPG spam from before the 1.19 balance passes.
2. Do not keep building civilian factories into the year a war starts, and do not switch production lines back and forth.
3. Do not declare a war you cannot supply, or fight one with no air superiority.
4. Do not let manpower or fuel run out unnoticed: raise conscription before the pool is empty, and watch the oil.
5. Do not change laws or advisors for small gains: every change costs political power that a war goal or a decision needed.
6. Do not open a second front by accident: a guarantee or a faction can declare it for you.
7. Do not judge a war by how long it has run. Judge it by surrender progress, and by the victory points taken and held.
8. Do not trust this sheet over the brief: if the game shows something else, the game is right.
