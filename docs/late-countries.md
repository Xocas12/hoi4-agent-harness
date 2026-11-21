# Countries that do not exist at the start

Some countries only appear during a game: the side that breaks away in a civil war, a nation released by a focus or a peace deal, a puppet. The harness can play them once they exist.

## A civil war's breakaway side

`start_civil_war` gives the new side a dynamic tag, D01 to D50, that nobody can name in advance. The mod's daily probe logs every dynamic country with the country it broke away from and its ruling ideology (`is_dynamic_country`, `original_tag`, `GetRulingIdeology`):

```
LLMB|v5|dyn|of=SPR|tag=D01|ideo=Fascism
```

So, for the Spanish Civil War, you play Spain and the LLM takes the side that breaks away:

```
hoi4-harness native --rival SPR --breakaway --breakaway-ideology fascism
```

`native` waits for the breakaway (the game keeps running; you play on), reads its tag from the log, and starts the rival on it. Orders it gives before the new country exists wait in the inbox.

## A fixed tag that appears later

A released country keeps its own tag (SLO, for example). Orders for a country that does not exist wait in the inbox: the dispatcher only addresses countries that exist. The start waits with no deadline:

```
hoi4-harness native --rival SLO --wait-for-country
```

## Unverified live

- that `GetRulingIdeology` prints the ideology's name in the log;
- that a breakaway keeps its D0x tag for the whole war.
