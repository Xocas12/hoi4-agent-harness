"""What the setup window's "Settings", "Countries" and "Run" tabs edit, with no window in it.

Three things live here, all testable without a display:

* **Settings.** :func:`field_specs` walks the ``HarnessConfig`` dataclass (and the nested
  ``planner`` and ``budget`` ones) and gives each field a typed widget kind, its default, its
  help text (the ``#`` / ``#:`` comment above or beside it in ``config.py``) and a section. A
  field added to the dataclass appears in the window with no edit here; the section rules are
  :data:`GROUP_EXACT` and :data:`GROUP_PREFIXES`, the only place that decides grouping. A field
  that is deliberately not offered is listed in :data:`HIDDEN` with the reason.
* **Countries.** :class:`CountryRow` is one line of the countries table (controller, mode,
  provider, model, key source, instruction, control map). :func:`apply_rows` writes the rows into
  the config fields they stand for (``campaign``, ``country``, ``rival``, ``rival_players``,
  ``handover``, ``mp_human_tags``, ``control``, ...) and :func:`rows_from_config` reads them back.
* **Profiles and runs.** :class:`SettingsModel` holds a ``HarnessConfig``, loads and saves
  profile JSON (only what differs from the defaults, as ``HarnessConfig.from_file`` expects),
  states the command line the settings stand for, runs the preflight checks and starts the run.

An API key never passes through here: a row names the environment variable that holds it, the
profile stores that name, and the run's own environment supplies the value.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import subprocess
import sys
import time
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import MISSING, dataclass, field, fields, is_dataclass
from pathlib import Path

from .config import DEFAULT_MODELS, HarnessConfig
from .home import home_dir

# --- which section a field is in --------------------------------------------------------------

#: Section order in the window. A field no rule names lands in ``OTHER``.
GROUPS = ("Model (planner)", "Budget", "Prompt and guidance", "Actions", "Pacing and wake rules",
          "Run", "Control map", "Console channel", "Unit orders", "Headless play",
          "Campaign mode", "Rival mode", "Co-op and multiplayer", "Playset and DLC",
          "Mod context, world and chat", "Fake game", "Other")
OTHER = "Other"

#: Whole-name rules, checked first.
GROUP_EXACT: dict[str, str] = {
    "adapter": "Run", "turns": "Run", "dry_run": "Run", "advisor": "Run", "run_dir": "Run",
    "save_dir": "Run", "log_path": "Run", "window_title": "Run", "ui_scripts_path": "Run",
    "calibration_path": "Run", "enforce_window_focus": "Run", "clock_owner": "Run",
    "clock_probe_seconds": "Run", "seed": "Run", "telemetry_hours": "Run",
    "guidance": "Prompt and guidance", "system_prompt_extra": "Prompt and guidance",
    "instruction": "Prompt and guidance", "objective": "Prompt and guidance",
    "enabled_actions": "Actions", "disabled_actions": "Actions", "require_confirmation": "Actions",
    "action_channels": "Console channel",
    "days_per_turn": "Pacing and wake rules", "max_actions_per_turn": "Pacing and wake rules",
    "max_tool_rounds_per_turn": "Pacing and wake rules",
    "max_consecutive_llm_errors": "Pacing and wake rules", "full_brief_every": "Pacing and wake rules",
    "planner_enabled": "Pacing and wake rules", "reflex_enabled": "Pacing and wake rules",
    "reflex_delegate": "Pacing and wake rules",
    "operational_control": "Control map", "control": "Control map", "steer": "Control map",
    "army_controller": "Control map", "allow_contested": "Control map",
    "auto_options": "Control map",
    "unit_orders": "Unit orders", "camera_path": "Unit orders", "battle_plans": "Unit orders",
    "handover": "Co-op and multiplayer", "multiplayer": "Co-op and multiplayer",
    "country": "Playset and DLC", "start_date": "Playset and DLC", "game_dir": "Playset and DLC",
    "mod_dirs": "Playset and DLC", "dlcs": "Playset and DLC", "user_dir": "Playset and DLC",
    "mod_context": "Mod context, world and chat", "observe_world": "Mod context, world and chat",
    "chat": "Mod context, world and chat", "chat_path": "Mod context, world and chat",
    "console_refresh": "Headless play",
    "event_windows": "Campaign mode", "event_screen": "Campaign mode", "campaign": "Campaign mode",
    "base_ai": "Campaign mode",
}

#: Prefix rules, in order, checked after the exact names. THE place to say where a family of
#: fields goes: a new ``campaign_*`` field needs no line here.
GROUP_PREFIXES: tuple[tuple[str, str], ...] = (
    ("planner.", "Model (planner)"),
    ("budget.", "Budget"),
    ("skill", "Prompt and guidance"),
    ("system_prompt", "Prompt and guidance"),
    ("wake_", "Pacing and wake rules"),
    ("console_", "Console channel"),
    ("inbox_", "Console channel"),
    ("headless_", "Headless play"),
    ("campaign_", "Campaign mode"),
    ("rival", "Rival mode"),
    ("fake_rival", "Rival mode"),
    ("fake_human", "Rival mode"),
    ("fake_", "Fake game"),
    ("handover", "Co-op and multiplayer"),
    ("human_", "Co-op and multiplayer"),
    ("mp_", "Co-op and multiplayer"),
    ("mod_context", "Mod context, world and chat"),
    ("unit_", "Unit orders"),
    ("battle_", "Unit orders"),
    ("dlc", "Playset and DLC"),
    ("disabled_dlc", "Playset and DLC"),
)

#: Fields the window does not offer as a setting, each with the reason.
HIDDEN: dict[str, str] = {
    "campaign_hands": "set by the adapter and campaign.runner.begin at the start of a run, read by "
                      "the prompt; not a setting",
    "rivals": "derived from ``rival`` (the tags it comes to once checked) when a run starts; "
              "the countries table writes ``rival``",
}

#: Choices for the fields whose comment names a closed set. ``None`` values are filled lazily.
_CHOICE_TABLE: dict[str, tuple[str, ...]] = {
    "planner.effort": ("low", "medium", "high"),
    "clock_owner": ("harness", "player"),
    "steer": ("auto", "llm", "off"),
    "console_hybrid_via": ("scripted", "decision"),
    "console_transport": ("keys", "inbox"),
    "headless_clock": ("auto", "console", "keys"),
    "headless_reader": ("log+save", "logtail", "savegame"),
    "campaign_on_budget": ("stop", "coast"),
    "campaign_on_memory": ("stop", "warn"),
    "campaign_control": ("game_ai", "llm"),
    "campaign_input": ("mouse", "none"),
    "campaign_historical_ai": ("any", "on", "off"),
    "base_ai": ("auto", "vanilla", "expert_ai"),
    "handover_mode": ("hotkey", "idle", "approve"),
}
#: Choices that are a suggestion, not a closed set: the field also takes free text.
_OPEN_CHOICES = frozenset({"guidance", "planner.model"})


def choices_for(path: str) -> tuple[str, ...] | None:
    """The closed set (or the suggestions, for an open field) for a field, else None."""
    if path in _CHOICE_TABLE:
        return _CHOICE_TABLE[path]
    if path == "adapter":
        from .adapters import ADAPTERS

        return tuple(ADAPTERS)
    if path == "planner.provider":
        from .agent.llm import PROVIDERS

        return tuple(PROVIDERS)
    if path == "operational_control":
        from .control import PRESETS

        return tuple(PRESETS)
    if path == "rival_act_when":
        from .rival.safety import POLICIES

        return tuple(POLICIES)
    if path == "army_controller":
        from .tactics import registry

        return tuple(registry.names())
    if path == "guidance":
        from .guidance import available

        return tuple(available())
    if path == "planner.model":
        return tuple(dict.fromkeys(DEFAULT_MODELS.values()))
    return None


#: Help for fields whose comment in config.py is a shared block or missing. A field with its own
#: comment never reads from here.
HELP_FALLBACK: dict[str, str] = {
    "planner.provider": "Which model service plans: anthropic, openai, google, claude-cli (the "
                        "Claude Code CLI on your subscription), scripted or mailbox.",
    "planner.model": "The model name the provider accepts. Empty: the provider's default.",
    "planner.api_key_env": "The environment variable holding the API key. Empty: the provider's "
                           "usual one (ANTHROPIC_API_KEY, ...). The key itself is never stored here.",
    "planner.max_tokens": "The most tokens the model may write in one reply.",
    "planner.temperature": "Sampling temperature. Empty: the provider's default.",
    "planner.extra": "Provider-specific options as a JSON object, e.g. the mailbox folder.",
    "budget.max_llm_calls": "Model calls allowed in one run; past it the reflex policy plays.",
    "budget.max_input_tokens": "Input tokens allowed in one run.",
    "budget.max_output_tokens": "Output tokens allowed in one run.",
    "budget.max_usd": "Spend ceiling in US dollars. Empty: none. Needs the prices below to count.",
    "budget.usd_per_m_output": "Dollars per million output tokens, from your provider's price sheet.",
    "budget.usd_per_m_cache_write": "Dollars per million cache-write tokens. Empty: billed as input.",
    "system_prompt_extra": "Text added to the end of the system prompt.",
    "skills_dirs": "Extra folders to look for skill packs in, separated by semicolons.",
    "skill_tokens": "Token budget for the skill packs in the prompt.",
    "system_prompt_path": "A file that replaces the whole system prompt, mechanics included.",
    "objective": "What the model is trying to achieve; put in the prompt.",
    "disabled_actions": "Action names to take away from the model, comma separated.",
    "require_confirmation": "Gate irreversible actions behind a confirmation. Turning it off only "
                            "matters with a live run.",
    "max_actions_per_turn": "The most actions the model may issue in one turn.",
    "max_tool_rounds_per_turn": "The most tool-call rounds the model gets in one turn.",
    "full_brief_every": "Send the full situation brief every this many turns (others get a delta).",
    "wake_on_free_research_slot": "Wake the model when a research slot is free.",
    "wake_on_no_focus": "Wake the model when no national focus is running.",
    "wake_on_supply_collapse": "Wake the model when supply collapses (on onset, not while it lasts).",
    "wake_on_capital_threat": "Wake the model when the capital is threatened (on onset).",
    "wake_on_ally_capitulation": "Wake the model when an ally capitulates.",
    "wake_on_front_quiet": "Wake the model when a front goes quiet.",
    "reflex_enabled": "Run the reflex policy between model wakes.",
    "adapter": "How the harness reads and drives the game (mock, headless, ...).",
    "turns": "How many turns to play.",
    "dry_run": "Refuse irreversible actions. Turn off for a live run.",
    "run_dir": "Where this run's transcript, memory and reports are written.",
    "save_dir": "The game's save folder. Empty: found automatically.",
    "window_title": "The game window's title, for the focus check.",
    "calibration_path": "The recorded screen coordinates. Empty: calibration.json in the run folder.",
    "seed": "Random seed, for runs that should repeat.",
    "console_guards": "Check each console line before it is typed.",
    "headless_poll_seconds": "Seconds between reads of the game's log.",
    "campaign_review_war_days": "Days between scheduled reviews while at war.",
    "campaign_review_min_days": "The shortest the model may set the next review.",
    "campaign_review_max_days": "The longest the model may set the next review.",
    "campaign_on_memory": "What past the memory limit does: stop (clean, resumable) or warn.",
    "rival_shares": "Each rival's weight for splitting the spend cap, as {\"FRA\": 2}. Default equal.",
    "fake_human_script": "A JSON script of what the fake game's person does and when.",
    "handover_idle_seconds": "Idle mode: seconds without keyboard or mouse before the model acts.",
    "handover_paused_seconds": "Idle mode: seconds the game date must stand still.",
    "mp_human_tags": "Countries people play in multiplayer; the model may not act on them.",
    "start_date": "The game's start date, YYYY-MM-DD.",
    "mod_dirs": "Mod folders in load order, separated by semicolons.",
    "user_dir": "The folder holding dlc_load.json. Empty: where the game keeps its saves.",
    "chat_path": "The shared chat log. Empty: ~/.hoi4-harness/chat.jsonl.",
    "mod_context_fetch": "Let the run fetch a Workshop page that is not cached.",
    "mod_context_tokens": "Token budget for the playset section of the prompt.",
    "mod_context_cache_dir": "Where fetched Workshop pages are cached.",
    "fake_game_dir": "The fake game's folder. Empty: <run folder>/fakegame.",
    "fake_game_files": "A running `hoi4-harness fakegame` process's folder.",
}


# --- field specs ------------------------------------------------------------------------------

BOOL, TRISTATE, INT, FLOAT, TEXT, PATH, LIST, PATHS, JSON, MAPPING_OR_TEXT, CHOICE = (
    "bool", "tristate", "int", "float", "text", "path", "list", "paths", "json", "control",
    "choice")


@dataclass(frozen=True)
class FieldSpec:
    path: str                  # ``turns`` or ``planner.model``
    kind: str                  # one of the kinds above: the widget
    default: object
    help: str
    group: str
    optional: bool = False     # None is a value (the box can be empty)
    choices: tuple[str, ...] | None = None
    open_choices: bool = False
    multiline: bool = False

    @property
    def label(self) -> str:
        return self.path.split(".")[-1].replace("_", " ")


def group_of(path: str) -> str:
    if path in GROUP_EXACT:
        return GROUP_EXACT[path]
    for prefix, group in GROUP_PREFIXES:
        if path.startswith(prefix):
            return group
    return OTHER


def _classify(hint) -> tuple[str, bool]:
    """``(kind, optional)`` for a type hint."""
    optional = False
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        optional = len(args) != len(typing.get_args(hint))
        if len(args) > 1:
            return MAPPING_OR_TEXT, optional
        hint = args[0]
        origin = typing.get_origin(hint)
    if hint is bool:
        return (TRISTATE if optional else BOOL), optional
    if hint is int:
        return INT, optional
    if hint is float:
        return FLOAT, optional
    if hint is str:
        return TEXT, optional
    if hint is Path:
        return PATH, optional
    if origin is list:
        inner = typing.get_args(hint)
        return (PATHS if inner and inner[0] is Path else LIST), optional
    if origin is dict or hint is dict:
        return JSON, optional
    return JSON, optional


_COMMENT = re.compile(r"^\s*#:?\s?(.*)$")
_FIELD = re.compile(r"^    (\w+)\s*:")
_INLINE = re.compile(r"\s{2,}#:?\s?(.*)$")


def help_texts(cls) -> dict[str, str]:
    """``{field: help}`` from the comments above and beside each field in ``cls``'s source."""
    import inspect

    try:
        lines = inspect.getsource(cls).splitlines()
    except (OSError, TypeError):
        return {}
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        match = _FIELD.match(line)
        if not match or line.lstrip().startswith("#"):
            continue
        block: list[str] = []
        j = i - 1
        while j >= 0 and lines[j].strip().startswith("#"):
            text = _COMMENT.match(lines[j]).group(1).strip()
            j -= 1
            if text.startswith("---"):
                break
            block.append(text)
        parts = list(reversed(block))
        inline = _INLINE.search(line)
        if inline:
            parts.append(inline.group(1).strip())
        text = " ".join(p for p in parts if p)
        if text:
            out[match.group(1)] = text
    return out


