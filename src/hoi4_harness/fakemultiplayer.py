"""A fake multiplayer game: the harness's client, and a person on another one (#91, #31).

``FakeRivalGame`` already models a harness country and a person's, with the person acting on a
wall-clock schedule. A multiplayer client differs in the ways this subclass adds, each one a
**simulation of what the live game is believed to do, not of what it does**:

* **both countries are human** (``ai=0``): the mod logs the harness's country and the person's
  alike, so the harness must keep to its own tag (``--country``), as in the rival fake;
* **the clock is the host's.** On a client the pause, resume and speed keys do nothing; each
  attempt is written to ``clock_attempts``, which a test asserts stays empty. Only the person's
  scheduled ``pause`` / ``resume`` (the host) move the date;
* **the console can be shut** (``console="blocked"``): a non-host client may not be allowed
  the console at all (docs/multiplayer.md, open question 1); ``"open"`` is the debug-mode case.
  A blocked console drops every command and says so in ``errors``;
* **the in-game chat**: Enter opens a chat box, typed keys fill it, Enter sends it. Lines the
  harness sends land in ``chat_sent``; what the person types to the harness is ``host_says``,
  read back through ``read_chat`` (which stands in for a reader the real harness does not have:
  the game's chat cannot be read back, see ``adapters/mp_chat.py``).

What is not simulated: lobby, latency between the two clients, desync, the host's own country
being scored by the same log, or a real chat box's limits. ``docs/multiplayer.md`` lists them.
"""

from __future__ import annotations

from pathlib import Path

from .fakerival import Act, FakeRivalGame


class FakeMultiplayerGame(FakeRivalGame):
    def __init__(self, user_dir: Path, harness: str = "FRA", human: str = "GER",
                 human_script: list[Act] | None = None, console: str = "open", **kwargs):
        if console not in ("open", "blocked"):
            raise ValueError("console is open (debug mode) or blocked")
        self.console_mode = console
        #: Every lock-step clock key the harness's client pressed (should stay empty).
        self.clock_attempts: list[str] = []
        self.chat_open = False
        self._chat_line = ""
        #: ``(date, text)`` of each line the harness's client sent to the game's chat.
        self.chat_sent: list[tuple[str, str]] = []
        self._incoming: list[str] = []
        super().__init__(user_dir, rival=harness, human=human, human_script=human_script, **kwargs)
        self.human_ai = False           # a human client: the mod logs it by is_ai = no

    @property
    def visible(self) -> bool:
        return True

    @property
    def harness(self) -> str:
        return self.country

    # --- the chat --------------------------------------------------------------------

    def host_says(self, text: str) -> None:
        """The person types a line to the harness's country (in a real game, into the chat)."""
        self._incoming.append(text)

    def read_chat(self) -> list[str]:
        """What the person has said since the last read: the seam a real harness would need."""
        out, self._incoming = self._incoming, []
        return out

    # --- keys ----------------------------------------------------------------------------

    def _key(self, name: str, mods: frozenset[str]) -> None:
        if name == "grave" or self.console_open:
            return super()._key(name, mods)
        if self.chat_open:
            if name == "enter" and not mods:
                self.chat_open = False
                line, self._chat_line = self._chat_line, ""
                if line:
                    self.chat_sent.append((self.date, line))
            elif name == "escape":
                self.chat_open, self._chat_line = False, ""
            elif name == "backspace":
                self._chat_line = self._chat_line[:-1]
            else:
                char = self._char(name, mods)
                if char is not None:
                    self._chat_line += char
            return None
        if name == "enter" and not mods and self.panel is None and self.search is None:
            self.chat_open = True
            return None
        if name in ("space", "kp_plus", "kp_minus") and not mods and self.search is None:
            self.clock_attempts.append(name)    # a client has no say over the host's clock
            return None
        return super()._key(name, mods)

    def _console(self, line: str) -> None:
        if self.console_mode == "blocked" and line.strip():
            self.errors.append("the console is not available to a client in this game")
            self._log("console disabled for this client")
            return None
        return super()._console(line)
