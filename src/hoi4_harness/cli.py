"""Command line entry point.

    hoi4-harness doctor                     what is installed and reachable
    hoi4-harness actions                    the agent's vocabulary
    hoi4-harness observe                    one situation brief, then exit
    hoi4-harness calibrate                  record screen coordinates for clicking
    hoi4-harness play --turns 20            run the loop
    hoi4-harness replay run.jsonl --turn 3  rebuild a turn's prompt, compare models
    hoi4-harness eval economy_ramp          run a scenario and score it
    hoi4-harness eval --no-llm              score every scenario on reflexes alone
    hoi4-harness compare economy_ramp       the same scenario across models, one table
    hoi4-harness measure --campaign wartime_1939_1941   brief size and wake rate in a war
    hoi4-harness index --game-dir PATH      what countries and focuses a playset defines
    hoi4-harness paths --country GER        the AI behaviour rule options and focus branches
    hoi4-harness mod-actions --country GER what a country can do in a playset, and what its mods are
    hoi4-harness experiment defensive_war   model-only vs AI-only vs hybrid, same seeds
    hoi4-harness play --headless            no mouse, no screen: the log in, the console and keys out
    hoi4-harness play --headless --fake-game   the same against a fake game, no HOI4 at all
    hoi4-harness headless-check --live      measure what headless play rests on, in the real game
    hoi4-harness fakegame                   a fake game as its own process (play --fake-game-files)
    hoi4-harness play --campaign --profile profiles/campaign-ger-world.json   an unattended campaign
    hoi4-harness campaign-report --run-dir runs/x   the markdown report of a campaign run
    hoi4-harness play --rival TAG --live    steer an AI country in your single-player game
    hoi4-harness play --handover            co-op: queue actions until the player hands over
    hoi4-harness handover                   hand the keyboard to a --handover session
    hoi4-harness verify-bridge              check the mod's tokens against an install and a log
    hoi4-harness save-inspect               what a save contains, for mapping it
    hoi4-harness check-scripts              pre-flight the input driver's scripts and calibration
    hoi4-harness chat --to POL "..."        talk to the LLM countries in a single-player game
    hoi4-harness setup                      a window: who plays which country, with which model
    hoi4-harness start --setup FILE         run what the setup window runs (no window)
    hoi4-harness chat-gui                   a window: talk to the LLM countries
    hoi4-harness map --state 141            where a state or province is, in map pixels
    hoi4-harness mod-directives --tags ...  regenerate the mod's per-target directives
    hoi4-harness mod-probe                  regenerate the focus/research probe from the install
    hoi4-harness tactics run hold_line --controller scripted --seeds 20   tactical simulator (#85)

Everything defaults to the mock adapter and the scripted model, so a fresh clone
does something useful with no API key and no game installed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from . import __version__, cadence, paths
from .actions import catalog
from .adapters import ADAPTERS, build_adapter
from .agent.llm import PROVIDERS, build_llm
from .agent.loop import AgentLoop
from .agent.memory import Memory
from .calibration import DEFAULT_FILENAME, DEFAULT_TARGETS
from .config import HarnessConfig
from .confirm import TerminalConfirmer
from .control import ALIASES, ControlError
from .env import HOI4Env
from .eval.runner import PlaysetMismatch
from .guidance import available as guidance_packs
from .keepawake import keep_awake_for
from .replay import ReplayError, list_turns, replay_turn
from .skills import SkillError


def _read_control_map(value: str):
    """``--control-map``: a JSON or TOML file, inline JSON, or ``domain=controller,...``."""
    path = Path(value)
    if path.suffix.lower() in (".json", ".toml") and path.exists():
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".toml":
            import tomllib

            data = tomllib.loads(text)
        else:
            data = json.loads(text)
        return data.get("control", data) if isinstance(data, dict) else data
    if value.lstrip().startswith("{"):
        return json.loads(value)
    return value


def _apply_setup_file(config: HarnessConfig, args: argparse.Namespace) -> None:
    """``--setup FILE``: what the setup window would run (setup_model.py). The flags come after."""
    from .setup_model import Setup

    try:
        setup = Setup.load(Path(args.setup))
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"--setup: {exc}") from exc
    problems = setup.problems()
    if problems:
        raise SystemExit("--setup: " + " ".join(problems))
    setup.apply_to(config)


#: ``--player-<flag> TAG=VALUE`` -> the key in HarnessConfig.rival_players.
_PLAYER_FLAGS = (("player_provider", "provider"), ("player_model", "model"),
                 ("player_base_url", "base_url"), ("player_key_env", "api_key_env"),
                 ("player_max_usd", "max_usd"), ("player_instruction", "instruction"),
                 ("player_price", "price"))


def _apply_player_flags(config: HarnessConfig, args: argparse.Namespace) -> None:
    """Per-country settings from the flags, and the run-wide model flags laid over the countries
    a ``--setup`` file listed (a flag beats the file)."""
    players = config.rival_players
    for attr, key in (("provider", "provider"), ("model", "model"), ("base_url", "base_url")):
        if getattr(args, attr, None):
            for row in players.values():
                row[key] = getattr(args, attr)
                if attr == "provider":
                    row.pop("model", None)
                    row["base_url"] = getattr(args, "base_url", None) or ""
                    row.pop("api_key_env", None)
    if getattr(args, "max_usd", None) is not None:
        for row in players.values():
            row.pop("max_usd", None)
    for attr, key in _PLAYER_FLAGS:
        for item in getattr(args, attr, None) or []:
            tag, sep, value = item.partition("=")
            tag = tag.strip().upper()
            if not sep or not tag:
                raise SystemExit(f"--{attr.replace('_', '-')} wants TAG=VALUE, not {item!r}")
            row = players.setdefault(tag, {})
            try:
                if key == "max_usd":
                    row[key] = float(value)
                elif key == "price":
                    low, _, high = value.partition(",")
                    row["usd_per_m_input"], row["usd_per_m_output"] = float(low), float(high or 0)
                else:
                    row[key] = value
            except ValueError:
                raise SystemExit(f"--{attr.replace('_', '-')} {item!r}: not a number") from None


def _config_from_args(args: argparse.Namespace) -> HarnessConfig:
    # Precedence, lowest first: defaults -> environment -> profile file -> flags.
    profile = getattr(args, "profile", None)
    config = HarnessConfig.from_file(profile) if profile else HarnessConfig.from_env()
    if getattr(args, "setup", None):
        _apply_setup_file(config, args)
    if getattr(args, "adapter", None):
        config.adapter = args.adapter
    if getattr(args, "provider", None):
        config.planner.switch_provider(args.provider)
    if getattr(args, "model", None):
        config.planner.model = args.model
    if getattr(args, "base_url", None):
        config.planner.base_url = args.base_url
    if getattr(args, "effort", None):
        config.planner.effort = args.effort
    if getattr(args, "turns", None):
        config.turns = args.turns
    if getattr(args, "days", None):
        config.days_per_turn = args.days
    if getattr(args, "max_usd", None) is not None:
        config.budget.max_usd = args.max_usd
    if getattr(args, "live", False):
        config.dry_run = False
    if getattr(args, "no_window_guard", False):
        config.enforce_window_focus = False
    if getattr(args, "advisor", False):
        config.advisor = True
    if getattr(args, "handover", False):
        config.handover = True
    if getattr(args, "handover_mode", None):
        config.handover_mode = args.handover_mode
        config.handover = True
    if getattr(args, "handover_hotkey", None):
        config.handover_hotkey = args.handover_hotkey
    if getattr(args, "handover_idle_seconds", None) is not None:
        config.handover_idle_seconds = args.handover_idle_seconds
    if getattr(args, "human", None):
        config.human_domains = [d.strip() for d in args.human.split(",") if d.strip()]
    if getattr(args, "mp_human", None):
        config.mp_human_tags = [t.strip().upper() for t in args.mp_human.split(",") if t.strip()]
        config.multiplayer = True
    if getattr(args, "mp_client", False):
        config.multiplayer = True
        config.clock_owner = "player"
    if getattr(args, "run_dir", None):
        config.run_dir = Path(args.run_dir)
    if getattr(args, "guidance", None):
        config.guidance = args.guidance
    if getattr(args, "skill_names", None):
        config.skills = [*config.skills, *args.skill_names]
    if getattr(args, "skills_dirs", None):
        config.skills_dirs = [Path(d) for d in args.skills_dirs]
    if getattr(args, "system_prompt", None):
        config.system_prompt_path = Path(args.system_prompt)
    if getattr(args, "objective", None):
        config.objective = args.objective
    if getattr(args, "actions", None):
        config.enabled_actions = [name.strip() for name in args.actions.split(",") if name.strip()]
    if getattr(args, "without", None):
        config.disabled_actions = [name.strip() for name in args.without.split(",") if name.strip()]
    if getattr(args, "allow_all", False):
        config.require_confirmation = False
    if getattr(args, "no_reflex", False):
        config.reflex_enabled = False
    if getattr(args, "player_clock", False):
        config.clock_owner = "player"
    if getattr(args, "no_llm", False):
        config.planner_enabled = False
    if getattr(args, "country", None):
        config.country = args.country.upper()
    if getattr(args, "start_date", None):
        config.start_date = args.start_date
    if getattr(args, "seed", None) is not None:
        config.seed = args.seed
    if getattr(args, "game_dir", None):
        config.game_dir = Path(args.game_dir)
    if getattr(args, "no_dlc", False):
        config.dlcs = []
    elif getattr(args, "dlcs", None):
        config.dlcs = list(args.dlcs)
    if getattr(args, "user_dir", None):
        config.user_dir = Path(args.user_dir)
    if getattr(args, "log_path_arg", None):
        config.log_path = Path(args.log_path_arg)
    if getattr(args, "mods", None):
        config.mod_dirs = [Path(m) for m in args.mods]
    if getattr(args, "mod_context", False):
        config.mod_context = True
    if getattr(args, "fetch_workshop", False):
        config.mod_context_fetch = True
    if getattr(args, "operational_control", None):
        config.operational_control = args.operational_control
    if getattr(args, "script", None):
        config.planner.extra["script"] = args.script
    if getattr(args, "headless", False):
        config.adapter = "headless"
    if getattr(args, "headless_clock", None):
        config.headless_clock = args.headless_clock
    if getattr(args, "headless_reader", None):
        config.headless_reader = args.headless_reader
    if getattr(args, "save_days", None) is not None:
        config.headless_save_days = args.save_days
    if getattr(args, "telemetry_hours", None) is not None:
        config.telemetry_hours = args.telemetry_hours
    if config.telemetry_hours is not None:
        try:
            config.telemetry_hours = cadence.validate(config.telemetry_hours)
        except ValueError as exc:
            raise ControlError(str(exc)) from None
    if getattr(args, "fake_game", False) or getattr(args, "fake_game_dir", None):
        config.fake_game = True
    if getattr(args, "fake_game_dir", None):
        config.fake_game_dir = Path(args.fake_game_dir)
    if getattr(args, "fake_game_files", None):
        config.fake_game_files = Path(args.fake_game_files)
    if getattr(args, "campaign_mode", False):
        config.campaign = True
    if getattr(args, "until", None):
        config.campaign_until = args.until
    if getattr(args, "choose_path", False):
        config.campaign_choose_path = True
    if getattr(args, "briefing_flag", False):
        config.campaign_briefing = True
    if getattr(args, "rebrief", None):
        config.campaign_rebrief = [x.strip() for x in args.rebrief.split(",") if x.strip()]
        config.campaign_briefing = True
    if getattr(args, "briefing_web", False):
        config.briefing_web_search = True
    if getattr(args, "lobby_rule", False):
        config.campaign_lobby_rule = True
    if getattr(args, "lobby_scan", False):
        config.campaign_lobby_rule = config.campaign_lobby_scan = True
    if getattr(args, "lobby_calibrate", False):
        config.campaign_lobby_rule = config.campaign_lobby_scan = True
        config.campaign_lobby_calibrate = True
    spec = getattr(args, "control", None)
    if spec:
        if "=" in spec or spec in ALIASES:
            config.control = spec            # a control map: --control focus=llm,research=script
        elif spec in ("game_ai", "llm"):
            config.campaign_control = spec
        else:
            raise SystemExit(f"play: --control wants game_ai, llm, economy, sovereign or domain=controller "
                             f"pairs (focus=llm,research=script), not {spec!r}")
    if getattr(args, "control_map", None):
        config.control = _read_control_map(args.control_map)
    if getattr(args, "steer", None):
        config.steer = args.steer
    if getattr(args, "army_controller", None):
        config.army_controller = args.army_controller
    if getattr(args, "allow_contested", False):
        config.allow_contested = True
    if getattr(args, "campaign_input", None):
        config.campaign_input = args.campaign_input
    if getattr(args, "no_human_ai", False):
        config.campaign_human_ai = False
    if getattr(args, "no_verify_ai", False):
        config.campaign_verify_ai = False
    config.control_map()        # a map that cannot be honoured stops here, with the reason
    if config.campaign:
        from .campaign.setup import apply_defaults

        apply_defaults(config)
    if getattr(args, "rival", None):
        config.rival = args.rival.upper()
    if getattr(args, "rival_act_when", None):
        config.rival_act_when = args.rival_act_when
    if getattr(args, "transport", None):
        config.console_transport = args.transport
    if getattr(args, "inbox_dir", None):
        config.inbox_dir = Path(args.inbox_dir)
    for item in getattr(args, "rival_share", None) or []:
        tag, _, weight = item.partition("=")
        try:
            config.rival_shares[tag.strip().upper()] = float(weight)
        except ValueError:
            raise SystemExit(f"play: --rival-share wants TAG=WEIGHT (FRA=2), not {item!r}") from None
    if getattr(args, "rival_window_cap", None) is not None:
        config.rival_window_cap = args.rival_window_cap
    if getattr(args, "rival_paused_seconds", None) is not None:
        config.rival_paused_seconds = args.rival_paused_seconds
    if getattr(args, "rival_idle_seconds", None) is not None:
        config.rival_idle_seconds = args.rival_idle_seconds
    if getattr(args, "rival_start_wait", None) is not None:
        config.rival_start_wait_minutes = args.rival_start_wait
    if getattr(args, "rival_exit_wait", None) is not None:
        config.rival_exit_wait_seconds = args.rival_exit_wait
    if getattr(args, "fake_human_script", None):
        config.fake_human_script = Path(args.fake_human_script)
    if getattr(args, "instruction", None):
        config.instruction = args.instruction
    _apply_player_flags(config, args)
    if config.rival:
        from .rival.setup import RivalConfigError
        from .rival.setup import apply_defaults as apply_rival

        try:
            apply_rival(config)
        except RivalConfigError as exc:
            raise SystemExit(f"play: {exc}") from exc
    if getattr(args, "mailbox", None) is not None:
        config.planner.switch_provider("mailbox")
        if args.mailbox:
            config.planner.extra["mailbox_dir"] = args.mailbox
    if config.planner.provider == "mailbox" and not config.planner.extra.get("mailbox_dir"):
        config.planner.extra["mailbox_dir"] = str(Path(config.run_dir) / "mailbox")
    if getattr(args, "mailbox_timeout", None):
        config.planner.extra["mailbox_timeout_minutes"] = args.mailbox_timeout
    if config.fake_game or config.fake_game_files:
        # A fake game has no window to focus and nothing a keystroke can hurt, so
        # it is the one target where --live is the point, not a risk.
        config.adapter, config.dry_run = "headless", False
    return config


def _env_from_config(config: HarnessConfig) -> HOI4Env:
    """The environment, with the playset's identifier index when one is configured."""
    return HOI4Env(build_adapter(config), config, index=config.build_index())


