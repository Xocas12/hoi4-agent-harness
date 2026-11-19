"""Headless play: the game read from its log, driven by the console and the keyboard.

Nothing in this path touches the mouse or the screen. The reader is the mod's
telemetry in ``game.log`` (``logtail``) -- or a text save -- the writer is the
console channel (``run llmb_turn.txt``) plus a keyboard script for the one action
the console has no faithful effect for (a national focus: alt+q, ctrl+f, the
name, shift+enter), and the clock is the console's ``gamespeed`` with the space
key as a fallback. A person can leave the game in a window that is not even
visible to the harness, as long as it has the keyboard when keys are sent.

What that costs is the actions that only exist as clicks. ``start_research``
needs the tech tree's grid and ``delegate_army_to_ai`` has no effect at all, so
both are removed from the tool list and the system prompt says why
(``docs/headless.md`` lists what was tried instead). ``build_headless`` also
accepts a ``FakeGame`` (``fakegame.py``) to send its keys and read its log, which
runs the whole stack with no game.

Telemetry is read at day granularity and a paused game logs nothing, so by
default every console run ends by asking the mod for two fresh ``state`` lines
(``ConsoleConfig.refresh``): that is what lets an action be verified while the
clock is stopped. A focus started from the keyboard is the exception, because
the probe only reports a focus once it has progress, which takes a day; its
result stays "unverified" until the clock has run.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from ..config import HarnessConfig
from ..types import ActionCall, ActionResult, GameState
from .base import AdapterInfo, GameAdapter
from .channels import ChannelRouter, resolve_channels
from .composite import CompositeAdapter
from .console import CONSOLE_ACTIONS, ConsoleChannel, ConsoleConfig
from .input_driver import ClockUnobservable, InputConfig, InputDriverAdapter
from .keys import ScanCodeKeys
from .ui_scripts import DEFAULT_FILENAME, Step, load_scripts, parse_steps
from .window import FocusCheck

#: Actions with no path that avoids the mouse. Removed from the tool list.
MOUSE_ACTIONS = frozenset({
    "start_research", "delegate_army_to_ai",
    # Division orders are a selection and a right-click (adapters/unit_orders.py).
    "move_division", "attack_province",
})
#: Actions the log cannot make safe. add_equipment_production adds a second line
#: for equipment that already has one (seen live on 1.19.5), and only a reader
#: that sees production -- a text save -- can check that first.
NEEDS_PRODUCTION_READER = frozenset({"set_production"})
#: The readers that see production.
PRODUCTION_READERS = ("log+save", "savegame")


def excluded_actions(reader: str = "log+save") -> frozenset[str]:
    """What a headless run with this reader does not offer."""
    return MOUSE_ACTIONS | (NEEDS_PRODUCTION_READER if reader not in PRODUCTION_READERS
                            else frozenset())
CLOCK_MODES = ("auto", "console", "keys", "exact")
#: ``pause_in_hours`` is told this many hours past the days asked for, so the game
#: cannot stop before the last day's state line whatever hour the daily tick runs at.
EXACT_MARGIN_HOURS = 12
READERS = ("log+save", "logtail", "savegame")

#: The one keyboard script headless ships: verified live on 1.19.5.
FOCUS_SCRIPT = [
    {"press": "national_focus"}, {"wait": 0.8}, {"press": "ctrl+f"}, {"wait": 0.3},
    {"type": "{focus_name}"}, {"wait": 0.6}, {"press": "shift+enter"},
]


def keyboard_only(scripts: dict[str, list[Step]]) -> tuple[dict[str, list[Step]], list[str]]:
    """The scripts that never click, and the names of the ones dropped for it."""
    kept = {name: steps for name, steps in scripts.items()
            if not any(step.kind == "click" for step in steps)}
    return kept, sorted(set(scripts) - set(kept))


def unavailable_actions(reader: str = "log+save") -> list[str]:
    """What a headless run cannot offer, for the prompt and the docs."""
    return sorted(excluded_actions(reader))


def _scaled_sleep(scale: float) -> Callable[[float], None]:
    """The real clock, optionally sped up (``headless_time_scale``, for tests)."""
    def sleep(seconds: float) -> None:
        time.sleep(seconds * scale)
    return sleep


class HeadlessClock:
    """Pause, resume and speed without the mouse, each proved by the date.

    The console's ``gamespeed N`` sets a speed outright (0 pauses), which the
    space key cannot do: it toggles, and nothing reads the speed back. The harness
    still never takes either on trust: after a command the date is watched, and
    a game that did not do what was asked is an error, not a belief.

    ``auto`` resumes through the console (exact speed, no toggle to get wrong; the
    game is paused while the keys are typed) and pauses with the space key (one
    press, so the game stops at once rather than two seconds into typing), each
    falling back to the other path when the date shows it did not take. ``console``
    and ``keys`` use one path for both.

    ``exact`` is ``auto`` for a turn the harness knows the length of: it hands the
    game ``pause_in_hours`` with the days and lets it stop itself, so the turn is
    N days with no overshoot and no key racing the clock. (Documented in
    console_commands_documentation.md; not yet seen in a live game.) The harness
    then looks at the date before touching the pause key, since pressing it on a
    game that has stopped would start it.
    """

    def __init__(self, driver: InputDriverAdapter, console: ConsoleChannel,
                 mode: str = "auto", speed: int = 3):
        if mode not in CLOCK_MODES:
            raise ValueError(f"clock mode {mode!r}; known: {', '.join(CLOCK_MODES)}")
        if not 1 <= speed <= 5:
            raise ValueError("game speed is 1-5")
        self.driver, self.console, self.mode, self.speed = driver, console, mode, speed
        self.used_keys = 0       # times the keys had to stand in for the console
        self.used_console = 0    # times the console had to stand in for the keys
        #: Days the turn in progress is for (set by HeadlessComposite.advance).
        self.hint_days: int | None = None

    @property
    def running(self) -> bool | None:
        return self.driver._running

    def pause(self) -> None:
        driver = self.driver
        if driver.dry_run:
            driver.log.append("pause: dry run, nothing sent")
            return
        if self.mode == "exact":
            driver._running = None           # it may have stopped itself: look before pressing
        if self.mode != "console":
            # The space key first: one press, where the console takes ~2 s of typing
            # during which the game is still running and a week becomes two.
            try:
                driver.pause()
                return
            except ClockUnobservable:
                if self.mode == "keys":
                    raise
                self.used_console += 1
        self.console.send_lines(["gamespeed 0"])
        if not driver._date_moves():
            driver._running = False
            return
        raise ClockUnobservable(
            f"the in-game date kept moving after gamespeed 0 (watched "
            f"{driver.config.clock_probe_seconds:g} s), so the game could not be paused")

    def resume(self, speed: int | None = None) -> None:
        driver = self.driver
        speed = speed or self.speed
        if driver.dry_run:
            driver.log.append("resume: dry run, nothing sent")
            return
        if self.mode != "keys":
            lines = [f"gamespeed {speed}"]
            if self.mode == "exact" and self.hint_days:
                lines.insert(0, f"pause_in_hours {self.hint_days * 24 + EXACT_MARGIN_HOURS}")
            self.console.send_lines(lines)
            if driver._date_moves():
                driver._running = True
                self.speed = speed
                return
            if self.mode == "console":
                raise ClockUnobservable(
                    f"the in-game date did not move within "
                    f"{driver.config.clock_probe_seconds:g} s of gamespeed {speed}, so the game "
                    "is not running. If a day takes longer at this speed, raise "
                    "clock_probe_seconds")
            self.used_keys += 1
        driver.resume()


class HeadlessComposite(CompositeAdapter):
    """The composite, telling the clock how long the turn is (for ``exact``), and
    taking the start-of-run and periodic save when the reader keeps saves."""

    def read_state(self) -> GameState:
        # Here and not in the reader: this is the once-a-turn read, outside any
        # clock wait or verification poll, so typing the savegame command cannot
        # collide with either. Each failure leaves the save's fields unknown.
        maybe_save = getattr(self.reader, "maybe_save", None)
        if maybe_save is not None:
            maybe_save()
        return super().read_state()

    def advance(self, days: int) -> GameState:
        self.writer.clock.hint_days = days
        try:
            return super().advance(days)
        finally:
            self.writer.clock.hint_days = None


class HeadlessWriter(GameAdapter):
    """The write side: the router's actions, with the clock and speed on top."""

    def __init__(self, router: ChannelRouter, clock: HeadlessClock, reader: str = "logtail"):
        self.router = router
        self.clock = clock
        self.excluded = excluded_actions(reader)
        self.supported_actions = frozenset(router.supported_actions - self.excluded)

    def attach_reader(self, read_state: Callable[[], GameState]) -> None:
        self.router.attach_reader(read_state)

    def info(self) -> AdapterInfo:
        driver = self.router.driver
        used = sorted(n for n, c in self.router.channels.items() if c == "console")
        return AdapterInfo(
            name="headless", readable=False, writable=True, clock_control=True,
            notes=(f"no mouse, no screen; clock via {self.clock.mode}; "
                   f"dry_run={driver.dry_run}; console channel for: {', '.join(used)}"))

    def read_state(self) -> GameState:
        return self.router.read_state()

    def apply(self, call: ActionCall) -> ActionResult:
        if call.name == "set_game_speed":
            return self._set_speed(call)
        if call.name in self.excluded:
            return self.unsupported(call)
        return self.router.apply(call)

    def apply_batch(self, calls: list[ActionCall]) -> list[ActionResult]:
        """Several actions: the console ones share one run file (``ChannelRouter.apply_batch``)."""
        own = [c for c in calls if c.name == "set_game_speed" or c.name in self.excluded]
        out = {id(c): self.apply(c) for c in own}
        rest = [c for c in calls if id(c) not in out]
        if rest:
            out.update({id(c): r for c, r in zip(rest, self.router.apply_batch(rest), strict=True)})
        return [out[id(c)] for c in calls]

    def _set_speed(self, call: ActionCall) -> ActionResult:
        speed = int(call.arguments["speed"])
        if not 0 <= speed <= 5:
            return self._result(call, False, "invalid_args", "speed is 0 (pause) to 5")
        try:
            if speed == 0:
                self.clock.pause()
                return self._result(call, True, None, "Paused.")
            self.clock.speed = speed
            if self.clock.running:
                self.clock.resume(speed)
                return self._result(call, True, None, f"Running at speed {speed}.")
        except (ClockUnobservable, OSError, RuntimeError, ValueError) as exc:
            return self._result(call, False, "rejected", str(exc))
        return self._result(call, True, None, (
            f"Speed {speed} noted: the game is paused, and the harness runs it at this speed "
            "when it advances."))

    @staticmethod
    def _result(call: ActionCall, ok: bool, kind: str | None, message: str) -> ActionResult:
        return ActionResult(ok=ok, action=call.name, call_id=call.call_id, message=message,
                            error_kind=kind)

    def pause(self) -> None:
        self.clock.pause()

    def resume(self, speed: int = 3) -> None:
        self.clock.resume()

    def advance(self, days: int) -> GameState:
        raise NotImplementedError("Compose with a reader to observe the clock.")

    def close(self) -> None:
        self.router.close()