def _walk(cls, prefix: str = "") -> typing.Iterator[tuple[str, object, object, str]]:
    hints = typing.get_type_hints(cls)
    helps: dict[str, str] = {}
    for base in reversed(cls.__mro__):          # a subclass keeps its parents' help
        if is_dataclass(base):
            helps.update(help_texts(base))
    for f in fields(cls):
        path = prefix + f.name
        default = (f.default if f.default is not MISSING
                   else f.default_factory() if f.default_factory is not MISSING else None)
        if is_dataclass(default):
            yield from _walk(type(default), path + ".")
            continue
        yield path, hints[f.name], default, helps.get(f.name, "")


def all_field_paths() -> list[str]:
    """Every settable field of ``HarnessConfig``, nested ones as ``planner.model``, hidden or not."""
    return [path for path, *_ in _walk(HarnessConfig)]


def field_specs() -> list[FieldSpec]:
    """A spec for every field of the dataclass that is not in :data:`HIDDEN`, in section order."""
    specs = []
    for path, hint, default, help_text in _walk(HarnessConfig):
        if path in HIDDEN:
            continue
        kind, optional = _classify(hint)
        choices = choices_for(path)
        if choices and kind == TEXT:
            kind = CHOICE
        if not help_text:
            help_text = HELP_FALLBACK.get(path) or f"{path.split('.')[-1].replace('_', ' ').capitalize()}."
        specs.append(FieldSpec(
            path=path, kind=kind, default=default, help=help_text, group=group_of(path),
            optional=optional, choices=choices, open_choices=path in _OPEN_CHOICES,
            multiline=path in ("objective", "instruction", "system_prompt_extra")))
    order = {g: i for i, g in enumerate(GROUPS)}
    specs.sort(key=lambda s: order.get(s.group, len(order)))     # stable: file order within a group
    return specs


