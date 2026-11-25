"""Configuration, resolved from CLI flags over environment over defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Per-provider defaults, used when no model is named. Nothing here is special-
# cased in the code: any model string the provider accepts will do.
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-5",
    "google": "gemini-2.5-pro",
    "scripted": "scripted",
    "mailbox": "mailbox",
    "claude-cli": "haiku",
}

#: Providers that bill nothing through this harness: a scripted model, a mailbox answered
#: by an agent outside it, and the Claude Code CLI on the person's subscription.
UNPRICED_PROVIDERS = frozenset({"scripted", "mailbox", "claude-cli"})

def mod_dirs_from_env(raw: str | None) -> list[Path]:
    """``HOI4_MOD_DIRS``: mod folders in load order, separated like PATH."""
    if not raw:
        return []
    return [Path(part) for part in raw.split(os.pathsep) if part.strip()]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float | None = None) -> float | None:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class LLMConfig:
    """The planner: the model that decides what to do when the loop wakes it.

    One role, deliberately. A second cheap model gating access to this one cannot
    pay for itself here -- see the note on the wake rule in agent/policy.py.
    """

    provider: str = "scripted"
    model: str = ""
    base_url: str | None = None          # OpenAI-compatible endpoints: Ollama, vLLM, OpenRouter
    api_key_env: str | None = None
    max_tokens: int = 16000
    effort: str = "high"                 # reasoning effort, where the provider supports it
    temperature: float | None = None
    cache_prefix: bool = True            # cache the stable system+tools prefix, where supported
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model:
            self.model = DEFAULT_MODELS.get(self.provider, "")

    def switch_provider(self, provider: str) -> None:
        """Change provider. A model that was only the old provider's default goes
        with it -- otherwise ``--provider anthropic`` would send the model name
        ``scripted`` to the real API -- while a model someone chose is kept."""
        if self.model == DEFAULT_MODELS.get(self.provider, ""):
            self.model = ""
        self.provider = provider
        self.__post_init__()

    @classmethod
    def from_env(cls, prefix: str = "HOI4_LLM") -> LLMConfig:
        provider = os.environ.get(f"{prefix}_PROVIDER", "scripted").strip().lower()
        model = os.environ.get(f"{prefix}_MODEL", "").strip()
        if not model:
            model = DEFAULT_MODELS.get(provider, "")
        return cls(
            provider=provider,
            model=model,
            base_url=os.environ.get(f"{prefix}_BASE_URL") or None,
            api_key_env=os.environ.get(f"{prefix}_API_KEY_ENV") or None,
            max_tokens=_env_int(f"{prefix}_MAX_TOKENS", 16000),
            effort=os.environ.get(f"{prefix}_EFFORT", "high").strip(),
            temperature=_env_float(f"{prefix}_TEMPERATURE"),
        )


@dataclass
class BudgetConfig:
    """Hard ceilings for one run. The loop degrades to the reflex policy when a
    ceiling is hit rather than stopping the game."""

    max_llm_calls: int = 250
    max_input_tokens: int = 4_000_000
    max_output_tokens: int = 400_000
    max_usd: float | None = None
    usd_per_m_input: float = 0.0         # set from your provider's price sheet
    usd_per_m_output: float = 0.0
    #: Cache reads and cache writes, per million. None bills them at the input
    #: rate -- which overstates a cached run, so set them when you set the rest.
    #: Typical ratios to the input rate: reads 0.1x (Anthropic), 0.25-0.5x
    #: (OpenAI, Gemini); writes 1.25x (Anthropic 5-minute cache), none elsewhere.
    usd_per_m_cached_input: float | None = None
    usd_per_m_cache_write: float | None = None
    #: Dollars kept back from ``max_usd``: a call is refused once spend + reserve
    #: would pass the cap, so the last call cannot be the one that overshoots it. A
    #: single long wake costs a few tenths of a dollar, which is what to set it to.
    reserve_usd: float = 0.0

    @classmethod
    def from_env(cls) -> BudgetConfig:
        return cls(
            max_llm_calls=_env_int("HOI4_MAX_LLM_CALLS", 250),
            max_input_tokens=_env_int("HOI4_MAX_INPUT_TOKENS", 4_000_000),
            max_output_tokens=_env_int("HOI4_MAX_OUTPUT_TOKENS", 400_000),
            max_usd=_env_float("HOI4_MAX_USD"),
            usd_per_m_input=_env_float("HOI4_USD_PER_M_INPUT", 0.0) or 0.0,
            usd_per_m_output=_env_float("HOI4_USD_PER_M_OUTPUT", 0.0) or 0.0,
            usd_per_m_cached_input=_env_float("HOI4_USD_PER_M_CACHED_INPUT"),
            usd_per_m_cache_write=_env_float("HOI4_USD_PER_M_CACHE_WRITE"),
            reserve_usd=_env_float("HOI4_RESERVE_USD", 0.0) or 0.0,
        )


@dataclass
class HarnessConfig:
    adapter: str = "mock"
    planner: LLMConfig = field(default_factory=LLMConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)

    # --- what the model is told ---------------------------------------------
    # A built-in pack name ("none", "unconstrained", "minimal", "doctrine",
    # "historical", "coach") or a path to your own file. `system_prompt_path`
    # replaces the whole prompt, mechanics included.
    guidance: str = "doctrine"
    system_prompt_extra: str = ""
    #: The person's standing instruction ("go alt-history Germany and ally the UK"): free text put
    #: in the system prompt, so it holds for the whole run. ``rival_players`` (below) can give a
    #: country its own.
    instruction: str = ""
    # Skill packs (docs/skills.md): SKILL.md folders from these directories, the home
    # folder, the repo and the active mods; `skills` names ones to include anyway.
    skills: list[str] = field(default_factory=list)
    skills_dirs: list[Path] = field(default_factory=list)
    skill_tokens: int = 3000
    system_prompt_path: Path | None = None
    objective: str = (
        "Build a defensible economy, keep stability and war support healthy, and do not "
        "enter a war you cannot supply."
    )

    # --- what the model may do ----------------------------------------------
    # `enabled_actions` is a whitelist (None = everything the adapter supports);
    # `disabled_actions` subtracts from it. `require_confirmation` False lets
    # irreversible actions through without a gate -- only meaningful with --live.
    enabled_actions: list[str] | None = None
    disabled_actions: list[str] = field(default_factory=list)
    require_confirmation: bool = True

    # --- pacing -------------------------------------------------------------
    # The harness owns the clock. It pauses the game, decides, then runs forward
    # `days_per_turn` in-game days before looking again. A critical event cuts
    # the wait short. It is also the scheduled review interval, so a turn that
    # nothing else wakes the planner for is a review.
    days_per_turn: int = 7
    max_actions_per_turn: int = 8
    max_tool_rounds_per_turn: int = 3
    #: Consecutive provider failures tolerated before the run stops. A fatal
    #: error (bad key, rejected schema) stops immediately regardless.
    max_consecutive_llm_errors: int = 5
    full_brief_every: int = 8
    wake_on_free_research_slot: bool = True
    wake_on_no_focus: bool = True
    # A front starting to lose ground. Like the wartime rules below it fires on
    # onset: a front that stays losing wakes the model once, not every turn.
    wake_on_losing_ground: bool = True
    # Wartime wake rules. Each fires on the onset of its condition, never on the
    # condition standing, so a long siege does not wake the model every turn.
    wake_on_encirclement: bool = True
    wake_on_supply_collapse: bool = True
    wake_on_capital_threat: bool = True
    wake_on_ally_capitulation: bool = True
    wake_on_front_quiet: bool = True
    # False is the reflex-only baseline: the model is never woken and the run is
    # free, which is what every planner-run score is reported against.
    planner_enabled: bool = True
    reflex_enabled: bool = True
    # The AI-only arm of the hybrid experiment: the reflex layer hands every
    # army to the game's AI. Never on by default -- see Policy._delegate.
    reflex_delegate: bool = False

    # --- run ----------------------------------------------------------------
    turns: int = 10
    dry_run: bool = True
    # Advisor mode: the model recommends and never acts. Tool calls are
    # intercepted and shown to a human, so a read-only adapter suffices.
    advisor: bool = False
    run_dir: Path = Path("runs")
    save_dir: Path | None = None
    log_path: Path | None = None          # game.log, for the logtail adapter
    window_title: str = "Hearts of Iron IV"
    # The input driver's recorded click paths and screen coordinates. Default:
    # ui_scripts.json and calibration.json in run_dir, when they exist.
    ui_scripts_path: Path | None = None
    calibration_path: Path | None = None
    # Refuse to send input unless the game is the focused window. Turning
    # this off is an explicit choice to let keystrokes land wherever they land.
    enforce_window_focus: bool = True
    # Which layer owns operations. "llm" = the model moves every army;
    # "ai" = the native AI runs fronts and the model sets intent (hybrid).
    operational_control: str = "llm"
    # --- modular control (control.py, docs/modular-control.md) -------------------------
    #: Who runs each part of the country: a table ``{"focus": "llm", "research": "script",
    #: "production": "game", ...}`` (controllers llm | script | game | off; ``default`` names the
    #: controller of every domain not listed), a ``"focus=llm,research=script"`` string or a
    #: preset name. Unset, the old settings (``operational_control``) are the control map.
    control: dict | str | None = None
    #: The steering layer (set_ai_directive, set_ai_posture, delegate_army_to_ai): ``llm``,
    #: ``off`` or ``auto`` (llm when the game's AI owns something). Only read with ``control``.
    steer: str = "auto"
    #: The tactical controller the ``army: script`` domain runs (tactics/registry.py).
    army_controller: str = "scripted"
    #: Turn the refusal of a contested map (control.py) into a warning.
    allow_contested: bool = False
    #: Options of the script controllers (auto/): ``research_list``, ``focus_path``,
    #: ``construction``, ``production``, ``politics_rules``, ``army_cooldown_days``.
    auto_options: dict = field(default_factory=dict)
    # Who owns the game clock. "harness" pauses, decides, and runs the game
    # forward itself. "player" never touches it -- required when a person is
    # playing the same campaign, since being paused mid-battle by your own
    # tooling is worse than having no tooling.
    clock_owner: str = "harness"
    # How long the in-game date must stand still before the input driver takes
    # the game as paused. The pause key is a toggle, so the driver looks before
    # and after pressing it; this must be longer than an in-game day takes at
    # the speed the game runs.
    clock_probe_seconds: float = 10.0
    # --- the console channel (#83) ---------------------------------------------
    # Which channel performs each action, as {action: "ui" | "console"}. Only the
    # actions you name change; the rest keep adapters.console.DEFAULT_CHANNELS.
    action_channels: dict[str, str] = field(default_factory=dict)
    #: Where the game's `run` command reads files from. UNVERIFIED: unset means
    #: the game's user directory. HOI4_CONSOLE_DIR.
    console_dir: Path | None = None
    console_filename: str = "llmb_turn.txt"          # HOI4_CONSOLE_FILE
    #: The console key: a key name (grave) or a raw scan code (0x29). HOI4_CONSOLE_KEY.
    console_key: str = "grave"
    #: The console command that runs an inline effect. HOI4_CONSOLE_EFFECT_COMMAND.
    console_effect_command: str = "eval_effect"
    #: How hybrid actions reach the mod: scripted | decision.
    console_hybrid_via: str = "scripted"
    console_guards: bool = True
    #: How the console channel's lines reach the game: ``keys`` (type ``run <file>`` into the
    #: console: needs the game in front and an idle person) or ``inbox`` (write them into the
    #: mod's inbox file, which a game started with -debug_mode reloads and runs once a day:
    #: no keyboard, no focus, no pause; docs/inbox.md). HOI4_TRANSPORT.
    console_transport: str = "keys"
    #: The installed mod folder the inbox writes into. Default: <user dir>/mod/llm_bridge.
    inbox_dir: Path | None = None
    #: Seconds to wait for the game to acknowledge a batch before reporting it as posted.
    inbox_ack_seconds: float = 6.0
    # --- division orders by click (adapters/unit_orders.py, docs/unit-orders.md) -----
    #: Offer move_division / attack_province. Needs the mouse, a playset (game_dir)
    #: for the map, and a camera fit recorded in the live game.
    #: None: off, except in an independent campaign with the mouse, where it is on.
    unit_orders: bool | None = None
    #: The recorded camera fit. Default: camera.json in run_dir. HOI4_CAMERA.
    camera_path: Path | None = None
    #: Offer draw_front_line / draw_offensive / assign_battle_plan (adapters/battle_plans.py).
    #: NOT live-verified, so off unless asked for; needs what unit_orders needs.
    battle_plans: bool = False
    #: Offer create_army / assign_divisions / assign_army_front / draw_fallback_line /
    #: set_army_execution / release_army (adapters/armies.py, docs/armies.md). NOT live-verified, so
    #: off unless asked for; needs what unit_orders needs.
    army_ui: bool = False
    # --- headless play (adapters/headless.py) -----------------------------------
    # adapter = "headless": the log for eyes, the console and the keyboard for
    # hands, no mouse and no screen. The fields below only matter there.
    #: How the clock is driven: console (gamespeed N), keys (space), auto (console,
    #: then keys when the console did not take).
    headless_clock: str = "auto"
    #: What reads the game: ``log+save`` (the mod's telemetry every tick, a text
    #: save the console writes on demand for production, construction, divisions
    #: and completed focuses), ``logtail`` (the log alone: production and the queue
    #: stay unseen, so set_production is not offered) or ``savegame`` (a save only).
    headless_reader: str = "log+save"
    #: With ``log+save``: take a save every this many in-game days at most (0: only
    #: at the start and around verified actions). A save is ~66 MB and takes a few
    #: seconds to write and ~1.4 s to read, so this is a cost, not a free refresh.
    headless_save_days: int = 7
    #: Seconds to wait for a save to appear after the console's ``savegame``.
    headless_save_timeout: float = 30.0
    #: The speed the harness runs the game at when it advances (1-5).
    headless_game_speed: int = 3
    headless_poll_seconds: float = 0.5
    #: Multiplies every real sleep. 1.0 is the game's own pace; tests shrink it.
    headless_time_scale: float = 1.0
    #: Ask the mod for fresh telemetry at the end of each console run, so an
    #: action can be verified while the game is paused. None: on for headless.
    console_refresh: bool | None = None
    #: Run against a fake game with no HOI4 at all (fakegame.py). ``fake_game`` is
    #: in this process, files under ``fake_game_dir`` (default: <run_dir>/fakegame);
    #: ``fake_game_files`` is a ``hoi4-harness fakegame`` process already running.
    fake_game: bool = False
    fake_game_dir: Path | None = None
    fake_game_files: Path | None = None
    # --- campaign mode (campaign/, docs/campaign.md) -----------------------------------
    # An unattended run: the game's AI runs the player's country (the console's
    # ``human_ai``) and the model is the grand strategist above it. ``campaign`` is set
    # by ``play --campaign`` or a profile; ``campaign.setup.apply_defaults`` fills in
    # the rest of what it implies (headless, game_ai, no reflexes, the wake rules).
    campaign: bool = False
    #: The date the run ends at; ``play --until`` overrides it.
    campaign_until: str = "1948-12-31"
    #: The scheduled review, and so the length of a turn: the clock runs this many
    #: in-game days between model wakes (a critical event cuts it short). The model's
    #: own advance_time moves the next one, within [min, max].
    campaign_review_peace_days: int = 30
    campaign_review_war_days: int = 14
    campaign_review_min_days: int = 7
    campaign_review_max_days: int = 90
    #: A critical event (a war, a capitulation) wakes the model only when this many
    #: days have passed since it last woke, so a string of capitulations is one wake.
    campaign_wake_gap_days: int = 7
    #: A recovery ``savegame`` every this many in-game months (0: none). 66 MB each.
    campaign_save_months: int = 6
    #: Keep the newest this-many saves the harness took, plus the first of the run, and
    #: delete the older ones it made (never one it did not make; campaign/saves.py).
    campaign_keep_saves: int = 6
    #: Stop, with a reason, when the game log has grown by nothing for this long while
    #: the game should be running.
    campaign_silence_minutes: float = 20.0
    #: How long the focus guard waits for the game to come back to the front, in all,
    #: before it gives up and stops the run.
    campaign_focus_wait_minutes: float = 360.0
    #: Opt in to bringing the game window to the front when it is not there (Windows
    #: ``SetForegroundWindow``). Off by default: the harness waits and never takes the
    #: keyboard from whatever the person is doing.
    campaign_take_focus: bool = False
    #: ...and only once the keyboard and mouse have been idle this long, so it never pulls
    #: the game over a window someone is using (a relaunch left VS Code in front, 2026-11-22).
    campaign_take_focus_idle_seconds: float = 120.0
    #: The game's process name, for the death check (empty: no check).
    campaign_game_process: str = "hoi4.exe"
    #: What an exhausted budget does: ``stop`` (leave the game paused, resumable) or
    #: ``coast`` (the AI plays on to the end date with no model calls).
    campaign_on_budget: str = "stop"
    #: HOI4 leaks memory over a long run (78 GB private after three hours on 2026-11-22; the
    #: run before crashed in 1944 at the same point). Past this many GB of the game's private
    #: memory, a recovery save is taken at the next turn and then ``campaign_on_memory``
    #: applies: ``stop`` (a clean, resumable stop: restart the game, load the save, --resume)
    #: or ``warn`` (a log line each turn). 0 turns the check off.
    campaign_memory_limit_gb: float = 48.0
    campaign_on_memory: str = "stop"
    #: Stop, with a reason, at the next turn boundary once the PLAYER's own country
    #: capitulates. Off (the default): play on in exile. A capitulated country lives on in
    #: HOI4 (government in exile, whatever states it still holds), so the run wakes the
    #: model with the news and goes on; it ends only when the country holds no states
    #: (see ``CampaignSupervisor._no_states_left``).
    campaign_stop_on_capitulation: bool = False
    #: Who plays the country: ``game_ai`` (the game's own AI runs it through ``human_ai``, the
    #: model steers with directives; the default) or ``llm`` (independent: ``human_ai`` is
    #: not sent, the model plays the country itself with the hands-on actions the headless
    #: adapter offers; not yet verified against the live game, docs/campaign.md).
    campaign_control: str = "game_ai"
    #: Issue #161 step 1, an experiment. With the game's AI running the country (human_ai), also
    #: offer the model ``add_production_line``, ``design_equipment`` and ``start_research`` (where
    #: the adapter has it): the AI may undo, switch or double them, which is what the run measures
    #: (``scripts/contested_report.py``). Off: the AI owns them, as before.
    campaign_contested_production: bool = False
    #: In an independent campaign (``campaign_control = "llm"``): ``mouse`` (the default) adds
    #: the input driver's mouse to the headless adapter, for the actions the console cannot do
    #: (research, division orders, handing an army to the game's AI); ``none`` keeps the
    #: keyboard-and-console-only run. The game-AI campaign ignores it. docs/campaign.md.
    campaign_input: str = "mouse"
    #: Event and news windows in an independent campaign (adapters/event_windows.py, issue #125):
    #: ``{"probes": {"country": {"x", "y", "rgb", "tolerance"}, "news": {...}}, "answers": {...},
    #: "attempts": 3, "settle": 0.5}``. With probes, every mouse tool first closes the windows that
    #: are open and refuses to click while one is; without them nothing knows a window is open
    #: (the start log says so). The points and colours are measured in the live game.
    event_windows: dict = field(default_factory=dict)
    #: Reading the open event window and choosing its option (adapters/event_choice.py,
    #: docs/events.md): ``{"enabled": true, "upscale": 2.0, "settle": 0.8, "grow": 0.0,
    #: "centre_growth": false}``. On by default in an independent campaign with the mouse; it
    #: needs Windows OCR and the playset's files, and says so at the start when it has neither.
    event_screen: dict = field(default_factory=dict)
    #: What the independent campaign found live or refused at its start (set by the adapter and
    #: ``campaign.runner.begin``, read by the prompt). Not a setting.
    campaign_hands: dict = field(default_factory=dict, repr=False)
    #: Toggle ``human_ai`` at the start (the one step that needs the game).
    campaign_human_ai: bool = True
    #: Stop unless the state line's ``ai=`` reads 1 after the toggle.
    campaign_verify_ai: bool = True
    #: The new-game screen's "Historical AI Focuses" rule, as this profile wants it:
    #: ``on``, ``off`` or ``any``. The run always logs what its saves say; ``on`` warns
    #: when a save reads ``historical=0`` (and ``off`` when it reads 1). ``any`` only logs.
    #: It never stops the run: whether to run with the rule on is the person's call.
    campaign_historical_ai: str = "any"
    #: Before the start, show the model the paths open to its country (the AI behaviour game
    #: rule's options and a focus tree summary), ask it once to choose one with a reason, record
    #: it in the run state and build the guidance around it (#160). The choice is kept across
    #: ``--resume``. Costs one model call.
    campaign_choose_path: bool = False
    #: With ``campaign_choose_path``: click the choice into the new-game lobby (Custom Game
    #: Rules, Historical AI Focuses off, Start) before the campaign begins. Sends real mouse
    #: clicks and needs ``campaign_lobby_points`` (docs/campaign.md); off, the choice only
    #: steers the guidance and the log says which rule to set by hand.
    campaign_lobby_rule: bool = False
    #: Screen points for the lobby steps nobody has recorded yet (campaign/lobby.py):
    #: ``{"rule_row": [x, y], "option:FASCIST": [x, y], "rules_close": [x, y]}``.
    campaign_lobby_points: dict = field(default_factory=dict)
    #: A briefing agent (#165, docs/briefing.md): before the first turn a separately configured
    #: model reads a dossier of the world and the country and writes an orientation (the
    #: situation and the viable paths, no plan), saved in the run dir, shown to the person and
    #: given to the player as context under the person's own instruction. Costs one call of
    #: the ``briefing`` model, outside ``budget.max_usd``.
    campaign_briefing: bool = False
    #: When to brief again: a list (or comma string) of ``yearly``, ``halfyearly``, dates
    #: ``YYYY-MM-DD`` and the events ``war``, ``capitulation``, ``faction``. Empty: never.
    campaign_rebrief: list = field(default_factory=list)
    #: Let the briefing model search the web for mods it cannot judge from files (opt-in;
    #: the Workshop brief is read first; only providers that have a search tool).
    briefing_web_search: bool = False
    #: The save the first briefing reads (default: the newest save of the country).
    briefing_save: Path | None = None
    #: The briefing model. An empty provider means the planner's own client briefs. The
    #: price per million tokens may go in ``extra`` (``usd_per_m_input``/``usd_per_m_output``).
    briefing: LLMConfig = field(default_factory=lambda: LLMConfig(provider=""))
    #: The AI the game runs underneath (docs/expert-ai.md): ``vanilla``, ``expert_ai`` (the
    #: Workshop mod; the campaign then turns on the strong directive weights, because its own
    #: plans would drown the normal ones) or ``auto`` (read dlc_load.json at the start).
    base_ai: str = "auto"
    #: How often the mod writes its telemetry (cadence.py, docs/mod-bridge.md): hours between two
    #: ticks. None (the default): the run does not touch it, so the mod keeps once a day, or
    #: whatever a loaded save was set to. 24 sets once a day explicitly; 1, 2, 3, 4, 6, 8 or 12
    #: a tick several times a day; a multiple of 24 (48, 72, ... 720) every N days. Sent through
    #: the console at the start of a headless run (play, campaign, rival).
    telemetry_hours: int | None = None

    # --- rival mode (rival/, docs/rival.md) -----------------------------------------
    # One human, one single-player game: the harness steers an AI country (``rival``,
    # a tag) while the person plays another. Every action is queued and typed only in a
    # window the person's own behaviour makes safe; the clock is never touched.
    rival: str = ""
    #: When the queue may be typed: ``paused`` (the person has paused the game, has not
    #: touched the keyboard or mouse for a while and the game is in front), ``handover``
    #: (only when ``hoi4-harness handover`` grants it) or ``never`` (shown, not run).
    rival_act_when: str = "paused"
    #: ``paused``: the in-game date in game.log must have stood still this many seconds.
    #: A day takes at most ~3 s at the slowest speed, so 10 is a pause, not a slow day.
    rival_paused_seconds: float = 10.0
    #: ``paused``: no key or mouse input from anyone for this many seconds.
    rival_idle_seconds: float = 5.0
    #: A queued action older than this many in-game days is reported stale, not run (0: never
    #: by age). A person can play months of game time between two pauses, so this is long.
    rival_stale_days: int = 180
    #: How long the start waits for a window in which to mark the country.
    rival_start_wait_minutes: float = 30.0
    #: How long the exit waits for a window in which to unmark it (then it says what to type).
    rival_exit_wait_seconds: float = 60.0
    #: Several rivals (docs/rival.md, "Several countries"): ``rival`` holds what was asked
    #: for (``FRA,ENG,SOV`` or ``majors``) and ``rivals`` the tags it comes to once checked;
    #: empty for the one-country form. Each gets its own strategist, its own run-dir folder
    #: and a share of ``budget.max_usd`` (``rival_shares``: tag -> weight, default equal).
    rivals: list[str] = field(default_factory=list)
    rival_shares: dict[str, float] = field(default_factory=dict)
    #: Per-country settings from the setup window / ``--setup`` file (setup_model.py), keyed by
    #: tag: any of ``provider``, ``model``, ``base_url``, ``api_key_env``, ``max_usd``,
    #: ``instruction``. A country not listed here uses the shared ``planner`` and ``instruction``.
    rival_players: dict[str, dict] = field(default_factory=dict)
    #: The most actions one window types across all the rivals together (a window is
    #: seconds at the person's keyboard); the rest wait for the next, taken in turn.
    rival_window_cap: int = 12
    #: Game days between two rivals' first reviews, so they do not all call the model at once.
    rival_stagger_days: int = 1
    #: The fake game's person: the country they play, and a JSON script of what they do and
    #: when (``[[seconds, "pause"], ...]``; default: play 45 s, pause 90 s, repeat).
    fake_rival_human: str = "GER"
    fake_human_script: Path | None = None

    # Co-op turn-taking: every action is queued and runs only when the player
    # grants a window (handover.py). For playing alongside a person, not for
    # scoring -- the eval runner refuses a handover run.
    handover: bool = False
    #: When the queue may run (coop.py): ``hotkey`` (the player runs `hoi4-harness handover`, or
    #: presses ``handover_hotkey`` when that is set), ``idle`` (the game is paused, the player
    #: has been quiet for ``handover_idle_seconds`` and the game is in front) or ``approve``
    #: (each action is shown to the player and runs only on a yes, via ``confirm_hook``).
    handover_mode: str = "hotkey"
    handover_idle_seconds: float = 5.0
    handover_paused_seconds: float = 10.0
    #: A global hotkey that grants the window, e.g. ``ctrl+alt+h``. Empty (the default): no
    #: listener, the grant file is the only trigger. Windows only.
    handover_hotkey: str = ""
    #: Co-op by domain (docs/co-op.md): these domains are the player's, the model never acts in
    #: them. Folded into the control map as ``domain=human``.
    human_domains: list[str] = field(default_factory=list)
    #: Multiplayer (docs/multiplayer.md): the harness plays ``country`` while the people in
    #: ``mp_human_tags`` play theirs. An action aimed at one of those countries is refused.
    multiplayer: bool = False
    mp_human_tags: list[str] = field(default_factory=list)
    # The player's tag. Unset, the mock plays Sweden and the log reader follows
    # whichever human country the game reports; set, the reader keeps to it.
    country: str | None = None
    start_date: str = "1936-01-01"
    seed: int = 1936
    # --- the playset: what identifiers exist --------------------------------
    # The install (the folder holding hoi4.exe and common/) and the active mods
    # in load order. With game_dir set, focus, technology, state and tag ids are
    # checked against what the game actually loads -- see identifiers.py.
    game_dir: Path | None = None
    mod_dirs: list[Path] = field(default_factory=list)
    # --- DLC (docs/dlc.md): which of the game's DLCs are on. ``dlcs`` None reads the install
    # (<game_dir>/dlc) minus ``disabled_dlcs`` in <user_dir>/dlc_load.json; a list names the
    # active set outright ([] is "no DLC"); ``--dlc NAME`` / ``--no-dlc`` set it. ``user_dir`` is
    # the folder holding dlc_load.json (default: where the game keeps its saves).
    dlcs: list[str] | None = None
    user_dir: Path | None = None
    # --- mod context (docs/mods.md): a "This playset" section in the system prompt
    # (a world brief per mod, the country's decisions and focus tree, what the
    # mods add) and the read-only list_decisions / describe_action tools. Off by
    # default: it changes the prompt. The Workshop pages it summarises are read from
    # the cache unless ``mod_context_fetch`` lets the run fetch a missing one.
    mod_context: bool = False
    #: The forces-and-map brief section and inspect_world, from the newest text save (#139).
    #: Rival and native runs turn it on.
    observe_world: bool = False
    #: The single-player chat (#133): the brief's messages and send_message. Rival and native
    #: runs turn it on. chat_path: the shared log, default ~/.hoi4-harness/chat.jsonl or HOI4_CHAT.
    chat: bool = False
    chat_path: Path | None = None
    mod_context_fetch: bool = False
    mod_context_tokens: int = 900
    mod_context_cache_dir: Path | None = None

    @classmethod
    def from_env(cls) -> HarnessConfig:
        save_dir = os.environ.get("HOI4_SAVE_DIR")
        log_path = os.environ.get("HOI4_LOG_PATH")
        system_prompt_path = os.environ.get("HOI4_SYSTEM_PROMPT")
        return cls(
            adapter=os.environ.get("HOI4_ADAPTER", "mock").strip().lower(),
            planner=LLMConfig.from_env("HOI4_LLM"),
            budget=BudgetConfig.from_env(),
            guidance=os.environ.get("HOI4_GUIDANCE", "doctrine").strip(),
            system_prompt_extra=os.environ.get("HOI4_SYSTEM_PROMPT_EXTRA", ""),
            instruction=os.environ.get("HOI4_INSTRUCTION", ""),
            system_prompt_path=Path(system_prompt_path) if system_prompt_path else None,
            objective=os.environ.get("HOI4_OBJECTIVE") or cls.objective,
            require_confirmation=_env_bool("HOI4_REQUIRE_CONFIRMATION", True),
            planner_enabled=_env_bool("HOI4_PLANNER_ENABLED", True),
            days_per_turn=_env_int("HOI4_DAYS_PER_TURN", 7),
            max_actions_per_turn=_env_int("HOI4_MAX_ACTIONS_PER_TURN", 8),
            max_tool_rounds_per_turn=_env_int("HOI4_MAX_TOOL_ROUNDS", 3),
            max_consecutive_llm_errors=_env_int("HOI4_MAX_CONSECUTIVE_LLM_ERRORS", 5),
            full_brief_every=_env_int("HOI4_FULL_BRIEF_EVERY", 8),
            turns=_env_int("HOI4_TURNS", 10),
            dry_run=_env_bool("HOI4_DRY_RUN", True),
            advisor=_env_bool("HOI4_ADVISOR", False),
            run_dir=Path(os.environ.get("HOI4_RUN_DIR", "runs")),
            save_dir=Path(save_dir) if save_dir else None,
            log_path=Path(log_path) if log_path else None,
            window_title=os.environ.get("HOI4_WINDOW_TITLE", "Hearts of Iron IV"),
            ui_scripts_path=(Path(os.environ["HOI4_UI_SCRIPTS"])
                             if os.environ.get("HOI4_UI_SCRIPTS") else None),
            calibration_path=(Path(os.environ["HOI4_CALIBRATION"])
                              if os.environ.get("HOI4_CALIBRATION") else None),
            enforce_window_focus=_env_bool("HOI4_ENFORCE_WINDOW_FOCUS", True),
            operational_control=os.environ.get("HOI4_OPERATIONAL_CONTROL", "llm").strip(),
            clock_owner=os.environ.get("HOI4_CLOCK_OWNER", "harness").strip(),
            clock_probe_seconds=_env_float("HOI4_CLOCK_PROBE_SECONDS", 10.0) or 10.0,
            console_dir=(Path(os.environ["HOI4_CONSOLE_DIR"])
                         if os.environ.get("HOI4_CONSOLE_DIR") else None),
            console_filename=os.environ.get("HOI4_CONSOLE_FILE", "llmb_turn.txt"),
            console_key=os.environ.get("HOI4_CONSOLE_KEY", "grave").strip(),
            console_transport=os.environ.get("HOI4_TRANSPORT", "keys").strip().lower() or "keys",
            console_effect_command=os.environ.get("HOI4_CONSOLE_EFFECT_COMMAND",
                                                  "eval_effect").strip(),
            handover=_env_bool("HOI4_HANDOVER", False),
            country=(os.environ.get("HOI4_COUNTRY") or "").strip().upper() or None,
            start_date=os.environ.get("HOI4_START_DATE", "1936-01-01"),
            seed=_env_int("HOI4_SEED", 1936),
            game_dir=Path(os.environ["HOI4_GAME_DIR"]) if os.environ.get("HOI4_GAME_DIR") else None,
            mod_dirs=mod_dirs_from_env(os.environ.get("HOI4_MOD_DIRS")),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> HarnessConfig:
        """Load a profile: one JSON or TOML file holding the whole configuration.

        Environment defaults are applied first, so a profile only has to state
        what it changes. Nested ``planner``/``budget`` tables are
        merged rather than replaced.
        """
        path = Path(path)
        raw = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".toml":
            import tomllib

            data = tomllib.loads(raw)
        else:
            import json

            data = json.loads(raw)
        return cls.from_env().merge(data)

    def merge(self, data: dict) -> HarnessConfig:
        """Apply a nested dict of overrides, returning self."""
        for key, value in data.items():
            if key == "planner" and isinstance(value, dict):
                role = getattr(self, key)
                for sub_key, sub_value in value.items():
                    if sub_key == "provider":
                        role.switch_provider(sub_value)
                    elif hasattr(role, sub_key):
                        setattr(role, sub_key, sub_value)
                    else:
                        raise KeyError(f"Unknown configuration key {key}.{sub_key!r}")
                role.__post_init__()
            elif key == "briefing" and isinstance(value, dict):
                role = self.briefing
                for sub_key, sub_value in value.items():
                    if sub_key == "provider":
                        role.switch_provider(sub_value)
                    elif hasattr(role, sub_key):
                        setattr(role, sub_key, sub_value)
                    else:
                        raise KeyError(f"Unknown configuration key briefing.{sub_key!r}")
                role.__post_init__()
            elif key == "budget" and isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    if not hasattr(self.budget, sub_key):
                        # A typo here is a spending cap that silently isn't one.
                        raise KeyError(f"Unknown configuration key budget.{sub_key!r}")
                    setattr(self.budget, sub_key, sub_value)
            elif key in {"run_dir", "save_dir", "log_path", "system_prompt_path", "game_dir",
                         "ui_scripts_path", "calibration_path", "console_dir", "camera_path",
                         "fake_game_dir", "fake_game_files", "mod_context_cache_dir", "inbox_dir", "user_dir",
                         "chat_path", "briefing_save",
                         "fake_human_script"} and value is not None:
                setattr(self, key, Path(value))
            elif key in ("mod_dirs", "skills_dirs"):
                setattr(self, key, [Path(v) for v in value])
            elif hasattr(self, key):
                setattr(self, key, value)
            else:
                raise KeyError(f"Unknown configuration key {key!r}")
        return self

    @property
    def hands_mouse(self) -> bool:
        """An independent campaign whose adapter has the mouse (``campaign_input = "mouse"``)."""
        return bool(self.campaign and self.campaign_control == "llm"
                    and self.campaign_input == "mouse")

    def control_map(self):
        """The control map this configuration means: ``control`` when it is set, else the old
        ``operational_control`` as the preset it stands for. Raises ``control.ControlError``
        for a map that cannot be honoured."""
        from .control import ControlError, parse_spec, preset, resolve

        control = self.control
        if self.human_domains:
            spec = parse_spec(control) if control else {}
            if "preset" in spec:
                raise ControlError("human_domains cannot be combined with a control preset; "
                                   "write the map as domain=controller pairs")
            control = {**spec, **{d.strip(): "human" for d in self.human_domains}}
        if control:
            cmap = resolve(
                control, steer=None if self.steer == "auto" else self.steer,
                army_controller=self.army_controller, planner_enabled=self.planner_enabled,
                allow_contested=self.allow_contested)
            if cmap.human_ai and not self.campaign:
                from dataclasses import replace

                cmap = replace(cmap, warnings=(*cmap.warnings,
                    "a domain is set to game outside a campaign: only a campaign sends human_ai, "
                    "so the game's AI must already be running the country"))
            return cmap
        return preset(self.operational_control, self.army_controller)

    @property
    def planner_active(self) -> bool:
        """Whether a model is ever woken: the planner is on and the control map gives it
        something to run (an llm domain, or steering). A map with none is a no-model run."""
        return bool(self.planner_enabled and self.control_map().uses_model)

    @property
    def paced(self) -> bool:
        """Whether the model is woken on the campaign's review schedule (a campaign, or
        rival mode: both steer a game AI that runs everything else)."""
        return bool(self.campaign or self.rival)

    def build_index(self):
        """The identifier index for the configured playset, or None without one."""
        if self.game_dir is None:
            return None
        from .identifiers import IdentifierIndex, Playset

        return IdentifierIndex.build(Playset.from_paths(self.game_dir, self.mod_dirs, self.dlc_set()))

    def dlc_set(self):
        """The DLC picture of this configuration (``dlc.DlcSet``); unknown without a game dir."""
        from . import dlc, paths

        if self.game_dir is None:
            return dlc.DlcSet()
        user = None
        if self.dlcs is None:
            user = self.user_dir if self.user_dir is not None else paths.find_game_dir()
        return dlc.detect(self.game_dir, user, self.dlcs)

    def allowed_actions(self, adapter_supports: set[str]) -> set[str]:
        """Adapter capability, narrowed by control mode then by the operator.

        The control mode goes first so that an explicit whitelist can never
        smuggle a direct army order into a run where the native AI is executing.
        """
        from .agent.hybrid import actions_for_mode

        cmap = self.control_map()
        if cmap.explicit:
            allowed = cmap.actions_for_model(set(adapter_supports))
        else:
            allowed = actions_for_mode(set(adapter_supports), self.operational_control)
        if (self.hands_mouse and cmap.controller("army") == "llm"
                and "delegate_army_to_ai" in adapter_supports):
            # The one standing-order verb an independent campaign keeps: the directives and the
            # posture stay out (the game's AI is not running the country), but handing an army
            # to the AI is what lets the model hold a front it is not ordering (adapters/hands.py).
            allowed.add("delegate_army_to_ai")
        from .control import CONTESTED_PRODUCTION, PRODUCTION_LINE_ACTION

        if self.campaign_contested_production and self.campaign and cmap.human_ai:
            allowed |= CONTESTED_PRODUCTION & set(adapter_supports)
        elif not self.campaign_contested_production:
            allowed.discard(PRODUCTION_LINE_ACTION)
        if self.clock_owner != "harness":
            # The player owns the clock, so the model may not take it either --
            # structurally, not by asking it nicely in the prompt.
            allowed -= {"set_game_speed"}
        if self.enabled_actions is not None:
            allowed &= set(self.enabled_actions)
        return allowed - set(self.disabled_actions)

    def script_offered(self, adapter_supports: set[str]) -> set[str]:
        """What the script controllers may use: what the adapter offers, the operator's lists
        and the clock rule, but NOT the control narrowing (a script's domain is, by
        definition, one the model has no tools for)."""
        allowed = set(adapter_supports)
        if self.clock_owner != "harness":
            allowed -= {"set_game_speed"}
        if self.enabled_actions is not None:
            allowed &= set(self.enabled_actions)
        return allowed - set(self.disabled_actions)
