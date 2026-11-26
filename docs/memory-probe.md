# Measuring the memory leak, and the automatic restart (#157)

HOI4 1.20 grew by about 30 GB an hour in the 2026-11-22 campaigns (results/overnight-2026-11-22.md).
Two tools, both off unless you ask for them.

## Finding the cause: `hoi4-harness memory-probe`

```
hoi4-harness memory-probe --save GER_1940_11_01.hoi4 --minutes 30 --variant nomod
hoi4-harness memory-probe --save X --variant nomod --steps-only     # just print the setup
hoi4-harness memory-probe --compare memory-probe-results            # every run, side by side
```

It reads hoi4.exe's private bytes every minute (`--interval` seconds), writes
`memory-probe-results/probe-<variant>[-<label>]-<time>.csv` (elapsed, time, bytes, GB, in-game date from
game.log when `--log` / `HOI4_LOG_PATH` is set) and a `.summary.json` with the growth in GB per
hour, a least-squares slope over all samples, and its R squared. It never launches, focuses or
clicks in the game, and no harness may run beside it.

`--variant` says what you set up before starting; each variant prints its exact steps:

| variant | setup |
|---|---|
| `mod` | llm_bridge enabled (`python scripts/live_setup.py prepare --apply`), `-debug_mode` |
| `nomod` | `python scripts/live_setup.py prepare --no-mod --apply` (llm_bridge left out of dlc_load.json), `-debug_mode` |
| `debug` / `nodebug` | the mod's setup, launched with / without `-debug_mode` (Steam launch options, or `hoi4.exe -gdpr-compliant [-debug_mode]`) |
| `log-daily` / `log-weekly` | console `effect <TAG> llm_bridge_mark_harness`, then `effect <TAG> llm_bridge_telemetry_every_24h` / `..._168h` |

`live_setup.py restore --apply` puts the mod list back afterwards. Only the mod and no-mod switch is
automated; launch flags and console lines are not, because the game must be started by a person.

### The four comparisons (the issue's acceptance)

Use ONE save for all of them, load it by the menu (Single Player > Load > Load > START), set the
speed to 5, unpause, touch nothing, and start the probe at once: 30 minutes each. A 30 GB/hour leak
is 15 GB in that time, far above the noise. Close the game between runs.

1. **Mod or game:** `nomod` against `mod`.
2. **`-debug_mode`:** `debug` against `nodebug`.
3. **Logging cadence:** `log-daily` against `log-weekly`.
4. **The game alone:** `nomod` launched without `-debug_mode`: run `nomod` setup with the plain
   launch and `--label nomod-nodebug`. If this still leaks, it is the game; the harness restarts it
   (below). If it is flat, add the mod, then `-debug_mode`, back one at a time.

Read the table from `--compare`. A variant that cuts the slope by more than half against its pair is
the cause (or most of it). The console path (a `run` file per wake) and text saves are not variants:
they only exist in a campaign, so if 1-4 find nothing, run a campaign segment with
`campaign_save_months = 0` and compare.

## Letting the harness restart the game: `campaign_on_memory = "restart"`

Past `campaign_memory_limit_gb` (48) the run, in the same process:

1. takes a recovery save and waits until it is complete on disk (size stable, last line `checksum=`);
2. kills only the hoi4.exe it recorded (`game_pid` in campaign_state.json; with no record it takes the
   only running hoi4.exe, and with two it kills nothing and stops);
3. relaunches `hoi4.exe -gdpr-compliant -debug_mode` through WMI (`Win32_Process.Create`), outside its
   own process tree, and waits for the window;
4. clicks Single Player, Load, the top save, Load, START (1920x1080 points scaled to the window), with
   the game confirmed in front before EVERY click. When it is not, the window is brought forward by
   `take_focus` in a fresh Python process, only after `campaign_take_focus_idle_seconds` (120) of no
   keyboard or mouse input and through the input guard. If it is still not in front after
   `campaign_restart_focus_wait_seconds`, the run stops resumably with the reason, the save's name
   and the steps to finish by hand;
5. marks the country and sets `human_ai` again (`begin`), resets the watchdog and plays on.

Config (all in the profile): `campaign_on_memory` (`stop` default, `warn`, `restart`),
`campaign_restart_max` (10), `campaign_restart_save_wait_seconds`, `..._window_wait_seconds`,
`..._menu_settle_seconds`, `..._load_wait_seconds`, `..._focus_wait_seconds`. `game_dir` must hold
hoi4.exe (campaign-check fails otherwise). Windows only.

### Live check (not verifiable without the game)

1. Profile with `"campaign_on_memory": "restart"`, `"campaign_memory_limit_gb": 8`, `game_dir` set.
2. `hoi4-harness campaign-check --profile ...` shows the `restart` line.
3. Start the campaign on a fresh load of a save; when hoi4.exe passes 8 GB the log shows
   `restart: ...` lines for each step. Leave the keyboard alone: the first click waits for 120 s idle.
4. Afterwards `game restarted from <save>` and `restart #1 done` appear, ai=1 on the next state line.
5. If a menu click lands wrong (another resolution or UI scale), the points are in
   `campaign/restart.py` `MENU_STEPS`; report which screen it stopped on.