def sections() -> list[tuple[str, list[FieldSpec]]]:
    """The specs by section, in window order, empty sections left out."""
    by: dict[str, list[FieldSpec]] = {}
    for spec in field_specs():
        by.setdefault(spec.group, []).append(spec)
    return [(g, by[g]) for g in GROUPS if g in by] + [(g, s) for g, s in by.items() if g not in GROUPS]


def _jsonable(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def format_value(spec: FieldSpec, value) -> str:
    """A value as the text its widget shows."""
    if value is None:
        return ""
    if spec.kind in (BOOL, TRISTATE):
        return "true" if value else "false"
    if spec.kind == LIST:
        return ", ".join(str(v) for v in value)
    if spec.kind == PATHS:
        return "; ".join(str(v) for v in value)
    if spec.kind == JSON:
        return json.dumps(_jsonable(value), ensure_ascii=False) if value != {} else ""
    if spec.kind == MAPPING_OR_TEXT:
        return value if isinstance(value, str) else json.dumps(_jsonable(value), ensure_ascii=False)
    return str(value)


def parse_value(spec: FieldSpec, text: str):
    """The widget's text as the field's value. Raises ``ValueError`` saying what is wrong."""
    raw = text if spec.multiline else text.strip()
    kind = spec.kind
    try:
        if kind in (BOOL, TRISTATE):
            if kind == TRISTATE and raw == "":
                return None
            low = raw.lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
            raise ValueError("true or false")
        if kind in (INT, FLOAT):
            if raw == "":
                if spec.optional:
                    return None
                raise ValueError("a number")
            return int(raw) if kind == INT else float(raw)
        if kind == PATH:
            return Path(raw) if raw else None
        if kind == PATHS:
            return [Path(p.strip()) for p in raw.split(";") if p.strip()]
        if kind == LIST:
            if raw == "" and spec.optional:
                return None
            if raw == "[]":
                return []
            return [p.strip() for p in raw.split(",") if p.strip()]
        if kind == JSON:
            if raw == "":
                return None if spec.optional else {}
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("a JSON object like {\"key\": \"value\"}")
            return data
        if kind == MAPPING_OR_TEXT:
            if raw == "":
                return None
            return json.loads(raw) if raw.startswith("{") else raw
        if kind == CHOICE and spec.choices and not spec.open_choices and raw not in spec.choices:
            raise ValueError("one of " + ", ".join(spec.choices))
        if raw == "" and spec.optional:
            return None
        return raw
    except ValueError as exc:
        raise ValueError(f"{spec.path}: expected {exc}") from None


# --- the countries table ----------------------------------------------------------------------

CONTROLLERS = ("llm", "game_ai", "person", "off")
MODES = ("campaign", "rival", "coop")
KEY_ENV_DEFAULTS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                    "google": "GOOGLE_API_KEY"}
_TAG = re.compile(r"^[A-Z0-9]{3}$")


