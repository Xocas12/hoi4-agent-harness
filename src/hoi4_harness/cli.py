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
import sys
from contextlib import nullcontext
from pathlib import Path

from . import __version__
from .actions import catalog
from .adapters import ADAPTERS, build_adapter
from .agent.llm import PROVIDERS, build_llm
from .agent.loop import AgentLoop
from .agent.memory import Memory
from .calibration import DEFAULT_FILENAME, DEFAULT_TARGETS
from .config import HarnessConfig
from .confirm import TerminalConfirmer
from .control import ControlError
from .env import HOI4Env
from .eval.runner import PlaysetMismatch
from .guidance import available as guidance_packs
from .keepawake import keep_awake_for
from .replay import ReplayError, list_turns, replay_turn


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


def _config_from_args(args: argparse.Namespace) -> HarnessConfig:
    # Precedence, lowest first: defaults -> environment -> profile file -> flags.
    profile = getattr(args, "profile", None)
    config = HarnessConfig.from_file(profile) if profile else HarnessConfig.from_env()
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
    spec = getattr(args, "control", None)
    if spec:
        if "=" in spec:
            config.control = spec            # a control map: --control focus=llm,research=script
        elif spec in ("game_ai", "llm"):
            config.campaign_control = spec
        else:
            raise SystemExit(f"play: --control wants game_ai, llm or domain=controller pairs "
                             f"(focus=llm,research=script), not {spec!r}")
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

    try:
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
        env.reset()
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"play: {exc}", file=sys.stderr)
        return 1
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
        loop.supervisor = CoopGuidance(run_dir, loop)
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
    seq, problems = mb.write_answer(directory, answer)
    if problems:
        print("REFUSED: " + "; ".join(problems))
        return 1
    print(f"answered request {seq}")
    return 0


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

    observe = sub.add_parser("observe", help="print one situation brief")
    common(observe)
    observe.set_defaults(func=cmd_observe)

    play = sub.add_parser("play", help="run the agent loop")
    common(play)
    play.add_argument("--turns", type=int)
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
    play.add_argument("--control", metavar="game_ai|llm|DOMAIN=CONTROLLER,...",
                      help="campaign: who plays the country. game_ai (default): the game's AI "
                           "runs it, the model steers. llm: independent mode, the model plays "
                           "it itself with the hands-on actions (not live-verified; "
                           "docs/campaign.md). Or a control map, domain=controller pairs "
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
    play.add_argument("--rival-act-when", dest="rival_act_when", choices=["paused", "handover", "never"],
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

    report = sub.add_parser(
        "campaign-report", help="write the markdown report of a campaign run directory")
    report.add_argument("--run-dir", dest="run_dir", required=True, metavar="PATH")
    report.add_argument("--out", metavar="PATH", help="default: <run-dir>/campaign-report.md")
    report.add_argument("--print", dest="print_report", action="store_true",
                        help="print the report instead of only writing it")
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

    mailbox = sub.add_parser("mailbox", help="answer a --mailbox run's requests (the agent side)")
    mailbox.add_argument("verb", choices=["wait", "show", "system", "answer"])
    mailbox.add_argument("--dir", required=True, help="the run's mailbox folder")
    mailbox.add_argument("--timeout", type=float, default=None, help="wait: seconds before giving up")
    mailbox.add_argument("--file", help="answer: a JSON file {text, calls: [{name, arguments}]}")
    mailbox.add_argument("--json", help="answer: the same JSON inline")
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

    index = sub.add_parser("index", help="list the countries and focuses a playset defines")
    common(index)
    # --country (from the common flags) picks the country whose tree is shown.
    index.add_argument("--all", action="store_true", help="with --country: list every focus")
    index.set_defaults(func=cmd_index)

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
    except PlaysetMismatch as exc:
        # A scenario run against the wrong playset is a usage error, not a crash.
        print(f"{args.command}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
