"""The chat window: talk to the LLM countries, and read what they say to you.

``ChatModel`` is everything the window knows, with no widget in it (so tests run headless):
which channels exist (Global, one per LLM country, and the human's own country, shown but not
messageable), what each conversation holds, what is unread, and how a line is sent. It reads and
writes only through ``chat.ChatLog``, the same file ``hoi4-harness chat`` and the countries use,
and takes the countries from the players registry (players.py) plus anyone seen in the chat.
``ChatWindow`` is the Tk shell: a channel list, the conversation, an entry. It polls the file once
a second, so what a country says shows up while it is open.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .chat import ChatLog, Message
from .players import PlayersRegistry

GLOBAL = "global"
POLL_MS = 1000


@dataclass(frozen=True)
class Channel:
    id: str                 # "global" or a country tag
    label: str
    kind: str               # "global", "llm" or "human"
    status: str = ""        # an LLM country's run status (running, waiting, stopped, ...)
    unread: int = 0

    @property
    def can_send(self) -> bool:
        return self.kind != "human"

    def title(self) -> str:
        text = self.label
        if self.kind == "llm" and self.status:
            text += f"  ({self.status})"
        elif self.kind == "human":
            text += "  (you)"
        if self.unread:
            text += f"  [{self.unread} new]"
        return text


class ChatModel:
    def __init__(self, log: ChatLog | None = None, registry: PlayersRegistry | None = None,
                 human_tags=(), names: dict[str, str] | None = None):
        self.log = log or ChatLog()
        self.registry = registry or PlayersRegistry()
        self.human_tags = [t.strip().upper() for t in human_tags if t and t.strip()]
        self.names = dict(names or {})
        self._read: dict[str, int] = {}

    # --- channels -----------------------------------------------------------

    def _label(self, tag: str) -> str:
        name = self.names.get(tag)
        return f"{tag} {name}" if name and name != tag else tag

    def llm_tags(self) -> list[str]:
        """Every LLM country: the registry's, and anyone who wrote to the person or was written
        to by the person (a run that has since cleared its registry row still has a thread)."""
        tags = {p.tag for p in self.registry.players()}
        for m in self.log.messages():
            if m.frm == "person" and m.to not in ("global", "person"):
                tags.add(m.to)
            elif m.frm != "person":
                tags.add(m.frm)
        return sorted(tags - set(self.human_tags))

    def channels(self) -> list[Channel]:
        status = {p.tag: p.status for p in self.registry.players()}
        out = [Channel(GLOBAL, "Everyone (global)", "global", unread=self.unread(GLOBAL))]
        for tag in self.llm_tags():
            out.append(Channel(tag, self._label(tag), "llm", status.get(tag, ""), self.unread(tag)))
        for tag in self.human_tags:
            out.append(Channel(tag, self._label(tag), "human"))
        return out

    def channel(self, channel_id: str) -> Channel:
        for c in self.channels():
            if c.id == channel_id:
                return c
        raise KeyError(channel_id)

    # --- conversations ------------------------------------------------------

    def conversation(self, channel_id: str) -> list[Message]:
        """Global: everything said to everyone. A country: the person's lines to it and its lines
        to the person (what two countries say to each other stays between them)."""
        if channel_id == GLOBAL:
            return [m for m in self.log.messages() if m.to == GLOBAL]
        tag = channel_id.upper()
        return [m for m in self.log.messages()
                if (m.frm == "person" and m.to == tag) or (m.frm == tag and m.to == "person")]

    def render(self, message: Message, channel_id: str) -> str:
        who = "You" if message.frm == "person" else self._label(message.frm)
        if channel_id == GLOBAL and message.frm == "person":
            who = "You (to everyone)"
        when = f"{message.date}  " if message.date else ""
        return f"{when}{who}: {message.text}"

    def lines(self, channel_id: str) -> list[str]:
        return [self.render(m, channel_id) for m in self.conversation(channel_id)]

    # --- unread -------------------------------------------------------------

    def unread(self, channel_id: str) -> int:
        seen = self._read.get(channel_id, 0)
        return sum(1 for m in self.conversation(channel_id) if m.id > seen and m.frm != "person")

    def mark_read(self, channel_id: str) -> None:
        convo = self.conversation(channel_id)
        if convo:
            self._read[channel_id] = max(self._read.get(channel_id, 0), convo[-1].id)

    # --- sending ------------------------------------------------------------

    def send(self, channel_id: str, text: str) -> Message:
        """Post the person's line to Global or to one country. Raises ValueError for an empty
        message or for the human's own country."""
        if channel_id != GLOBAL and channel_id.upper() in self.human_tags:
            raise ValueError(f"{channel_id} is the country you play; there is no one to message")
        message = self.log.post("person", GLOBAL if channel_id == GLOBAL else channel_id, text)
        self.mark_read(channel_id)
        return message


