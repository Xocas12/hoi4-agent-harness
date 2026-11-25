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

from ..baseai import game_version
from ..config import HarnessConfig
from ..types import ActionCall, ActionResult, GameState
from .base import AdapterInfo, GameAdapter
from .battle_plans import PLAN_ACTIONS, enable_battle_plans
from .channels import ChannelRouter, resolve_channels
from .composite import CompositeAdapter
from .console import CONSOLE_ACTIONS, ConsoleChannel, ConsoleConfig
from .hands import (
    DELEGATE_TOGGLE,
    HAND_ACTIONS,
    Hands,
    delegable_armies,
    delegate_steps,
    research_path_for,
    research_steps,
)
from .input_driver import ClockUnobservable, InputConfig, InputDriverAdapter
from .keys import ScanCodeKeys
from .ui_scripts import DEFAULT_FILENAME, Step, load_scripts, parse_steps
from .unit_orders import OrdersComposite
from .window import FocusCheck

#: Actions with no path that avoids the mouse. Removed from the tool list.
MOUSE_ACTIONS = frozenset({
    "start_research", "delegate_army_to_ai",
    # Division orders are a selection and a right-click (adapters/unit_orders.py).
    "move_division", "attack_province",
    # Battle plans are drawn with the mouse too (adapters/battle_plans.py; not live-verified).
    "draw_front_line", "draw_offensive", "assign_battle_plan",
    # Editing a production line is a click on the production screen (adapters/production_ui.py).
    "change_production_equipment",
    # Choosing an event option is a click on the window (adapters/event_choice.py).
    "choose_event_option",
})
#: Actions the log cannot make safe. add_equipment_production adds a second line
#: for equipment that already has one (seen live on 1.19.5), and only a reader
#: that sees production -- a text save -- can check that first.
NEEDS_PRODUCTION_READER = frozenset({"set_production", "change_production_equipment"})
#: The readers that see production.
PRODUCTION_READERS = ("log+save", "savegame")


def excluded_actions(reader: str = "log+save", mouse: bool = False) -> frozenset[str]:
    """What a headless run with this reader does not offer. With the mouse (an independent
    campaign, ``adapters/hands.py``) the mouse actions are offered when their own
    prerequisites hold, so only the production rule remains."""
    return (frozenset() if mouse else MOUSE_ACTIONS) | (
        NEEDS_PRODUCTION_READER if reader not in PRODUCTION_READERS else frozenset())
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


class HeadlessOrdersComposite(HeadlessComposite, OrdersComposite):
    """The headless composite with division orders: the order ledger and state names are
    added to every read (``OrdersComposite``) and the clock hint and saves stay."""


