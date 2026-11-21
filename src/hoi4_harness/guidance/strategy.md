<!-- guidance: strategy -- how HOI4 works (any playset) and what plays well in vanilla 1.20; combine with another pack (rival+strategy). Also the Claude Code skill .claude/skills/hoi4-strategy (generated). -->
# Hearts of Iron IV: how the game works, and how to play a country well

Part 1 is the engine: the dynamics that hold in vanilla and in mods (Kaiserreich, TNO, Millennium Dawn, Old World Blues, Road to 56 and the rest), because mods change the content and the numbers, not the machine. Part 2 is vanilla 1.20: exact numbers and what plays well now.

In a mod, trust Part 1 and check Part 2 against what the playset says. The brief, the "This playset" section, `describe_action`, `list_decisions` and `list_gui_buttons` show the playset's real ids, costs and conditions. When they disagree with this sheet, the game is right.

# The game in brief

Hearts of Iron IV is a real-time grand strategy game of the Second World War era (mods move it to other eras and worlds). You lead one country. Time runs in hours and days at a speed the player sets, and can be paused. There is no turn order: every country acts at once, and the game's AI runs every country nobody plays.

A country's life runs in a loop. Political power, earned daily, pays for laws, advisors and decisions. Those set what the economy and army can do. Factories build more factories and produce equipment, and equipment fills divisions. Divisions fight wars. Wars are won by taking victory points until the enemy surrenders.

The words that recur:
- **State and province.** The map's units: a province is a tile, and a state is a group of them holding factories, resources and manpower. A state has an owner; the country occupying it in a war is its controller.
- **National focus.** One step on the country's own tree of goals, about 70 days each. It grants factories, claims, wars, alliances and spirits. Most of a country's story is in its focus tree.
- **Decisions.** Actions bought with political power, sometimes timed, often specific to a country. Missions are timed challenges the game gives you.
- **Ideas.** Laws (economy, trade, conscription), advisors and national spirits: standing modifiers on the country.
- **Civilian factory** (builds and trades), **military factory** (equipment), **dockyard** (ships).
- **Division and template.** A division is a unit on the map. Its template is the recipe of battalions and support companies, and it decides cost, width and stats.
- **Organization and strength.** How long a division keeps fighting, and how much of it is left.
- **War support and stability.** How willing the people are to fight, and how content they are. Both gate laws and wars.
- **World tension.** How close the world is to war. Aggression raises it, and some actions (notably for democracies) need it high.
- **Faction.** An alliance with a leader. Members share wars.
- **Victory points.** Cities and capitals; losing them drives surrender.

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

## Navy

The sea decides three things: who can move supply, trade and armies across water, who can land on whose coast, and who can strike a coast from the air. A navy that does none of these is an expensive picture.

**Fleets, task forces and their roles.** Ships are grouped into task forces, and each task force is given a mission in one or more sea regions.
- **Strike force.** Hunts and fights enemy fleets in its regions. It is the mission that wins or loses control of a sea.
- **Patrol.** Holds a region and builds naval dominance there without hunting. Good for a few ships guarding a chokepoint.
- **Convoy escort.** Sails with your convoys and fights raiders. It needs light, numerous, cheap ships with detection, not battleships.
- **Convoy raiding.** Hunts enemy convoys. Submarines are the usual raiders, because they are hard to see.
- **Mine laying and sweeping.** Mines hurt ships crossing a region and sweepers clear them. A cheap way to make a region costly for a stronger navy.
- **Naval invasion support.** Covers a landing. The landing fails when the sea around it is not yours.
- **Training and reserve.** Ships gain experience and are kept out of harm.

**Naval dominance (supremacy) per region.** Each sea region tracks how much naval power each side has there. Dominance comes from ships on missions in the region (the mission type weights it), and from bases, air bases and radar around it. It is slowly won and lost, and it is judged against intelligence: a side that cannot see the enemy counts for less. A side whose dominance is high enough over its enemies controls the region.
What control gates:
- convoys cannot pass through a region the enemy controls, so supply, trade and lend-lease by sea die there;
- naval invasions need the sea around the landing, or they are slaughtered on the way;
- mines, positioning and recovery all improve for the controlling side;
- the air above the sea and the ships below it feed each other, since bombers punish ships and ships carry or cover aircraft.

**Convoys and supply by sea.** Convoys are built at dockyards and are needed for overseas resources, trade, lend-lease, and supply to units across water. A longer route needs more convoys. A route through enemy-held water is blocked. Sinking convoys lowers the efficiency of every convoy that country has, and efficiency returns only after a quiet spell, so a raiding campaign is a drain that builds on itself. The loser feels it as missing resources and war support, not as lost ships.

