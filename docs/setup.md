# Setting up AI countries: `hoi4-harness setup`

## For everyone: the window

You play Hearts of Iron IV as usual. One or more other countries are played by an AI, in the background, while you play. This window is where you choose which countries, and what the AI thinks with.

Open it from a terminal (a Command Prompt or PowerShell window; you only need this once):

```
hoi4-harness setup
```

1. **Choose countries.** Search by name or three-letter tag (POL is Poland) and double-click, or type a tag and press Enter. If the window finds your game it lists every country in your playset; if not, just type the tag. Put your own country in the small box underneath so the AI never takes it.
2. **For each country, pick what it thinks with:**
   - *Claude Code (your subscription, no key)*: uses the Claude Code you already pay for. Nothing to paste. This is the default.
   - *Anthropic / OpenAI / Google API key*: paste your key in step 3. These cost money per use, so set a **spend limit** (US dollars). The AI stops asking the model once it would pass it. The limit needs the price per million tokens (it is on your provider's price page); without it the window will not let you start, because a limit that counts nothing is worse than none.
   - *Local model*: a model running on your own PC through Ollama or LM Studio. Give its address (for Ollama, `http://localhost:11434/v1`) and the model's name (for example `llama3.1`). Free, but only as smart as the model.
3. **Keys.** Paste a key once and press Save or Start. The box empties and says "saved (hidden)": the key is never shown again, only replaced or forgotten. It is kept in `~/.hoi4-harness/setup.json` (your user folder), readable only by you where Windows or Linux allows, and is passed to the AI only as a private setting of the program that plays, never typed on a command line and never written to a log. If you already have `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `GOOGLE_API_KEY` set on your PC, the window says "found on this PC" and uses it.
4. **A starting instruction** for each country, in plain words: "go alt-history Germany and ally the UK", "go communist", "stay out of every war until 1940". The AI is told it holds for the whole game, not one turn. It guides the AI's strategy; it cannot make the game allow something it does not.
5. **Options.**
   - *Start the game for me in debug mode*: the AI's orders reach the game through a file the game only reloads in debug mode. Tick this and the game is started that way (needs the game folder, which the window fills in when it can find it). Otherwise start the game from the launcher with Play, then "Open game in Debug Mode".
   - *Keep going when I restart the game*: the AI waits for the next game instead of ending.
   - *Game reports every (hours)*: how often the game tells the AI what is happening. Leave blank unless you know you want it.
   - *Stop at date*: for example `1941-06-22`. Blank means play until you quit.
6. **Save** writes the file. **Start** saves, checks everything and starts the AI in the background. If something is missing it tells you in words, for example "FRA: no Anthropic API key saved." The box at the bottom shows each country ("waiting for the game to start", "playing", "stopped") and the last lines the AI wrote. You can close the window; the AI keeps playing. Open it again later to see how it is going.
7. **Open chat window** (or `hoi4-harness chat-gui`) lets you talk to the AI countries and read what they say: see [chat.md](chat.md).

The mod has to be installed once, with the game closed (see [inbox.md](inbox.md)); `Start` reports it if it is not.

## Technical: what it runs

The window writes `~/.hoi4-harness/setup.json` (`HOI4_HARNESS_HOME` moves the folder). `Start` runs one background process:

```
hoi4-harness start --setup <key-less copy of setup.json>
```

with the API keys in that process's environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`; a local model gets `HOI4_LOCAL_API_KEY=not-needed`, since the OpenAI client wants some key). The copy under `~/.hoi4-harness/runs/` has no keys, so the command line names a file with no secret in it. Output goes to `~/.hoi4-harness/logs/run-<time>.log`. `start` is `native` with the countries, models and instructions read from the file, so everything in [inbox.md](inbox.md) applies.

**One process for all countries, not one each.** `native --rival POL,FRA` is a single multi-rival run ([rival.md](rival.md)). Orders reach the game through one inbox file that the process rewrites from its own list; two processes would overwrite each other's orders. Each country still has its own strategist, model, instruction, memory, transcript folder and budget: `HarnessConfig.rival_players` (tag -> provider, model, base_url, api_key_env, max_usd, prices, instruction) is applied per country, in `rival/setup.py` (`planner_config_for`, `instruction_for`) and `rival/multi.py` (`split_budget` uses each country's own cap when any is set).

### setup.json

