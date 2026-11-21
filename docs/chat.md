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

## The chat window

`hoi4-harness chat-gui` (or the "Open chat window" button in `hoi4-harness setup`) is the same chat in a window, for someone who would rather not type commands. The list on the left has **Everyone (global)** at the top, then one entry per AI country, then your own country (marked "you", not messageable). A country shows its run status (playing, waiting for the game, stopped) and a count of messages you have not read. Pick one, read the conversation, type, press Enter. New lines from the countries appear while the window is open (it re-reads the file every second). It reads and writes only through `chat.ChatLog`, so it and `hoi4-harness chat` see the same thing.

Which countries are AIs comes from `~/.hoi4-harness/players.json`, which every `native` or `rival` run writes when it starts (tag, provider, model, status, pid, started) and marks when it ends; a run that died without marking is shown as stopped. `HOI4_HARNESS_HOME` moves the whole `~/.hoi4-harness` folder (chat, players, setup), which is how the tests keep off the real one. The window needs a desktop; without one it says so, and `hoi4-harness chat` still works.

Multiplayer is different: see [multiplayer.md](multiplayer.md) (talking to the other player).

Not yet: an in-game popup for messages to you (it would go through the inbox as a hidden news event).