**Naval bases, repair, range and fuel.** Ships must return to a port with enough base level to repair. Range is limited by fuel and by how far the nearest friendly port is, so forward bases extend where a navy can fight. Ships out of fuel are nearly useless. Naval bases also carry supply to overseas armies, so a landing without a port is a landing on short rations.

**Submarines and detection.** Submarines are cheap to hide and costly to find. They win by being unseen. Detection comes from surface ships' detection stats, sonar, aircraft and radar. Destroyers and escorts with detection and depth charges counter them; capital ships do not. Raid convoys with subs, and protect your own with escorts that can see. A navy with no detection loses its convoys silently.

**Carriers and carrier air.** A carrier carries wings of naval aircraft. Its strength is reach: planes spot, and planes strike far past gun range. Carrier aircraft fight air battles over the fleet, so losing the air fight over a fleet loses the fleet. Planes need the carrier's hangar capacity and the carrier itself needs escorts. Carriers are worth it in oceans with distance; in narrow seas, land-based air does the same work for less.

**Spotting and positioning.** A fleet that sees the enemy first fires first. Spotting comes from detection, radar, aircraft and the intel you hold. Positioning decides how many of your ships actually shoot. A fleet with screens in front and capital ships behind fights well; capital ships without screens are torpedoed by destroyers they could have shrugged off.

**Composition.** Capital ships (heavy guns, armour) win fleet battles if they are shielded and screened. Screens (destroyers, light cruisers) protect them, escort convoys and hunt subs. Submarines raid. Carriers extend the reach of air. A fleet short of screens is the commonest design error.

**What the AI does badly at sea [community].** It leaves fleets in port, splits ships across too many missions, loses convoys to raiders because escorts are thin, and invades poorly. If a navy matters to your plan, assign the task forces and regions yourself.

## Air

**Air regions, bases and range.** Air power works through air regions. Wings are based at air bases, and each base can operate only so many planes for its level, so too many planes in one base cripple all of them. A wing flies missions only into regions in its range. Move wings forward, as the front moves, or the range runs out.

**Superiority.** Air superiority in a region is the balance of the fighters fighting for it. The side with superiority gives its ground troops an attack and defence bonus, protects its supply and bombers, and brings the enemy's reinforcement and entrenchment down. It is why air multiplies everything below it. Fighters win it, and the others only operate well once it is won.

**Missions.**
- **Air superiority.** Fighters fight for the region.
- **Interception.** Fighters attack incoming enemy missions. A good defence against bombers.
- **Close air support.** Hits enemy ground units in battles. Strong against the unarmoured, weak at night, and exposed to air defences.
- **Tactical bombing.** Hits enemy units, infrastructure and supply in a region.
- **Strategic bombing.** Hits factories, rails, resources and ports. Slow, costly, and powerful only with superiority and escorts. It also earns war score.
- **Naval strike and port strike.** Hit ships and ships in port. A strong answer to a fleet that cannot be reached by ships.
- **Paradrop and air supply.** Land troops, or supply an encircled force.
- **Logistics strike.** Hits trucks, trains and railways. A good way to starve an offensive.
- **Recon and training.** Build intel and experience.

**Anti-air and radar.** Anti-air buildings and guns shoot at bombers and reduce the enemy's superiority bonus. Radar finds aircraft and ships and helps interception. Both are cheap ways to blunt an air campaign.

**Aces.** Named pilots give a bonus to a wing, strongest in a small wing.

**Losses and efficiency.** Planes are shot down and the wing loses strength and efficiency. Moving a wing between regions costs efficiency for a time. Lack of fuel or supply cuts what a wing can do. Experience raises a wing's quality. A wing kept fresh does more than a wing thrown in at once.

**Order of work.** Build air bases where the front is. Win superiority. Then add close air support or tactical bombers, and bombers against logistics or ports. Do not build bombers and leave them with no fighter cover.

## Logistics in depth

