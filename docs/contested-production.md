# Contested production (issue #161, step 1)

With `human_ai` on, the game's AI runs the country and the harness used to refuse production,
designs and research as contested. `campaign_contested_production` opens exactly three of them to
the model, to measure what the AI does about it:

| Action | What it does |
|---|---|
| `add_production_line` | a NEW line through `add_equipment_production`, for an equipment or one of the model's own designs (`variant`); factories requested; a marker logged inside the guard proves it ran |
| `design_equipment` | unchanged (`docs/designers.md`) |
| `start_research` | only where the adapter has it (it is a mouse action: a `game_ai` headless campaign has no mouse, so there the research lever stays `steer_research`) |

The prompt tells the model the AI may switch, starve or delete what it opens. A design is referenced
by name (`version_name`); the typed console cannot carry a name with a space, so such a design is
refused with that reason (name designs with letters, digits, underscores, hyphens).

Every line the game confirmed is appended to `contested_lines.jsonl` in the run directory.

## Running it

    hoi4-harness play --campaign --live --profile profiles/contested-ger.json \
        --max-usd 15 --run-dir runs/contested-ger-1936

The profile saves monthly and keeps 40 saves (`campaign_save_months`, `campaign_keep_saves`): the
measurement needs a save before the first action and successive saves after it.

## Measuring

    python scripts/contested_report.py runs/contested-ger-1936 --saves "<the campaign's save folder>"

Per line the model added: found or not, still exists, factories over time, what the AI changed
(factories, equipment, removal), and whether the AI builds the model's design on lines of its own.
`--json` gives the whole timeline. A line is found by its stable id: the first line of that
equipment (and design) in a save that was not in the last save before the action. With no save
before the action the line is reported `not_found` rather than guessed.

Unverified against the live game: that `add_equipment_production` with `version_name` and `creator`
references a design, and what a new line gets when no factory is free. Step 2 (the model owns the
country, the AI runs only the armies) waits on the numbers.
