"""Adapter registry."""

from __future__ import annotations

from pathlib import Path

from ..config import HarnessConfig
from .base import AdapterInfo, GameAdapter
from .composite import CompositeAdapter
from .mock import MockAdapter

__all__ = [
    "ADAPTERS",
    "AdapterInfo",
    "CompositeAdapter",
    "GameAdapter",
    "MockAdapter",
    "build_adapter",
]

ADAPTERS = (
    "mock",
    "savegame",
    "screen",
    "logtail",
    "logtail+input",
    "savegame+input",
    "screen+input",
    "headless",
)


def build_adapter(config: HarnessConfig) -> GameAdapter:
    """Construct the adapter named by ``config.adapter``.

    Heavier adapters are imported lazily, so the mock path -- what the tests and
    the offline demo use -- never pulls an optional dependency.
    """
    name = config.adapter.strip().lower()
    if name == "mock":
        return MockAdapter(
            seed=config.seed, country=config.country or "SWE", start=config.start_date
        )
    if name == "headless":
        from .headless import build_headless

        return build_headless(config)
    if name == "savegame":
        from .savegame import SaveGameAdapter

        return SaveGameAdapter(config.save_dir)
    if name == "screen":
        from .screen import ScreenAdapter

        return ScreenAdapter(config.window_title)
    if name == "logtail":
        from .logtail import LogTailAdapter

        return LogTailAdapter(config.log_path, country=config.country)
    if "+" in name:
        read_name, _ = name.split("+", 1)
        from .input_driver import InputConfig, InputDriverAdapter

        reader = build_adapter(
            HarnessConfig(
                adapter=read_name,
                save_dir=config.save_dir,
                log_path=config.log_path,
                country=config.country,
                window_title=config.window_title,
            )
        )
        from .ui_scripts import DEFAULT_FILENAME as SCRIPTS_FILENAME
        from .ui_scripts import load_scripts

        scripts_path = config.ui_scripts_path or config.run_dir / SCRIPTS_FILENAME
        scripts = load_scripts(scripts_path) if Path(scripts_path).exists() else None
        writer = InputDriverAdapter(
            InputConfig(
                window_title=config.window_title,
                enforce_focus=config.enforce_window_focus,
                clock_probe_seconds=config.clock_probe_seconds,
            ),
            dry_run=config.dry_run,
            scripts=scripts,
            # Localised names and tech positions for the click paths; None without
            # a --game-dir, in which case a script that needs them refuses.
            index=config.build_index() if scripts else None,
        )
        calibration = config.calibration_path or config.run_dir / "calibration.json"
        if Path(calibration).exists():
            writer.load_calibration(Path(calibration))
        router = _with_console(writer, config)
        ledger = getattr(router, "order_ledger", None)
        if ledger is not None:
            from .unit_orders import OrdersComposite, state_names

            return OrdersComposite(
                reader, router, ledger, state_names(router.order_layer, config.build_index()),
                clock_owner=config.clock_owner)
        return CompositeAdapter(reader, router, clock_owner=config.clock_owner)
    raise ValueError(f"Unknown adapter {name!r}. Known: {', '.join(ADAPTERS)}")


def build_inbox(config: HarnessConfig):
    """The inbox for ``config`` (inbox.py), its empty files written if the mod has none."""
    from .. import paths
    from ..inbox import Inbox, default_mod_dir
    from .logtail import find_log

    mod_dir = config.inbox_dir or default_mod_dir(paths.find_game_dir())
    if mod_dir is None:
        raise ValueError("the inbox needs the installed mod's folder: set inbox_dir "
                         "(<Documents>/Paradox Interactive/Hearts of Iron IV/mod/llm_bridge)")
    inbox = Inbox(Path(mod_dir), log_path=config.log_path or find_log(),
                  effect_command=config.console_effect_command)
    inbox.ensure()
    return inbox


def _with_console(driver, config: HarnessConfig):
    """Wrap the input driver in the per-action channel router (#83)."""
    from .channels import ChannelRouter, resolve_channels
    from .console import ConsoleChannel, ConsoleConfig

    channels = resolve_channels(config.action_channels)
    console = ConsoleChannel(
        driver,
        ConsoleConfig(
            directory=config.console_dir,
            filename=config.console_filename,
            open_key=config.console_key,
            close_key=config.console_key,
            effect_command=config.console_effect_command,
            hybrid_via=config.console_hybrid_via,
            guards=config.console_guards,
            archive_dir=Path(config.run_dir),
            country=config.country,
            refresh=bool(config.console_refresh), refresh_front=bool(config.console_refresh),
        ),
        index=config.build_index(),
    )
    if getattr(config, "console_transport", "keys") == "inbox":
        console.inbox = build_inbox(config)
        console.config.inbox_ack_seconds = config.inbox_ack_seconds
    if config.unit_orders:
        from .unit_orders import enable_from_config

        enable_from_config(driver, console, config)
    return ChannelRouter(driver, console, channels)
