<!-- guidance: strategy -- how HOI4 works (any playset) and what plays well in vanilla 1.20; combine with another pack (rival+strategy). Also the Claude Code skill .claude/skills/hoi4-strategy (generated). -->
# Hearts of Iron IV: how the game works, and how to play a country well

Part 1 is the engine: the dynamics that hold in vanilla and in mods (Kaiserreich, TNO, Millennium Dawn, Old World Blues, Road to 56 and the rest), because mods change the content and the numbers, not the machine. Part 2 is vanilla 1.20: exact numbers and what plays well now.

In a mod, trust Part 1 and check Part 2 against what the playset says. The brief, the "This playset" section, `describe_action`, `list_decisions` and `list_gui_buttons` show the playset's real ids, costs and conditions. When they disagree with this sheet, the game is right.

# Part 1: the engine (any playset)

## How a battle is decided

- **Organization and strength.** Every division has organization (how long it keeps fighting) and strength (its men and equipment, its HP).
  - Attacks hit both, and a division at zero organization retreats, whatever its strength. So most battles are won by draining organization, not by killing.
  - Strength decides what survives and how fast a division comes back.
  - High-organization divisions hold lines. High-attack, low-organization divisions break them but cannot hold for long.
- **Attack against defense; breakthrough against attack.**
  - A defender's defense, and an attacker's breakthrough, decide how many incoming attacks are absorbed. Attacks beyond that hit far harder.
  - So infantry holds by defense, entrenchment and terrain, and tanks attack with breakthrough.
  - Soft attack hurts unarmoured targets and hard attack hurts armour. Armour that the enemy's piercing cannot beat takes and deals much better trades.
- **Combat width.** Each battle has a frontage set by the terrain, and wider again for each extra direction the attack comes from.
  - Divisions that fit the width fight at full strength. Width over the limit is penalised. Width left unfilled is wasted.
  - So division size is chosen to fit the terrain where you fight. Attacking from several directions at once is how a big army brings more of itself to bear.
- **Terrain, rivers, forts, weather.**
  - Attacking into mountains, marshes, forests, cities, across rivers or into forts costs heavily.
  - Winter, mud and heat add attrition. Night favours the side with better equipment for it.
  - The defender chooses where to stand. A front along a river or a mountain line holds with fewer men.
- **Entrenchment and planning.** Divisions dig in while they hold, and plans gain a bonus while they are drawn up. Both are lost by moving. A rushed attack throws away the planning bonus; a defender who has been sitting on a line for months is much harder to move.
- **Air.** Air superiority over a region multiplies everything below it.
  - Enemy close air support and bombers hit divisions and supply.
  - Losing the air slows reinforcement and lowers combat performance.
  - Fighters for superiority come first, ground attack second.
- **Experience and leaders.** Generals' traits and skill, and the army's experience and doctrine, change the numbers. Doctrine is a long commitment that shapes which templates work.

## Supply, the thing that ends offensives

- Supply flows from the capital along railways to hubs, then to the front by trucks or horses. A front past its hubs, or cut off by enemy control, loses organization and strength every day.
- Ports, rails, hubs and trucks are what keep an advance moving, not more divisions. An advance that outruns its rail stalls and then breaks.
- Fuel is separate. Motorised, mechanised and armoured divisions, planes and ships burn it, and a country that runs dry stops moving.
- Islands and overseas fronts live on convoys. Convoys sunk on the way are supply that never arrives.

## Production, construction and research

- **Construction.** Civilian factories build: more factories, infrastructure, rail, forts, refineries. A civilian factory spent on construction compounds. Trade, consumer goods and some laws or spirits take civilian factories away from it.
- **Production.**
  - Military factories make equipment and dockyards make ships.
  - Each line builds efficiency over time, so switching a line throws efficiency away.
  - A line short of a resource slows down.
  - Equipment unproduced is equipment unreinforced: a large army without the factories behind it is a hollow one.
- **The core trade-off.** Factories built early compound; factories built late come too late. The longer the peace, the more building pays. The nearer the war, the more each civilian factory should have been a military one. Know when your war starts.
- **Research.**
  - Each slot researches one tech at a time. Techs ahead of their year cost much more; techs behind it cost less.
  - Industry and construction techs pay back across everything, so they come early. Then equipment you actually produce, the doctrine you committed to, and air.
  - Research nothing you will not build. An idle slot is the most common waste in the game.