- **Hubs and flow.** Supply starts at the capital and travels along railways to supply hubs, then to units by trucks. Each hub has a reach. A hub with more railway, more infrastructure and more motorisation reaches further.
- **Railways.** Railway level caps how much supply can flow to a hub. A weak rail makes a large army hungry however many trains there are.
- **Trucks and trains.** Trains carry supply along rails. Trucks extend a hub's reach to the front. Both are equipment you must produce and keep, and both are bombed. A shortage of either reduces the supply that arrives.
- **Ports.** A port with a high level supplies units across water. An overseas army lives on port throughput and convoys.
- **Attrition and out of supply.** Attrition is a daily loss from terrain, weather and supply. A unit out of supply carries a few days of its own, then loses organisation, speed, attack and defence and equipment, and gets worse for each day it stays out.
- **Non-core territory.** Newly occupied land gives less local supply. A fast advance into foreign land is slowed by this.
- **Encirclement.** A pocket cut from supply fights on its stored supply, then collapses. It can be fed by air supply, or by breaking in. Encirclement is cheaper than a frontal battle and decides campaigns.
- **Fuel** is separate: armour, mechanised units, planes and ships burn it, and a unit at low fuel loses speed and equipment performance.

## Intelligence

- **Agency and upgrades.** Create an intelligence agency, then upgrade it. Upgrades give operative slots, better operations and defence against the enemy's agents.
- **Operatives** are agents you send to other countries. Missions include building an intelligence network, infiltrating, boosting or lowering an ideology, harassing, stealing technology or equipment designs, and rescuing or killing. They can be caught and lost.
- **Operations** are multi-step missions with a target and a cost, and they give bonuses (a broken cypher, a cut in an enemy's production, a coup attempt).
- **The intel ledger.** You hold a level of intel on every country in four areas: civil, army, navy and air. Sources are networks, operations, radar, shared alliance intel, and what you spot. Intel decays unless fed.
- **What low intel hides.** With low intel, the enemy's units show as ranges and guesses, fleets are fuzzy, and naval dominance is penalised. With high army intel, your ground troops gain a small combat bonus. A decision made on a number you hold with low intel is a guess, and the guess may be far off.
- **Counter-intelligence.** Your agency's defence raises the cost of hostile actions against you and the chance of catching agents.

## Special projects, scientists and facilities

- **Facilities** (such as reactors and rocket sites) are buildings a country builds and supplies. They are also targets: they can be captured or bombed, and a captured facility hands part of its progress to the captor.
- **Special projects** are long, expensive research items on a facility. Each runs through a prototype phase made of iterations, and each iteration can pay out a reward. A project needs a scientist with the right specialisation, supply to the facility and resources.
- **Scientists** are characters you recruit with political power. They gain skill by working, and their level speeds a project. They can also do basic research, which gives research bonuses to matching technologies.
- **Choices.** Do not start a project the country cannot supply, or that does not serve the plan. A faction can share projects, and supportive scientists can add progress to an ally's project.

## Industrial organisations (MIOs)

- Each organisation serves one kind of equipment. You assign it to a **research slot** (a design team, which speeds up techs it matches) or to a **production line** (a manufacturer, which feeds funds as the line produces).
- **Funds** make the organisation grow. Each growth step gives points to unlock **traits**, which are permanent bonuses to that equipment (production, stats, cost).
- **Policies** shape what the organisation focuses on. Assigning one costs political power.
- Pick the organisations that match what the country will actually build, and put them on the lines that run for years. An organisation on an idle line grows nothing.

## Trade and the market

- Countries that produce more of a resource than they use can export it. Countries that lack one can buy it. Trade needs convoys when the route crosses water.
- Trade laws set how much of your surplus is offered to the market, and what that costs or gives your economy. Selling more helps your economy and starves your own war effort if you need the resource.
- Buying a resource costs civilian factories' output. A country that must import steel, oil, aluminium, rubber, tungsten or chromium pays for it from its building budget.
- A resource short in a production line slows it. A fuel shortage grounds the army.
- Overseas states, and lines across the sea, are vulnerable to raiding. Keep important resources on land you can defend.
- Synthetic refineries and domestic mines are slow insurance against the market. Take them when the war is long and the sea is not yours.

## Occupation, resistance, compliance and collaboration

- **Occupation laws** set how an occupier runs the land: how many garrisons it needs, how much resistance builds, what it takes in factories, resources and manpower. Harsher policies take more and breed more resistance, and need more divisions.
- **Garrisons** are divisions placed to hold down a state. Without enough, resistance grows and cuts output. Cheap, numerous garrison templates are the right answer.
- **Resistance** grows with a state's value, unrest at home, claims and the occupier's laws. It sabotages, kills garrison and cuts output. If it reaches the top, the state can rebel.
- **Compliance** grows slowly with time and with good laws. High compliance cuts resistance and unlocks the state's factories and resources.
- **Collaboration.** A government that sides with the occupier can lower resistance and ease surrender. Enemy operatives can raise resistance behind your lines.
- **Puppets versus annexation.** A puppet gives you a share of its output and a base, and costs you no garrison. A direct annexation gives you the land and the burden.

## Diplomacy actions

- **Guarantee of independence.** A promise that you will join if the guaranteed country is attacked. It pulls you into a war you may not want and deters the attacker if you are strong.
- **Military access.** Lets a friend move armies through your land, supply them, and use your ports and air bases. Giving access to an ally can extend their supply and reach.
- **Volunteers.** Send divisions to an ally's war. They bring their equipment and fight under the ally's command, and sending them needs no war declaration.
- **Lend-lease.** Send equipment or convoys to an ally. It needs convoys, can give war score, and takes from your own stockpile.
- **Factions (current rules).** The faction leader sets the rules for the faction (who may join, who may declare war, who must be called, how peace is handled, how a leader is replaced) and picks faction goals. Goals reward members when met. Members contribute (manpower, industry, projects) to faction power. Leaders can be replaced by a member with enough influence.
- **Alliance as a chain.** A faction's wars become members' wars. Check each rule before joining.
- **Truces.** After a war ends, a truce blocks a new declaration between the same sides for a while. Breaking it by joining a war costs political power.
- **Peace conferences.** When a side capitulates, the winners meet to share what it held. War score is earned by fighting (ground damage, province capture, strategic bombing, ships and convoys sunk, lend-lease). A participant spends its score on bids for states, ships, puppets, liberation or a change of government. Bids on the same state raise each other's cost, and a state no-one else bids for is cheap, so claim early what you want most. A winner that did the fighting takes most. An ally that did little gets little.

## Research and doctrines

- **Cost and ahead of time.** A technology has a base cost and a year. Researching it before its year multiplies the cost, and researching it after lowers it. A late tech is a bargain, an early tech is a gamble.
- **Research slots** are the scarce item. Fill each with something you will build in this war. A slot left idle banks only a small amount of progress.
- **Sharing.** Factions can share research, and licensing equipment gives a bonus to the research it matches.
- **Doctrines.** Each branch has a grand doctrine you commit to. You earn **mastery** by training and fighting with units of that branch, and you spend it on subdoctrine rewards. Doctrines change what the army is good at. Choose one that matches the templates you can produce, not the other way round.
- **Equipment line.** Better equipment needs new production lines, and each new line starts at low efficiency. Upgrade a variant when the gain is large and the line is not about to be needed.

## Leaders

- **Generals** command corps, **field marshals** command armies, and **admirals** command fleets. Each has a skill level and traits that give modifiers: attack, defence, planning, logistics, terrain, speed, and many more.
- **Experience** raises skill and unlocks traits. Leaders with matching traits suit particular tasks: a terrain specialist in mountains, an armour commander at a breakthrough.
- **Command power** is spent to recruit and to assign leaders, and to use some abilities. Spend it on the front that matters.
- A good leader on the main front is worth more than a bad one with more men. A bad one on a quiet front costs little.
- **Theatre and faction commanders.** In a faction, some leaders can direct allies' fronts and bring supply and mastery benefits.

## Division design

- **Pick a role.** Infantry that holds, infantry that assaults, armour that breaks, motorised or mechanised that exploits, and cheap garrison. A template that tries to be everything is expensive and dull.
- **Width.** Choose widths that divide the terrain's width, so no part of the front is wasted. Narrow divisions are cheaper and more flexible. Wide divisions pack more punch for their organisation.
- **Support companies and regimental support.** Engineers help against terrain and entrenchment, recon helps the first moves, artillery adds firepower, anti-tank counters armour, anti-air protects from bombers, logistics and field hospitals sustain. Add what the enemy forces on you.
- **Armour and piercing.** Armour that the enemy's piercing cannot beat gives a great exchange. Piercing that beats the enemy's armour gives the same. Check the enemy's equipment and update.
- **Reliability.** Low reliability breaks equipment in the field and in supply attrition. A strong design that breaks is a weak design.
- **Supply and cost.** Heavy units use more supply and more factories. A cheaper design that fits the factories wins over a better one that fills half the divisions.
- **Reserve and recovery.** Organisation recovery and strength decide how often a division can be rotated. Rotate rather than burn.

## Battle plans and the front AI

- **Fronts.** The game draws fronts between you and each enemy. You order a front with a battle plan, and the game's AI assigns divisions and moves them.
- **Plans** are made of arrows and areas: attack a province, defend a line, hold an area. A plan builds a planning bonus while it is drawn, and loses it when changed.
- **Execution types.** **Careful** attacks only when the odds are good and supply is full, and avoids strong forts. **Balanced** attacks on fair odds and accepts some risk. **Rush** attacks everywhere it can, with no care for supply or odds, and burns organisation and strength fast. Use careful where you hold, balanced by default, rush for a short, decisive thrust.
- **Cohesion.** Lets you decide how much units stay together or are drawn in from far away.
- **Reserves and coverage.** The AI spreads divisions across the front by what it thinks the threat is. It can leave a weak point beside a strong one. Check where the fight is.
- **What the front AI does well and badly.** It reacts quickly, reinforces, and picks targets. It overextends, ignores supply on rush, and attacks into forts when told to. Steer it by plan and posture, not by trying to command each division.

## Elections, coups, civil wars and ideology drift

- **Popularity** of each ideology moves with focuses, events, laws, propaganda, foreign operatives and war. The ruling party changes when it falls, in a country that has elections, at the next vote, or by coup or civil war.
- **Elections** in democracies come on a timetable and can change the government. Keep popularity high before a vote.
- **Coups.** A foreign agency can attempt a coup in a country with low stability and a rival party in the right place.
- **Civil wars** split the country between the old government and a revolter, with their own divisions. Win or lose them by taking the capital's states.
- **Drift.** Neighbouring powers, faction leaders and propaganda pull ideologies. A country far from the great powers drifts slowly. A minor beside a dominant ideology often drifts.
- **Government as a constraint.** The ideology that rules decides which factions, wars, laws and focuses are open. A change of government closes some doors and opens others.

## Victory and surrender

- **Surrender progress** is the share of the country's victory points, cores and states that the enemy holds. It is a score for the whole country, not a measure of land. The war ends in capitulation when it reaches the country's limit, and the limit is lower for a country with no cores.
- **Capitulation.** The country stops fighting, its states pass to its occupiers' control, and part of its stockpiles pass to the winners. It may live on as an exile government if allies host it.
- **Peace.** After capitulation the winners hold a peace conference, and the one who took the capital has a strong claim. A war can also end by white peace if nobody wants more.
- **Winning the scenario.** A game has an end date and victory conditions. Many mods set their own (hold a number of points, own the world). Check the brief for what the playset calls victory.
- **A war is won by taking the right places.** Capital, big cities and the ports that feed the enemy. Do not chase the map.

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

## More numbers [files]

Each number below was read from the 1.20 install (`common/defines/00_defines.lua` unless named). Defines are the base values: laws, techs, spirits and traits modify them.

### Navy
- **Control of a sea region.** A side controls a region when its dominance is at least 60% of what is needed over its enemies (`DOMINANCE_CONTROLLED_THRESHOLD_RATIO` 0.60).
- **Dominance moves slowly, and falls faster.** Each day it gains 2% of the target value and loses 4% (`DOMINANCE_DAILY_GAIN_FACTOR` 0.02, `DOMINANCE_DAILY_LOSS_FACTOR` 0.04).
- **Mission weights for dominance.** Patrol 1.0, strike force 1.0, naval invasion support 1.0, convoy raiding 0.5, convoy escort 0.5, mine laying and sweeping 0.3, hold, training and reserve 0 (`MISSION_DOMINANCE_RATIOS`). A task force on the wrong mission builds nothing.
- **Land and air help the sea.** Each air base level adds 0.02, each radar level 0.05 and each naval base level 0.01 to the dominance target in a region (`AIR_BASE_DOMINANCE_FACTOR`, `RADAR_DOMINANCE_FACTOR`, `NAVAL_BASE_DOMINANCE_FACTOR`).
- **Intel gates dominance.** Below naval intel 0.4 (on a 0 to 1 scale) dominance is reduced, and at 0.1 or below you keep only half of it (`NAVAL_DOMINANCE_INTEL_LOW`, `..._MIN_PENALTY` 0.5).
- **Convoys.** Convoy efficiency starts to regain after 7 quiet days, at 0.04 a day, and never goes below 0.05 (`CONVOY_EFFICIENCY_REGAIN_AFTER_DAYS`, `CONVOY_EFFICIENCY_REGAIN_BASE_SPEED`, `CONVOY_EFFICIENCY_MIN_VALUE`). Each supply needs 0.25 convoys (`SUPPLY_CONVOY_FACTOR`), and a route through water you control needs 25% fewer (`CONVOY_CONTROLLED_ROUTE_COST_REDUCTION_FACTOR`).
- **Invasions.** A naval invasion takes 60 days to prepare and one plan can run at a time by default (`NAVAL_INVASION_PREPARE_DAYS` 60, `NAVAL_INVASION_PLAN_CAP` 1). An airborne invasion takes 7 days (`AIR_INVASION_PREPARE_DAYS`).
- **Ports.** Each naval base level repairs 0.05 strength and can repair as many ships as its level (`NAVALBASE_REPAIR_MULT`) and gives 3 supply throughput (`SUPPLY_PORT_LEVEL_THROUGHPUT`). Buildings top out at level 15 (`MAX_BUILDING_LEVELS`).

### Air
- **Base capacity.** Each air base level holds 200 planes (`AIRBASE_CAPACITY_MULT`). A level-5 base holds 1,000.
- **Deployment.** A new wing takes 2 days to deploy (`AIR_DEPLOYMENT_DAYS`). A wing holds up to 1,000 planes (`AIR_WING_MAX_SIZE`).
- **Dogfights.** Up to 3 planes can shoot at each enemy plane (`COMBAT_MULTIPLANE_CAP` 3.0). A speed or agility edge counts up to factor differences of 3.5 and 4.0 (`BIGGEST_SPEED_FACTOR_DIFF`, `BIGGEST_AGILITY_FACTOR_DIFF`).
- **Anti-air.** Each building level of anti-air counts as 5 points of enemy air superiority reduction (`ANTI_AIR_SUPERIORITY_MULT`).
- **CAS at night.** Close air support damage in ground battles at night is multiplied by 0.1 (`CAS_NIGHT_ATTACK_FACTOR`).
- **Fuel.** A base that lacks fuel flies missions at 0.25 efficiency (`MISSION_EFFICIENCY_MULT_AT_LACK_OF_FUEL`).
- **Region change.** Changing region costs efficiency, which returns at 0.888 a day for fighters and CAS, 0.192 for tactical and naval bombers and only 0.072 for strategic bombers (`EFFICIENCY_REGION_CHANGE_DAILY_GAIN_*`). Moving bombers around is slow; do it rarely.
- **Carrier defence.** 35% of a carrier's planes can defend it from air attack (`CARRIER_PERCENTAGE_DEFEND`).

### Supply, attrition and fuel
- **Grace.** Troops carry 72 hours (3 days) of supply (`SUPPLY_GRACE`).
- **Out of supply** reaches its full penalty after 30 days (`MAX_OUT_OF_SUPPLY_DAYS`): up to 20% attrition, speed down 80%, organisation regain down 20%, and maximum organisation capped to 35% (`OUT_OF_SUPPLY_ATTRITION`, `OUT_OF_SUPPLY_SPEED`, `OUT_OF_SUPPLY_MORALE`, `SUPPLY_ORG_MAX_CAP`).
- **In combat without supply,** an attacker loses 25% attack and 65% defence, and a defender loses 35% attack and 15% defence (`COMBAT_SUPPLY_LACK_*`). Being out of supply hurts an attacker's defence most.
- **Capital supply.** 5 base, plus 0.3 per civilian factory, 0.6 per military factory and 0.4 per dockyard (`CAPITAL_SUPPLY_*`). Industry is also logistics.
- **Rail.** Railways go to level 5. Base flow is 10, plus 5 per level (`RAILWAY_BASE_FLOW`, `RAILWAY_FLOW_PER_LEVEL`, `MAX_RAILWAY_LEVEL`). A captured railway cannot be used for 10 days (5 for a core state) (`RAILWAY_CONVERSION_COOLDOWN`).
- **Hubs.** Fully motorising a hub costs 60 trucks and adds 2.2 to its reach (`SUPPLY_HUB_FULL_MOTORIZATION_TRUCK_COST`, `..._BONUS`).
- **Fuel.** Countries start with 25% of the maximum, and the base capacity is 50,000 (`STARTING_FUEL_RATIO`, `BASE_FUEL_CAPACITY`). Armies start losing performance below 25% of fuel, then move at 0.4 speed and give 10% of their equipment stats with no fuel (`FUEL_PENALTY_START_RATIO`, `OUT_OF_FUEL_SPEED_MULT`, `OUT_OF_FUEL_EQUIPMENT_MULT`).
- **Stacking.** A battle with more than 5 divisions on a side takes 2% per extra division (`COMBAT_STACKING_START`, `COMBAT_STACKING_PENALTY`).

### Intelligence
- **Agency.** Creating the agency takes 30 days and 5 factories, and each upgrade takes 30 days (`AGENCY_CREATION_DAYS`, `AGENCY_CREATION_FACTORIES`, `AGENCY_UPGRADE_DAYS`). Every 5 upgrades unlock an operative slot, up to one (`AGENCY_UPGRADE_PER_OPERATIVE_SLOT`, `MAX_OPERATIVE_SLOT_FROM_AGENCY_UPGRADES`). A spymaster needs 3 upgrades (`BECOME_SPYMASTER_MIN_UPGRADES`).
- **Intel level.** Intel on a country is capped at 100 in each of civil, army, navy and air (`COUNTRY_LEVEL_INTEL_MAXIMUMS`). Radar alone can give at most 10 civil and army intel and 20 navy and air (`STATIC_INTEL_SOURCE_RADAR_MAXIMUMS`). Allies share 30% of the gap to the best-informed member (`INTEL_FROM_ALLIANCE_FACTOR` 0.3).
- **Combat value of army intel.** Intel from 5 to 50 on an enemy gives up to a 15% combat bonus (`ARMY_INTEL_COMBAT_BONUS_*`).

### Projects, scientists and industrial organisations
- **Scientists.** Hiring costs 25, 50, 75, then 100 political power depending on how many you already have free (`RECRUIT_SCIENTIST_COST`). Levelling up needs 100, 100, 300, 700 and 1500 experience for the successive levels, and the level sets a progress modifier: -100% at level 0, -5% at level 1, then +5%, +10%, +15% and +25% at the top (`SCIENTIST_SKILL_LEVEL_THRESHOLDS`, `SCIENTIST_SKILL_LEVEL_SPEED_MODIFIER`). A new scientist has a 35% chance of a trait. Unassigning one takes 10 days (`DAYS_TO_REMOVE_SCIENTIST`).
- **Projects.** A prototype phase needs 100 progress points (`PROTOTYPE_PHASE_MAX_PROGRESS`). Full speed needs 3 supply in the facility's province and the floor is 20% of speed (`NEEDED_SUPPLY_FOR_FULL_SPEED_PROJECT`, `MINIMUM_PROJECT_SPEED_FACTOR_FROM_SUPPLY`). A cancelled project takes 10 days to stop and dismantling a facility takes 100 (`DEFAULT_STOP_PROJECT_DAYS`, `DISMANTLE_FACILITY_DAYS`). A captured facility passes 20% of its progress to the captor (`PROJECT_CAPTURE_GAIN_RATIO`).
- **Faction share.** A faction can run at most 3 projects and a facility can have 3 supportive scientists (`MAX_PROJECT_COUNT` in `NFactions`, `AMOUNT_OF_SUPPORTIVE_SCIENTISTS`).
- **MIOs.** A design team on a matching tech gives +5% research speed and costs 0.1 political power a day (`DESIGN_TEAM_RESEARCH_BONUS`, `ASSIGN_DESIGN_TEAM_PP_COST_PER_DAY`). The first size-up needs 700 funds (`FUNDS_FOR_SIZE_UP`) and gives 1 trait point.

### Research and doctrine
- **Tech cost.** Base 110, with a year-ahead penalty factor of 2 (`BASE_TECH_COST`, `BASE_YEAR_AHEAD_PENALTY_FACTOR`). A slot can bank 30 research points (`BASE_RESEARCH_POINTS_SAVED`). License production gives +20% research on that tech (`LICENSE_PRODUCTION_TECH_BONUS`). Tech sharing gives up to +50% (`MAX_TECH_SHARING_BONUS`).
- **Mastery.** A doctrine reward by default costs 100 mastery, monthly mastery gain is capped at 40, and the bank holds at most 200 (`DEFAULT_REWARD_MASTERY`, `MAX_MONTHLY_MASTERY_GAIN`, `MASTERY_BANK_MAX`). Training counts for a tenth of what combat does (`TRAINING_MASTERY_GAIN_FACTOR`).

### Occupation
- **Resistance.** The base resistance target is 35%. It drops 10 points at peace and 5 for a state you claim, and rises 0.2 for each point of stability below 100% (`RESISTANCE_TARGET_BASE`, `..._IS_AT_PEACE`, `..._HAS_CLAIM`, `..._PER_STABILITY_LOSS`). Each point of compliance lowers the target by 0.5 (`RESISTANCE_TARGET_MODIFIER_PER_COMPLIANCE`). When the enemy has capitulated the target rises 10 (`..._OCCUPIED_CAPITULATED`).
- **Compliance** grows at 0.075 a day base, with a bonus at peace and for claims (`COMPLIANCE_GROWTH_BASE`).
- **Laws.** The occupation laws in `common/occupation_laws` include no garrison, autonomous occupation, foreign civilian oversight, local police force garrison, secret police oversight, liberate workers, military governor, martial law, forced labour, harsh quotas and brutally oppressive occupation, and colonial and princely variants for some countries. Some are limited by government (autonomous occupation is for democracies).

### War score, peace and surrender
- **Surrender limit.** A country capitulates at 80% occupation by default, never lower than 20%, and 70% of that if it has no cores (`BASE_SURRENDER_LIMIT`, `MIN_SURRENDER_LIMIT`, `SURRENDER_LIMIT_MULT_FOR_COUNTRIES_WITH_NO_CORES`). A war must have lasted 7 days before anyone can surrender (`DAYS_OF_WAR_BEFORE_SURRENDER`). A country regains its status when it retakes 10% and its capital (`UNCAPITULATE_LEVEL`).
- **War score sources.** A first-time province capture gives 4 times its worth, and casualties give 0.5 per 1,000 (`WAR_SCORE_PROVINCE_FACTOR`, `WAR_SCORE_LOSSES_RATIO`). Ships sunk give 0.05 per IC of the ship, and sunk convoys 0.05 each (`WAR_SCORE_GAIN_FOR_SUNK_SHIP_PRODUCTION_COST_FACTOR`, `WAR_SCORE_GAIN_FOR_SUNK_CONVOY`). Strategic bombing gives 0.02 per building damage and decays monthly with the target's civilian factories (`WAR_SCORE_STRATEGIC_BOMBING_FACTOR`). Lend-lease gives 0.003 per IC sent (`WAR_SCORE_LEND_LEASE_GIVEN_IC_FACTOR`). Patch 1.18 cut the value of sunk ships and faction war score [notes], so the file values above may not be the whole story.
- **Peace conference.** Contested bids on one state raise its cost by 1.2 each time. A bid nobody contests rises 0.15 per turn from 1.15 to a cap of 1.6 (`PEACE_COST_FACTOR_CONTESTED_BID`, `PEACE_COST_FACTOR_UNCONTESTED_BID_*`). A state you do not control costs 10 times as much, and one a faction mate controls 0.5 times (`BASE_PEACE_TAKE_UNCONTROLLED_STATE_FACTOR`, `BASE_PEACE_TAKE_FACTION_CONTROLLED_STATE_FACTOR`).
- **Truce.** A base truce lasts 180 days, and breaking it by joining a war costs a base 200 political power (`BASE_TRUCE_PERIOD`, `TRUCE_BREAK_COST_PP`).

### Factions
- **Rule names** in `common/factions/rules` include joining rules (same ideology, non-fascist, non-democratic, neighbours only, support percentage, puppets only, no offensive war), war declaration rules (any country, independent only, leader only, major only), call to war rules, change leader rules (never, manpower, industry), dismissal rules (world tension, offensive war, ideology), peace rules (puppeting, no puppeting, liberation, ideology, conquest, claimed states) and manpower contribution (small, medium, high). Goals come in short, medium and long term files.
- **Initiative.** Changing a rule costs 1 faction initiative point (`FACTION_INITIATIVE_CHANGE_RULE_COST`). Taking over leadership needs 40% of the faction's influence (`BECOME_FACTION_LEADER_INFLUENCE_THRESHOLD`), and the leader gets 200 influence as a bonus (`FACTION_INFLUENCE_LEADER_BONUS`). A theatre commander can lead 3 countries and 3 regions at base (`FACTION_THEATER_COMMANDER_COUNTRY_LIMIT_BASE`).
- **Kicking a member** leaves a 60-day truce (`TRUCE_PERIOD_AFTER_KICKING_FROM_FACTION`).

### Battle plans
- **Execution types [files].** Careful needs a plan score of 25 and full supply for an attack, and never attacks forts of level 5 or more. Balanced needs a score of 0 and ignores supply. Rush accepts a score of -200 and ignores supply (`PLAN_EXECUTE_CAREFUL_LIMIT`, `PLAN_EXECUTE_BALANCED_LIMIT`, `PLAN_EXECUTE_RUSH`, `PLAN_EXECUTE_CAREFUL_MAX_FORT`, `PLAN_EXECUTE_SUPPLY_CHECK`). Naval invasions gain 2% planning a day (`NAVAL_INVASION_PLANNING_BONUS_GAIN`).
- **Leaders.** Recruiting a general or admiral costs 5 command power times the number you already have, to a maximum of 80 before modifiers (`ARMY_LEADER_COST`, `ARMY_LEADER_MAX_COST`).

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