def _renderer_line(config: HarnessConfig) -> str:
    """The renderer pdx_settings.txt names. OpenGL leaks 20-32 GB an hour in HOI4 1.20 (#157)."""
    from .campaign.preflight import renderer_check

    try:
        user = config.user_dir if config.user_dir is not None else paths.find_game_dir()
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        return f"unknown ({type(exc).__name__}: {exc})"
    if user is None:
        return "unknown -- no Hearts of Iron IV user folder found (pass --user-dir)"
    check = renderer_check(user)
    if check.status == "FAIL":
        return f"opengl -- LEAKS 20-32 GB an hour (issue #157). {check.detail.split('Fix: ', 1)[-1]}"
    return check.detail


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    print(f"hoi4-agent-harness {__version__}")
    print(f"python           {sys.version.split()[0]}")
    print(f"adapter          {config.adapter}")
    planner = f"{config.planner.provider}:{config.planner.model}"
    print(f"planner          {planner if config.planner_enabled else 'disabled (reflex only)'}")
    print(f"control          {config.control_map().describe_short()}")
    print(f"dry run          {config.dry_run}")
    print(f"clock owner      {config.clock_owner}")
    print(f"guidance         {config.system_prompt_path or config.guidance}")
    print(f"confirm gate     {config.require_confirmation}")
    budget = config.budget
    if config.planner_enabled and config.planner.provider != "scripted" and not (
        budget.usd_per_m_input or budget.usd_per_m_output
    ):
        print("pricing          UNSET -- spend will read $0.00; set HOI4_USD_PER_M_INPUT/OUTPUT")
    elif budget.usd_per_m_input or budget.usd_per_m_output:
        cached = budget.usd_per_m_cached_input
        print(
            f"pricing          ${budget.usd_per_m_input}/M in, ${budget.usd_per_m_output}/M out, "
            + (f"${cached}/M cache read" if cached is not None else "cache reads at the input rate")
        )

    print(f"renderer         {_renderer_line(config)}")

    for module, extra in (
        ("anthropic", "anthropic"),
        ("openai", "openai"),
        ("google.genai", "google"),
        ("pyautogui", "input"),
        ("mss", "input"),
    ):
        try:
            __import__(module)
            print(f"  [x] {module}")
        except ImportError:
            print(f"  [ ] {module}  (pip install 'hoi4-agent-harness[{extra}]')")

    try:
        from .adapters.window import check_focus

        focus = check_focus(config.window_title)
        if focus.supported:
            print(f"game focused     {focus.focused}" + (f" ({focus.title})" if focus.title else ""))
        else:
            print(f"game focused     unknown -- {focus.reason}")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        print(f"game focused     check failed: {exc}")

    try:
        adapter = build_adapter(config)
        info = adapter.info()
        print(f"adapter ok       {info.name}: read={info.readable} write={info.writable}")
        print(f"                 {info.notes}")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        print(f"adapter FAILED   {type(exc).__name__}: {exc}")
        return 1
    return 0


def cmd_actions(args: argparse.Namespace) -> int:
    if args.json:
        print(json.dumps([spec.__dict__ for spec in catalog.ACTIONS], indent=2, default=str))
        return 0
    for spec in catalog.ACTIONS:
        flag = " [confirm]" if spec.requires_confirmation else ""
        required = ", ".join(spec.parameters.get("required", []))
        print(f"{spec.name}{flag}  ({spec.category})")
        print(f"    {spec.description}")
        print(f"    required: {required or '-'}")
    return 0


def cmd_observe(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    try:
        env = _env_from_config(config)
        observation = env.reset()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"observe: {exc}", file=sys.stderr)
        return 1
    print(observation.brief)
    return 0


def cmd_campaign(args: argparse.Namespace, config: HarnessConfig) -> int:
    """``play --campaign``: the unattended run (docs/campaign.md)."""
    from .adapters.base import GameLost
    from .campaign.runner import CampaignStartError, run_campaign

    def echo(line: str) -> None:
        print(line, file=sys.stderr)

    try:
        _, summary = run_campaign(config, resume=bool(getattr(args, "resume", False)),
                                  quiet=args.quiet, echo=echo,
                                  profile=getattr(args, "profile", None))
    except CampaignStartError as exc:
        print(f"campaign: {exc}", file=sys.stderr)
        return 2
    except GameLost as exc:
        print(f"campaign: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"campaign: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["reached_end_date"] else 1


def cmd_rival(args: argparse.Namespace, config: HarnessConfig) -> int:
    """``play --rival TAG``: steer an AI country in the person's single-player game."""
    from .adapters.base import GameLost
    from .rival.runner import RivalStartError, run_rival

    def echo(line: str) -> None:
        print(line, file=sys.stderr)

    from .players import RunMark

    try:
        with RunMark(_run_tags(config), _player_describe(config), config.run_dir):
            _, summary = run_rival(config, resume=bool(getattr(args, "resume", False)),
                                   quiet=args.quiet, echo=echo, until=getattr(args, "until", None))
    except RivalStartError as exc:
        print(f"rival: {exc}", file=sys.stderr)
        return 2
    except GameLost as exc:
        print(f"rival: {exc}", file=sys.stderr)
        return 1
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"rival: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["reached_end_date"] or not summary["stopped_reason"] else 1


def cmd_campaign_check(args: argparse.Namespace) -> int:
    """``campaign-check``: the unattended-night preflight, nothing touched (docs/campaign.md)."""
    from .campaign.preflight import FAIL, WARN, run_checks

    config = _config_from_args(args)
    checks = run_checks(config, user_dir=Path(args.user_dir) if args.user_dir else None,
                        max_usd=args.max_usd)
    for check in checks:
        print(check.line())
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    print(f"{len(checks)} checks: {fails} FAIL, {warns} WARN")
    return 1 if fails else 0


def cmd_campaign_report(args: argparse.Namespace) -> int:
    from .campaign.report import write_report

    if args.budget:
        from .campaign.budget import load_budget

        try:
            text = load_budget(Path(args.run_dir)).render()
        except FileNotFoundError as exc:
            print(f"campaign-report: {exc}", file=sys.stderr)
            return 1
        print(text)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        return 0

    try:
        text, path = write_report(Path(args.run_dir), out=Path(args.out) if args.out else None)
    except FileNotFoundError as exc:
        print(f"campaign-report: {exc}", file=sys.stderr)
        return 1
    if args.print_report:
        print(text)
    else:
        print(f"wrote {path}")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    """``chat``: talk to the LLM countries in a single-player game (#133)."""
    from .chat import ChatLog

    log = ChatLog(Path(args.chat) if args.chat else None)
    text = " ".join(args.text).strip()
    if text:
        try:
            sent = log.post("person", args.to, text)
        except ValueError as exc:
            print(f"chat: {exc}", file=sys.stderr)
            return 1
        print(f"#{sent.id} to {sent.to}: {sent.text}")
        if not args.follow:
            return 0
    shown = 0
    for message in log.visible_to("person")[-args.last:]:
        print(f"#{message.id} {message.line()}")
        shown = message.id
    if not args.follow:
        if shown == 0:
            print(f"(no messages yet in {log.path})")
        return 0
    print(f"(following {log.path}; Ctrl+C to stop)")
    try:
        while True:
            for message in log.visible_to("person"):
                if message.id > shown:
                    print(f"#{message.id} {message.line()}", flush=True)
                    shown = message.id
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """``setup``: the window for who plays which country, with which model (docs/setup.md)."""
    from .setup_gui import run

    return run()


def cmd_chat_gui(args: argparse.Namespace) -> int:
    """``chat-gui``: the chat window (docs/chat.md)."""
    from .chat_gui import run

    return run(Path(args.chat) if args.chat else None)


def cmd_guide(args: argparse.Namespace) -> int:
    """``guide``: leave advice for a running campaign, or list / clear what stands."""
    from .campaign import inbox

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"guide: {run_dir} is not a run directory", file=sys.stderr)
        return 1
    text = " ".join(args.text).strip()
    did = False
    if args.expire is not None:
        gone = inbox.expire(run_dir, args.expire)
        print(f"expired {len(gone)} item(s) older than {args.expire:g} day(s)")
        did = True
    if args.remove:
        gone = inbox.clear(run_dir, args.remove)
        print(f"removed {len(gone)} item(s): {', '.join(f'#{i}' for i in gone) or 'none matched'}")
        did = True
    if args.clear:
        gone = inbox.clear(run_dir)
        print(f"cleared {len(gone)} item(s)")
        did = True
    if text:
        try:
            item = inbox.add(run_dir, text)
        except ValueError as exc:
            print(f"guide: {exc}", file=sys.stderr)
            return 1
        print(f"added #{item.id}: the run reads it at its next turn and wakes the model for it")
        did = True
    if args.list or not did:
        items = inbox.active(run_dir)
        if not items:
            print("no standing guidance")
        for item in items:
            print(f"#{item.id} [{item.ts}] {item.text}")
    return 0


def cmd_domains(args: argparse.Namespace) -> int:
    """``domains``: move a co-op domain between the person and the model, mid-game (#31)."""
    from .control import DOMAINS
    from .coop import DomainBoard

    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"domains: {run_dir} is not a run directory", file=sys.stderr)
        return 1
    board = DomainBoard(run_dir)
    moves = [(d, "human") for d in args.take] + [(d, "llm") for d in args.give]
    for domain, to in moves:
        names = list(DOMAINS) if domain == "all" else [domain]
        for name in names:
            try:
                row = board.post(name, to)
            except ValueError as exc:
                print(f"domains: {exc}", file=sys.stderr)
                return 1
            who = "you hold" if to == "human" else "the model holds"
            print(f"#{row['id']}: {who} {name} from the run's next turn")
    if not moves:
        rows = board.rows()
        if not rows:
            print("no domain has moved in this run")
        for row in rows:
            print(f"#{row['id']} {row['domain']} -> {'you' if row['to'] == 'human' else 'the model'}"
                  f" ({row.get('source', 'command')})")
    return 0


def cmd_play(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    if config.campaign:
        return cmd_campaign(args, config)
    if config.rival:
        with keep_awake_for(config, lambda line: print(line, file=sys.stderr)):
            return cmd_rival(args, config)
    if config.handover and config.advisor:
        print("play: --advisor never acts, so there is nothing to hand over; pick one",
              file=sys.stderr)
        return 2
    from .coop import validate as validate_coop

    if problems := validate_coop(config):
        print("play: " + "; ".join(problems), file=sys.stderr)
        return 2
    try:
        env = _env_from_config(config)
        first = env.reset()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"play: {exc}", file=sys.stderr)
        return 1
    if config.multiplayer:
        from .adapters.mp_chat import attach as attach_mp_chat

        attach_mp_chat(env, config)         # send_message to a human goes to the game's own chat
    if config.telemetry_hours is not None and config.adapter == "headless":
        tag = (config.country or first.state.country or "").strip()
        tag = "" if tag == "___" else tag
        cadence.apply(cadence.console(env.adapter), tag, config.telemetry_hours,
                      lambda line: print(line, file=sys.stderr))
    run_dir = config.run_dir
    resume = bool(getattr(args, "resume", False))

    def echo(line: str) -> None:
        print(line, file=sys.stderr)      # stdout stays the final JSON, for piping

    loop = AgentLoop(
        env=env,
        planner=build_llm(config.planner) if config.planner_active else None,
        config=config,
        # A fresh run starts with no memory, as it starts with an empty
        # transcript; only --resume carries the old one forward.
        memory=Memory.load(run_dir / "memory.json") if resume else Memory(),
        transcript_path=run_dir / "transcript.jsonl",
        resume=resume,
        progress=None if args.quiet else echo,
    )
    if resume and loop.report.resumed_from:
        print(f"resuming {run_dir}: {loop.report.turns} turns already played", file=sys.stderr)
    if env.handover is not None:
        # What the player was shown as queued is what runs when they hand over,
        # across a restart; a fresh run starts empty.
        if resume and (restored := env.handover.restore()):
            print(f"handover: {restored} queued action(s) restored", file=sys.stderr)
        elif not resume:
            env.handover.clear()
    from .coop import CoopGuidance, wants_guidance

    if wants_guidance(config) and loop.supervisor is None:
        if env.chat is None and (config.chat or not config.multiplayer):
            # The person talks to the model, and gets "here is what I did", in the file chat
            # (`hoi4-harness chat`); a multiplayer game talks through the game's own chat.
            from .chat import ChatChannel, ChatLog

            tag = (config.country or first.state.country or "LLM").upper()
            env.attach_chat(ChatChannel(ChatLog(config.chat_path), tag, run_dir / "chat_seen.json"))
        loop.supervisor = CoopGuidance(run_dir, loop)
        if config.multiplayer and env.mp_chat is not None:
            from .adapters.mp_chat import find_driver
            from .adapters.mp_chat_reader import build_reader

            fake = getattr(env.adapter, "fake_game", None)
            reader, note = build_reader(config, find_driver(env.adapter), env.mp_chat,
                                        backend=getattr(fake, "chat_ocr", None))
            if reader is not None:
                loop.supervisor.incoming = reader
            echo(f"multiplayer chat: {note if reader is not None else 'not read (' + note + ')'}")
    listener = None
    if env.handover is not None:
        from .coop import build_hotkey_listener, wire_handover

        if wire_handover(env, config) is not None and config.handover_mode == "approve" \
                and env.confirm_hook is None:
            # Approve mode IS the confirmation: ask in the terminal even without --confirm.
            env.confirm_hook = TerminalConfirmer(loop.transcript)
        try:
            listener = build_hotkey_listener(config)
            if listener is not None:
                listener.start(env.handover.grant)
                echo(f"handover: hotkey {config.handover_hotkey} grants a window")
        except RuntimeError as exc:
            print(f"play: {exc}", file=sys.stderr)
            return 2
    if getattr(args, "confirm", False):
        # The hook is the gate's only UI; without this flag a live gated action
        # runs unanswered, which is what keeps unattended runs working.
        env.confirm_hook = TerminalConfirmer(loop.transcript)
        if not config.require_confirmation:
            echo("confirm: the gate is off (--allow-all), so nothing will be asked")
        elif config.dry_run:
            echo("confirm: dry run refuses irreversible actions before the prompt; use --live")
    # Only a headless run types into the game; keep the PC awake for it (no-op when not live).
    awake = (keep_awake_for(config, lambda line: print(line, file=sys.stderr))
             if config.adapter == "headless" else nullcontext())
    try:
        with awake:
            report = loop.run(config.turns)
    finally:
        if listener is not None:
            listener.stop()
    loop.memory.save(run_dir / "memory.json")
    print(json.dumps(report.to_dict(), indent=2, default=str))
    return 0