def human_tags_from_log() -> list[str]:
    """Best effort: the countries game.log shows as human. Empty when the game is not found."""
    try:
        from .adapters.logtail import find_log
        from .rival.setup import human_tags_in_log

        return human_tags_in_log(find_log(None))
    except Exception:
        return []


def default_model(chat_path: Path | None = None) -> ChatModel:
    """The model the real window uses: the shared chat file, the registry, the human country from
    the saved setup (or the game log), and country names when a game folder is found."""
    from . import setup_model

    human: list[str] = []
    names: dict[str, str] = {}
    try:
        saved = setup_model.Setup.load()
        if saved.my_country:
            human.append(saved.my_country)
        game_dir = setup_model.find_install(saved.game_dir or None)
    except (OSError, ValueError):
        game_dir = setup_model.find_install()
    for tag in human_tags_from_log():
        if tag not in human:
            human.append(tag)
    try:
        names = dict(setup_model.load_countries(game_dir))
    except OSError:
        pass
    return ChatModel(ChatLog(chat_path), PlayersRegistry(), human, names)


class ChatWindow:                                               # pragma: no cover - needs a display
    """The Tk shell around ChatModel. ``master`` given: a window on top of it (the setup window)."""

    def __init__(self, model: ChatModel, master=None):
        import tkinter as tk
        from tkinter import ttk

        self.model = model
        self.tk = tk
        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        self.root.title("Chat with the AI countries")
        self.root.geometry("820x520")
        self.current = GLOBAL
        self._ids: list[str] = []
        self._shown = (None, 0)

        left = ttk.Frame(self.root, padding=8)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Who").pack(anchor="w")
        self.listbox = tk.Listbox(left, width=30, exportselection=False, activestyle="none")
        self.listbox.pack(fill="y", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._picked)
        right = ttk.Frame(self.root, padding=8)
        right.pack(side="left", fill="both", expand=True)
        self.header = ttk.Label(right, font=("TkDefaultFont", 11, "bold"))
        self.header.pack(anchor="w")
        box = ttk.Frame(right)
        box.pack(fill="both", expand=True, pady=6)
        self.text = tk.Text(box, wrap="word", state="disabled", height=20)
        bar = ttk.Scrollbar(box, command=self.text.yview)
        self.text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        row = ttk.Frame(right)
        row.pack(fill="x")
        self.entry = ttk.Entry(row)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _e: self.send())
        self.send_button = ttk.Button(row, text="Send", command=self.send)
        self.send_button.pack(side="left", padx=(6, 0))
        self.note = ttk.Label(right, foreground="#a00")
        self.note.pack(anchor="w")
        self.refresh()
        self.root.after(POLL_MS, self._tick)

    def _picked(self, _event=None) -> None:
        picked = self.listbox.curselection()
        if picked and picked[0] < len(self._ids):
            self.current = self._ids[picked[0]]
            self.refresh()

    def refresh(self) -> None:
        channels = self.model.channels()
        self._ids = [c.id for c in channels]
        if self.current not in self._ids:
            self.current = GLOBAL
        titles = [c.title() for c in channels]
        if titles != getattr(self, "_titles", None):
            self._titles = titles
            self.listbox.delete(0, "end")
            for title in titles:
                self.listbox.insert("end", title)
        self.listbox.selection_clear(0, "end")
        index = self._ids.index(self.current)
        self.listbox.selection_set(index)
        channel = channels[index]
        self.header.configure(text="Everyone" if channel.kind == "global" else channel.label)
        self.send_button.configure(state="normal" if channel.can_send else "disabled")
        self.entry.configure(state="normal" if channel.can_send else "disabled")
        convo = self.model.conversation(self.current)
        stamp = (self.current, convo[-1].id if convo else 0, len(convo))
        if stamp != self._shown:
            self._shown = stamp
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            for line in self.model.lines(self.current):
                self.text.insert("end", line + "\n")
            if not convo:
                self.text.insert("end", "No messages yet." if channel.can_send
                                 else "This is the country you play.")
            self.text.configure(state="disabled")
            self.text.see("end")
        self.model.mark_read(self.current)

    def send(self) -> None:
        try:
            self.model.send(self.current, self.entry.get())
        except ValueError as exc:
            self.note.configure(text=str(exc))
            return
        self.note.configure(text="")
        self.entry.delete(0, "end")
        self.refresh()

    def _tick(self) -> None:
        try:
            self.refresh()
        finally:
            self.root.after(POLL_MS, self._tick)


def run(chat_path: Path | None = None) -> int:                  # pragma: no cover - needs a display
    import tkinter

    try:
        window = ChatWindow(default_model(chat_path))
    except tkinter.TclError as exc:
        print(f"chat-gui: no display to open a window on ({exc}). "
              "Use `hoi4-harness chat` in a terminal.")
        return 1
    window.root.mainloop()
    return 0