```json
{
  "players": [
    {"tag": "POL", "provider": "claude-cli", "model": "", "instruction": "go communist"},
    {"tag": "FRA", "provider": "anthropic", "model": "claude-opus-5", "max_usd": 5.0,
     "usd_per_m_input": 5.0, "usd_per_m_output": 25.0, "instruction": "ally the UK"},
    {"tag": "ROM", "provider": "local", "model": "llama3.1", "base_url": "http://localhost:11434/v1"}
  ],
  "keys": {"anthropic": "..."},
  "launch_game": false, "game_dir": "", "telemetry_hours": null,
  "keep_running": true, "until": "", "my_country": "GER"
}
```

`provider` is one of `claude-cli`, `anthropic`, `openai`, `google`, `local` (a local model is the `openai` provider with a `base_url`). An empty `model` is the provider's default (`haiku` for Claude Code). `max_usd` applies to the paid providers only. A file that fails its checks (a tag that is not three characters, a paid provider with no key, a cap with no prices, a bad date) is refused with the reason before anything starts.

### The same thing as flags

Every field has a flag. `hoi4-harness start --setup FILE` and the flags below mean the same run (`Setup.native_args()` prints the flags for a file; tests assert the two build the same configuration). A flag beats the file.

| Window field | Flag |
|---|---|
| Countries | `--rival POL,FRA` |
| Model type, model, address | `--player-provider TAG=anthropic`, `--player-model TAG=NAME`, `--player-base-url TAG=URL` (all countries: `--provider`, `--model`, `--base-url`) |
| Key variable for a country | `--player-key-env TAG=VARIABLE` (the key itself: environment variable only, there is no flag for it by design) |
| Spend limit and prices | `--player-max-usd TAG=5`, `--player-price TAG=IN,OUT` (whole run: `--max-usd`) |
| Starting instruction | `--player-instruction TAG=TEXT`; for every country without its own, `--instruction TEXT` (also on `play` and `native`) |
| Start the game in debug mode | `--launch` (with `--game-dir PATH`) |
| Keep running across restarts | `--keep-running` |
| Game reports every N hours | `--telemetry-hours N` |
| Stop at date | `--until YYYY-MM-DD` |
| Your own country | `--my-country TAG` (refused as an AI country) |
| The file | `--setup FILE` (on `start`, `native`, `play`) |

A local model from the command line: `--player-provider ROM=openai --player-model ROM=llama3.1 --player-base-url ROM=http://localhost:11434/v1 --player-key-env ROM=HOI4_LOCAL_API_KEY`, with that variable set to anything.

`--instruction` / the `instruction` config field (`HOI4_INSTRUCTION` in the environment) is added to the system prompt after the guidance and any `system_prompt_extra` (`agent/prompts.py`, `INSTRUCTION`), so it is in the cached prefix and identical every turn. `--system-prompt` replaces the whole prompt and so drops it.

A spend cap only counts if the prices are set (`--player-price`, or `HOI4_USD_PER_M_INPUT/OUTPUT`); the window and the file check enforce that. Claude Code and local models cost nothing per call, so they take no cap.

### The players registry

Every `native` / `play --rival` run writes `~/.hoi4-harness/players.json` when it starts and marks it when it ends: `tag`, `provider`, `model`, `status` (`waiting`, `running`, `finished`, `stopped`, `failed`), `pid`, `started`, `updated`, `run_dir`. A live status whose pid is gone reads back as `stopped`. No key is ever written there. The chat window uses it ([chat.md](chat.md)).

### Not verified

- The windows were built and exercised headless (controllers and models, in `tests/test_setup_gui.py` and `tests/test_chat_gui.py`) and opened once on a desktop to check they construct; no one has used them end to end against a real game. Expect rough edges in layout.
- The Windows file restriction for `setup.json` uses `icacls` and is best effort. Treat the file as sensitive either way.
- The model names offered in the lists (`claude-opus-5`, `claude-sonnet-5-5`, `gpt-5`, `gpt-5-mini`, `gemini-2.5-pro`, `gemini-2.5-flash`, `haiku`/`sonnet`/`opus`, `llama3.1`, `qwen2.5`) are suggestions; the box is editable, and the provider rejects a name it does not know.
- A run started from the window goes through the same untested-live inbox path as `native` ([inbox.md](inbox.md)).