- **Manpower.** Conscription laws set how much of the population can be drafted. Losses drain it. Training divisions holds manpower before they reach the front.

## Politics: political power, stability, war support

- **Political power** pays for laws, advisors, decisions and war goals. It is the scarcest currency early, and a decision that compounds (a factory, a construction bonus, a research bonus) is worth more taken now than later.
- **Stability** keeps factory output and political power up and prevents unrest.
- **War support** gates mobilisation laws and war declarations, and sets how readily people accept losses.
- Both move with focuses, decisions, events, ideology and war. A democracy at peace usually cannot mobilise or declare war freely.
- **Ideology.** Governments differ in what they can do: justify war goals, join factions, use some laws, puppet and annex. Mods often add their own ideologies and coups. A country's government constrains its plan before anything else does.

## Diplomacy and war

- **War goals and world tension.** Starting a war usually needs a war goal: justified with political power and time, or given by a focus or decision. Aggression raises world tension, and democracies react to it. Guarantees and factions turn one war into several.
- **Factions** share research and intelligence, and drag every member into the leader's wars. Joining a faction that cannot reach you only adds enemies.
- **Surrender** comes from losing victory points (the capital and the big cities) more than from losing ground. Take those, and an enemy falls; hold a line in empty land, and it does not.
- **Occupation.** Occupied land needs garrisons. Resistance cuts the factories and resources taken, and compliance grows slowly.

## Judging your country and choosing a plan

Before choosing a plan, read your country:

1. **Size.** Factories, manpower and resources against your neighbours'. A country with half its rival's industry cannot win a long war against it, and needs allies, terrain, or a short war on its own terms.
2. **Geography.** Coast or landlocked, an island or a land border, what terrain the border runs through, how many neighbours could attack at once. Mountains, rivers and sea are a minor's best army.
3. **Government and focus tree.** What the tree is built to do (rearm, unify, expand, survive), what it gives (claims, wars, factories, spirits) and when. In mods the tree is often the whole plot, and its decisions and custom windows are where the country's mechanics live.
4. **Timeline.** When the big war comes to you, and who will be on which side. Plan the economy to peak when it is needed.
5. **The other players.** Who the person plays, and who the AI's strong countries are.

Then choose a style that fits, and keep to it unless the situation changes:

- **Builder.** Build long, stay out of wars until strong. Best for large, safe economies far from the first war (the US, the Soviet Union early on, distant minors).
- **Early aggressor.** Strike weaker neighbours before they or their friends are ready. It needs a war goal, war support and the industry lead now, not later.
- **Opportunist.** Wait for a neighbour to be busy or losing, then take claims. It needs good timing, and a war goal ready when the moment comes.
- **Fortress / survivor.** For minors next to great powers: forts and good terrain, a strong defensive line, guarantees or a faction that can actually reach you. Make attacking you more expensive than it is worth.
- **Maritime.** For islands and coastal empires: navy and convoys first, an army sized to defend the homeland and strike where the navy can land it.
- **Faction member.** Feed the faction's war where it matters most (a front, lend-lease, research), not where your own glory is.

A plan says what to build toward, the next war you want and why you can win it, and what would make you change course. Write it down with `note` and judge it by results: surrender progress, victory points held, factories gained.

## Reading a mod

- **Content and numbers change; the machine does not.** Organization, width, supply, efficiency and political power work the same. Expect different start dates, ideologies, factions, tech trees, unit types and resources.
- **Read the playset section and use the tools.**
  - `list_decisions` shows the country's decisions.
  - `list_gui_buttons` shows the buttons in its custom windows, with how the game's AI weighs each.
  - `describe_action` shows any decision, focus, window, button or balance of power bar in full.
  - A mod's mechanics are usually there: elections, cabinets, factions, economy programmes, unification, crises.
