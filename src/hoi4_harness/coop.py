"""Co-op and multiplayer: who may act where, and when the queue may run (#31, #33, #91).

Three things live here, all of them about a person being in the game too.

**Handover modes** (#33). The queue in ``handover.py`` is the primitive; this module says
what opens a window for it (``handover_mode``):

* ``hotkey``: the player grants a window, with ``hoi4-harness handover`` or, when
  ``handover_hotkey`` is set, a global hotkey. While it runs the harness also watches the OS
  input clock: if the player touches the keyboard or mouse it stops, keeps what had not run
  and hands back.
* ``idle``: no signal. The game is paused (its date has stood still in game.log), the game is
  in front and nobody has touched the keyboard or mouse for a while. The same guard stops the
  window the moment the player comes back.
* ``approve``: every action is shown to the player and runs only on a yes, through
  ``HOI4Env.confirm_hook`` (#21). No input watching: the player is at the terminal.

Windows needs two OS services, both behind injectable seams so a test never reads the real
idle clock or registers a real hotkey: ``InputWatch`` (``GetLastInputInfo``, rival/safety.py)
and ``HotkeyListener`` (below). Neither sends input. The hotkey listener is off unless asked.

**Domains the player holds** (#31). ``domain=human`` in the control map (``control.py``); here
is the refusal that backs the tool list up, and the brief lines that say who holds what.

**Countries another person plays** (#91). ``multiplayer`` with ``country`` (the harness's) and
``mp_human_tags``: an action aimed at one of those countries is refused.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .adapters.window import FocusCheck
from .handover import GRANT_FILENAME, HandoverQueue
from .rival.safety import (
    Gate,
    InputWatch,
    KeyWatch,
    LogClock,
    PausedPolicy,
    WindowPolicy,
    system_input_watch,
)
from .types import ActionCall

HANDOVER_MODES = ("hotkey", "idle", "approve")

#: Arguments that name a country, across the action catalog (``actions/catalog.py``).
COUNTRY_ARGS = ("target", "from_country", "theater", "enemy")


# --- the refusals ---------------------------------------------------------------------------


def target_tags(call: ActionCall) -> list[str]:
    """The country tags an action is aimed at (upper case)."""
    out = []
    for name in COUNTRY_ARGS:
        value = call.arguments.get(name)
        if isinstance(value, str) and value.strip():
            out.append(value.strip().upper())
    return out


def refusal(config, call: ActionCall) -> str | None:
    """Why ``call`` must not be done at all, or None.

    A domain the player holds (``human``), or a country another person plays in a multiplayer
    game. Independent of the tool list: it is checked again when a queued call runs, so an
    action that got into a queue file some other way still does not reach the game."""
    why = config.control_map().human_refusal(call.name)
    if why:
        return why
    if config.multiplayer:
        humans = {t.upper() for t in config.mp_human_tags}
        for tag in target_tags(call):
            if tag in humans:
                return (f"{call.name} is aimed at {tag}, a country a person plays in this "
                        "multiplayer game: the harness never acts on it")
    return None


def coop_lines(config) -> list[str]:
    """Brief lines that say who holds what, every turn (a model forgets a prompt paragraph)."""
    lines = []
    human = config.control_map().human_domains()
    if human:
        lines.append("Held by the player, not yours (do not act there): " + ", ".join(human))
    if config.multiplayer:
        others = ", ".join(t.upper() for t in config.mp_human_tags) or "none named"
        lines.append(f"Multiplayer: you play {config.country or '?'}; people play {others}. "
                     "Actions aimed at them are refused.")
    return lines


def validate(config) -> list[str]:
    """Problems with the co-op settings, said before a run starts."""
    problems = []
    if config.handover_mode not in HANDOVER_MODES:
        problems.append(f"handover_mode must be one of {', '.join(HANDOVER_MODES)}, "
                        f"not {config.handover_mode!r}")
    if config.handover_hotkey and config.handover_mode != "hotkey":
        problems.append("handover_hotkey only applies to handover_mode hotkey")
    if config.handover_hotkey:
        try:
            parse_hotkey(config.handover_hotkey)
        except ValueError as exc:
            problems.append(str(exc))
    if config.multiplayer:
        humans = [t.upper() for t in config.mp_human_tags]
        if not config.country:
            problems.append("multiplayer needs country: the tag the harness plays")
        if not humans:
            problems.append("multiplayer needs mp_human_tags: the countries people play")
        if config.country and config.country.upper() in humans:
            problems.append(f"{config.country} is both the harness's country and a person's")
        if config.clock_owner == "harness":
            problems.append("multiplayer needs clock_owner player: the host owns speed and "
                            "pause, and the harness cannot pause to think")
    return problems


# --- the windows ----------------------------------------------------------------------------


class GrantWindow(WindowPolicy):
    """``hotkey``: open while the grant file exists; closed by the player removing it, or by
    their touching the keyboard or mouse (when the OS can say). Ends by removing the grant."""

    name = "hotkey"

    def __init__(self, grant_path: Path, watch: InputWatch | None = None):
        self.grant_path = Path(grant_path)
        self.watch = watch
        self._expect: int | None = None

    def granted(self) -> bool:
        return self.grant_path.exists()

    def check(self) -> Gate:
        if self.granted():
            return Gate(True)
        return Gate(False, "no grant", "waiting for `hoi4-harness handover` or the hotkey")

    def begin(self) -> None:
        # The grant itself (a hotkey press) is input; what counts is what comes after it.
        self._expect = self.watch.tick() if self.watch else None

    def note_own_input(self) -> None:
        self._expect = self.watch.tick() if self.watch else None

    def interrupted(self) -> str | None:
        if not self.granted():
            return "the player withdrew the grant"
        if self.watch is not None:
            tick = self.watch.tick()
            if tick is not None and tick != self._expect:
                return "the player started using the keyboard or mouse"
        return None

    def end(self) -> None:
        self._release()

    def idle(self) -> None:
        self._release()

    def _release(self) -> None:
        try:
            self.grant_path.unlink()
        except FileNotFoundError:
            pass


class ApprovalWindow(WindowPolicy):
    """``approve``: always open; the queue asks before each action instead."""

    name = "approve"

    def check(self) -> Gate:
        return Gate(True)


def find_driver(adapter):
    """The input driver of a composite adapter (through the channel router), or None."""
    writer = getattr(adapter, "writer", None)
    for holder in (writer, getattr(writer, "router", None)):
        driver = getattr(holder, "driver", None)
        if driver is not None:
            return driver
    return writer if hasattr(writer, "key_watch") else None


def build_window(config, adapter=None, *, watch: InputWatch | None = None,
                 log_path: Path | None = None, now: Callable[[], float] | None = None,
                 focus: Callable[[], FocusCheck] | None = None) -> WindowPolicy:
    """The window policy for ``config.handover_mode``. ``watch``, ``now`` and ``focus`` are the
    seams a test (or the fake game) replaces; unset, they are the real OS ones."""
    import time

    mode = config.handover_mode
    if mode == "approve":
        return ApprovalWindow()
    if watch is None:
        watch = system_input_watch()
    if mode == "hotkey":
        return GrantWindow(Path(config.run_dir) / GRANT_FILENAME, watch if watch.supported else None)
    if mode == "idle":
        driver = find_driver(adapter)
        if focus is None:
            focus = driver.focus if driver is not None else (lambda: FocusCheck(True, None))
        path = log_path or getattr(getattr(adapter, "reader", None), "log_path", None) \
            or config.log_path
        return PausedPolicy(LogClock(path, now or time.monotonic), watch, focus,
                            config.handover_paused_seconds, config.handover_idle_seconds)
    raise ValueError(f"handover_mode must be one of {', '.join(HANDOVER_MODES)}, not {mode!r}")


def wire_handover(env, config, **seams) -> WindowPolicy | None:
    """Give ``env``'s handover queue its window policy and, for the modes that watch the
    player, the guard the input driver asks before every key. Returns the policy."""
    queue = env.handover
    if queue is None or not isinstance(queue, HandoverQueue) or type(queue) is not HandoverQueue:
        return None                                   # rival mode brings its own (rival/)
    problems = validate(config)
    if problems:
        raise ValueError("; ".join(problems))
    window = build_window(config, env.adapter, **seams)
    watch = None
    if config.handover_mode != "approve":
        driver = find_driver(env.adapter)
        if driver is not None and not getattr(driver, "dry_run", True):
            watch = KeyWatch(window, config.console_key)
            driver.key_watch = watch
            keys = driver._keys()
            keys.on_send, keys.before_send = watch.own_input, watch.check
    queue.use_window(window, watch, mode=config.handover_mode)
    if config.handover_mode == "approve":
        # No hook installed means nobody to ask: that is a no, as in confirm.py.
        queue.approver = lambda request: bool(env.confirm_hook and env.confirm_hook(request))
    return window


# --- the hotkey -----------------------------------------------------------------------------

_MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """``"ctrl+alt+h"`` as (Windows modifier mask, virtual-key code). Raises ValueError."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError(f"hotkey {spec!r}: give at least one modifier and a key, e.g. ctrl+alt+h "
                         "(a bare key would be taken from the game)")
    *mods, key = parts
    mask = 0
    for mod in mods:
        if mod not in _MODIFIERS:
            raise ValueError(f"hotkey {spec!r}: unknown modifier {mod!r} "
                             f"(one of {', '.join(sorted(_MODIFIERS))})")
        mask |= _MODIFIERS[mod]
    if len(key) == 1 and key.isalnum():
        return mask, ord(key.upper())
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        return mask, 0x70 + int(key[1:]) - 1
    raise ValueError(f"hotkey {spec!r}: key must be a letter, a digit or F1 to F12, not {key!r}")