# --- building ----------------------------------------------------------------


def _always_focused(title: str) -> FocusCheck:
    return FocusCheck(True, "fake game")


def build_fake(config: HarnessConfig):
    """The in-process fake game a ``fake_game`` config asks for, wired to the config:
    its folders become the log and console paths, its playset the identifier index."""
    from ..fakegame import FakeGame, demo_index

    base = Path(config.fake_game_dir or Path(config.run_dir) / "fakegame")
    if config.game_dir is None:
        config.game_dir = base / "playset"
        index = demo_index(config.game_dir)
    else:
        index = config.build_index()
    if config.rival:
        # Two countries and a person at the keyboard (fakerival.py): the rival is the
        # harness's, an AI country; the person's is whatever else they play.
        from ..fakerival import FakeRivalGame, human_cycle, load_script

        # (Several rivals: the first is the game's own country, the rest ride along.)
        tags = config.rivals or [config.rival]
        config.country = tags[0]
        human = config.fake_rival_human if config.fake_rival_human not in tags else "POL"
        script = (load_script(config.fake_human_script) if config.fake_human_script
                  else human_cycle())
        fake = FakeRivalGame(base / "user", rival=tags[0], human=human, human_script=script,
                             extra_rivals=tuple(tags[1:]), seed=config.seed,
                             start=config.start_date, index=index)
        config.log_path, config.console_dir = fake.log_path, fake.user_dir
        config.save_dir = config.save_dir or fake.save_dir
        return fake
    config.country = config.country or "SWE"
    fake = FakeGame(base / "user", country=config.country, seed=config.seed,
                    start=config.start_date, index=index)
    config.log_path, config.console_dir = fake.log_path, fake.user_dir
    config.save_dir = config.save_dir or fake.save_dir
    return fake


