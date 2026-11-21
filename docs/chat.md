# Chat with the LLM countries (#133)

In a single-player game where countries are played by LLMs (`native --rival`, `rival`), you can talk to them, they answer, and they can talk to each other. Nothing goes through the game: the chat is a file beside it, so your keyboard and the game window are never touched.

```
hoi4-harness chat --to POL "Poland, join me against the Soviets and I will guarantee you"
hoi4-harness chat "Anyone who attacks Romania answers to me"      # global: every country reads it
hoi4-harness chat --follow                                        # watch replies as they come
```

- **The file:** `~/.hoi4-harness/chat.jsonl` (or `HOI4_CHAT`, or `--chat PATH`), one JSON line per message: `id`, `ts`, `date` (game date when a country wrote it), `from` (`person` or a tag), `to` (`global`, `person` or a tag), `text`.
- **A country reads** the global channel and its own direct lines, never another pair's. New lines show in its brief under "Messages", marked NEW, with a few earlier lines for context.
- **A message addressed to a country wakes it** at the harness's next poll. Global messages wait for its next turn.
- **A country writes** with `send_message(to, text)`: to `person`, `global`, or another country's tag. Two LLM countries in one game (multi rival) can talk the same way.
- **Talk binds nobody.** The strategist is told to judge others, and expect to be judged, by what is done. A promise in chat is not an action; the game's state is what counts.

Not yet: an in-game popup for messages to you (it would go through the inbox as a hidden news event).