class HotkeyListener:
    """Calls ``on_press`` when a global hotkey is pressed. It must never send input itself:
    it registers for a key and waits. Off by default; ``build_hotkey_listener`` makes one."""

    def start(self, on_press: Callable[[], None]) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError


class FakeHotkeyListener(HotkeyListener):
    """For tests and the fake game: ``press()`` is the player's keypress."""

    def __init__(self, spec: str = "ctrl+alt+h"):
        self.spec = spec
        self.running = False
        self._on_press: Callable[[], None] | None = None

    def start(self, on_press: Callable[[], None]) -> None:
        self.running, self._on_press = True, on_press

    def stop(self) -> None:
        self.running, self._on_press = False, None

    def press(self) -> None:
        if self.running and self._on_press is not None:
            self._on_press()


class Win32HotkeyListener(HotkeyListener):  # pragma: no cover - needs Windows
    """``RegisterHotKey`` on a thread of its own. The hotkey is taken from every program while
    this runs (that is how a global hotkey works), so pick one the game does not use."""

    WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
    MOD_NOREPEAT = 0x4000

    def __init__(self, spec: str):
        self.spec = spec
        self.mods, self.vk = parse_hotkey(spec)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self.error: str | None = None

    def start(self, on_press: Callable[[], None]) -> None:
        def run() -> None:
            import ctypes
            from ctypes import wintypes

            user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
            self._thread_id = kernel32.GetCurrentThreadId()
            if not user32.RegisterHotKey(None, 1, self.mods | self.MOD_NOREPEAT, self.vk):
                self.error = f"another program already holds {self.spec}"
                self._ready.set()
                return
            self._ready.set()
            msg = wintypes.MSG()
            try:
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == self.WM_HOTKEY:
                        on_press()
            finally:
                user32.UnregisterHotKey(None, 1)

        self._thread = threading.Thread(target=run, name="handover-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self.error:
            raise RuntimeError(self.error)

    def stop(self) -> None:
        import ctypes

        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(2)


def build_hotkey_listener(config) -> HotkeyListener | None:
    """The listener ``config.handover_hotkey`` asks for; None when it is empty (the default)."""
    if not config.handover_hotkey:
        return None
    if sys.platform != "win32":
        raise RuntimeError("handover_hotkey needs Windows; use `hoi4-harness handover` bound to "
                           "a key with your OS's own tool instead")
    return Win32HotkeyListener(config.handover_hotkey)  # pragma: no cover


# --- the guidance channel -----------------------------------------------------------------


# --- domains that change hands mid-game (#31) -------------------------------------------------

DOMAINS_FILENAME = "domains.json"
_STOP = frozenset({"the", "my", "to", "me", "you", "back", "over", "and", "domain", "domains",
                   "please", "for", "your", "now", "model", "llm", "ai", "agent", "harness",
                   "claude", "it", "mine", "yours"})
_ECONOMY = ("focus", "research", "construction", "production")
_ALIASES = {"fleet": "navy", "fleets": "navy", "armies": "army", "land": "army",
            "diplo": "diplomacy", "politic": "politics", "economy": None, "all": None,
            "everything": None}


def parse_domain_message(text: str, domains: tuple[str, ...]) -> list[tuple[str, str]]:
    """The domain commands in a chat line: ``[(domain, "human" | "llm"), ...]``, [] when the line
    is ordinary talk. Strict on purpose: the line must START with a command word and every other
    word must be a domain (or filler), so "I will take a look at the economy later" moves
    nothing.

    ``take army``, ``take back the navy and air``, ``/take all``  ->  the person holds them;
    ``give army``, ``hand over diplomacy``, ``give the economy back``, ``you take the navy``  ->
    the model does. The economy is focus, research, construction and production."""
    import re

    tokens = re.findall(r"[a-z]+", (text or "").lower())
    if not tokens:
        return []
    if tokens[0] in ("take", "taking", "keep"):
        direction, rest = "human", tokens[1:]
    elif tokens[0] in ("give", "hand", "release"):
        direction, rest = "llm", tokens[1:]
    elif tokens[:2] == ["you", "take"]:
        direction, rest = "llm", tokens[2:]
    elif tokens[:3] == ["you", "can", "have"]:
        direction, rest = "llm", tokens[3:]
    else:
        return []
    out: list[str] = []
    for word in rest:
        if word in _STOP:
            continue
        mapped = _ALIASES.get(word, word)
        if word == "economy":
            out += _ECONOMY
        elif word in ("all", "everything"):
            out += list(domains)
        elif mapped in domains:
            out.append(mapped)
        else:
            return []           # an unknown word: this is talk, not a command
    return [(d, direction) for d in dict.fromkeys(out)]


class DomainBoard:
    """``hoi4-harness domains``: the person's commands to move a domain, a file in the run
    directory (``domains.json``) the way ``handover.grant`` and the guidance inbox are. Append-only
    with ids, so a command is read once and the record of who held what stays."""

    def __init__(self, run_dir: Path, clock=None):
        import time

        self.path = Path(run_dir) / DOMAINS_FILENAME
        self.clock = clock or time.time
        self._done = 0

    def rows(self) -> list[dict]:
        import json

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [r for r in data if isinstance(r, dict) and "id" in r] if isinstance(data, list) else []

    def post(self, domain: str, to: str, source: str = "command") -> dict:
        import json

        from .control import DOMAINS

        if domain not in DOMAINS:
            raise ValueError(f"unknown domain {domain!r}: one of {', '.join(DOMAINS)}")
        if to not in ("human", "llm"):
            raise ValueError("to must be human (the person holds it) or llm (the model does)")
        rows = self.rows()
        row = {"id": (rows[-1]["id"] + 1) if rows else 1, "ts": self.clock(), "domain": domain,
               "to": to, "source": source}
        rows.append(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8")
        tmp.replace(self.path)
        return row

    def poll(self) -> list[dict]:
        """Commands not yet taken, oldest first."""
        fresh = [r for r in self.rows() if r["id"] > self._done]
        if fresh:
            self._done = fresh[-1]["id"]
        return fresh


def _describe_call(call: ActionCall, result) -> str:
    args = [str(v) for v in call.arguments.values() if isinstance(v, (str, int, float))][:2]
    text = " ".join([call.name, *args])
    if not result.ok:
        return f"{text} (failed)"
    if "queued" in (result.message or "").lower():
        return f"{text} (queued for you)"
    return text


class CoopGuidance:
    """What a plain co-op run hangs off the loop: the person's guidance, domain handovers and the
    model's explanation of what it did.

    * **Guidance** (``hoi4-harness guide``): the campaign's channel, reused. Read each turn,
      written to the transcript once, shown in the brief (new items marked) and the model is woken
      for it once. Advice the model weighs; nothing here executes it.
    * **Domains** (#31): the person moves a domain with ``hoi4-harness domains --take army`` /
      ``--give army``, or by typing ``take army`` / ``give army`` in the chat (``incoming`` is where
      the chat's lines come from: the file chat by default; in multiplayer the game's chat cannot
      be read back, so the file chat or the command is the way). The change takes effect before
      the next decision: the tool list, the wake rules, the system prompt and the refusal all
      follow ``config.coop_overrides``; what the model had queued in a domain taken back is
      refused when it comes to run. The model is woken when it gains a domain, told once in the
      brief, and the person gets an answer in chat.
    * **Explanation**: after a turn the model acted in, one chat line says what it did, unless the
      model already said something itself that turn.
    """

    def __init__(self, run_dir: Path, loop, incoming=None):
        from .campaign.inbox import GuidanceBoard

        self.board = GuidanceBoard(run_dir)
        self.domains = DomainBoard(run_dir)
        self.loop = loop
        self.env = loop.env
        self.incoming = incoming
        self._logged: set[int] = set()
        self._chat_done: set[int] = set()
        self._notice: list[str] = []
        self._wake: str | None = None
        self._said_at = 0
        loop.env.brief_hooks.append(self._brief)

    # --- the brief --------------------------------------------------------------------

    def _brief(self, state) -> list[str]:
        lines = list(self.board.brief_lines(state))
        if self._notice:
            lines.append("Domain changes since your last turn: " + "; ".join(self._notice))
        if self.env.chat is not None or self.env.mp_chat is not None:
            lines.append("The player reads the chat: say in one send_message what you did and "
                         "why (the harness adds a plain line if you do not).")
        return lines

    # --- the person's commands ----------------------------------------------------------

    def _chat_commands(self) -> list[tuple[str, str, str]]:
        """``(domain, to, source)`` from the person's chat lines, each line once."""
        from .control import DOMAINS

        out: list[tuple[str, str, str]] = []
        if self.incoming is not None:
            for text in self.incoming():
                out += [(d, to, "chat") for d, to in parse_domain_message(text, DOMAINS)]
        chat = self.env.chat
        if chat is not None:
            for m in chat.new():
                if m.id in self._chat_done or m.frm != "person":
                    continue
                self._chat_done.add(m.id)
                out += [(d, to, "chat") for d, to in parse_domain_message(m.text, DOMAINS)]
        return out

    def _apply(self, changes: list[tuple[str, str, str]], date: str) -> None:
        config = self.loop.config
        gained: list[str] = []
        answers: list[str] = []
        for domain, to, source in changes:
            now = self.loop.control.controller(domain)
            if now not in ("llm", "human"):
                answers.append(f"{domain} is {now}, not something to hand back and forth")
                continue
            if now == to:
                answers.append(f"{domain} is already " + ("yours" if to == "human" else "mine"))
                continue
            before = dict(config.coop_overrides)
            config.coop_overrides[domain] = to
            try:
                self.loop.refresh_control()
            except ValueError as exc:       # ControlError: a map that cannot be honoured
                config.coop_overrides.clear()
                config.coop_overrides.update(before)
                self.loop.refresh_control()
                answers.append(f"{domain} stays where it was: {exc}")
                continue
            if to == "llm":
                gained.append(domain)
                self._notice.append(f"the player handed {domain} to you; it is yours now")
                answers.append(f"{domain} is mine now")
            else:
                self._notice.append(f"the player took {domain} back; do not act there. Anything "
                                    "you queued in it will be refused")
                answers.append(f"{domain} is yours again; I will not touch it")
            self.loop.transcript.write("domain_change", date=date, domain=domain, to=to,
                                       source=source)
        if gained:
            self._wake = f"the player handed you {', '.join(gained)}"
        if answers:
            self.env.say("person", "; ".join(answers).capitalize() + ".", date)

    # --- the loop's hooks -----------------------------------------------------------------

    def before_turn(self, state) -> str | None:
        for item in self.board.poll():
            if item.id in self._logged:
                continue
            self._logged.add(item.id)
            self.loop.transcript.write("guidance", date=state.date, id=item.id, ts=item.ts,
                                       text=item.text)
        changes = [(r["domain"], r["to"], r.get("source", "command")) for r in self.domains.poll()]
        changes += self._chat_commands()
        self._wake = None
        if changes:
            self._notice = []
            self._apply(changes, state.date)
        self.env.take_turn_log()                   # what the reflexes did is not this turn's talk
        self._said_at = self.env.chat_sent
        self.loop.policy.pending_wake = self.board.wake_reason() or self._wake
        return None

    def after_turn(self, state, *, woke: bool, reason: str, actions: list[str],
                   tokens: int) -> None:
        log = self.env.take_turn_log()
        if woke:
            self.board.shown()
            self._notice = []
        if not woke or not log or self.env.chat_sent != self._said_at:
            return
        if self.env.chat is None and self.env.mp_chat is None:
            return
        done = "; ".join(_describe_call(c, r) for c, r in log[:6])
        more = f" and {len(log) - 6} more" if len(log) > 6 else ""
        reply = self.env.say("person", f"This turn I did: {done}{more}.", state.date)
        self.loop.transcript.write("coop_explained", date=state.date, ok=reply.ok, text=done,
                                   note=None if reply.ok else reply.message)


def wants_guidance(config) -> bool:
    """A co-op run that is not a campaign or a rival run (those have their own supervisors)."""
    return bool((config.handover or config.human_domains or config.multiplayer)
                and not config.campaign and not config.rival)