def cmd_headless_check(args: argparse.Namespace) -> int:
    from .headless_check import run_checks

    args.headless = True
    config = _config_from_args(args)
    if config.dry_run:
        print("headless-check: it sends keys and starts the clock, so it needs --live "
              "(or a fake game). Load the game, pause it, put it in front, and re-run.",
              file=sys.stderr)
        return 2
    try:
        adapter = build_adapter(config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"headless-check: {exc}", file=sys.stderr)
        return 1
    results = run_checks(adapter, days=args.days or 7)
    failed = [r.name for r in results if not r.ok]
    print(f"{len(results) - len(failed)} of {len(results)} passed"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


def cmd_fakegame(args: argparse.Namespace) -> int:
    import threading

    from .fakegame import FakeGame, demo_index

    base = Path(args.dir)
    if args.game_dir:
        from .identifiers import IdentifierIndex, Playset

        index = IdentifierIndex.build(Playset.from_paths(Path(args.game_dir)))
    else:
        index = demo_index(base / "playset")
    game = FakeGame(base / "user", country=args.country.upper(), seed=args.seed or 1936,
                    start=args.start_date or "1936-01-01", index=index)
    print(f"fake game running. log: {game.log_path}", file=sys.stderr)
    print(f"play against it with: hoi4-harness play --headless --fake-game-files {base / 'user'}",
          file=sys.stderr)
    stop = threading.Event()
    try:
        game.serve(stop, time_scale=args.time_scale)
    except KeyboardInterrupt:
        stop.set()
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    try:
        if args.list_turns:
            print(list_turns(Path(args.transcript)))
            return 0
        if args.turn is None:
            print("replay: pass --turn N to choose a turn (see --list)", file=sys.stderr)
            return 2
        config = _config_from_args(args)
        print(replay_turn(Path(args.transcript), args.turn, config).render())
        return 0
    except (ReplayError, ValueError) as exc:
        # ValueError: a misconfigured provider reaches build_llm here, and its
        # message already says what is wrong and what the options are.
        print(f"replay: {exc}", file=sys.stderr)
        return 1


def _refuse_scenario_overrides(args: argparse.Namespace, command: str) -> bool:
    """A scenario fixes its own game: the mock, its country, its start and its
    seed. Flags that would change them are refused rather than dropped, so a
    score is never mistaken for one measured on something else."""
    given = [
        flag for flag, value in (
            ("--adapter", getattr(args, "adapter", None) not in (None, "mock")),
            ("--country", getattr(args, "country", None)),
            ("--start-date", getattr(args, "start_date", None)),
            ("--seed", getattr(args, "seed", None) is not None),
        ) if value
    ]
    if given:
        print(f"{command}: a scenario fixes its adapter, country, start and seed; "
              f"drop {', '.join(given)} (vary seeds with --seeds N)", file=sys.stderr)
    return bool(given)


def cmd_eval(args: argparse.Namespace) -> int:
    from .eval import SCENARIOS, ScoreCard, run_scenario, run_seeds, write_baselines

    if _refuse_scenario_overrides(args, "eval"):
        return 2
    config = _config_from_args(args)
    if getattr(args, "write_baseline", False) and config.planner_enabled:
        # A baseline recorded from a model run would corrupt the thing every
        # score is compared against, so refuse rather than quietly mis-record.
        print(
            "--write-baseline records reflex-only scores; pass --no-llm to measure them.",
            file=sys.stderr,
        )
        return 2

    seeds = max(1, getattr(args, "seeds", 1) or 1)
    if getattr(args, "write_baseline", False) and seeds > 1:
        print("--write-baseline records one reflex-only run per scenario; drop --seeds.",
              file=sys.stderr)
        return 2
    if args.scenario and args.scenario not in SCENARIOS:
        print(f"eval: unknown scenario {args.scenario!r}. Known: {', '.join(SCENARIOS)}",
              file=sys.stderr)
        return 2

    keys = [args.scenario] if args.scenario else list(SCENARIOS)
    failed = 0
    cards: dict[str, ScoreCard] = {}
    for key in keys:
        if seeds > 1:
            aggregate = run_seeds(SCENARIOS[key], config, seeds, transcript_dir=config.run_dir)
            print(aggregate.render())
            print()
            # --strict means every run passes, not the median one.
            failed += aggregate.low < 1.0
            continue
        card = run_scenario(key, config, transcript_dir=config.run_dir)
        print(card.render())
        print()
        failed += card.score < 1.0
        cards[key] = card
    if getattr(args, "write_baseline", False):
        write_baselines(cards)
    return 0 if not args.strict else min(failed, 1)


def cmd_compare(args: argparse.Namespace) -> int:
    from .eval import compare

    if _refuse_scenario_overrides(args, "compare"):
        return 2
    config = _config_from_args(args)
    specs = [s.strip() for s in args.models.split(",") if s.strip()] if args.models else None
    try:
        print(compare(args.scenario, specs, config, seeds=max(1, args.seeds or 1)).render())
    except KeyError as exc:
        # The scenario is resolved before any model runs; the message names the
        # known ones.
        print(f"compare: {exc.args[0]}", file=sys.stderr)
        return 2
    return 0


def cmd_measure(args: argparse.Namespace) -> int:
    from .eval import CAMPAIGNS, measure, run_scenario

    if bool(args.transcript) == bool(args.campaign):
        print("measure: give a transcript, or --campaign NAME to play one first", file=sys.stderr)
        return 2
    if args.campaign:
        if args.campaign not in CAMPAIGNS:
            print(f"measure: unknown campaign {args.campaign!r}. Known: {', '.join(CAMPAIGNS)}",
                  file=sys.stderr)
            return 2
        if _refuse_scenario_overrides(args, "measure"):
            return 2
        config = _config_from_args(args)
        run_scenario(CAMPAIGNS[args.campaign], config, transcript_dir=config.run_dir)
        path = config.run_dir / f"{args.campaign}.jsonl"
    else:
        path = Path(args.transcript)
        if not path.exists():
            print(f"measure: no such transcript {path}", file=sys.stderr)
            return 1
    print(measure(path).render())
    return 0


def cmd_memory_probe(args: argparse.Namespace) -> int:
    from . import memory_probe as mp

    if args.compare:
        print(mp.compare(Path(args.compare)))
        return 0
    if not args.save or not args.variant:
        print("memory-probe: give --save and --variant (or --compare DIR)", file=sys.stderr)
        return 2
    print(f"memory-probe {args.variant}: {mp.VARIANTS[args.variant].question}")
    print("Set the game up like this, then the probe samples (it never touches the game):")
    for line in mp.manual_steps(args.variant, args.save):
        print("  " + line)
    if args.steps_only:
        return 0
    from .campaign.watch import process_private_bytes

    log = args.log or os.environ.get("HOI4_LOG_PATH")
    clock = None
    if log:
        from .rival.safety import LogClock

        clock = LogClock(Path(log))
    name = args.process
    result = mp.run_probe(
        save=args.save, minutes=args.minutes, variant=args.variant, out_dir=Path(args.out),
        read_bytes=lambda: process_private_bytes(name), interval=args.interval, label=args.label or "",
        read_date=(clock.last_date if clock else (lambda: None)))
    print(result.render())
    print(f"csv: {result.csv_path}")
    print(f"summary: {result.summary_path}")
    return 0 if result.summary()["samples"] else 1


def cmd_reconcile(args: argparse.Namespace) -> int:
    from .eval import reconcile as rc

    try:
        rates = dict(rc.parse_rate(r) for r in args.rate or [])
        for path in (args.transcript, args.export):
            if not Path(path).is_file():
                print(f"reconcile: no such file {path}", file=sys.stderr)
                return 2
        harness = rc.read_transcript_keyed(args.transcript)
        if not harness:
            print(f"reconcile: {args.transcript} has no llm records (a scripted or empty run?)",
                  file=sys.stderr)
            return 2
        export = rc.read_export_keyed(args.export, args.format)
        if args.by == "day":
            report = rc.reconcile_daily(harness, export, rates, args.tolerance)
            print(rc.render_daily(report))
            return 0 if report.ok else 1
        report = rc.reconcile(rc.fold(harness), rc.fold(export), rates, args.tolerance)
    except rc.ReconcileError as exc:
        print(f"reconcile: {exc}", file=sys.stderr)
        return 2
    print(rc.render(report))
    return 0 if report.ok else 1


def provider_check_env(args: argparse.Namespace, base: dict[str, str]) -> dict[str, str]:
    """The environment a live conformance run gets: the opt-in, the cap, the chosen providers.
    Keys are inherited from ``base`` and never read or printed here."""
    env = dict(base)
    env["HOI4_LIVE_PROVIDERS"] = "1"
    if args.max_calls is not None:
        env["HOI4_LIVE_MAX_CALLS"] = str(args.max_calls)
    if args.provider:
        env["HOI4_LIVE_ONLY"] = ",".join(args.provider)
    return env


def cmd_provider_check(args: argparse.Namespace) -> int:
    """Run the live provider conformance suite (#22), opt-in, capped, keys never printed."""
    import subprocess

    from .eval import live_conformance as lc

    suite = Path(__file__).resolve().parents[2] / "tests" / "live"
    if not suite.is_dir():
        print("provider-check: tests/live is not here (run it from a source checkout)",
              file=sys.stderr)
        return 2
    env = provider_check_env(args, dict(os.environ))
    plan = lc.render_plan(env, lc.runnable(env))
    print(plan)
    if "nothing will run" in plan:
        return 2
    if args.plan:
        return 0
    cmd = [sys.executable, "-m", "pytest", str(suite), "-v", "-rs", "-p", "no:cacheprovider"]
    return subprocess.run(cmd, env=env, cwd=str(suite.parent.parent)).returncode


def cmd_mailbox(args: argparse.Namespace) -> int:
    """The answering side of ``--mailbox``: wait for a request, show it, answer it."""
    import json as _json

    from .agent.llm import mailbox as mb

    directory = Path(args.dir)
    if args.verb == "wait":
        state, req = mb.wait_for_request(directory, args.timeout)
        if state == "closed":
            print("CLOSED: the run has ended; no more requests")
            return 3
        if state == "timeout":
            print(f"TIMEOUT: no request in {args.timeout:.0f} s; wait again")
            return 2
        print((directory / mb.REQUEST_MD).read_text(encoding="utf-8"))
        return 0
    if args.verb == "show":
        req = mb.pending(directory)
        print((directory / mb.REQUEST_MD).read_text(encoding="utf-8") if req else "no request waiting")
        return 0 if req else 2
    if args.verb == "system":
        print((directory / mb.SYSTEM_MD).read_text(encoding="utf-8"))
        return 0
    raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.json
    if not raw:
        print("answer: give --file PATH or --json TEXT", file=sys.stderr)
        return 2
    try:
        answer = _json.loads(raw)
    except ValueError as exc:
        print(f"REFUSED: not JSON: {exc}")
        return 1
    seq, problems = mb.write_answer(directory, answer, source=args.file or None,
                                    force=getattr(args, "force", False))
    if problems:
        print("REFUSED: " + "; ".join(problems))
        return 1
    print(f"answered request {seq}")
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    """``inbox status|probe|clear``: what the inbox holds, and whether the game reloads it."""
    from . import native, paths
    from .adapters.logtail import find_log
    from .inbox import Inbox, default_mod_dir
    from .rival.setup import human_tags_in_log

    mod_dir = Path(args.inbox_dir) if args.inbox_dir else default_mod_dir(paths.find_game_dir())
    log = Path(args.log) if args.log else find_log()
    if mod_dir is None:
        print("inbox: no installed mod found; pass --inbox-dir", file=sys.stderr)
        return 2
    box = Inbox(mod_dir, log_path=log)
    problems = native.installed_mod_problems(mod_dir)
    if args.verb == "status":
        print(f"mod:  {mod_dir}" + ("" if not problems else "  NOT READY: " + "; ".join(problems)))
        print(f"log:  {log}")
        if native.game_running():
            print(f"game: {native.check_debug_mode(native.game_command_line()).message}")
        else:
            print("game: not running")
        print(f"pending: {len(box.pending)} batch(es)")
        for b in box.pending:
            print(f"  {b.id} {b.tag}: " + " | ".join(b.script))
        print(f"acknowledged in this log: {len(box.acked)}")
        return 0
    if args.verb == "clear":
        n = len(box.pending)
        box.pending = []
        box._write()
        print(f"cleared {n} batch(es); the inbox is empty")
        return 0
    # probe: a batch that only asks for a state line. The game must be running, unpaused.
    if problems:
        print("inbox probe: the installed mod has no inbox: " + "; ".join(problems), file=sys.stderr)
        return 2
    humans = human_tags_in_log(log)
    if not humans:
        print("inbox probe: game.log shows no human country yet (load a game first)", file=sys.stderr)
        return 2
    tag = humans[-1]
    started = time.monotonic()
    (batch,) = box.post([f"effect {tag} llm_bridge_emit_state"])
    print(f"posted probe batch {batch.id} for {tag}; unpause the game and wait "
          f"(up to {args.timeout:.0f} s)...", flush=True)
    if box.wait([batch.id], args.timeout, poll_seconds=0.5):
        print(f"ACKNOWLEDGED after {time.monotonic() - started:.1f} s via "
              f"{box.acked[batch.id]}: the game reloads the inbox")
        return 0
    print(f"NO ACKNOWLEDGEMENT in {args.timeout:.0f} s. Is the game unpaused, started with "
          "-debug_mode, and running the installed mod? The batch stays posted; "
          "`hoi4-harness inbox clear` removes it.")
    return 1


def _run_tags(config: HarnessConfig) -> list[str]:
    return list(config.rivals) if len(config.rivals) > 1 else [config.rival]


def _player_describe(config: HarnessConfig):
    from .rival.setup import planner_config_for

    def describe(tag: str) -> tuple[str, str]:
        planner = planner_config_for(config, tag)
        return planner.provider, planner.model
    return describe


def _adopt_setup(args: argparse.Namespace) -> None:
    """``native --setup FILE``: the file's run options where a flag did not say otherwise, and its
    saved keys into this process's environment (never argv, never printed)."""
    from .setup_model import Setup, inject_environment

    try:
        setup = Setup.load(Path(args.setup))
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"--setup: {exc}") from exc
    args.launch = bool(getattr(args, "launch", False) or setup.launch_game)
    args.keep_running = bool(getattr(args, "keep_running", False) or setup.keep_running)
    if not getattr(args, "until", None) and setup.until:
        args.until = setup.until
    if not getattr(args, "game_dir", None) and setup.game_dir:
        args.game_dir = setup.game_dir
    if not getattr(args, "my_country", None) and setup.my_country:
        args.my_country = setup.my_country
    inject_environment(setup)