@dataclass
class CountryRow:
    tag: str
    controller: str = "llm"      # llm | game_ai | person | off
    mode: str = "rival"          # campaign | rival | coop
    provider: str = "claude-cli"  # a harness provider (agent.llm.PROVIDERS)
    model: str = ""              # empty: the provider's default
    key_env: str = ""            # the environment variable holding the key; empty: the provider's own
    base_url: str = ""
    instruction: str = ""
    control: str = ""            # a control map: "focus=llm,research=script" or a preset name
    max_usd: float | None = None

    @property
    def stored(self) -> bool:
        """Whether a profile can hold this row. A country the game's AI plays, or nobody does, is
        simply not listed in a profile: that is what the game does with a country nobody names."""
        return self.controller in ("llm", "person") or (
            self.controller == "game_ai" and self.mode == "campaign")


def key_env_for(row: CountryRow) -> str | None:
    """The variable the run reads this country's key from; None when it needs no key."""
    if row.provider in ("claude-cli", "scripted", "mailbox"):
        return None
    return row.key_env.strip() or KEY_ENV_DEFAULTS.get(row.provider)


def key_status(row: CountryRow, environ: Mapping[str, str] | None = None) -> str:
    """``not needed``, ``found`` or ``missing``. Never the key."""
    name = key_env_for(row)
    if name is None:
        return "not needed"
    env = os.environ if environ is None else environ
    return "found" if env.get(name) else "missing"