class HeadlessWriter(GameAdapter):
    """The write side: the router's actions, with the clock and speed on top."""

    def __init__(self, router: ChannelRouter, clock: HeadlessClock, reader: str = "logtail",
                 mouse: bool = False):
        self.router = router
        self.clock = clock
        self.excluded = excluded_actions(reader, mouse)
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
    if config.multiplayer:
        # A client in a multiplayer game (fakemultiplayer.py): the harness's country and a
        # person's, both human; the host's clock and, if asked, a console that is shut.
        from ..fakemultiplayer import FakeMultiplayerGame
        from ..fakerival import human_cycle, load_script

        config.country = config.country or "FRA"
        human = next((t for t in config.mp_human_tags if t != config.country), "GER")
        script = (load_script(config.fake_human_script) if config.fake_human_script
                  else human_cycle())
        fake = FakeMultiplayerGame(base / "user", harness=config.country, human=human,
                                   human_script=script, seed=config.seed,
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
    # An independent campaign may have the mouse (adapters/hands.py); the fake game in another
    # process has none, and the real pyautogui must never click for it.
    hands = Hands(mouse=config.hands_mouse and config.fake_game_files is None)
    if config.hands_mouse and not hands.mouse:
        hands.refuse(sorted(HAND_ACTIONS), "the fake game in another process (fake_game_files) "
                                           "has no mouse")
    # Research cannot be started without the mouse, so an idle slot is not news (the mouse
    # branch below turns it back on once start_research is known to be offered).
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
    if hands.mouse:
        version = game_version(config.game_dir)
        path = research_path_for(version)
        scripts["start_research"] = research_steps(version)
        hands.research_path = (
            f"{path} click path (game version {version or 'unknown'}"
            + ("" if version else ": the 1.19 path is kept") + ")"
            + ("; research.confirm and research.mio_choice are scaled from the measured 1920x1080 "
               "points unless recorded" if path == "1.20" else ""))
        scripts["delegate_army_to_ai"] = delegate_steps()
        if Path(user_scripts).exists():
            recorded = load_scripts(Path(user_scripts))
            scripts.update({name: steps for name, steps in recorded.items()
                            if name in ("start_research", "delegate_army_to_ai")})
        if index is None or not index.tech_places:
            scripts.pop("start_research")
            hands.refuse("start_research", "the playset has no technologies to place on the tree "
                         "(set game_dir / --game-dir to the HOI4 install)")
    elif Path(user_scripts).exists():
        scripts.update(keyboard_only(load_scripts(Path(user_scripts)))[0])

    input_config = InputConfig(
        window_title=config.window_title, enforce_focus=config.enforce_window_focus,
        clock_probe_seconds=config.clock_probe_seconds, mouse=hands.mouse)
    extra = {}
    if focus_check is not None:
        extra["focus_check"] = focus_check
    if hands.mouse and fake is not None:
        # The fake game's mouse and screen: no real pointer, and the points are placed in the
        # screen the fake draws, whatever this machine's settings say.
        from ..uilayout import Geometry

        input_config.geometry = Geometry(*fake.screen)
        extra["gui"] = fake.gui
        extra["display_settings"] = lambda: None
    driver = InputDriverAdapter(
        input_config, dry_run=config.dry_run, scripts=scripts, index=index, sleep=sleep,
        keys=ScanCodeKeys(send=send, sleep=sleep) if send is not None else None, **extra)
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
    if getattr(config, "console_transport", "keys") == "inbox":
        # Orders go into the mod's inbox file; no key is sent (inbox.py, docs/inbox.md).
        from . import build_inbox

        if fake is not None and hasattr(fake, "inbox"):
            console.inbox = fake.inbox(config)
        else:
            console.inbox = build_inbox(config)
        console.config.inbox_ack_seconds = config.inbox_ack_seconds
    if config.headless_reader == "log+save":
        reader.take = lambda: console.send_lines(["savegame"])
        reader.enabled = not config.dry_run
        console.saves = reader
    ledger = None
    if hands.mouse:
        ledger = _wire_hands(driver, console, config, hands, fake)
    if getattr(driver, "production_executor", None) is not None:
        channels.setdefault("set_production", "ui")
        if "set_production" not in config.action_channels:
            channels["set_production"] = "ui"
    router = ChannelRouter(driver, console, channels)
    clock = HeadlessClock(driver, console, config.headless_clock, config.headless_game_speed)
    writer = HeadlessWriter(router, clock, config.headless_reader, mouse=hands.mouse)
    kwargs = dict(clock_owner=config.clock_owner, poll_seconds=config.headless_poll_seconds,
                  sleep=sleep)
    if ledger is not None:
        from .unit_orders import state_names

        adapter = HeadlessOrdersComposite(
            reader, writer, ledger, state_names(driver.order_layer, index), **kwargs)
    else:
        adapter = HeadlessComposite(reader, writer, **kwargs)
    adapter.fake_game = fake
    adapter.hands = hands
    adapter.event_reader = hands.event_reader
    if hands.mouse:
        _announce_hands(config, hands, adapter)
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
    take = None
    if config.campaign_take_focus and fake is None:
        from ..rival.safety import system_input_watch
        from .window import take_focus

        take = idle_gated(take_focus, system_input_watch().idle_seconds,
                          config.campaign_take_focus_idle_seconds)
    patient = PatientFocus(inner, sleep=sleep, max_wait_seconds=config.campaign_focus_wait_minutes * 60,
                           take=take)
    driver._focus_check = patient
    adapter.patient_focus = patient



def idle_gated(take, idle_seconds, quiet: float):
    """``take`` (bring the game to the front) only after ``quiet`` seconds with no keyboard or
    mouse input: a person at the machine keeps their window. An idle clock that cannot be read
    counts as someone there."""
    def gated(title: str) -> bool:
        idle = idle_seconds()
        if idle is None or idle < quiet:
            return False
        return take(title)
    return gated


def _wire_hands(driver, console, config: HarnessConfig, hands: Hands, fake):
    """Everything the mouse tools need beyond the driver, each prerequisite checked now so a
    tool that cannot work is not offered. Returns the order ledger, or None without orders."""
    from ..mapdata import MapError
    from ..uilayout import LayoutRefused
    from .unit_orders import OrderRefused, display_fingerprint, enable_from_config

    # The recorded screen points (calibration.json): the army list, the AI toggle, anything the
    # operator overrode. Research needs none (the interface files give its points).
    calibration = config.calibration_path or Path(config.run_dir) / "calibration.json"
    if Path(calibration).exists():
        try:
            driver.load_calibration(Path(calibration),
                                    current_resolution=fake.screen if fake is not None else None)
        except (OSError, ValueError, RuntimeError) as exc:
            hands.refuse("delegate_army_to_ai", f"calibration.json unusable ({exc})")
    coordinates = driver.config.coordinates
    hands.armies = delegable_armies(coordinates)
    if DELEGATE_TOGGLE not in coordinates or not hands.armies:
        hands.refuse("delegate_army_to_ai", (
            "no recorded click points for the army list and the AI toggle: "
            f"hoi4-harness calibrate {DELEGATE_TOGGLE} army.all army.<name> ... "
            "(the game's interface files do not say where they are)"))
    if "delegate_army_to_ai" in hands.refused:
        _drop(driver, "delegate_army_to_ai")
    # The display must be readable for any computed point: settings.txt against the window.
    # (The fake pins its own.)
    if fake is None and not config.dry_run:
        try:
            driver.geometry()
        except LayoutRefused as exc:
            for name in ("start_research", "delegate_army_to_ai"):
                hands.refuse(name, f"the display could not be read ({exc})")
                _drop(driver, name)
    if "start_research" in driver.scripts and fake is None and not config.dry_run:
        try:
            driver.index.research_layout()
        except LayoutRefused as exc:
            # A recorded research.free_slot would stand in for the slot, but the folder tabs
            # are computed too: refuse rather than offer a path that fails per folder.
            hands.refuse("start_research", f"the research panel layout cannot be read ({exc})")
            _drop(driver, "start_research")
    # Division orders: the map, the camera record and the display it was recorded under.
    ledger = None
    orders_ok = None
    if config.unit_orders is False:
        hands.refuse(("move_division", "attack_province"), "unit_orders is false in the profile")
    else:
        try:
            executor = enable_from_config(driver, console, config)
            record = executor.record()
            if record.fit_province not in executor.layer.provinces:
                raise OrderRefused(f"the camera's fit province {record.fit_province} is not on "
                                   "this map")
            display = display_fingerprint(driver)
            record.camera_for(executor.layer, record.fit_province,
                              screen=driver.config.screen or (
                                  tuple(display["resolution"]) if display else None),
                              display=display, unattended=True)
            ledger = executor.ledger
            orders_ok = executor
        except (OrderRefused, MapError, ValueError, OSError, KeyError) as exc:
            hands.refuse(("move_division", "attack_province"), _orders_reason(exc))
            driver.order_executor = None
            driver.supported_actions = driver.supported_actions - {"move_division",
                                                                  "attack_province"}
    # Battle plans (UNVERIFIED, off unless the profile asks): the same map and camera record.
    if not config.battle_plans:
        hands.refuse(PLAN_ACTIONS, "battle_plans is off in the profile (the click path is not "
                                   "verified in the game)")
    elif orders_ok is None:
        hands.refuse(PLAN_ACTIONS, "battle plans need what division orders need (the map and a "
                                   "recorded camera): "
                                   + hands.refused.get("move_division", "unit_orders is off"))
    else:
        enable_battle_plans(driver, orders_ok.layer, orders_ok._camera, orders_ok.centre,
                            unattended=bool(config.campaign))
    _wire_production(driver, console, config, hands, driver.index)
    _wire_events(driver, config, hands, fake)
    _wire_event_reader(driver, config, hands, fake)
    if "start_research" in driver.scripts:
        config.wake_on_free_research_slot = True
        if not config.dry_run:
            driver.after_input = _refresh_after_input(console, config)
    return ledger


def _wire_events(driver, config: HarnessConfig, hands: Hands, fake) -> None:
    """The event-window guard (adapters/event_windows.py): needs a probe, which only the profile
    (or the fake game's window) can give; without one the start log says nothing guards it."""
    from .event_windows import guard_from_config

    if not config.event_windows:
        hands.events = {"guarded": False, "why": "no event_windows probes in the profile"}
        return
    try:
        guard = guard_from_config(
            config.event_windows, config.run_dir, sleep=driver._sleep,
            probe=fake.event_probe if fake is not None else None)
    except (ValueError, RuntimeError) as exc:
        hands.events = {"guarded": False, "why": str(exc)}
        return
    driver.event_guard = guard
    hands.events = {"guarded": True, "missing": guard.missing(driver.config.coordinates)}


def _wire_event_reader(driver, config: HarnessConfig, hands: Hands, fake) -> None:
    """Reading the open event and choosing its option (adapters/event_choice.py). Needs the
    playset's events, a way to read the screen (Windows OCR, or the fake game's) and the mouse;
    each missing piece is named at the start and ``choose_event_option`` is then not offered."""
    from ..modcontext.events import EventCatalog
    from . import event_choice as ec
    from .ocr import WindowsOcr

    spec = config.event_screen or {}
    if spec.get("enabled") is False:
        hands.event_reading = {"on": False, "why": "event_screen.enabled is false in the profile"}
        return
    if driver.index is None:
        hands.event_reading = {"on": False, "why": "no playset index (the event texts come from "
                                                  "the playset's files)"}
        return
    if fake is not None:
        backend = getattr(fake, "event_ocr", None)
        if backend is None:
            hands.event_reading = {"on": False, "why": "the fake game draws no event text"}
            return
    elif config.dry_run:
        hands.event_reading = {"on": False, "why": "a dry run reads no screen"}
        return
    else:
        backend = WindowsOcr(upscale=float(spec.get("upscale", 2.0)))
    why = backend.available()
    if why:
        hands.event_reading = {"on": False, "why": why}
        return
    ctx = driver.index.mod_context()
    catalog = EventCatalog(ctx).for_country(config.country)
    reader = ec.EventReader(
        catalog, backend, driver.geometry, layout=ec.layout_for(driver.index.playset),
        focused=lambda: driver.focus().focused,
        probe=driver.event_guard.probe if driver.event_guard is not None else None,
        log_path=Path(config.run_dir) / "events.jsonl", grow=float(spec.get("grow", 0.0)),
        centre_growth=bool(spec.get("centre_growth", False)))
    if driver.event_guard is not None:
        driver.event_guard.namer = reader.open_title
    ec.enable_events(driver, reader, settle=float(spec.get("settle", 0.8)))
    hands.event_reader = reader
    hands.event_reading = {"on": True, "ocr": type(backend).__name__,
                           "layout": reader.layout.source}


def _wire_production(driver, console, config: HarnessConfig, hands: Hands, index) -> None:
    """Production by click (adapters/production_ui.py): needs a reader that sees the lines (a text
    save), so the rows can be named. ``set_production`` then edits an existing line when the
    counter points are recorded and refuses, naming them, when they are not; the equipment change
    is offered only when its points are recorded."""
    from . import production_ui as pu

    coordinates = driver.config.coordinates
    hands.production = pu.recorded_summary(coordinates)
    if config.headless_reader not in PRODUCTION_READERS:
        hands.refuse(pu.PRODUCTION_ACTIONS, f"the {config.headless_reader} reader does not see "
                     "production lines, so no row can be named (needs log+save or savegame)")
        return
    buildable = index.equipment_types if index is not None else None
    pu.enable_production(
        driver, console, saves=console.saves,
        buildable=(lambda name: name in buildable()) if buildable is not None else None)
    # set_production leaves the console channel for the click path (the executor opens a new
    # line through the console itself, the one case it is faithful for).
    hands.production["edit_missing"] = pu.missing_for_edit(coordinates)
    hands.production["equipment_missing"] = pu.missing_for_equipment(coordinates)
    if hands.production["equipment_missing"]:
        hands.refuse(pu.EQUIPMENT_ACTION, "no recorded points for it: "
                     + pu.calibrate_line(hands.production["equipment_missing"]))
        _drop(driver, pu.EQUIPMENT_ACTION)


def _drop(driver, name: str) -> None:
    driver.scripts.pop(name, None)
    driver.supported_actions = driver.supported_actions - {name}


def _orders_reason(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    if isinstance(exc, KeyError):
        text = f"missing {text}"
    return text


def _refresh_after_input(console, config: HarnessConfig):
    """After a scripted click path, ask the mod for fresh telemetry so the result can be
    verified while the game is paused (research shows in the probe lines)."""
    def refresh() -> None:
        if config.country and console.config.refresh:
            console.send_lines(console.refresh_lines(config.country.upper()))
    return refresh


def _announce_hands(config: HarnessConfig, hands: Hands, adapter) -> None:
    """Record what is live for the prompt, the guidance pack and the wake rules."""
    from ..campaign.setup import GUIDANCE_LLM, GUIDANCE_LLM_HANDS

    offered = adapter.supported_actions
    config.campaign_hands = hands.summary(offered)
    if "start_research" not in offered:
        config.wake_on_free_research_slot = False
    if not hands.live(offered) and config.guidance == GUIDANCE_LLM_HANDS:
        config.guidance = GUIDANCE_LLM