def build_headless(config: HarnessConfig, fake=None) -> CompositeAdapter:
    """The headless composite for ``config``.

    ``fake`` (or ``config.fake_game``) is an in-process fake game: its scan-code
    receiver and its clock replace SendInput and ``time.sleep`` everywhere in the
    stack, so a run is instant and deterministic. ``config.fake_game_files`` is a
    ``hoi4-harness fakegame`` process in another terminal: keys go to its
    ``keys.log``, time is the wall clock. Neither touches the real keyboard.
    """
    if config.headless_clock not in CLOCK_MODES:
        raise ValueError(f"headless_clock {config.headless_clock!r}; known: "
                         f"{', '.join(CLOCK_MODES)}")
    if config.headless_reader not in READERS:
        raise ValueError(f"headless_reader {config.headless_reader!r}; known: "
                         f"{', '.join(READERS)}")
    channels = resolve_channels(config.action_channels)
    for action in CONSOLE_ACTIONS:
        if channels[action] != "console":
            raise ValueError(f"{action} on the ui channel needs the mouse; headless runs it "
                             "through the console")
    if fake is None and config.fake_game:
        fake = build_fake(config)
    send = sleep = focus_check = None
    if fake is not None:
        send, sleep, focus_check = fake.send, fake.sleep, fake.focus_check
    elif config.fake_game_files is not None:
        from ..fakegame import FileKeys

        base = Path(config.fake_game_files)
        send, focus_check = FileKeys(base / "keys.log"), _always_focused
        config.log_path, config.console_dir = base / "logs" / "game.log", base
        config.save_dir = config.save_dir or base / "save games"
    if sleep is None:
        sleep = _scaled_sleep(config.headless_time_scale)

    index = config.build_index()
    # Research cannot be started without the mouse, so an idle slot is not news.
    config.wake_on_free_research_slot = False

    if config.headless_reader == "savegame":
        from .savegame import SaveGameAdapter

        reader: GameAdapter = SaveGameAdapter(config.save_dir)
    elif config.headless_reader == "log+save":
        from .combined import CombinedReader
        from .logtail import LogTailAdapter

        reader = CombinedReader(
            LogTailAdapter(config.log_path, country=config.country), config.save_dir, index=index,
            sleep=sleep, every_days=config.headless_save_days,
            timeout=config.headless_save_timeout)
    else:
        from .logtail import LogTailAdapter

        reader = LogTailAdapter(config.log_path, country=config.country)

    scripts: dict[str, list[Step]] = {"set_national_focus": parse_steps(FOCUS_SCRIPT)}
    user_scripts = config.ui_scripts_path or Path(config.run_dir) / DEFAULT_FILENAME
    if Path(user_scripts).exists():
        scripts.update(keyboard_only(load_scripts(Path(user_scripts)))[0])

    driver = InputDriverAdapter(
        InputConfig(window_title=config.window_title, enforce_focus=config.enforce_window_focus,
                    clock_probe_seconds=config.clock_probe_seconds, mouse=False),
        dry_run=config.dry_run, scripts=scripts, index=index, sleep=sleep,
        keys=ScanCodeKeys(send=send, sleep=sleep) if send is not None else None,
        **({"focus_check": focus_check} if focus_check is not None else {}),
    )
    refresh = True if config.console_refresh is None else config.console_refresh
    console = ConsoleChannel(
        driver,
        ConsoleConfig(
            directory=config.console_dir, filename=config.console_filename,
            open_key=config.console_key, close_key=config.console_key,
            effect_command=config.console_effect_command, hybrid_via=config.console_hybrid_via,
            guards=config.console_guards, archive_dir=Path(config.run_dir),
            country=config.country, refresh=refresh and config.headless_reader != "savegame",
            refresh_front=refresh and config.headless_reader != "savegame"),
        index=index, sleep=sleep)
    if config.headless_reader == "log+save":
        reader.take = lambda: console.send_lines(["savegame"])
        reader.enabled = not config.dry_run
        console.saves = reader
    router = ChannelRouter(driver, console, channels)
    clock = HeadlessClock(driver, console, config.headless_clock, config.headless_game_speed)
    adapter = HeadlessComposite(reader, HeadlessWriter(router, clock, config.headless_reader),
                               clock_owner=config.clock_owner,
                               poll_seconds=config.headless_poll_seconds, sleep=sleep)
    adapter.fake_game = fake
    adapter.watchdog = adapter.patient_focus = None
    if config.campaign:
        _watch_for_campaign(adapter, config, fake, driver, sleep)
    return adapter