def row_problems(rows: list[CountryRow], environ: Mapping[str, str] | None = None) -> list[str]:
    """What stops these rows from being a run, in words."""
    from .agent.llm import PROVIDERS
    from .control import ControlError, resolve

    out: list[str] = []
    seen: set[str] = set()
    for r in rows:
        where = f"{r.tag or '(no tag)'}: "
        if not _TAG.match(r.tag):
            out.append(where + "a country tag is three letters or digits, like POL or GER.")
        elif r.tag in seen:
            out.append(where + "listed twice.")
        seen.add(r.tag)
        if r.controller not in CONTROLLERS:
            out.append(where + f"controller {r.controller!r}; known: {', '.join(CONTROLLERS)}.")
        if r.mode not in MODES:
            out.append(where + f"mode {r.mode!r}; known: {', '.join(MODES)}.")
        if r.controller == "person" and r.mode == "campaign":
            out.append(where + "the campaign mode hands a country to the game's AI or the model; "
                       "a person plays it in co-op.")
        if r.controller == "llm" and r.provider not in PROVIDERS:
            out.append(where + f"unknown provider {r.provider!r}.")
        if r.controller == "llm" and key_status(r, environ) == "missing":
            out.append(where + f"the key variable {key_env_for(r)} is not set.")
        if r.control.strip():
            if r.mode == "rival":
                out.append(where + "a rival country is steered, so a control map does not apply; "
                           "use it on a campaign or co-op country.")
            else:
                try:
                    spec = r.control.strip()
                    resolve(json.loads(spec) if spec.startswith("{") else spec)
                except (ControlError, ValueError) as exc:
                    out.append(where + f"control map: {exc}")
        if r.max_usd is not None and r.max_usd <= 0:
            out.append(where + "the spend cap must be above zero (or empty for none).")
    own = [r for r in rows if r.stored and r.controller != "person" and r.mode in ("campaign", "coop")]
    if len(own) > 1:
        out.append("Only one country can be played in campaign or co-op mode: "
                   + ", ".join(r.tag for r in own) + ".")
    if own and any(r.stored and r.controller == "llm" and r.mode == "rival" for r in rows):
        out.append("Rival countries and a campaign or co-op country are different run modes; "
                   "pick one.")
    return out


