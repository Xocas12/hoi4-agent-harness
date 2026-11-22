"""The inbox: orders reach the running game through a mod file it reloads by itself.

The console channel types ``run llmb_turn.txt`` into the game: it needs the keyboard, the
game window in front and a person who is not using either. The inbox needs none of them.
Started with ``-debug_mode`` (the launcher's "Open game in Debug Mode"), HOI4 watches its
files and reloads the database a changed file belongs to, events among them (dev diary for
1.5.2). So the harness rewrites two mod files and the game picks the orders up on its own,
while it runs, whoever is at the keyboard:

* ``events/llm_bridge_inbox_events.txt`` -- two hidden events. ``llmb_inbox.1`` is fired
  once a day by the mod's ``on_daily`` for every harness country (``llm_bridge_is_harness``:
  the person's, a campaign's under human_ai, a marked rival; a static hook in
  ``llm_bridge_on_actions.txt``); it fires ``llmb_inbox.2`` in the scope of every country
  that has orders waiting, so ``ROOT`` is that country, exactly as it is for
  ``effect <TAG> <name>`` typed in the console. ``llmb_inbox.2`` holds the orders.
* ``common/scripted_effects/llm_bridge_inbox.txt`` -- the same two steps as scripted effects
  (``llm_bridge_inbox_dispatch``, ``llm_bridge_inbox_run``), which the two events also call.
  Whichever database the game reloads, events or scripted effects, carries the orders: the
  copies are guarded by the same flag, so one runs and the other finds it done.

Each batch is guarded by a global flag named after the batch (``llmb_inbox_<id>``), set
when it runs and logged as ``LLMB|v5|inbox|tag=..|id=<id>|via=event|effect``. A flag and
not a counter: loading an older save cannot block new orders (a counter would be ahead in
the file and behind in the save) and cannot replay old ones (an acknowledged batch leaves
the file). The flag expires after a year so a long campaign does not pile them up.

What goes into a batch is what the console channel would have typed, converted line by
line: ``eval_effect TAG = { ... }`` becomes ``TAG = { ... }`` and ``effect TAG name`` becomes
``TAG = { name = yes }``. Console commands that are not effects (``savegame``,
``gamespeed``) have no inbox form and are refused.

UNVERIFIED until a live game says so (docs/inbox.md): that 1.20 still reloads events or
scripted effects from a mod folder in Documents, and how long after the write it does.
``hoi4-harness inbox probe`` settles it.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

EVENTS_FILE = "events/llm_bridge_inbox_events.txt"
EFFECTS_FILE = "common/scripted_effects/llm_bridge_inbox.txt"
NAMESPACE = "llmb_inbox"
FLAG_PREFIX = "llmb_inbox_"
#: Days a batch's done-flag lives in the save.
FLAG_DAYS = 365

_TAG = re.compile(r"^[A-Z0-9]{3}$")
_EVAL = re.compile(r"^\s*(?P<cmd>\S+)\s+(?P<tag>[A-Z0-9]{3})\s*=\s*\{(?P<body>.*)\}\s*$")
_EFFECT = re.compile(r"^\s*effect\s+(?P<tag>[A-Z0-9]{3})\s+(?P<name>[A-Za-z0-9_.\-]+)\s*$")
_ID = re.compile(r"^[A-Za-z0-9_]+$")
_BEGIN = re.compile(r"^\s*# batch (?P<id>[A-Za-z0-9_]+) tag=(?P<tag>[A-Z0-9]{3}) "
                    r"posted=(?P<posted>[0-9.]+)\s*$")
_END = re.compile(r"^\s*# end (?P<id>[A-Za-z0-9_]+)\s*$")
ACK = re.compile(r"LLMB\|v\d+\|inbox\|[^\r\n]*?\bid=(?P<id>[A-Za-z0-9_]+)\|via=(?P<via>[a-z]+)")

HEADER = (
    "# WRITTEN by hoi4-harness (src/hoi4_harness/inbox.py) while it runs: the orders the\n"
    "# harness has posted and the game has not yet acknowledged. The game reloads this file\n"
    "# when it changes (-debug_mode). Do not edit by hand. See docs/inbox.md.\n"
)


class InboxError(ValueError):
    """A line the inbox cannot carry, or a batch it cannot write."""


def to_script(line: str, effect_command: str = "eval_effect") -> tuple[str, str]:
    """One console line -> (country tag, effect script run in that country's scope)."""
    match = _EFFECT.match(line)
    if match:
        return match.group("tag"), f"{match.group('name')} = yes"
    match = _EVAL.match(line)
    if match and match.group("cmd") == effect_command:
        return match.group("tag"), match.group("body").strip()
    raise InboxError(f"{line.split()[0] if line.split() else line!r} is a console command, not an "
                     "effect: the inbox has no form for it (only `effect TAG name` and "
                     f"`{effect_command} TAG = {{ ... }}` lines)")


@dataclass
class Batch:
    id: str
    tag: str
    script: list[str]
    posted: float = 0.0

    def block(self, via: str, indent: str) -> list[str]:
        flag = FLAG_PREFIX + self.id
        inner = indent + "\t"
        out = [f"{indent}# batch {self.id} tag={self.tag} posted={self.posted:.0f}",
               f"{indent}if = {{",
               f"{inner}limit = {{ tag = {self.tag} NOT = {{ has_global_flag = {flag} }} }}",
               f"{inner}set_global_flag = {{ flag = {flag} value = 1 days = {FLAG_DAYS} }}"]
        out += [f"{inner}{line}" for line in self.script]
        out += [f'{inner}log = "LLMB|v5|inbox|tag=[ROOT.GetTag]|id={self.id}|via={via}"',
                f"{indent}}}",
                f"{indent}# end {self.id}"]
        return out


def render(batches: list[Batch]) -> dict[str, str]:
    """The two files for ``batches`` (relative path -> text). No batches: the empty inbox."""
    tags = list(dict.fromkeys(b.tag for b in batches))
    # A country that does not exist yet (a release, a civil war's side) is skipped, and its orders
    # wait in the file until it does.
    dispatch = [f"\t\tif = {{ limit = {{ country_exists = {tag} }} "
                f"{tag} = {{ country_event = {{ id = {NAMESPACE}.2 }} }} }}" for tag in tags]
    events = [HEADER, f"add_namespace = {NAMESPACE}", "",
              "# Fired once a day by on_daily for each harness country (llm_bridge_on_actions.txt).",
              "country_event = {", f"\tid = {NAMESPACE}.1", "\thidden = yes", "\tis_triggered_only = yes",
              "\timmediate = {", *dispatch, "\t\tllm_bridge_inbox_dispatch = yes", "\t}", "}", "",
              "# Fired in the scope of each country with orders waiting: ROOT is that country.",
              "country_event = {", f"\tid = {NAMESPACE}.2", "\thidden = yes", "\tis_triggered_only = yes",
              "\timmediate = {"]
    for batch in batches:
        events += batch.block("event", "\t\t")
    events += ["\t\tllm_bridge_inbox_run = yes", "\t}", "}", ""]
    effects = [HEADER, "# The scripted-effect copy of the inbox events, for a game that reloads",
               "# scripted effects and not events. Guarded by the same flags: one copy runs.", "",
               "llm_bridge_inbox_dispatch = {"]
    effects += [line[1:] for line in dispatch]
    effects += ["}", "", "llm_bridge_inbox_run = {"]
    for batch in batches:
        effects += batch.block("effect", "\t")
    effects += ["}", ""]
    return {EVENTS_FILE: "\n".join(events), EFFECTS_FILE: "\n".join(effects)}


def parse_batches(text: str) -> list[Batch]:
    """The batches in an inbox events file (the ``via=event`` copies), in order."""
    out: list[Batch] = []
    current: Batch | None = None
    body: list[str] = []
    for raw in text.splitlines():
        begin = _BEGIN.match(raw)
        if begin:
            current, body = Batch(begin.group("id"), begin.group("tag"), [],
                                  float(begin.group("posted"))), []
            continue
        if current is None:
            continue
        if _END.match(raw):
            # body: the if, the limit, the flag ... the log, the closing brace
            current.script = [line.strip() for line in body[3:-2]]
            out.append(current)
            current = None
            continue
        body.append(raw)
    return out


@dataclass
class Ack:
    id: str
    via: str


@dataclass
class Inbox:
    """Post batches into the mod folder; read the game's acknowledgements from game.log."""

    mod_dir: Path
    log_path: Path | None = None
    effect_command: str = "eval_effect"
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    pending: list[Batch] = field(default_factory=list)
    acked: dict[str, str] = field(default_factory=dict)
    _offset: int = 0
    _counter: int = 0

    def __post_init__(self) -> None:
        self.mod_dir = Path(self.mod_dir)
        path = self.mod_dir / EVENTS_FILE
        if path.exists():
            # Orders a previous harness posted and the game never ran stay posted.
            self.pending = parse_batches(path.read_text(encoding="utf-8"))
        if self.log_path is not None and Path(self.log_path).exists():
            # Acknowledgements already in the log are for batches that are done.
            self.poll()

    # --- writing ---------------------------------------------------------------------

    def ensure(self) -> None:
        """Write the empty inbox if the mod has none (the game must see the events at load)."""
        if not (self.mod_dir / EVENTS_FILE).exists() or not (self.mod_dir / EFFECTS_FILE).exists():
            self._write()

    def new_id(self) -> str:
        self._counter += 1
        stamp = time.strftime("%y%m%d%H%M%S", time.localtime(self.clock()))
        return f"{stamp}_{self._counter:02d}"

    def post(self, lines: list[str]) -> list[Batch]:
        """Convert console ``lines`` into one batch per country and write the inbox.

        Every line is checked before anything is written: one that has no inbox form
        refuses the whole post (``InboxError``), as the console refuses a file it would split.
        """
        by_tag: dict[str, list[str]] = {}
        for line in lines:
            tag, script = to_script(line, self.effect_command)
            if "\n" in script or '"' in script and script.count('"') % 2:
                raise InboxError(f"unbalanced script in {line!r}")
            by_tag.setdefault(tag, []).append(script)
        self.poll()
        batches = [Batch(self.new_id(), tag, scripts, self.clock()) for tag, scripts in by_tag.items()]
        self.pending.extend(batches)
        self._write()
        return batches

    def _write(self) -> None:
        for rel, text in render(self.pending).items():
            target = self.mod_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # In place, not tmp + rename: the game's file watcher in -debug_mode may react to a
            # modification and not to the rename an atomic replace makes (first live run,
            # 2026-11-22: no batch acknowledged after an atomic replace).
            for attempt in range(20):
                try:
                    with open(target, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(text)
                    break
                except PermissionError:
                    # The game is reading the file: it holds it for milliseconds.
                    if attempt == 19:
                        raise
                    self.sleep(0.1)

    # --- reading the acknowledgements --------------------------------------------------

    def poll(self) -> list[Ack]:
        """New acknowledgements in game.log; acknowledged batches leave the inbox."""
        if self.log_path is None:
            return []
        path = Path(self.log_path)
        try:
            size = path.stat().st_size
        except OSError:
            return []
        if size < self._offset:
            self._offset = 0          # a new game session started a new log
        with path.open("rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
            self._offset = fh.tell()
        new: list[Ack] = []
        for match in ACK.finditer(data.decode("utf-8", errors="replace")):
            if match.group("id") not in self.acked:
                self.acked[match.group("id")] = match.group("via")
                new.append(Ack(match.group("id"), match.group("via")))
        if new:
            before = len(self.pending)
            self.pending = [b for b in self.pending if b.id not in self.acked]
            if len(self.pending) != before:
                self._write()
        return new

    def wait(self, ids: list[str], timeout: float, poll_seconds: float = 0.25) -> bool:
        """Block until every batch in ``ids`` is acknowledged, or ``timeout`` seconds pass."""
        deadline = self.clock() + timeout
        while True:
            self.poll()
            if all(i in self.acked for i in ids):
                return True
            if self.clock() >= deadline:
                return False
            self.sleep(poll_seconds)

    def oldest_pending_age(self) -> float | None:
        if not self.pending:
            return None
        return self.clock() - min(b.posted for b in self.pending)


def default_mod_dir(user_dir: Path | None) -> Path | None:
    """The installed mod's folder under the game's user directory."""
    return Path(user_dir) / "mod" / "llm_bridge" if user_dir else None