def _watch_for_campaign(adapter, config: HarnessConfig, fake, driver, sleep) -> None:
    """An unattended run's guards: the death/silence watchdog inside every clock wait, and
    a focus check that waits for the game to come back to the front instead of failing."""
    from ..campaign.watch import PatientFocus, Watchdog, process_running
    if fake is None and not config.enforce_window_focus:
        raise ValueError("a campaign never types without the game in front: leave the window "
                         "guard on (drop --no-window-guard)")
    if fake is not None:
        alive, now = (lambda: fake.alive), (lambda: fake._wall)
    else:
        name = config.campaign_game_process
        alive, now = (lambda: process_running(name)), time.monotonic
    watchdog = Watchdog(config.log_path, silence_seconds=config.campaign_silence_minutes * 60,
                        process_alive=alive, now=now)
    adapter.watchdog = watchdog
    adapter.on_poll, adapter.on_advance = watchdog.check, watchdog.reset
    # Every key the harness sends is gated on this; it now waits for the focus (a minute at
    # a time) where it used to refuse, and still never sends a key to another window.
    inner = driver._focus_check
    patient = PatientFocus(inner, sleep=sleep, max_wait_seconds=config.campaign_focus_wait_minutes * 60)
    driver._focus_check = patient
    adapter.patient_focus = patient