def _control_value(text: str):
    text = text.strip()
    if not text:
        return None
    return json.loads(text) if text.startswith("{") else text


def apply_rows(config: HarnessConfig, rows: list[CountryRow]) -> HarnessConfig:
    """Write the rows into the config fields they stand for. Only fields a row owns are touched;
    a row the profile cannot hold (``stored`` False) writes nothing."""
    stored = [r for r in rows if r.stored]
    rivals = [r for r in stored if r.controller == "llm" and r.mode == "rival"]
    persons = [r for r in stored if r.controller == "person"]
    own = [r for r in stored if r.controller != "person" and r.mode in ("campaign", "coop")]
    config.rival = ",".join(r.tag for r in rivals)
    config.rivals = []
    config.rival_players = {}
    for r in rivals:
        row: dict = {"provider": r.provider, "model": r.model.strip() or DEFAULT_MODELS.get(r.provider, ""),
                     "base_url": r.base_url.strip(), "instruction": r.instruction.strip()}
        env = r.key_env.strip()
        if env:
            row["api_key_env"] = env
        if r.max_usd is not None:
            row["max_usd"] = r.max_usd
        config.rival_players[r.tag] = row
    config.mp_human_tags = [r.tag for r in persons]
    config.multiplayer = bool(persons)
    config.campaign = False
    config.handover = False
    if own:
        r = own[0]
        config.country = r.tag
        config.control = _control_value(r.control)
        config.instruction = r.instruction.strip()
        planner = config.planner
        if r.provider != planner.provider:
            planner.switch_provider(r.provider)
        if r.model.strip():
            planner.model = r.model.strip()
        planner.base_url = r.base_url.strip() or None
        planner.api_key_env = r.key_env.strip() or None
        if r.max_usd is not None:
            config.budget.max_usd = r.max_usd
        if r.mode == "campaign":
            config.campaign = True
            config.campaign_control = "llm" if r.controller == "llm" else "game_ai"
        else:
            config.handover = True
    elif rivals:
        config.country = rivals[0].tag
    return config