def cmd_start(args: argparse.Namespace) -> int:
    """``start --setup FILE``: exactly what the setup window's Start runs."""
    if not getattr(args, "setup", None):
        print("start: --setup FILE is required (hoi4-harness setup writes "
              "~/.hoi4-harness/setup.json)", file=sys.stderr)
        return 2
    return cmd_native(args)


def cmd_native(args: argparse.Namespace) -> int:
    """``native --rival TAG``: rival mode through the inbox, following the game's lifetime."""
    from . import native, paths
    from .inbox import default_mod_dir
    from .players import RunMark

    def echo(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    # The console (keys) is the default: the first live run (2026-11-22) found that 1.20 does not
    # reload the inbox files from the mod folder, so `--transport inbox` is opt-in. The keys act in
    # the person's pauses (`paused`), or whenever the keyboard is idle (`idle`, unattended runs).
    args.live = True
    args.transport = getattr(args, "transport", None) or "keys"
    if not getattr(args, "rival_act_when", None):
        args.rival_act_when = "inbox" if args.transport == "inbox" else "paused"
    for name in ("launch", "keep_running", "no_debug_check"):
        if not hasattr(args, name):
            setattr(args, name, False)
    if getattr(args, "setup", None):
        _adopt_setup(args)
    elif not getattr(args, "rival", None):
        print("native: --rival TAG[,TAG...] (or --setup FILE) says which countries the LLM plays",
              file=sys.stderr)
        return 2
    base = _config_from_args(args)
    if not getattr(args, "provider", None) and base.planner.provider == "scripted":
        # No provider chosen anywhere (flag, profile or setup file): the Claude Code CLI, as before.
        # A flag here would also have overwritten every country's own provider.
        base.planner.switch_provider("claude-cli")
    tags = _run_tags(base)
    mine = (getattr(args, "my_country", None) or "").strip().upper()
    if mine and mine in tags:
        echo(f"native: {mine} is the country you play; the LLM cannot play it too")
        return 2
    with RunMark(tags, _player_describe(base), base.run_dir, status="waiting") as mark:
        code = _native_loop(args, base, echo, mark, native, paths, default_mod_dir)
        mark.status = "finished" if code == 0 else "stopped"
        return code


def _native_loop(args, base, echo, mark, native, paths, default_mod_dir) -> int:
    mod_dir = base.inbox_dir or default_mod_dir(paths.find_game_dir())
    problems = native.installed_mod_problems(mod_dir)
    if problems:
        echo("native: the installed mod has no inbox yet: " + "; ".join(problems))
        echo("native: with the game CLOSED, install it: python scripts/live_setup.py prepare --apply")
        return 2
    from .playset_files import stale

    why = stale(mod_dir, paths.find_game_dir()) if paths.find_game_dir() else None
    if why:
        echo(f"native: WARNING: {why}")
    base_run = Path(base.run_dir)
    process = base.campaign_game_process
    code = 0
    while True:
        mark.set("waiting")
        if args.launch and not native.game_running(process):
            if base.game_dir is None:
                echo("native: --launch needs --game-dir (the HOI4 install)")
                return 2
            echo("native: starting Hearts of Iron IV with -debug_mode")
            native.launch_game(base.game_dir)
        native.wait_for_game(lambda: native.game_running(process), time.sleep, echo)
        check = native.check_debug_mode(native.game_command_line(process))
        echo(f"native: {check.message}")
        if check.ok is False and not args.no_debug_check:
            if not args.keep_running:
                return 2
        else:
            config = _config_from_args(args)
            config.run_dir = native.session_dir(base_run)
            if getattr(args, "breakaway", False):
                from .adapters.logtail import find_log
                from .late import wait_for_breakaway

                found = wait_for_breakaway(
                    config.log_path or find_log(), config.rival, args.breakaway_ideology, echo,
                    alive=lambda: native.game_running(process))
                if found is None:
                    echo("native: the game quit before the breakaway appeared")
                    if not args.keep_running:
                        return 1
                    continue
                config.rival = found.tag
            if getattr(args, "wait_for_country", False) or getattr(args, "breakaway", False):
                # A late country: no deadline on the start (the game runs the mark when it exists).
                config.rival_start_wait_minutes = 10 ** 6
            echo(f"native: playing {config.rival} in the background; run folder {config.run_dir}")
            with keep_awake_for(config, echo):
                code = cmd_rival(args, config)
        if not args.keep_running:
            return code
        while native.game_running(process):
            time.sleep(10)
        echo("native: the game has quit; waiting for the next start")


def cmd_handover(args: argparse.Namespace) -> int:
    """Grant (or take back) the keyboard for a running --handover session."""
    from .handover import QUEUE_FILENAME, HandoverQueue

    config = _config_from_args(args)
    queue = HandoverQueue(config.run_dir)
    if args.release:
        queue._release()
        print("control taken back: the harness stops before its next action")
        return 0
    pending = config.run_dir / QUEUE_FILENAME
    if pending.exists():
        items = json.loads(pending.read_text(encoding="utf-8"))
        for item in items:
            print(f"  #{item['position']} {item['action']} {json.dumps(item['arguments'])}")
        if not items:
            print("nothing queued")
    queue.grant()
    print(f"control handed over ({queue.grant_path}); the harness runs its queue at the next turn")
    return 0


def cmd_mod_directives(args: argparse.Namespace) -> int:
    """Regenerate the mod's per-target directive files, or check they are current."""
    from . import modgen

    mod_dir = Path(args.mod_dir)
    tags = [t for t in (args.tags or "").split(",") if t.strip()] or list(modgen.DEFAULT_TAGS)
    try:
        if args.check:
            stale = modgen.drift(mod_dir, tags)
            for path in stale:
                print(f"stale: {path}", file=sys.stderr)
            return 1 if stale else 0
        for path in modgen.write(mod_dir, tags):
            print(f"wrote {path}")
    except ValueError as exc:
        print(f"mod-directives: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_mod_probe(args: argparse.Namespace) -> int:
    """Regenerate the focus/research probe from the configured install."""
    from . import modgen

    config = _config_from_args(args)
    if config.game_dir is None:
        print("mod-probe: point --game-dir (or HOI4_GAME_DIR) at the HOI4 install", file=sys.stderr)
        return 2
    try:
        index = config.build_index()
    except FileNotFoundError as exc:
        print(f"mod-probe: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {modgen.write_probe(Path(args.mod_dir), index)}")
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    """Where a state or province is on the map: centroid, a point inside it, neighbours."""
    from .identifiers import Playset
    from .mapdata import MapError, MapLayer

    config = _config_from_args(args)
    if config.game_dir is None:
        print("map: point --game-dir (or HOI4_GAME_DIR) at the HOI4 install", file=sys.stderr)
        return 2
    if (args.state is None) == (args.province is None):
        print("map: give exactly one of --state or --province", file=sys.stderr)
        return 2
    try:
        playset = Playset.from_paths(config.game_dir, config.mod_dirs)
        cache = False if args.no_cache else (Path(args.cache_dir) if args.cache_dir else None)
        layer = MapLayer.load(playset, cache)
        print(f"map              {layer.width}x{layer.height} px, {len(layer.provinces)} provinces, "
              f"{len(layer.states)} states ({'cache' if layer.from_cache else 'read from the picture'})")
        if args.state is not None:
            _print_state(layer, args.state)
        else:
            _print_province(layer, args.province)
    except (FileNotFoundError, MapError) as exc:
        print(f"map: {exc}", file=sys.stderr)
        return 1
    return 0


def _print_state(layer, sid: int) -> None:
    state = layer._state(sid)
    cx, cy = layer.state_centroid(sid)
    ix, iy = layer.interior_point("state", sid)
    inside = layer.state_of(layer.province_at(round(cx), round(cy)) or -1) == sid
    print(f"state {sid}        {state.name_key}" + (f"  owner {state.owner}" if state.owner else ""))
    print(f"provinces        {len(state.provinces)}: {' '.join(str(p) for p in state.provinces)}")
    print(f"pixels           {layer.state_pixels(sid)}")
    print(f"bounding box     x {layer.state_bbox(sid)[0]}..{layer.state_bbox(sid)[2]}, "
          f"y {layer.state_bbox(sid)[1]}..{layer.state_bbox(sid)[3]}")
    print(f"centroid         ({cx:.1f}, {cy:.1f})" + ("" if inside else "  -- outside the state"))
    print(f"click point      ({ix}, {iy})  province {layer.province_at(ix, iy)}")


def _print_province(layer, pid: int) -> None:
    prov = layer.provinces.get(pid)
    if prov is None or not prov.pixels:
        raise FileNotFoundError(f"province {pid} is not on the map")
    ix, iy = layer.interior_point("province", pid)
    print(f"province {pid}    {prov.kind}, {prov.terrain}" + (", coastal" if prov.coastal else ""))
    state = layer.state_of(pid)
    print(f"state            {state if state is not None else '-'}")
    print(f"pixels           {prov.pixels}")
    print(f"bounding box     x {prov.bbox[0]}..{prov.bbox[2]}, y {prov.bbox[1]}..{prov.bbox[3]}")
    print(f"centroid         ({prov.centroid[0]:.1f}, {prov.centroid[1]:.1f})")
    print(f"click point      ({ix}, {iy})")
    shown = [f"{n}{'~' if n in layer.straits.get(pid, ()) else ''}" for n in layer.neighbours(pid)]
    print(f"neighbours       {' '.join(shown) or '-'}   (~ = strait, no shared border)")


def cmd_dlc(args: argparse.Namespace) -> int:
    """Which DLCs are installed, disabled and active, and the features each turns on."""
    from . import dlc

    config = _config_from_args(args)
    if config.game_dir is None:
        from . import setup_model

        config.game_dir = setup_model.find_install()
    if config.game_dir is None:
        print("dlc: point --game-dir (or HOI4_GAME_DIR) at the HOI4 install", file=sys.stderr)
        return 2
    picture = config.dlc_set()
    print(f"source           {picture.source}")
    if picture.source == "explicit":
        print("(an explicit list: the installed and disabled lists are not read)")
    else:
        print(f"installed        {len(picture.installed)}: " + ("; ".join(picture.installed) or "-"))
        print(f"disabled         {len(picture.disabled)}: " + ("; ".join(picture.disabled) or "-"))
    if picture.active is None:
        print("active           unknown: nothing is filtered by DLC")
    else:
        print(f"active           {len(picture.active)}: " + ("; ".join(sorted(picture.active)) or "-"))
    for note in picture.notes:
        print(f"  NOTE           {note}")
    print()
    print("features the game files tie to a DLC:")
    for row in dlc.feature_table(picture.active):
        state = "unknown" if row["on"] is None else "ON" if row["on"] else "off"
        print(f"  {state:8s} {row['label']}  (needs {' or '.join(row['needs'])})")
    print("balance of power bars:")
    for bar, name in sorted(dlc.BOP_DLC.items()):
        state = ("unknown" if picture.active is None else "ON" if name in picture.active else "off")
        print(f"  {state:8s} {bar}  (needs {name})")
    print("everything else the DLCs add is gated per item (focus, decision, law, project, ...) by "
          "its own has_dlc conditions, which the harness evaluates; see docs/dlc.md")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    """What exists in the configured playset: countries, focuses, technologies, states."""
    config = _config_from_args(args)
    if config.game_dir is None:
        print("index: point --game-dir (or HOI4_GAME_DIR) at the HOI4 install", file=sys.stderr)
        return 2
    try:
        index = config.build_index()
    except FileNotFoundError as exc:
        print(f"index: {exc}", file=sys.stderr)
        return 1
    summary = index.summary()
    print(f"playset          {summary['playset']}  [{summary['fingerprint']}]")
    for mod in index.playset.mods:
        print(f"  mod            {mod.name} {mod.version}".rstrip() + f"  ({mod.path})")
    for warning in index.playset.load_order_warnings():
        print(f"  WARNING        {warning}")
    print(f"countries        {summary['tags']}: {' '.join(sorted(index.tags)[:40])}"
          + (" ..." if summary["tags"] > 40 else ""))
    print(f"focus trees      {summary['focus_trees']} ({summary['focuses']} focuses)")
    print(f"technologies     {summary['technologies']}")
    print(f"states           {summary['states']}")
    if args.country:
        tag = args.country.upper()
        trees = index.trees_for(tag)
        focuses = index.focuses_for(tag)
        print()
        print(f"{tag}: {', '.join(t.id for t in trees) or 'no focus tree'}"
              f" -- {len(focuses)} focuses")
        roots = [f.id for f in focuses.values() if not f.prerequisites]
        print(f"  start with: {', '.join(roots) or '-'}")
        if args.all:
            for focus_id in sorted(focuses):
                print(f"  {focus_id}")
    return 0


def cmd_paths(args: argparse.Namespace) -> int:
    """The paths open to a country: its AI behaviour rule options and its focus tree branches."""
    from . import pathchoice

    config = _config_from_args(args)
    if config.game_dir is None:
        print("paths: point --game-dir (or HOI4_GAME_DIR) at the HOI4 install", file=sys.stderr)
        return 2
    if not args.country:
        print("paths: name a country with --country TAG", file=sys.stderr)
        return 2
    try:
        index = config.build_index()
    except FileNotFoundError as exc:
        print(f"paths: {exc}", file=sys.stderr)
        return 1
    menu = pathchoice.build_menu(index, args.country)
    print(pathchoice.menu_json(menu) if args.json else pathchoice.render_menu(menu))
    return 0


def cmd_brief_world(args: argparse.Namespace) -> int:
    """The briefing agent on its own: the dossier of the world and the country from a save and
    the playset, and the orientation the configured model writes from it (#165)."""
    from . import briefing as B
    from .adapters.savegame import find_save_dir
    from .agent.llm import build_llm
    from .world import newest_save

    config = _config_from_args(args)
    if config.game_dir is None:
        print("brief-world: point --game-dir (or HOI4_GAME_DIR) at the HOI4 install", file=sys.stderr)
        return 2
    if not args.country:
        print("brief-world: name a country with --country TAG", file=sys.stderr)
        return 2
    tag = args.country.upper()
    try:
        index = config.build_index()
    except FileNotFoundError as exc:
        print(f"brief-world: {exc}", file=sys.stderr)
        return 1
    save = Path(args.save) if args.save else config.briefing_save
    if save is None:
        folder = config.save_dir or find_save_dir()
        save = newest_save(Path(folder), None, tag) if folder else None
    layer = None
    if save is not None:
        try:
            from .mapdata import MapLayer

            layer = MapLayer.load(index.playset)
        except Exception as exc:  # noqa: BLE001
            print(f"brief-world: no map layer ({type(exc).__name__}: {exc})", file=sys.stderr)
    if save is None:
        print("brief-world: no save found (--save PATH); the dossier has the playset's files only",
              file=sys.stderr)
    spec = config.briefing if config.briefing.provider else config.planner
    client = None if args.dossier_only else build_llm(spec)
    web_on, why = (False, "web search is off")
    if client is not None:
        web_on, why = B.enable_web(client, args.web or config.briefing_web_search)
    if args.web or config.briefing_web_search:
        print(f"brief-world: {why}", file=sys.stderr)
    dossier = B.build_dossier(config, index, tag, save=save, layer=layer, offline=not web_on)
    if args.dossier_only:
        print(dossier.render())
        return 0
    made = B.brief(client, dossier, web=web_on, max_tokens=spec.max_tokens)
    out = Path(args.out) if args.out else config.run_dir
    path = B.save_briefing(out, made, dossier)
    print(made.text)
    print(f"saved: {path} (dossier beside it in {Path(out) / 'briefings'}); "
          f"{made.input_tokens:,} in / {made.output_tokens:,} out", file=sys.stderr)
    if made.missing:
        print("brief-world: the briefing lacks: " + ", ".join(made.missing), file=sys.stderr)
    return 0


def cmd_mod_actions(args: argparse.Namespace) -> int:
    """What a country can do in the playset, and what the mods are about."""
    import time

    from .modcontext import playset_briefs

    config = _config_from_args(args)
    if config.game_dir is None:
        print("mod-actions: point --game-dir (or HOI4_GAME_DIR) at the HOI4 install", file=sys.stderr)
        return 2
    started = time.perf_counter()
    try:
        index = config.build_index()
    except FileNotFoundError as exc:
        print(f"mod-actions: {exc}", file=sys.stderr)
        return 1
    indexed = time.perf_counter() - started
    ctx = index.mod_context()
    tag = (args.country or config.country or "").upper()
    if not tag:
        print("mod-actions: name a country with --country TAG", file=sys.stderr)
        return 2
    if index.tags and tag not in index.tags:
        print(f"mod-actions: there is no country '{tag}' in this playset", file=sys.stderr)
        return 1
    surface = ctx.surface(tag)
    surface_time = time.perf_counter() - started - indexed
    briefs = playset_briefs(index.playset.mods, cache_dir=config.mod_context_cache_dir,
                            offline=args.offline)
    timing = {"index_s": round(indexed, 1), "surface_s": round(surface_time, 1)}
    if args.json:
        data = surface.to_dict()
        if not args.all:
            data["decisions"] = data["decisions"][:args.limit]
            data["focus"]["details"] = data["focus"]["details"][:args.limit]
            data["new_technologies"] = data["new_technologies"][:args.limit]
        data["world"] = [b.to_dict() for b in briefs]
        data["timing"] = timing
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    print(f"playset          {surface.playset}  [{index.fingerprint()}]")
    for b in briefs:
        print(f"world            {b.text}   [{b.source}{', ' + b.how if b.how else ''}]")
    print(f"country          {tag}")
    for key, value in surface.counts.items():
        print(f"  {key.replace('_', ' '):<26}{value}")
    print(f"  {'timing':<26}index {timing['index_s']}s, surface {timing['surface_s']}s")
    cap = None if args.all else args.limit
    print("\ncategories")
    for c in surface.categories[:cap]:
        gui = "  [custom GUI]" if c.get("custom_gui") else ""
        print(f"  {c['id']:<40} {c['decisions']:>4}  {c['name']}{gui}")
    print("\ndecisions")
    for d in surface.decisions[:cap]:
        cost = f" {d['cost']:g}pp" if d.get("cost") else ""
        days = f" {d['days_remove']}d" if d.get("days_remove") else ""
        kind = "never" if d.get("blocked") else "inert" if d.get("inert") else d["kind"]
        print(f"  {d['id']:<48} {kind:<8}{cost}{days}  {d.get('name', '')}")
    print(f"\nfocus tree       {', '.join(surface.focus['trees']) or 'none'} "
          f"({surface.focus['focuses']} focuses)")
    for f in surface.focus["available_now"][:cap]:
        print(f"  start with     {f['id']}  {f['name']}")
    print(f"\nnew technologies {len(surface.new_technologies)}")
    for t in surface.new_technologies[:cap]:
        print(f"  {t['id']:<40} {t['folder'] or '-':<24} {t['name']}")
    mech = surface.mechanics
    for label in ("ideologies", "resources", "buildings", "on_actions"):
        entry = mech[label]
        print(f"new {label:<13}{len(entry['new'])} ({entry['dropped']} base ones dropped): "
              f"{' '.join(entry['new'][:12])}")
    print(f"spirits / laws / advisors   {len(surface.spirits)} / {len(surface.laws)} / "
          f"{len(surface.advisors)}")
    ev = surface.events
    print(f"events           {ev['events']} in {ev['namespaces']} namespaces; {tag}: "
          f"{', '.join(f'{k} ({v})' for k, v in list(ev['country'].items())[:8]) or 'none'}")
    for line in surface.excluded:
        print(f"excluded         {line}")
    return 0


def cmd_experiment(args: argparse.Namespace) -> int:
    from .eval.experiment import run_experiment

    if _refuse_scenario_overrides(args, "experiment"):
        return 2
    config = _config_from_args(args)
    try:
        result = run_experiment(args.scenario, config, seeds=args.seeds,
                                transcript_dir=config.run_dir / "experiment")
    except KeyError as exc:
        print(f"experiment: {exc.args[0]}", file=sys.stderr)
        return 2
    print(result.render())
    if getattr(args, "markdown", None):
        Path(args.markdown).write_text(result.markdown() + "\n", encoding="utf-8")
    return 0


def cmd_save_inspect(args: argparse.Namespace) -> int:
    """Show what a real save contains, so the save mapping is written from the file."""
    from .adapters.savegame import find_save_dir, inspect, latest_save

    config = _config_from_args(args)
    path = Path(args.save) if args.save else None
    if path is None:
        save_dir = find_save_dir(config.save_dir)
        path = latest_save(save_dir) if save_dir else None
    if path is None or not path.exists():
        print("save-inspect: no save found; pass a path or set HOI4_SAVE_DIR", file=sys.stderr)
        return 1
    print(inspect(path))
    return 0


def cmd_verify_bridge(args: argparse.Namespace) -> int:
    """Check the bridge mod's tokens against the install, and its telemetry against a log."""
    from .adapters.logtail import find_log
    from .bridge_check import check_log, check_tokens

    config = _config_from_args(args)
    checked, failed = 0, False
    if config.game_dir is not None:
        report = check_tokens(config.game_dir)
        print(report.render())
        checked += 1
        failed |= not report.ok
    log = find_log(config.log_path)
    if log is not None and log.exists():
        if checked:
            print()
        report = check_log(log, player=config.country)
        print(report.render())
        checked += 1
        failed |= not report.ok
    if not checked:
        print("verify-bridge: give --game-dir (tokens) and/or HOI4_LOG_PATH (telemetry)",
              file=sys.stderr)
        return 2
    return 1 if failed else 0


def cmd_prompt(args: argparse.Namespace) -> int:
    """Print the exact system prompt a run would use. Nothing is hidden."""
    from .agent.prompts import system_for

    config = _config_from_args(args)
    print(system_for(config, config.build_index()))
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    from . import skills as sk

    if args.skills_command == "new":
        directory = Path(args.new_dir) if args.new_dir else sk.harness_home() / "skills"
        print(f"wrote {sk.scaffold(args.name, directory, args.new_mods or (), args.new_tags or ())}")
        return 0
    config = _config_from_args(args)
    if config.game_dir is not None:
        mods = config.build_index().playset.mods
    else:
        from .identifiers import Mod

        mods = [Mod.load(Path(m)) for m in config.mod_dirs]
    found = sk.discover(config.skills_dirs, mods)
    tag = config.country
    if args.skills_command == "show":
        match = next((s for s in found if s.name.lower() == args.name.lower()), None)
        if match is None:
            raise sk.SkillError(f"No skill named {args.name!r}. Found: "
                                f"{', '.join(s.name for s in found) or 'none'}")
        reason = sk.why(match, mods, tag, config.skills)
        print(f"{match.name}  ({match.path}, from {match.source}, ~{match.tokens} tokens)")
        print(f"applies: {reason or 'no' + ('' if match.has_harness_keys else ' (Claude Code skill only)')}")
        print()
        print(match.body)
        return 0
    chosen = sk.select(found, mods, tag, config.skills)
    selection = sk.fit(chosen, config.skill_tokens, config.guidance)
    reasons = {s.name: r for s, r in chosen}
    if not found:
        print("no skills found. Searched:")
    for source, folder in sk.search_paths(config.skills_dirs, mods):
        print(f"  {source:<14}{folder}")
    for s in found:
        if s in selection.included:
            state = f"INCLUDED ({reasons[s.name]})"
        elif s in selection.left_out:
            state = f"left out for space ({reasons[s.name]})"
        elif s in selection.duplicates:
            state = "skipped: the strategy guidance pack is already in the prompt"
        else:
            state = "not applied" + ("" if s.has_harness_keys else " (Claude Code skill only)")
        print(f"{s.name:<24} ~{s.tokens:>5} tok  {s.source:<14} {state}")
        print(f"    {s.path}")
    return 0


def calibration_targets(config: HarnessConfig) -> list[str]:
    """What to calibrate when the operator names nothing: every concrete target
    the recorded UI scripts click, else the default panel list. A templated
    target ('construction.{building}') cannot be aimed at, so it is reported
    and left for the operator to name per value."""
    from .adapters.ui_scripts import DEFAULT_FILENAME as SCRIPTS_FILENAME
    from .adapters.ui_scripts import expanded_targets, load_scripts

    path = config.ui_scripts_path or config.run_dir / SCRIPTS_FILENAME
    if not Path(path).exists():
        return list(DEFAULT_TARGETS)
    targets = expanded_targets(load_scripts(path))
    templated = [t for t in targets if "{" in t]
    for target in templated:
        print(f"note: {target} depends on the action's arguments; calibrate each value by name",
              file=sys.stderr)
    return [t for t in targets if "{" not in t] or list(DEFAULT_TARGETS)


def cmd_tactics(args: argparse.Namespace) -> int:
    from .tactics import CONTROLLERS, list_scenarios
    from .tactics.evaluate import evaluate, run_episode, table

    if args.tactics_command == "list":
        for name in list_scenarios():
            print(name)
        return 0
    if args.tactics_command == "train":
        from .tactics.learned import PARAMS_PATH
        from .tactics.train import train

        scenarios = None if args.scenario == "all" else [args.scenario]
        if scenarios and scenarios[0] not in list_scenarios():
            print(f"tactics: unknown scenario {scenarios[0]!r}", file=sys.stderr)
            return 2
        policy = train(args.minutes, args.seed, scenarios)
        policy.save(args.out or PARAMS_PATH)
        print(f"saved {args.out or PARAMS_PATH}")
        return 0
    if args.tactics_command == "train-plans":
        from .tactics.learned_plans import PARAMS_PATH as PLAN_PARAMS
        from .tactics.train_plans import train as train_plans

        policy = train_plans(args.minutes, args.seed)
        policy.save(args.out or PLAN_PARAMS)
        print(f"saved {args.out or PLAN_PARAMS}")
        return 0
    names = list_scenarios() if args.scenario == "all" else [args.scenario]
    unknown = [n for n in names if n not in list_scenarios()]
    if unknown:
        known = ", ".join(list_scenarios())
        print(f"tactics: unknown scenario {unknown[0]!r}. Known: {known}", file=sys.stderr)
        return 2
    groups = {"both": ["vanilla", "scripted"], "all": list(CONTROLLERS)}
    controllers = groups.get(args.controller, [args.controller])
    if "learned" in controllers and getattr(args, "params", None):
        from .tactics.learned import LearnedController, Policy

        learned = LearnedController(Policy.load(args.params))
        controllers = [learned if c == "learned" else c for c in controllers]
    if args.tactics_command == "episode":
        print(json.dumps(run_episode(names[0], args.seed, controllers[0], args.opponent), indent=2))
        return 0
    rows = [evaluate(n, c, args.seeds, args.opponent, args.first_seed) for n in names for c in controllers]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(table(rows))
        rate = sum(r["steps_per_second"] for r in rows) / len(rows)
        print(f"\n{args.seeds} seeds per row against the {args.opponent} opponent; "
              f"about {rate:.0f} environment steps per second (6 game hours each, controller included).")
    return 0


def cmd_check_scripts(args: argparse.Namespace) -> int:
    """Pre-flight for the input driver: scripts parse, placeholders exist,
    every concrete click target is calibrated. Nothing touches the game."""
    from .adapters.ui_scripts import DEFAULT_FILENAME as SCRIPTS_FILENAME
    from .adapters.ui_scripts import load_scripts, missing_targets, targets_in
    from .calibration import load_calibration

    config = _config_from_args(args)
    scripts_path = config.ui_scripts_path or config.run_dir / SCRIPTS_FILENAME
    calibration_path = config.calibration_path or config.run_dir / DEFAULT_FILENAME
    if not Path(scripts_path).exists():
        print(f"check-scripts: no {scripts_path}; copy profiles/ui_scripts.1.19.json there",
              file=sys.stderr)
        return 2
    try:
        scripts = load_scripts(scripts_path)
    except ValueError as exc:
        print(f"scripts INVALID  {exc}")
        return 1
    print(f"scripts          {scripts_path}: {', '.join(sorted(scripts)) or 'none'}")
    coordinates: dict = {}
    if Path(calibration_path).exists():
        try:
            calibration = load_calibration(calibration_path)
        except (RuntimeError, ValueError) as exc:
            print(f"calibration INVALID  {exc}")
            return 1
        coordinates = calibration.coordinates
        width, height = calibration.screen
        print(f"calibration      {calibration_path}: {len(coordinates)} targets at {width}x{height}")
    else:
        print(f"calibration      none at {calibration_path}")
    missing = missing_targets(scripts, coordinates)
    for target in missing:
        print(f"  MISSING        {target}")
    for target in (t for t in targets_in(scripts) if "{" in t):
        prefix = target.split("{", 1)[0]
        have = sorted(k for k in coordinates if k.startswith(prefix))
        print(f"  per value      {target}: " + (", ".join(have) if have else "none calibrated yet"))
    if missing:
        print(f"{len(missing)} target(s) to calibrate: hoi4-harness calibrate {' '.join(missing)}")
        return 1
    print("ok: every concrete target is calibrated")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    from .calibration import capture_calibration, save_calibration

    config = _config_from_args(args)
    out = Path(args.out) if args.out else config.run_dir / DEFAULT_FILENAME
    targets = args.targets or calibration_targets(config)
    try:
        calibration = capture_calibration(targets)
    except RuntimeError as exc:  # most likely the missing 'input' extra; report, don't crash
        print(f"calibrate FAILED   {type(exc).__name__}: {exc}")
        return 1
    save_calibration(calibration, out)
    width, height = calibration.screen
    print(f"Wrote {len(calibration.coordinates)} targets at {width}x{height} to {out}")
    return 0


def _run_options(p: argparse.ArgumentParser) -> None:
    """The setup window's fields as flags (docs/setup.md); play, native and start share them."""
    p.add_argument("--setup", metavar="FILE",
                   help="a setup file written by `hoi4-harness setup` (~/.hoi4-harness/setup.json): "
                        "the countries, models, instructions and options it holds; flags win")
    p.add_argument("--instruction", metavar="TEXT",
                   help="a standing instruction for the whole run, e.g. 'go alt-history Germany "
                        "and ally the UK' (per country: --player-instruction)")
    for flag, what in (("provider", "anthropic, openai, google, claude-cli"),
                       ("model", "a model name"), ("base-url", "a local OpenAI-compatible server"),
                       ("key-env", "the environment variable holding that country's key"),
                       ("max-usd", "its spend cap in dollars"),
                       ("price", "IN,OUT dollars per million tokens, what the cap counts with"),
                       ("instruction", "its own standing instruction")):
        p.add_argument(f"--player-{flag}", dest=f"player_{flag.replace('-', '_')}",
                       action="append", metavar="TAG=VALUE",
                       help=f"one country's {what} (repeatable)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hoi4-harness", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--adapter", choices=ADAPTERS)
        p.add_argument("--provider", choices=PROVIDERS)
        p.add_argument("--model")
        p.add_argument("--base-url", dest="base_url")
        p.add_argument("--effort", choices=["low", "medium", "high"])
        p.add_argument("--run-dir", dest="run_dir")
        p.add_argument("--live", action="store_true",
                       help="allow irreversible actions (default: dry run)")
        p.add_argument("--max-usd", type=float)
        p.add_argument("--no-window-guard", dest="no_window_guard", action="store_true",
                       help="send input even when the game is not the focused window")
        p.add_argument("--profile", help="JSON/TOML file holding a whole configuration")
        p.add_argument("--guidance", metavar="NAME|PATH",
                       help="how much to coach the model: " + ", ".join(guidance_packs())
                            + ", or a path to your own file")
        p.add_argument("--skill", dest="skill_names", action="append", metavar="NAME",
                       help="include this skill pack in the prompt (repeatable; docs/skills.md)")
        p.add_argument("--skills-dir", dest="skills_dirs", action="append", metavar="PATH",
                       help="look for skill packs here first (repeatable)")
        p.add_argument("--system-prompt", dest="system_prompt", metavar="PATH",
                       help="replace the entire system prompt, mechanics included")
        p.add_argument("--objective", help="the standing objective given every turn")
        p.add_argument("--actions", metavar="A,B,C", help="only allow these actions")
        p.add_argument("--without", metavar="A,B,C", help="forbid these actions")
        p.add_argument("--allow-all", dest="allow_all", action="store_true",
                       help="skip the confirmation gate on irreversible actions")
        p.add_argument("--no-reflex", dest="no_reflex", action="store_true",
                       help="disable the deterministic reflex layer; the model decides everything")
        p.add_argument("--operational-control", dest="operational_control",
                       choices=["llm", "ai", "game_ai"],
                       help="llm: the model moves units; ai: the game's AI does, and the model "
                            "sets directives and postures; game_ai: the AI runs the whole "
                            "country (campaign mode)")
        p.add_argument("--control-map", dest="control_map", metavar="FILE|JSON|PAIRS",
                       help="who runs each domain (focus, research, construction, production, "
                            "politics, diplomacy, army, navy, air): llm, script, game or off. A "
                            "JSON/TOML file, inline JSON or domain=controller pairs; the old "
                            "--operational-control levels are presets of it "
                            "(docs/modular-control.md)")
        p.add_argument("--steer", choices=["llm", "off", "auto"],
                       help="with a control map: whether the model steers the game's AI "
                            "(directives, posture, delegation); auto: when the AI owns something")
        p.add_argument("--army-controller", dest="army_controller", metavar="NAME",
                       help="with army=script: the tactical controller (scripted, learned, "
                            "vanilla_like, ...)")
        p.add_argument("--allow-contested", dest="allow_contested", action="store_true",
                       help="with a control map: warn instead of refusing where the game's AI "
                            "(human_ai) would undo what the model or a script does")
        p.add_argument("--player-clock", dest="player_clock", action="store_true",
                       help="a person is playing: never pause, resume or set game speed")
        p.add_argument("--no-llm", dest="no_llm", action="store_true",
                       help="never wake the planner: reflexes only, zero model calls")
        p.add_argument("--country", help="country tag: the mock's country (default SWE), or the one "
                       "the log reader keeps to (default: whichever the game reports)")
        p.add_argument("--start-date", dest="start_date", help="start date (mock adapter)")
        p.add_argument("--seed", type=int, help="mock adapter seed")
        p.add_argument("--game-dir", dest="game_dir", metavar="PATH",
                       help="HOI4 install folder: check ids against what the game loads")
        p.add_argument("--dlc", dest="dlcs", action="append", metavar="NAME",
                       help="treat exactly these DLCs as active (repeatable), instead of the "
                            "installed ones minus the launcher's disabled list (docs/dlc.md)")
        p.add_argument("--no-dlc", dest="no_dlc", action="store_true",
                       help="treat no DLC as active")
        p.add_argument("--user-dir", dest="user_dir", metavar="PATH",
                       help="the game's user folder, where dlc_load.json is (default: found)")
        p.add_argument("--headless", action="store_true",
                       help="read the game's log, act through the console and the keyboard: "
                            "no mouse, no screen (docs/headless.md)")
        p.add_argument("--headless-clock", dest="headless_clock",
                       choices=["auto", "console", "keys", "exact"],
                       help="with --headless: how the clock is driven (docs/headless.md)")
        p.add_argument("--headless-reader", dest="headless_reader",
                       choices=["log+save", "logtail", "savegame"],
                       help="with --headless: what reads the game (default log+save: the log "
                            "plus a text save taken through the console)")
        p.add_argument("--save-days", dest="save_days", type=int, metavar="N",
                       help="with log+save: at most one periodic save per N in-game days "
                            "(0: only at the start and around verified actions)")
        p.add_argument("--transport", choices=["keys", "inbox"],
                       help="how console actions reach the game: keys (type `run` into the "
                            "console) or inbox (a mod file the game reloads by itself when "
                            "started with -debug_mode: no keyboard, no focus; docs/inbox.md)")
        p.add_argument("--inbox-dir", dest="inbox_dir", metavar="PATH",
                       help="with --transport inbox: the installed llm_bridge mod folder "
                            "(default: <Documents>/Paradox Interactive/Hearts of Iron IV/mod/llm_bridge)")
        p.add_argument("--telemetry-hours", dest="telemetry_hours", type=int, metavar="H",
                       help="with --headless: how often the mod writes its telemetry, in "
                            "in-game hours: 1, 2, 3, 4, 6, 8 or 12 (several a day), 24 (once a "
                            "day) or a multiple of 24 (every N days). Default: leave the game "
                            "as it is (once a day). docs/mod-bridge.md has the cost")
        p.add_argument("--fake-game", dest="fake_game", action="store_true",
                       help="with --headless: run against a fake game in this process, no HOI4")
        p.add_argument("--fake-game-dir", dest="fake_game_dir", metavar="PATH",
                       help="where the fake game keeps its log and files (implies --fake-game)")
        p.add_argument("--fake-game-files", dest="fake_game_files", metavar="PATH",
                       help="with --headless: talk to a `hoi4-harness fakegame` process by its files")
        p.add_argument("--mod", dest="mods", action="append", metavar="PATH",
                       help="an active mod folder; repeat in load order")
        p.add_argument("--mod-context", dest="mod_context", action="store_true",
                       help="add a 'This playset' section to the prompt and the list_decisions / "
                            "describe_action tools (docs/mods.md); needs --game-dir")
        p.add_argument("--fetch-workshop", dest="fetch_workshop", action="store_true",
                       help="with --mod-context: fetch a mod's Steam page when it is not cached "
                            "(default: cache only, no network)")

    doctor = sub.add_parser("doctor", help="check the environment")
    common(doctor)
    doctor.set_defaults(func=cmd_doctor)

    actions = sub.add_parser("actions", help="list the action catalog")
    actions.add_argument("--json", action="store_true")
    actions.set_defaults(func=cmd_actions)

    prompt = sub.add_parser("prompt", help="print the system prompt a run would use")
    common(prompt)
    prompt.set_defaults(func=cmd_prompt)

    skills = sub.add_parser("skills", help="skill packs: list, new, show (docs/skills.md)")
    skills_sub = skills.add_subparsers(dest="skills_command", required=True)
    s_list = skills_sub.add_parser("list", help="found skills, where, and which apply to this run")
    common(s_list)
    s_list.set_defaults(func=cmd_skills)
    s_new = skills_sub.add_parser("new", help="write a template SKILL.md")
    s_new.add_argument("name")
    s_new.add_argument("--mod", dest="new_mods", action="append", metavar="NAME",
                       help="a mod name or workshop id the skill is for (repeatable)")
    s_new.add_argument("--tag", dest="new_tags", action="append", metavar="TAG",
                       help="a country tag the skill is for (repeatable)")
    s_new.add_argument("--dir", dest="new_dir", metavar="PATH",
                       help="where to put it (default: ~/.hoi4-harness/skills)")
    s_new.set_defaults(func=cmd_skills)
    s_show = skills_sub.add_parser("show", help="print a skill and why it does or does not apply")
    s_show.add_argument("name")
    common(s_show)
    s_show.set_defaults(func=cmd_skills)

    observe = sub.add_parser("observe", help="print one situation brief")
    common(observe)
    observe.set_defaults(func=cmd_observe)

    play = sub.add_parser("play", help="run the agent loop")
    common(play)
    play.add_argument("--turns", type=int)
    _run_options(play)
    play.add_argument("--advisor", action="store_true",
                      help="recommend, never act: every tool call is shown to the player instead")
    play.add_argument("--handover", action="store_true",
                      help="co-op: queue every action until the player runs `hoi4-harness handover`")
    play.add_argument("--handover-mode", dest="handover_mode",
                      choices=["hotkey", "idle", "approve"],
                      help="what opens a handover window (implies --handover): hotkey (you grant "
                           "it), idle (game paused and you are quiet), approve (you say yes to "
                           "each action)")
    play.add_argument("--handover-hotkey", dest="handover_hotkey", metavar="KEYS",
                      help="a global hotkey that grants a window, e.g. ctrl+alt+h (Windows; "
                           "off by default)")
    play.add_argument("--handover-idle-seconds", dest="handover_idle_seconds", type=float,
                      help="idle mode: seconds without keyboard or mouse before it may act")
    play.add_argument("--human", metavar="DOMAINS",
                      help="co-op: the domains you keep (focus,research,construction,production,"
                           "politics,diplomacy,army,navy,air); the model never acts in them")
    play.add_argument("--mp-human", dest="mp_human", metavar="TAGS",
                      help="multiplayer: the countries people play (GER,SOV); --country is the "
                           "one the harness plays. Actions aimed at them are refused")
    play.add_argument("--mp-client", dest="mp_client", action="store_true",
                      help="multiplayer client: player clock, the model never pauses the game")
    play.add_argument("--days", type=int, help="in-game days per turn")
    play.add_argument(
        "--resume",
        action="store_true",
        help="continue an interrupted run in --run-dir, rebuilding state from its transcript",
    )
    play.add_argument("--script", metavar="PATH",
                      help="scripted provider: replay these responses (JSON), then fall back "
                           "to the keyword policy; offline, no model call")
    play.add_argument("--mailbox", nargs="?", const="", metavar="DIR",
                      help="no model call: write each request into DIR (default <run-dir>/mailbox) "
                           "and wait for an answer from outside, e.g. a Claude Code agent running "
                           "'hoi4-harness mailbox wait/answer'; see docs/mailbox.md")
    play.add_argument("--mailbox-timeout", dest="mailbox_timeout", type=float, metavar="MIN",
                      help="mailbox: give up (the run stops) after MIN minutes without an answer")
    play.add_argument("--campaign", dest="campaign_mode", action="store_true",
                      help="the unattended campaign: the game's AI runs the country (human_ai), "
                           "the model steers it; see docs/campaign.md")
    play.add_argument("--until", metavar="YYYY-MM-DD",
                      help="stop when the game reaches this date (campaign default 1948-12-31)")
    play.add_argument("--choose-path", dest="choose_path", action="store_true",
                      help="campaign: before the start the model picks its country's path (AI "
                           "behaviour rule + focus tree summary; see `paths`) and the choice "
                           "shapes the guidance (#160)")
    play.add_argument("--briefing", dest="briefing_flag", action="store_true",
                      help="campaign: before the start a briefing model writes an orientation "
                           "(the situation and the open paths, no plan) for the player (#165; "
                           "docs/briefing.md)")
    play.add_argument("--rebrief", metavar="SCHEDULE",
                      help="with --briefing: brief again at yearly, halfyearly, YYYY-MM-DD or on "
                           "war, capitulation, faction (comma list)")
    play.add_argument("--briefing-web", dest="briefing_web", action="store_true",
                      help="with --briefing: let the briefing model search the web for mods")
    play.add_argument("--lobby-rule", dest="lobby_rule", action="store_true",
                      help="with --choose-path: click the rule and Historical AI Focuses off into "
                           "the new-game lobby before starting (needs calibration; see "
                           "docs/campaign.md)")
    play.add_argument("--lobby-scan", dest="lobby_scan", action="store_true",
                      help="with --choose-path: set the rule by reading the Custom Game Rules "
                           "window (OCR: collapse sections, find the row, pick the option) "
                           "instead of recorded points (#160; implies --lobby-rule)")
    play.add_argument("--lobby-calibrate", dest="lobby_calibrate", action="store_true",
                      help="read the open Custom Game Rules window once, log every line, click "
                           "nothing and stop (open the window by hand first; #160)")
    play.add_argument("--control", metavar="game_ai|llm|economy|sovereign|DOMAIN=CONTROLLER,...",
                      help="campaign: who plays the country. game_ai (default): the game's AI "
                           "runs it, the model steers. llm: independent mode, the model plays "
                           "it itself with the hands-on actions (not live-verified; "
                           "docs/campaign.md). economy: the model runs research, production "
                           "and construction by hand. sovereign: the model owns the country "
                           "and the game's AI runs only the armies it gives a front "
                           "(docs/modular-control.md). Or a control map, domain=controller pairs "
                           "(focus=llm,research=script,army=game; controllers llm, script, game, "
                           "off; docs/modular-control.md)")
    play.add_argument("--campaign-input", dest="campaign_input", choices=["mouse", "none"],
                      help="campaign --control llm: mouse (default) adds research and army "
                           "tools the harness performs with the mouse, while the game window is "
                           "in front; none keeps the keyboard-and-console run")
    play.add_argument("--no-human-ai", dest="no_human_ai", action="store_true",
                      help="campaign: do not hand the country to the game's AI")
    play.add_argument("--no-verify-ai", dest="no_verify_ai", action="store_true",
                      help="campaign: carry on when the state line still reads ai=0 after human_ai "
                           "(only if you can see the AI running the country yourself)")
    play.add_argument("--rival", metavar="TAG[,TAG...]",
                      help="single player: steer this AI country (the game's AI runs it) while "
                           "you play another; acts only in your pauses; several at once as "
                           "FRA,ENG,SOV, or 'majors' for the 1936 great powers minus yours; "
                           "see docs/rival.md")
    play.add_argument("--rival-share", dest="rival_share", action="append", metavar="TAG=WEIGHT",
                      help="several rivals: this country's weight in the split of --max-usd "
                           "(default 1 each, an equal split); repeat per country")
    play.add_argument("--rival-window-cap", dest="rival_window_cap", type=int, metavar="N",
                      help="several rivals: the most actions one window types across all of "
                           "them, taken in turn (default 12)")
    play.add_argument("--rival-act-when", dest="rival_act_when",
                      choices=["paused", "handover", "never", "inbox", "idle"],
                      help="rival: when the queue may be typed: paused (you paused and left the "
                           "keyboard alone), handover (only when you run `hoi4-harness handover`), "
                           "never (shown, not run)")
    play.add_argument("--rival-paused-seconds", dest="rival_paused_seconds", type=float,
                      metavar="N", help="rival, paused: the in-game date must have stood still "
                                        "this long (default 10)")
    play.add_argument("--rival-idle-seconds", dest="rival_idle_seconds", type=float, metavar="N",
                      help="rival, paused: no keyboard or mouse input for this long (default 5)")
    play.add_argument("--rival-start-wait", dest="rival_start_wait", type=float, metavar="MINUTES",
                      help="rival: how long the start waits for a window to mark the country "
                           "(default 30)")
    play.add_argument("--rival-exit-wait", dest="rival_exit_wait", type=float, metavar="SECONDS",
                      help="rival: how long the exit waits for a window to unmark it (default 60)")
    play.add_argument("--fake-human-script", dest="fake_human_script", metavar="PATH",
                      help="with --fake-game --rival: a JSON script of what the fake person does "
                           "([[seconds, \"pause\"], ...]; default: play 45 s, pause 90 s)")
    play.add_argument("--quiet", action="store_true",
                      help="no per-turn progress line on stderr")
    play.add_argument("--confirm", action="store_true",
                      help="ask a person before every irreversible action (needs --live)")
    play.set_defaults(func=cmd_play)

    campaign_check = sub.add_parser(
        "campaign-check",
        help="before leaving a campaign overnight: check the mod, settings, disk, key, budget and "
             "power plan without touching the game (exit 1 on any FAIL)")
    campaign_check.add_argument("--profile", required=True, help="the campaign profile to check")
    campaign_check.add_argument("--max-usd", type=float, help="the cap you will run with")
    campaign_check.add_argument("--user-dir", dest="user_dir", metavar="PATH",
                                help="the Hearts of Iron IV user directory (default: found next to "
                                     "HOI4_SAVE_DIR, else searched for)")
    campaign_check.set_defaults(func=cmd_campaign_check)

    guide = sub.add_parser(
        "guide",
        help="give a running campaign guidance (advice the model weighs; it is woken for it and "
             "sees it in every brief until cleared), or --list / --clear / --expire it")
    guide.add_argument("--run-dir", dest="run_dir", required=True, metavar="PATH",
                       help="the campaign's run directory")
    guide.add_argument("text", nargs="*", help="the guidance, e.g. \"Take Poland before France\"")
    guide.add_argument("--list", action="store_true", help="show the standing guidance")
    guide.add_argument("--clear", action="store_true", help="remove all standing guidance")
    guide.add_argument("--remove", type=int, action="append", metavar="ID",
                       help="remove one item by its number (repeatable)")
    guide.add_argument("--expire", type=float, metavar="DAYS",
                       help="remove items older than DAYS days")
    guide.set_defaults(func=cmd_guide)

    domains = sub.add_parser(
        "domains",
        help="co-op: take a domain back from the model or hand it over, mid-game "
             "(--take army, --give army; no flags lists what moved)")
    domains.add_argument("--run-dir", dest="run_dir", required=True, metavar="PATH",
                         help="the co-op run's directory")
    domains.add_argument("--take", action="append", default=[], metavar="DOMAIN",
                         help="you hold DOMAIN from now on (or all); the model stops acting there")
    domains.add_argument("--give", action="append", default=[], metavar="DOMAIN",
                         help="the model holds DOMAIN from now on (or all)")
    domains.set_defaults(func=cmd_domains)

    chat = sub.add_parser(
        "chat", help="talk to the LLM countries in a single-player game: send a message, show the "
                     "chat, or follow it (docs/chat.md)")
    chat.add_argument("text", nargs="*", help='the message, e.g. "Poland, join me against the Soviets"')
    chat.add_argument("--to", default="global", metavar="TAG|global",
                      help="a country tag for a direct message (default: global, everyone)")
    chat.add_argument("--last", type=int, default=20, metavar="N", help="messages to show (20)")
    chat.add_argument("--follow", "-f", action="store_true", help="keep showing new messages")
    chat.add_argument("--chat", metavar="PATH", help="the chat file (default ~/.hoi4-harness/chat.jsonl "
                                                     "or HOI4_CHAT)")
    chat.set_defaults(func=cmd_chat)

    report = sub.add_parser(
        "campaign-report", help="write the markdown report of a campaign run directory")
    report.add_argument("--run-dir", dest="run_dir", required=True, metavar="PATH")
    report.add_argument("--out", metavar="PATH", help="default: <run-dir>/campaign-report.md")
    report.add_argument("--print", dest="print_report", action="store_true",
                        help="print the report instead of only writing it")
    report.add_argument("--budget", action="store_true",
                        help="print the performance budget instead: wall-clock minutes per game "
                             "month, split into model, game and overhead time, per game year "
                             "(--out also writes it)")
    report.set_defaults(func=cmd_campaign_report)

    check = sub.add_parser(
        "headless-check",
        help="try each claim headless play rests on (console clock, refresh) and say which hold")
    common(check)
    check.add_argument("--days", type=int, help="days for the pause_in_hours check (default 7)")
    check.set_defaults(func=cmd_headless_check)

    fakegame = sub.add_parser(
        "fakegame", help="run a fake HOI4 that speaks the mod's log, run file and keys")
    fakegame.add_argument("--dir", default="runs/fakegame", metavar="PATH",
                          help="where it keeps its files (default runs/fakegame)")
    fakegame.add_argument("--country", default="SWE")
    fakegame.add_argument("--seed", type=int)
    fakegame.add_argument("--start-date", dest="start_date")
    fakegame.add_argument("--game-dir", dest="game_dir", metavar="PATH",
                          help="use an install's content instead of the built-in demo playset")
    fakegame.add_argument("--time-scale", dest="time_scale", type=float, default=1.0,
                          help="game seconds per wall second multiplier (default 1.0)")
    fakegame.set_defaults(func=cmd_fakegame)

    replay = sub.add_parser("replay", help="replay one turn from a transcript and compare models")
    replay.add_argument("transcript", metavar="PATH", help="a transcript.jsonl written by play or eval")
    replay.add_argument("--turn", type=int, help="which planner turn to replay (see --list)")
    replay.add_argument("--list", dest="list_turns", action="store_true",
                        help="show the turns in the transcript, then exit")
    common(replay)
    replay.set_defaults(func=cmd_replay)

    native = sub.add_parser(
        "native", help="you play; an LLM plays another country through the inbox, with no "
                       "keyboard, console or focus (docs/inbox.md)")
    common(native)
    _run_options(native)
    native.add_argument("--rival", metavar="TAG[,TAG...]",
                        help="the country (or countries) the LLM plays (or give --setup)")
    native.add_argument("--my-country", dest="my_country", metavar="TAG",
                        help="the country you play: refused as an LLM country, and labelled in chat-gui")
    native.add_argument("--keep-running", dest="keep_running", action="store_true",
                        help="when the game quits, wait for the next start and play again")
    native.add_argument("--launch", action="store_true",
                        help="start hoi4.exe with -debug_mode if it is not running (needs --game-dir)")
    native.add_argument("--no-debug-check", dest="no_debug_check", action="store_true",
                        help="play even when the game seems to run without -debug_mode")
    native.add_argument("--until", metavar="YYYY-MM-DD", help="stop at this in-game date")
    native.add_argument("--rival-act-when", dest="rival_act_when",
                        choices=["paused", "handover", "never", "inbox", "idle"],
                        help="when orders are typed: paused (your pauses; default with keys), idle "
                             "(whenever the keyboard is idle: unattended runs), inbox (default with "
                             "--transport inbox)")
    native.add_argument("--breakaway", action="store_true",
                        help="--rival TAG is the country a civil war splits: play the side that "
                             "breaks away from it (a new D01-D50 tag), once it exists "
                             "(docs/late-countries.md)")
    native.add_argument("--breakaway-ideology", dest="breakaway_ideology", metavar="IDEOLOGY",
                        help="with --breakaway: the side whose ruling ideology matches (fascism, "
                             "communism, democratic, neutrality)")
    native.add_argument("--wait-for-country", dest="wait_for_country", action="store_true",
                        help="the rival does not exist yet (released later): wait for it with no "
                             "deadline")
    native.add_argument("--quiet", action="store_true", help="no per-turn progress line on stderr")
    native.set_defaults(func=cmd_native)

    start = sub.add_parser(
        "start", help="run what the setup window runs: `start --setup FILE` is `native` with the "
                      "countries, models and instructions read from the file (docs/setup.md)")
    common(start)
    _run_options(start)
    start.add_argument("--rival", metavar="TAG[,TAG...]", help="only these countries of the file")
    start.add_argument("--my-country", dest="my_country", metavar="TAG", help=argparse.SUPPRESS)
    for flag in ("--keep-running", "--launch", "--no-debug-check"):
        start.add_argument(flag, dest=flag[2:].replace("-", "_"), action="store_true",
                           help="as for native")
    start.add_argument("--until", metavar="YYYY-MM-DD", help="stop at this in-game date")
    start.add_argument("--quiet", action="store_true", help="no per-turn progress line on stderr")
    start.set_defaults(func=cmd_start)

    sub.add_parser("setup", help="open the setup window: who plays which country, with which "
                                 "model (docs/setup.md)").set_defaults(func=cmd_setup)
    cgui = sub.add_parser("chat-gui", help="open the chat window: talk to the LLM countries "
                                           "(docs/chat.md)")
    cgui.add_argument("--chat", metavar="PATH", help="the chat file (default ~/.hoi4-harness/chat.jsonl)")
    cgui.set_defaults(func=cmd_chat_gui)

    inbox_p = sub.add_parser("inbox", help="the inbox transport: what is posted, and a live probe")
    inbox_p.add_argument("verb", choices=["status", "probe", "clear"])
    inbox_p.add_argument("--inbox-dir", dest="inbox_dir", metavar="PATH",
                         help="the installed llm_bridge mod folder (default: found)")
    inbox_p.add_argument("--log", dest="log", metavar="PATH", help="game.log (default: found)")
    inbox_p.add_argument("--timeout", type=float, default=120.0,
                         help="probe: seconds to wait for the game to acknowledge (default 120)")
    inbox_p.set_defaults(func=cmd_inbox)

    mailbox = sub.add_parser("mailbox", help="answer a --mailbox run's requests (the agent side)")
    mailbox.add_argument("verb", choices=["wait", "show", "system", "answer"])
    mailbox.add_argument("--dir", required=True, help="the run's mailbox folder")
    mailbox.add_argument("--timeout", type=float, default=None, help="wait: seconds before giving up")
    mailbox.add_argument("--file", help="answer: a JSON file {text, calls: [{name, arguments}]}")
    mailbox.add_argument("--json", help="answer: the same JSON inline")
    mailbox.add_argument("--force", action="store_true",
                         help="answer: hand over a --file even if it looks like the previous answer")
    mailbox.set_defaults(func=cmd_mailbox)

    evaluate = sub.add_parser("eval", help="run a scenario and score it")
    common(evaluate)
    evaluate.add_argument("scenario", nargs="?")
    evaluate.add_argument("--strict", action="store_true", help="exit 1 unless every objective passes")
    evaluate.add_argument("--write-baseline", dest="write_baseline", action="store_true",
                          help="record the reflex-only scores as the committed baseline (needs --no-llm)")
    evaluate.add_argument("--seeds", type=int, default=1, metavar="N",
                          help="run each scenario at N seeds and report median and range")
    evaluate.set_defaults(func=cmd_eval)

    compare = sub.add_parser(
        "compare", help="run one scenario across models, score next to spend"
    )
    common(compare)
    compare.add_argument("scenario")
    compare.add_argument(
        "--models", metavar="P:M[,P:M...]",
        help="provider:model entries to race, e.g. anthropic:claude-opus-5,openai:qwen2.5:14b "
             "(default: the configured planner)",
    )
    compare.add_argument("--days", type=int, help="in-game days per turn")
    compare.add_argument("--seeds", type=int, default=1, metavar="N",
                         help="N seeds per model; rows report median and range")
    compare.set_defaults(func=cmd_compare)

    experiment = sub.add_parser(
        "experiment", help="one scenario three ways: model-only, AI-only, hybrid"
    )
    common(experiment)
    experiment.add_argument("scenario")
    experiment.add_argument("--seeds", type=int, default=5, metavar="N",
                            help="seeds per arm (default 5)")
    experiment.add_argument("--markdown", metavar="PATH",
                            help="also write the results as a markdown table to PATH")
    experiment.set_defaults(func=cmd_experiment)

    measure_p = sub.add_parser(
        "measure", help="brief size, wake rate and cache hits, peacetime vs wartime"
    )
    common(measure_p)
    measure_p.add_argument("transcript", nargs="?", metavar="PATH",
                           help="a transcript.jsonl to measure")
    measure_p.add_argument("--campaign", metavar="NAME",
                           help="play a long measurement campaign first, then measure it "
                                "(peacetime_1936_1939, wartime_1939_1941)")
    measure_p.set_defaults(func=cmd_measure)

    from .memory_probe import VARIANTS as PROBE_VARIANTS

    probe_mem = sub.add_parser(
        "memory-probe", help="record hoi4.exe's private bytes every minute and fit GB/hour (#157)")
    probe_mem.add_argument("--save", metavar="NAME", help="the save the game was loaded from (a label)")
    probe_mem.add_argument("--minutes", type=float, default=30.0, help="how long to sample (default 30)")
    probe_mem.add_argument("--variant", choices=list(PROBE_VARIANTS), help="the setup being measured")
    probe_mem.add_argument("--label", help="extra words for the file names, e.g. nomod-nodebug")
    probe_mem.add_argument("--interval", type=float, default=60.0, help="seconds between samples")
    probe_mem.add_argument("--out", default="memory-probe-results", help="folder for the CSV and summary")
    probe_mem.add_argument("--process", default="hoi4.exe", help="process image name")
    probe_mem.add_argument("--log", metavar="PATH", help="game.log, for the in-game date column "
                                                          "(default HOI4_LOG_PATH)")
    probe_mem.add_argument("--steps-only", action="store_true", help="print the setup steps and stop")
    probe_mem.add_argument("--compare", metavar="DIR", help="table of every summary in DIR, then exit")
    probe_mem.set_defaults(func=cmd_memory_probe)

    reconcile_p = sub.add_parser(
        "reconcile", help="compare a run's token log with a provider usage export (#23)")
    reconcile_p.add_argument("transcript", metavar="TRANSCRIPT", help="the run's transcript.jsonl")
    reconcile_p.add_argument("export", metavar="EXPORT",
                             help="usage export: Anthropic usage_report JSON, OpenAI usage JSON, or the CSV "
                                  "documented in eval/reconcile.py")
    reconcile_p.add_argument("--format", choices=["auto", "csv", "json", "anthropic", "openai"],
                             default="auto")
    reconcile_p.add_argument("--tolerance", type=float, default=0.03,
                             help="allowed relative difference, as a fraction of the export (default 0.03)")
    reconcile_p.add_argument("--by", choices=["model", "day"], default="model",
                             help="one block per model (default), or one row per model and UTC day")
    reconcile_p.add_argument("--rate", action="append", metavar="MODEL:input=..,output=..",
                             help="dollars per million tokens for MODEL (or * for all); repeatable. "
                                  "Without it only tokens are compared")
    reconcile_p.set_defaults(func=cmd_reconcile)

    provider_p = sub.add_parser(
        "provider-check", help="run the provider conformance suite against live keys (#22)")
    provider_p.add_argument("--provider", action="append",
                            choices=["anthropic", "openai", "google", "claude-cli"],
                            help="only this provider (repeatable); default: every one with a key")
    provider_p.add_argument("--max-calls", type=int, metavar="N",
                            help="hard cap on live calls (default 12)")
    provider_p.add_argument("--plan", action="store_true",
                            help="print the calls and worst-case cost, send nothing")
    provider_p.set_defaults(func=cmd_provider_check)

    handover = sub.add_parser("handover", help="hand the keyboard to a --handover session")
    common(handover)
    handover.add_argument("--release", action="store_true",
                          help="take control back; queued actions stay queued")
    handover.set_defaults(func=cmd_handover)

    verify_bridge = sub.add_parser(
        "verify-bridge", help="check the mod's tokens against the install and a game.log"
    )
    common(verify_bridge)
    verify_bridge.add_argument("--log", dest="log_path_arg", metavar="PATH",
                               help="game.log to check (default: HOI4_LOG_PATH or discovery)")
    verify_bridge.set_defaults(func=cmd_verify_bridge)

    save_inspect = sub.add_parser(
        "save-inspect", help="list what a save contains, for mapping it to game state"
    )
    common(save_inspect)
    save_inspect.add_argument("save", nargs="?", metavar="PATH",
                              help="a .hoi4 text save (default: the newest in the save folder)")
    save_inspect.set_defaults(func=cmd_save_inspect)

    mod_actions = sub.add_parser(
        "mod-actions", help="what a country can do in the playset: decisions, focuses, new techs")
    common(mod_actions)
    mod_actions.add_argument("--json", action="store_true", help="the whole surface as JSON")
    mod_actions.add_argument("--all", action="store_true", help="no cap on the lists")
    mod_actions.add_argument("--limit", type=int, default=25, help="rows per list (default 25)")
    mod_actions.add_argument("--offline", action="store_true",
                             help="never fetch a Workshop page: cache and local files only")
    mod_actions.set_defaults(func=cmd_mod_actions)

    dlc_cmd = sub.add_parser("dlc", help="which DLCs are installed, disabled and active, and what "
                                         "each enables")
    common(dlc_cmd)
    dlc_cmd.set_defaults(func=cmd_dlc)

    index = sub.add_parser("index", help="list the countries and focuses a playset defines")
    common(index)
    # --country (from the common flags) picks the country whose tree is shown.
    index.add_argument("--all", action="store_true", help="with --country: list every focus")
    index.set_defaults(func=cmd_index)

    paths_cmd = sub.add_parser("paths", help="the paths open to a country: its AI behaviour rule "
                                             "options and focus tree branches (#160)")
    common(paths_cmd)
    paths_cmd.add_argument("--json", action="store_true", help="machine-readable output")
    paths_cmd.set_defaults(func=cmd_paths)

    brief_cmd = sub.add_parser("brief-world", help="the briefing agent: a dossier of the world and "
                                                   "the country, and an orientation from the model (#165)")
    common(brief_cmd)
    brief_cmd.add_argument("--save", metavar="PATH",
                           help="the save to read (default: the country's newest)")
    brief_cmd.add_argument("--out", metavar="DIR",
                           help="where briefing.md and briefings/ go (default: the run dir)")
    brief_cmd.add_argument("--web", action="store_true",
                           help="let the model search the web for mods it cannot judge from files "
                                "(anthropic and claude-cli only)")
    brief_cmd.add_argument("--dossier-only", dest="dossier_only", action="store_true",
                           help="print the dossier and call no model")
    brief_cmd.set_defaults(func=cmd_brief_world)

    mapcmd = sub.add_parser("map", help="where a state or province is, in map pixels")
    common(mapcmd)
    mapcmd.add_argument("--state", type=int, help="a state id")
    mapcmd.add_argument("--province", type=int, help="a province id")
    mapcmd.add_argument("--cache-dir", dest="cache_dir", metavar="PATH",
                        help="where the parsed map is cached (default: the user cache folder)")
    mapcmd.add_argument("--no-cache", dest="no_cache", action="store_true",
                        help="read the picture every time")
    mapcmd.set_defaults(func=cmd_map)

    moddir = sub.add_parser(
        "mod-directives", help="regenerate the mod's per-target directive blocks"
    )
    moddir.add_argument("--tags", metavar="GER,ENG,...",
                        help="country tags to generate for (default: the vanilla majors and more)")
    moddir.add_argument("--mod-dir", dest="mod_dir", default="mod/llm_bridge",
                        help="the llm_bridge mod folder (default: mod/llm_bridge)")
    moddir.add_argument("--check", action="store_true",
                        help="exit 1 if the committed files differ from what would be generated")
    moddir.set_defaults(func=cmd_mod_directives)

    probe = sub.add_parser(
        "mod-probe", help="regenerate the mod's focus/research probe from the game install"
    )
    common(probe)
    probe.add_argument("--mod-dir", dest="mod_dir", default="mod/llm_bridge",
                       help="the llm_bridge mod folder (default: mod/llm_bridge)")
    probe.set_defaults(func=cmd_mod_probe)

    check_scripts = sub.add_parser(
        "check-scripts", help="pre-flight the input driver's scripts and calibration"
    )
    common(check_scripts)
    check_scripts.set_defaults(func=cmd_check_scripts)

    tactics = sub.add_parser("tactics", help="run the tactical simulator and its baseline controllers")
    tsub = tactics.add_subparsers(dest="tactics_command", required=True)
    tsub.add_parser("list", help="list the scenarios").set_defaults(func=cmd_tactics)
    for verb, helptext in (("run", "play a scenario over several seeds and summarise"),
                           ("episode", "play one seeded episode and print its metrics")):
        tp = tsub.add_parser(verb, help=helptext)
        tp.add_argument("scenario", help="scenario name, or 'all' (run only)")
        tp.add_argument("--controller", choices=["vanilla", "scripted", "learned", "planner", "planner_full",
                                 "learned_plans", "vanilla_plans", "both", "all"],
                        default="scripted", help="'both' = vanilla + scripted, 'all' adds learned")
        tp.add_argument("--params", help="learned-controller parameters (default: the committed file)")
        tp.add_argument("--opponent", choices=["vanilla", "scripted"], default="vanilla",
                        help="who plays the other side (default: the vanilla-like AI)")
        tp.add_argument("--seeds", type=int, default=20)
        tp.add_argument("--first-seed", dest="first_seed", type=int, default=0)
        tp.add_argument("--seed", type=int, default=0, help="the seed for 'episode'")
        tp.add_argument("--json", action="store_true", help="machine-readable output")
        tp.set_defaults(func=cmd_tactics)

    trp = tsub.add_parser("train", help="train the learned controller (behaviour cloning, then ES)")
    trp.add_argument("scenario", nargs="?", default="all", help="one scenario, or 'all' (default)")
    trp.add_argument("--minutes", type=float, default=12.0, help="wall-clock budget (default 12)")
    trp.add_argument("--seed", type=int, default=0)
    trp.add_argument("--out", help="where to write the parameters (default: the committed file)")
    trp.set_defaults(func=cmd_tactics)

    tpp = tsub.add_parser("train-plans", help="train the learned plan chooser (distillation from the "
                          "full-information planner, then ES)")
    tpp.add_argument("--minutes", type=float, default=18.0, help="wall-clock budget (default 18)")
    tpp.add_argument("--seed", type=int, default=0)
    tpp.add_argument("--out", help="where to write the parameters (default: the committed file)")
    tpp.set_defaults(func=cmd_tactics)

    calibrate = sub.add_parser("calibrate", help="record screen coordinates for the input driver")
    common(calibrate)
    calibrate.add_argument("targets", nargs="*", metavar="TARGET",
                           help="targets to record (default: " + ", ".join(DEFAULT_TARGETS) + ")")
    calibrate.add_argument("--out", metavar="PATH",
                           help=f"where to write (default: <run-dir>/{DEFAULT_FILENAME})")
    calibrate.set_defaults(func=cmd_calibrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ControlError as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return 2
    except SkillError as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return 2
    except PlaysetMismatch as exc:
        # A scenario run against the wrong playset is a usage error, not a crash.
        print(f"{args.command}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
