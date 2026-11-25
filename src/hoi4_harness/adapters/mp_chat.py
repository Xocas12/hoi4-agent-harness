"""The in-game chat, for a multiplayer game (docs/multiplayer.md, "Talking to the other player").

In single player the chat is a file beside the game (chat.py). In a multiplayer game
(``config.multiplayer``) the person on the other end is in the game, reading the game's own chat,
so ``send_message`` to a human (``person``, ``global`` or a country in ``mp_human_tags``) is typed
into that chat through the input driver's key layer instead: Enter opens the chat box, the text
goes in, Enter sends it. Only what the key layer can type goes in (``typable``): a bracket or a
colon would make it refuse the whole line. A message to another LLM country is not routed here
(it is not in the game's chat to read); it goes to the file chat when the run has one.

UNVERIFIED against a live multiplayer game: that Enter opens the chat box in a lobby-less client
session and Enter sends it, that the box takes ``/`` and ``:`` and non-ASCII text, how long a line
may be, and whether a line typed while the game is paused is accepted. The keys are constructor
arguments (``OPEN_KEY``, ``SEND_KEY``) so the first live session can correct them without a code
change elsewhere. Nothing here reads the game's chat back: the other player's words reach the
model only if they are also written to the file chat (``hoi4-harness chat``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

#: UNVERIFIED: the keys that open the in-game chat box and send what is in it.
OPEN_KEY = "enter"
SEND_KEY = "enter"
#: UNVERIFIED: a conservative line length for the game's chat box.
MAX_CHARS = 200
#: Where a message addressed to "person" or "global" means the game's chat.
HUMAN_TARGETS = ("person", "global")


#: What the key layer can type (adapters/keys.py): letters, digits, space and  - ' , . _
_TYPABLE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -',._")


def typable(text: str) -> str:
    """``text`` as the key layer can type it: accents dropped (``o`` for the o-umlaut), anything
    else with no scan code (brackets, colon, question mark, quotes) replaced by a space, spaces
    squeezed. ``type_text`` refuses a whole string over one such character, so a chat line
    with a ``[`` in it would never have been sent."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return " ".join("".join(c if c in _TYPABLE else " " for c in plain).split())


@dataclass(frozen=True)
class SentLine:
    id: int
    to: str
    text: str


class MultiplayerChat:
    """Types a message into the game's chat through an input driver (``press`` and ``type_text``)."""

    def __init__(self, driver, tag: str, human_tags=(), open_key: str = OPEN_KEY,
                 send_key: str = SEND_KEY, sleep: Callable[[float], None] = time.sleep,
                 settle: float = 0.15):
        self.driver = driver
        self.tag = tag.upper()
        self.human_tags = {t.upper() for t in human_tags}
        self.open_key, self.send_key = open_key, send_key
        self._sleep, self.settle = sleep, settle
        self.count = 0

    def reaches(self, to: str) -> bool:
        """Whether ``to`` is somebody in the game's chat: the person, everyone, or a human country."""
        to = (to or "").strip()
        return to.lower() in HUMAN_TARGETS or to.upper() in self.human_tags

    def send(self, to: str, text: str, date: str | None = None) -> SentLine:
        text = typable(text)
        if not text:
            raise ValueError("an empty message (nothing in it can be typed into the game's chat)")
        to = (to or "").strip()
        if not self.reaches(to):
            raise ValueError(f"{to!r} is not a person in this game (person, global, or one of "
                             f"{', '.join(sorted(self.human_tags)) or 'no human countries'})")
        # The game's chat has no addressee in a plain line, so say who it is from and to.
        who = "everyone" if to.lower() in ("person", "global") else to.upper()
        line = f"{self.tag} to {who}, {text}"[:MAX_CHARS]
        self.driver.press(self.open_key)
        self._sleep(self.settle)
        self.driver.type_text(line)
        self._sleep(self.settle)
        self.driver.press(self.send_key)
        self.count += 1
        return SentLine(self.count, to.lower() if to.lower() in HUMAN_TARGETS else to.upper(), line)


def find_driver(adapter):
    """The input driver inside ``adapter`` (a composite's writer, the headless writer's router,
    or the adapter itself), or None."""
    from .input_driver import InputDriverAdapter

    seen = []
    queue = [adapter]
    while queue:
        candidate = queue.pop(0)
        if candidate is None or any(candidate is s for s in seen):
            continue
        seen.append(candidate)
        if isinstance(candidate, InputDriverAdapter):
            return candidate
        # ``vars`` and not getattr: the router forwards unknown names to its driver, which
        # would answer for anything.
        held = vars(candidate) if hasattr(candidate, "__dict__") else {}
        queue += [held.get(name) for name in ("writer", "router", "driver")]
    return None


def attach(env, config) -> MultiplayerChat | None:
    """Wire the in-game chat into ``env`` for a multiplayer run. None when there is no input
    driver to type with (a log-only adapter), in which case send_message is refused."""
    driver = find_driver(env.adapter)
    if driver is None:
        return None
    sender = MultiplayerChat(driver, config.country or "", config.mp_human_tags)
    env.attach_mp_chat(sender)
    return sender