def rows_from_config(config: HarnessConfig) -> list[CountryRow]:
    """The countries table a config stands for: the inverse of :func:`apply_rows`."""
    rows: list[CountryRow] = []
    own_tag = (config.country or "").strip().upper()
    spec = config.control
    control = spec if isinstance(spec, str) else (json.dumps(spec) if spec else "")
    planner = config.planner
    if config.campaign or config.handover:
        if own_tag:
            controller = "llm" if (config.campaign_control == "llm" or not config.campaign) else "game_ai"
            rows.append(CountryRow(
                tag=own_tag, controller=controller, mode="campaign" if config.campaign else "coop",
                provider=planner.provider,
                model="" if planner.model == DEFAULT_MODELS.get(planner.provider, "") else planner.model,
                key_env=planner.api_key_env or "", base_url=planner.base_url or "",
                instruction=config.instruction, control=control, max_usd=config.budget.max_usd))
    tags = list(config.rivals) if len(config.rivals) > 1 else [
        t.strip().upper() for t in re.split(r"[,\s]+", config.rival or "") if t.strip()]
    for tag in tags:
        own = config.rival_players.get(tag) or {}
        provider = own.get("provider") or planner.provider
        model = own.get("model") or ""
        rows.append(CountryRow(
            tag=tag, controller="llm", mode="rival", provider=provider,
            model="" if model == DEFAULT_MODELS.get(provider, "") else model,
            key_env=own.get("api_key_env") or "", base_url=own.get("base_url") or "",
            instruction=own.get("instruction") or "", max_usd=own.get("max_usd")))
    for tag in config.mp_human_tags:
        rows.append(CountryRow(tag=tag, controller="person", mode="coop", provider="claude-cli"))
    return rows


# --- profiles ---------------------------------------------------------------------------------


def to_profile(config: HarnessConfig) -> dict:
    """The profile JSON for ``config``: only what differs from the defaults, nested as
    ``planner`` / ``budget`` tables, so ``HarnessConfig.from_file`` rebuilds it."""
    base = HarnessConfig()
    out: dict = {}
    for path, *_ in _walk(HarnessConfig):
        if path in HIDDEN:
            continue
        mine, theirs = _get(config, path), _get(base, path)
        if mine == theirs:
            continue
        if "." in path:
            head, tail = path.split(".", 1)
            out.setdefault(head, {})[tail] = _jsonable(mine)
        else:
            out[path] = _jsonable(mine)
    return out


def _get(config, path: str):
    obj = config
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _set(config: HarnessConfig, path: str, value) -> None:
    if path == "planner.provider":
        config.planner.switch_provider(value)
        return
    obj = config
    *head, last = path.split(".")
    for part in head:
        obj = getattr(obj, part)
    setattr(obj, last, value)


# --- the model --------------------------------------------------------------------------------


@dataclass
class PreflightResult:
    problems: list[str] = field(default_factory=list)     # the countries table
    checks: list = field(default_factory=list)            # campaign.preflight.Check lines

    @property
    def ok(self) -> bool:
        return not self.problems and not any(c.status == "FAIL" for c in self.checks)

    def lines(self) -> list[str]:
        return [f"FAIL  countries: {p}" for p in self.problems] + [c.line() for c in self.checks]


@dataclass
class RunPlan:
    argv: list[str]
    profile_path: Path
    log_path: Path