- **Weigh what the game's AI does.** `ai_weights` on a button and `ai_will_do` on a decision are what the mod's authors think the AI should do. They are a good guide to what a mechanic is for, not a command.
- **Balance of power bars** run between two sides, with bands that each carry a modifier, and the outer bands often run effects or unlock focuses. They move through the decisions in their category, focuses and events, never by a click on the bar. Choose the band you want; the extremes usually cost as well as give.
- **Global and other-country buttons.** Some windows change the rules for everyone (settings menus, cheat menus, world events). The harness refuses those clicks unless the run allows it, because they would change the other players' game.

# Part 2: vanilla 1.20

Sources are marked. **[files]**: read from the 1.20 game files, so exact. **[notes]**: from patch notes for 1.17 to 1.20. **[community]**: the common view of experienced players. It is a sound default, not a law, and some of it is older than the latest balance patches.

## What changed recently (do not play an old meta)

- **1.17 (Nov 2025) rebuilt doctrines [notes, files].** Each branch now has one grand doctrine plus subdoctrine tracks earned with mastery. Land grand doctrines: Mobile Warfare, Superior Firepower, Grand Battleplan, Mass Assault. The land tracks are infantry, combat support, armor and operations. Advice about the old doctrine trees and their old bonuses is out of date, even where the names survived. The same patch rebuilt factions (rules and goals set by the faction leader), capped divisions per naval invasion plan, and lowered naval dominance to 60%.
- **1.18 (Apr 2026) [notes].** War score from factions and sunk ships was cut, so winning means taking ground and victory points. The AI got better for Germany, Italy and the UK (Germany now takes Service by Requirement before Barbarossa). Strike forces can no longer be told to avoid combat.
- **1.19 (Jun 2026), with balance passes in Sep and Oct [notes].** It added a regimental support row in the division designer, Special Forces and Dispersed Operations doctrines, and nerfs to tank destroyers, SPGs and self-propelled AA (less HP and breakthrough). Line tanks gained HP at high reliability, and line artillery gained breakthrough. "Spam heavy tank destroyers" and "7/2 is dead" both date from before these changes.
- **1.20 (Nov 2026) [notes].** A small balance patch with Romania's new tree. The Soviet Desperate Measures spirit doubled to +50% mobilisation speed, the Stalin Line got land forts, and France starts 1936 with Stronghold Network. Expect a sturdier USSR in 1941 and a slightly harder France.

## Numbers

- **Combat width [files].** Plains, hills and desert 70; forest and jungle 60; mountain and marsh 50; urban 80. Each extra attack direction adds half the base again (35 on plains). Width over the limit costs 1% per point, up to -33%. Infantry, tank and motorised battalions are 2 wide, line artillery 3, support companies 0. Divisions of about 20 and 25-30 width fit most terrain well [community].
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
- **War goals [files].** A war with a claim or core raises world tension by 10, one without by 14. Annexing states raises it further.

## What plays well [community]

- **Economy opener.** Civilian factories in 1936 and 1937, military factories from 1938 (earlier if a war is close). Refineries or synthetic plants only if short of oil or rubber. Infrastructure where a factory slot or supply needs it, not everywhere. A civilian factory repays itself in about a year.
- **Political power.** Spend it in this order:
  1. the economy and trade laws;
  2. an advisor that adds factories or construction speed;
  3. research or army advisors.

  Keep a reserve for a war goal or an urgent decision.
- **Templates (after 1.19).**
  - Holding infantry: about 20 width, with engineers, support artillery and anti-tank or anti-air as the enemy demands, plus heavy weapons in the regimental row.
  - Assault infantry or armour: 25-35 width, with recon, engineers, logistics and a field hospital.
  - Garrison: the cheapest template, with military police.

  Two or three templates are enough. Many templates split the equipment and the AI handles them badly.
- **Air and navy.** Fighters for superiority before close air support. A navy only where the country lives by the sea. Naval invasions need the sea zones held, and the AI rarely invades well.
- **Balance of power, vanilla [files].** Italy, Sweden, Switzerland, Finland, Brazil, Communist China, Czechoslovakia, Denmark and Ethiopia have bars. The brief shows where the bar stands and which category moves it.

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
8. Do not carry vanilla numbers into a mod without checking them, and do not trust this sheet over the brief: if the game shows something else, the game is right.
