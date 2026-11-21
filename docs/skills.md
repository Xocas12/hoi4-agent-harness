# Skill packs

A skill pack is a note you write for the strategist: how your mod works, how a country
plays, how you like to play. The harness adds the ones that apply to the system prompt.
It is a folder with one file, `SKILL.md`, in the same format as a Claude Code skill, so the
same file also works in Claude Code (see the last section).

Guidance packs (`--guidance`, see [guidance.md](guidance.md)) are how the harness coaches
in general. Skills are the extensible part: anyone can add one without touching the harness.

## Write one

```
hoi4-harness skills new my-mod-notes --mod "Kaiserreich" --tag GER
```

writes `~/.hoi4-harness/skills/my-mod-notes/SKILL.md` from a template (`--dir PATH` puts it
elsewhere). Fill it in. There is also a blank example in
[`examples/skills/example-mod-skill/`](../examples/skills/example-mod-skill/SKILL.md).

```markdown
---
name: my-mod-notes
description: How Kaiserreich plays for Germany. Use when steering Germany in Kaiserreich.
mods: [Kaiserreich, 1521695605]
tags: [GER]
---

# Kaiserreich, Germany

## What the mod changes
...
```

The sections the template suggests: what the mod changes, key mechanics and decisions,
custom windows worth pressing, balance of power bars, what plays well, what not to do.
Write only what you have checked. The model trusts it, and a wrong fact costs more than a
missing one. Keep it short: the prompt has a budget (below).

## Frontmatter

| key | needed | meaning |
| --- | --- | --- |
| `name` | yes | the skill's name; `--skill NAME` uses it. Defaults to the folder name |
| `description` | yes for Claude Code | one or two sentences on what it covers and when to use it |
| `mods` | no | mod names (any case) or Steam workshop ids; applies when one is in the playset |
| `tags` | no | country tags; applies when the run's country is one of them |
| `always` | no | `true` to include in every run |

Lists are written `[a, b]`, `a, b`, or one `- item` per line. The frontmatter is read by the
harness itself, so no YAML library is needed and only this simple shape is understood.

A skill with none of `mods`, `tags` or `always` is a Claude Code skill only. The harness
leaves it out unless you name it with `--skill`.

A mod matches by the `name` in its `descriptor.mod`, or by its `remote_file_id` (the workshop
id), or by the numeric folder name under `workshop/content`.

## Where to put it

Searched in this order; if two skills share a name the first one wins.

1. `--skills-dir PATH` (repeatable) or `skills_dirs` in a profile. `PATH` holds skill folders,
   or is itself one.
2. `~/.hoi4-harness/skills/`. Set `HOI4_HARNESS_HOME` to move `~/.hoi4-harness`.
3. The repository's `.claude/skills/`. The shipped `hoi4-strategy` is here.
4. `harness_skills/` inside each active mod (`--mod`), so a mod can ship its own skill:
   `<mod>/harness_skills/<name>/SKILL.md`.

## What gets into the prompt

A skill is included when any of these holds:

- `always: true`;
- one of its `mods` is in the playset;
- the run's country (`--country`) is in its `tags`;
- you named it with `--skill NAME` (repeatable).

Order is fixed: named skills first, then the rest, each by name. The total skill text is capped
at `skill_tokens` (default 3000, about four characters to a token; a profile setting). A skill that
does not fit is left out whole, and the next one still gets its chance. The prompt says which
skills were included and which were left out for space, and `hoi4-harness skills list` shows
the same. If you pass `--guidance rival+strategy`, `hoi4-strategy` is not added a second time.

`--system-prompt` replaces the whole prompt, so it skips skills too.

## Test it

```
hoi4-harness skills list --game-dir "<HOI4 install>" --mod "<mod folder>" --country GER
hoi4-harness skills show my-mod-notes --country GER
hoi4-harness prompt --game-dir "<HOI4 install>" --mod "<mod folder>" --country GER
```

`skills list` prints every skill found, where it came from, and whether it applies to that
playset and country. `prompt` prints the exact system prompt, including the skill section.
`--skill NAME` and `--skills-dir PATH` work on `play`, `native` and the rest of the run commands.

## The same file in Claude Code

Claude Code reads `SKILL.md` from `.claude/skills/<name>/` in a project or
`~/.claude/skills/<name>/`. Copy the folder there and it works as a normal skill, using
`name` and `description`. Claude Code ignores `mods`, `tags` and `always`.