class SettingsModel:
    """The settings, the countries table and the run, for the window."""

    def __init__(self, config: HarnessConfig | None = None, rows: list[CountryRow] | None = None,
                 profile_path: Path | None = None, environ: Mapping[str, str] | None = None):
        self.config = config if config is not None else HarnessConfig()
        self.rows = rows if rows is not None else rows_from_config(self.config)
        self.profile_path = Path(profile_path) if profile_path else home_dir() / "profiles" / "setup.json"
        self.environ = environ
        self.specs = field_specs()
        self.by_path = {s.path: s for s in self.specs}

    # settings ---------------------------------------------------------------

    def get_text(self, path: str) -> str:
        return format_value(self.by_path[path], _get(self.config, path))

    def set_text(self, path: str, text: str) -> None:
        """Set a field from its widget's text; ``ValueError`` (config unchanged) if it is wrong."""
        value = parse_value(self.by_path[path], text)
        _set(self.config, path, value)
        self._refresh_rows()

    def set_value(self, path: str, value) -> None:
        if path not in self.by_path:
            raise KeyError(path)
        _set(self.config, path, value)
        self._refresh_rows()

    def is_default(self, path: str) -> bool:
        return _get(self.config, path) == _get(HarnessConfig(), path)

    def _refresh_rows(self) -> None:
        """The table follows a settings edit of the fields it owns; rows a profile cannot hold
        (game AI outside a campaign, off) are the person's and stay."""
        kept = [r for r in self.rows if not r.stored]
        self.rows = rows_from_config(self.config) + kept

    # countries --------------------------------------------------------------

    def set_rows(self, rows: list[CountryRow]) -> None:
        self.rows = rows
        apply_rows(self.config, rows)

    def add_row(self, tag: str, **fields_) -> CountryRow:
        row = CountryRow(tag=tag.strip().upper(), **fields_)
        self.set_rows([*self.rows, row])
        return row

    def remove_row(self, tag: str) -> None:
        self.set_rows([r for r in self.rows if r.tag != tag.strip().upper()])

    def update_row(self, tag: str, **fields_) -> CountryRow:
        row = next(r for r in self.rows if r.tag == tag.strip().upper())
        for key, value in fields_.items():
            if key not in CountryRow.__dataclass_fields__:
                raise KeyError(key)
            setattr(row, key, value)
        self.set_rows(self.rows)
        return row

    def unstored_rows(self) -> list[CountryRow]:
        return [r for r in self.rows if not r.stored]

    # profiles ---------------------------------------------------------------

    def profile(self) -> dict:
        return to_profile(self.config)

    def save(self, path: Path | None = None) -> Path:
        path = Path(path) if path else self.profile_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.profile(), indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        self.profile_path = path
        return path

    def load(self, path: Path) -> None:
        """Load a profile (JSON or TOML) the way a run does, so what the window shows is what
        runs. A key it does not know is refused with the reason, the settings left as they were."""
        config = HarnessConfig.from_file(path)
        self.config = config
        self.rows = rows_from_config(config)
        self.profile_path = Path(path)

    # the command line -------------------------------------------------------

    def subcommand(self) -> str:
        """``native`` for rival countries, ``play`` otherwise (campaign, co-op and the rest are
        profile fields ``play`` reads)."""
        return "native" if self.config.rival else "play"

    def argv(self, profile: Path | None = None) -> list[str]:
        """The arguments after ``hoi4-harness`` that build this config from its profile."""
        path = str(profile or self.profile_path)
        args = [self.subcommand(), "--profile", path]
        if self.config.rival:
            args += ["--rival", self.config.rival]
        return args

    def command(self) -> str:
        """The command line for the settings, to paste in a terminal. Save first: it names the
        profile file."""
        parts = ["hoi4-harness", *self.argv()]
        if sys.platform == "win32":
            return subprocess.list2cmdline(parts)
        return shlex.join(parts)

    # preflight and run ------------------------------------------------------

    def effective_config(self) -> HarnessConfig:
        """The config the run will have, with the mode's defaults applied (a copy)."""
        config = copy.deepcopy(self.config)
        if config.campaign:
            from .campaign.setup import apply_defaults

            apply_defaults(config)
        if config.rival:
            from .rival.setup import apply_defaults as apply_rival

            apply_rival(config)
        return config

    def preflight(self, run_checks: Callable | None = None) -> PreflightResult:
        """The countries table's problems, then the campaign preflight checks (which send no key
        and never look at the game) for the config the run will have."""
        result = PreflightResult(problems=row_problems(self.rows, self.environ))
        if run_checks is None:
            from .campaign.preflight import run_checks
        try:
            config = self.effective_config()
            kwargs = {"env": self.environ} if self.environ is not None else {}
            result.checks = list(run_checks(config, **kwargs))
        except Exception as exc:       # a map or a rival list that cannot be honoured
            result.problems.append(str(exc))
        return result

    def start(self, run_checks: Callable | None = None, spawn: Callable = subprocess.Popen,
              python: str | None = None) -> tuple[PreflightResult, RunPlan | None]:
        """Preflight, and when it passes save the profile and start the run in the background.
        Nothing starts on a failed preflight."""
        result = self.preflight(run_checks)
        if not result.ok:
            return result, None
        path = self.save()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        plan = RunPlan([python or sys.executable, "-m", "hoi4_harness.cli", *self.argv(path)], path,
                       home_dir() / "logs" / f"run-{stamp}.log")
        plan.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = plan.log_path.open("ab")
        options: dict = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
                         "close_fds": True}
        if sys.platform == "win32":
            options["creationflags"] = 0x08000000 | 0x00000200 | 0x00000008
        else:
            options["start_new_session"] = True
        try:
            spawn(plan.argv, **options)
        finally:
            log.close()
        return result, plan


def default_model_for(provider: str) -> str:
    return DEFAULT_MODELS.get(provider, "")

