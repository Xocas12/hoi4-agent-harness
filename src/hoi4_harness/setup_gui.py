"""The setup window: who plays which country, with which model.

``SetupController`` is what the window does, with no widget in it (tests drive it headless): edit
the countries, set a model and an instruction for each, hold the API keys without ever handing one
back, save ``setup.json``, start the run and report how it is going. ``SetupWindow`` is the Tk
shell around it. Both are written for someone who has never opened a terminal: plain words, no
flags, and a message (not a traceback) when something is missing.

The window can be closed while the run goes on: Start launches ``hoi4-harness start --setup ...``
as a background process (setup_model.launch), and the chat window or ``players.json`` shows it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import setup_model
from .players import PlayersRegistry
from .setup_model import PlayerSetup, Setup


@dataclass
class StartResult:
    ok: bool
    problems: list[str]
    pid: int | None = None
    log_path: Path | None = None


@dataclass
class PlayerStatus:
    tag: str
    text: str


class SetupController:
    def __init__(self, setup: Setup | None = None, path: Path | None = None,
                 countries: list[tuple[str, str]] | None = None, launcher=None,
                 registry: PlayersRegistry | None = None, environ=None):
        self.path = Path(path) if path else setup_model.default_path()
        self.setup = setup if setup is not None else self._load()
        self.environ = environ
        self.install = setup_model.find_install(self.setup.game_dir or None, environ)
        if countries is None:
            try:
                countries = setup_model.load_countries(self.install)
            except OSError:
                countries = []
        self.countries = countries
        self.launcher = launcher or setup_model.launch
        self.registry = registry or PlayersRegistry()
        self.launched: tuple[Path, int] | None = None     # (log path, pid) of the last Start

    def _load(self) -> Setup:
        try:
            return Setup.load(self.path)
        except (FileNotFoundError, ValueError):
            return Setup()

    # --- countries ----------------------------------------------------------

    def search(self, query: str) -> list[tuple[str, str]]:
        return setup_model.filter_countries(self.countries, query)

    def name_of(self, tag: str) -> str:
        return dict(self.countries).get(tag.upper(), "")

    def add_player(self, tag: str) -> PlayerSetup:
        tag = (tag or "").strip().upper()
        if len(tag) != 3 or not tag.isalnum():
            raise ValueError("A country tag is three letters or digits, like POL or GER.")
        if any(p.tag == tag for p in self.setup.players):
            raise ValueError(f"{tag} is already in the list.")
        if tag == self.setup.my_country:
            raise ValueError(f"{tag} is your own country; the AI plays a different one.")
        player = PlayerSetup(tag)
        self.setup.players.append(player)
        return player

    def remove_player(self, tag: str) -> None:
        self.setup.players = [p for p in self.setup.players if p.tag != tag.upper()]

    def player(self, tag: str) -> PlayerSetup:
        for p in self.setup.players:
            if p.tag == tag.upper():
                return p
        raise KeyError(tag)

    def set_provider(self, tag: str, choice_key: str) -> PlayerSetup:
        """Pick the model type for a country: its model name and cap go back to sensible defaults."""
        choice = setup_model.choice_for(choice_key)
        p = self.player(tag)
        p.provider = choice.key
        p.model = "" if choice.needs_url else choice.models[0]
        p.base_url = setup_model.DEFAULT_LOCAL_URL if choice.needs_url else ""
        p.max_usd = setup_model.DEFAULT_CAP_USD if choice.paid else None
        if not choice.paid:
            p.usd_per_m_input = p.usd_per_m_output = None
        return p

    def update_player(self, tag: str, **fields) -> PlayerSetup:
        p = self.player(tag)
        for key, value in fields.items():
            if key not in PlayerSetup.__dataclass_fields__ or key in ("tag", "provider"):
                raise KeyError(key)
            setattr(p, key, value)
        return p

    # --- keys ---------------------------------------------------------------

    def set_key(self, choice_key: str, value: str) -> None:
        """Store a key. An empty value changes nothing (the field is blank once a key is saved)."""
        if choice_key not in setup_model.BY_KEY or setup_model.BY_KEY[choice_key].key_env is None:
            raise KeyError(choice_key)
        if value and value.strip():
            self.setup.keys[choice_key] = value.strip()

    def remove_key(self, choice_key: str) -> None:
        self.setup.keys.pop(choice_key, None)

    def key_status(self, choice_key: str) -> str:
        """``saved``, ``environment`` (the variable is already set) or ``missing``. Never the key."""
        if self.setup.keys.get(choice_key):
            return "saved"
        choice = setup_model.choice_for(choice_key)
        env = self.environ if self.environ is not None else __import__("os").environ
        return "environment" if choice.key_env and env.get(choice.key_env) else "missing"

    # --- options ------------------------------------------------------------

    def set_options(self, **fields) -> None:
        for key, value in fields.items():
            if key not in ("launch_game", "game_dir", "telemetry_hours", "keep_running", "until",
                           "my_country"):
                raise KeyError(key)
            setattr(self.setup, key, value)
        self.install = setup_model.find_install(self.setup.game_dir or None, self.environ)
        self.countries = setup_model.load_countries(self.install)

    # --- save / start / status ----------------------------------------------

    def problems(self) -> list[str]:
        return self.setup.problems(self.environ)

    def save(self) -> Path:
        return self.setup.save(self.path)

    def start(self) -> StartResult:
        problems = self.problems()
        if problems:
            return StartResult(False, problems)
        self.save()
        plan, pid = self.launcher(self.setup)
        self.launched = (plan.log_path, pid)
        return StartResult(True, [], pid, plan.log_path)

    def status(self) -> list[PlayerStatus]:
        rows = {p.tag: p for p in self.registry.players()}
        out = []
        for p in self.setup.players:
            row = rows.get(p.tag)
            out.append(PlayerStatus(p.tag, _words(row.status) if row else "not started"))
        return out

    def log_tail(self, lines: int = 8) -> list[str]:
        return setup_model.tail(self.launched[0], lines) if self.launched else []


def _words(status: str) -> str:
    return {"waiting": "waiting for the game to start", "running": "playing",
            "finished": "finished", "stopped": "stopped", "failed": "stopped with an error",
            "starting": "starting"}.get(status, status)


# --- the window ----------------------------------------------------------------------------

TELEMETRY_CHOICES = ("", "1", "2", "3", "4", "6", "8", "12", "24", "48", "72", "168")


class SetupWindow:                                              # pragma: no cover - needs a display
    def __init__(self, controller: SetupController, settings=None):
        import tkinter as tk
        from tkinter import ttk

        from .settings_gui import build_tabs
        from .settings_model import SettingsModel

        self.c = controller
        self.tk, self.ttk = tk, ttk
        self.root = tk.Tk()
        self.root.title("Hearts of Iron IV: AI countries")
        self.root.geometry("1040x800")
        self.selected: str | None = None
        self._loading = False
        self.settings = settings if settings is not None else SettingsModel()
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True)
        self.quick = ttk.Frame(self.tabs)
        self.tabs.add(self.quick, text="Quick start")
        self._build()
        self.advanced = build_tabs(self.tabs, self.settings)
        self._reload_players()
        self.root.after(2000, self._tick)

    # layout ------------------------------------------------------------------

    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.quick, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Pick the countries an AI will play beside you, and what it uses to "
                              "think. Nothing starts until you press Start.",
                  wraplength=900).pack(anchor="w")

        top = ttk.Frame(outer)
        top.pack(fill="both", expand=True, pady=8)

        pick = ttk.LabelFrame(top, text="1. Choose countries", padding=6)
        pick.pack(side="left", fill="y")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._fill_countries())
        ttk.Label(pick, text="Search by name or tag" if self.c.countries
                  else "Type a country tag (three letters, like POL)").pack(anchor="w")
        entry = ttk.Entry(pick, textvariable=self.search)
        entry.pack(fill="x")
        entry.bind("<Return>", lambda _e: self._add_typed())
        self.country_list = tk.Listbox(pick, width=30, height=12, exportselection=False)
        self.country_list.pack(fill="y", expand=True, pady=4)
        self.country_list.bind("<Double-Button-1>", lambda _e: self._add_selected())
        ttk.Button(pick, text="Add to the AI's countries", command=self._add_selected).pack(fill="x")
        ttk.Label(pick, text="Your own country (so the AI never takes it)").pack(anchor="w", pady=(8, 0))
        self.mine = tk.StringVar(value=self.c.setup.my_country)
        mine = ttk.Entry(pick, textvariable=self.mine, width=8)
        mine.pack(anchor="w")
        self.mine.trace_add("write", lambda *_: self.c.set_options(
            my_country=self.mine.get().strip().upper()))
        self._fill_countries()

        mid = ttk.LabelFrame(top, text="2. The AI's countries", padding=6)
        mid.pack(side="left", fill="both", expand=True, padx=8)
        self.players = tk.Listbox(mid, height=5, exportselection=False)
        self.players.pack(fill="x")
        self.players.bind("<<ListboxSelect>>", lambda _e: self._select())
        ttk.Button(mid, text="Remove selected", command=self._remove).pack(anchor="e", pady=2)

        self.detail = ttk.Frame(mid)
        self.detail.pack(fill="both", expand=True)
        labels = {c.label: c.key for c in setup_model.CHOICES}
        self.label_to_key = labels
        self.key_to_label = {v: k for k, v in labels.items()}
        self.v_model_kind = tk.StringVar()
        self.v_model = tk.StringVar()
        self.v_url = tk.StringVar()
        self.v_cap = tk.StringVar()
        self.v_price_in = tk.StringVar()
        self.v_price_out = tk.StringVar()
        d = self.detail
        ttk.Label(d, text="Thinks with").grid(row=0, column=0, sticky="w")
        self.kind_box = ttk.Combobox(d, textvariable=self.v_model_kind, state="readonly",
                                     values=list(labels), width=40)
        self.kind_box.grid(row=0, column=1, sticky="we", pady=2)
        self.kind_box.bind("<<ComboboxSelected>>", lambda _e: self._kind_changed())
        ttk.Label(d, text="Model").grid(row=1, column=0, sticky="w")
        self.model_box = ttk.Combobox(d, textvariable=self.v_model, width=40)
        self.model_box.grid(row=1, column=1, sticky="we", pady=2)
        self.url_label = ttk.Label(d, text="Address of your local model")
        self.url_entry = ttk.Entry(d, textvariable=self.v_url)
        self.url_label.grid(row=2, column=0, sticky="w")
        self.url_entry.grid(row=2, column=1, sticky="we", pady=2)
        self.cap_label = ttk.Label(d, text="Spend limit (US dollars)")
        self.cap_entry = ttk.Entry(d, textvariable=self.v_cap, width=10)
        self.cap_label.grid(row=3, column=0, sticky="w")
        self.cap_entry.grid(row=3, column=1, sticky="w", pady=2)
        self.price_label = ttk.Label(d, text="Price per million tokens, in / out ($)")
        price = ttk.Frame(d)
        ttk.Entry(price, textvariable=self.v_price_in, width=8).pack(side="left")
        ttk.Entry(price, textvariable=self.v_price_out, width=8).pack(side="left", padx=4)
        self.price_frame = price
        self.price_label.grid(row=4, column=0, sticky="w")
        price.grid(row=4, column=1, sticky="w", pady=2)
        ttk.Label(d, text="Starting instruction").grid(row=5, column=0, sticky="nw")
        self.instruction = tk.Text(d, height=4, width=48, wrap="word")
        self.instruction.grid(row=5, column=1, sticky="we", pady=2)
        ttk.Label(d, text='e.g. "go alt-history Germany and ally the UK" or "go communist"',
                  foreground="#666").grid(row=6, column=1, sticky="w")
        d.columnconfigure(1, weight=1)
        for var in (self.v_model, self.v_url, self.v_cap, self.v_price_in, self.v_price_out):
            var.trace_add("write", lambda *_: self._store())
        self.instruction.bind("<<Modified>>", lambda _e: self._instruction_edited())

        keys = ttk.LabelFrame(outer, text="3. Keys (only for the paid choices; never shown again once saved)",
                              padding=6)
        keys.pack(fill="x")
        self.key_vars: dict[str, tuple] = {}
        for col, key in enumerate(("anthropic", "openai", "google")):
            frame = ttk.Frame(keys)
            frame.grid(row=0, column=col, padx=6, sticky="w")
            ttk.Label(frame, text=setup_model.BY_KEY[key].label).pack(anchor="w")
            var = tk.StringVar()
            ttk.Entry(frame, textvariable=var, show="*", width=26).pack(side="left")
            state = ttk.Label(frame, width=14)
            state.pack(side="left", padx=4)
            ttk.Button(frame, text="Forget", width=7,
                       command=lambda k=key: (self.c.remove_key(k), self._key_states())).pack(side="left")
            self.key_vars[key] = (var, state)
        self._key_states()

        opts = ttk.LabelFrame(outer, text="4. Options", padding=6)
        opts.pack(fill="x", pady=6)
        s = self.c.setup
        self.v_launch = tk.BooleanVar(value=s.launch_game)
        self.v_keep = tk.BooleanVar(value=s.keep_running)
        self.v_tele = tk.StringVar(value="" if s.telemetry_hours is None else str(s.telemetry_hours))
        self.v_until = tk.StringVar(value=s.until)
        self.v_dir = tk.StringVar(value=s.game_dir or (str(self.c.install) if self.c.install else ""))
        ttk.Checkbutton(opts, text="Start the game for me in debug mode", variable=self.v_launch,
                        command=self._options).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(opts, text="Keep going when I restart the game", variable=self.v_keep,
                        command=self._options).grid(row=0, column=1, sticky="w", padx=12)
        ttk.Label(opts, text="Game reports every (hours)").grid(row=0, column=2, padx=(12, 2))
        ttk.Combobox(opts, textvariable=self.v_tele, values=TELEMETRY_CHOICES, width=5).grid(row=0, column=3)
        ttk.Label(opts, text="Stop at date").grid(row=1, column=0, sticky="e", pady=4)
        ttk.Entry(opts, textvariable=self.v_until, width=12).grid(row=1, column=1, sticky="w")
        ttk.Label(opts, text="Game folder").grid(row=1, column=2, sticky="e")
        ttk.Entry(opts, textvariable=self.v_dir, width=40).grid(row=1, column=3, columnspan=2, sticky="we")
        ttk.Button(opts, text="Browse...", command=self._browse).grid(row=1, column=5, padx=4)
        for var in (self.v_tele, self.v_until, self.v_dir):
            var.trace_add("write", lambda *_: self._options())

        bar = ttk.Frame(outer)
        bar.pack(fill="x")
        ttk.Button(bar, text="Save", command=self._save).pack(side="left")
        ttk.Button(bar, text="Start", command=self._start).pack(side="left", padx=6)
        ttk.Button(bar, text="Open chat window", command=self._chat).pack(side="left")
        ttk.Button(bar, text="Close (runs keep going)", command=self.root.destroy).pack(side="right")
        self.message = ttk.Label(outer, wraplength=900, foreground="#a00")
        self.message.pack(anchor="w", pady=2)
        self.status = tk.Text(outer, height=7, state="disabled")
        self.status.pack(fill="x")

    # actions -----------------------------------------------------------------

    def _say(self, text: str, bad: bool = True) -> None:
        self.message.configure(text=text, foreground="#a00" if bad else "#060")

    def _fill_countries(self) -> None:
        self.country_list.delete(0, "end")
        for tag, name in self.c.search(self.search.get()):
            self.country_list.insert("end", f"{tag}  {name}" if name != tag else tag)

    def _add_typed(self) -> None:
        self._add(self.search.get())

    def _add_selected(self) -> None:
        picked = self.country_list.curselection()
        if picked:
            self._add(self.country_list.get(picked[0]).split()[0])
        elif self.search.get().strip():
            self._add(self.search.get())

    def _add(self, tag: str) -> None:
        try:
            self.c.add_player(tag)
        except ValueError as exc:
            self._say(str(exc))
            return
        self._say("")
        self._reload_players(select=tag.strip().upper())

    def _remove(self) -> None:
        if self.selected:
            self.c.remove_player(self.selected)
            self.selected = None
            self._reload_players()

    def _reload_players(self, select: str | None = None) -> None:
        self.players.delete(0, "end")
        for p in self.c.setup.players:
            name = self.c.name_of(p.tag)
            kind = self.key_to_label[p.provider].split(" (")[0]
            self.players.insert("end", f"{p.tag}  {name}  -  {kind}")
        tags = [p.tag for p in self.c.setup.players]
        if select in tags:
            self.players.selection_set(tags.index(select))
        elif tags and not self.selected:
            self.players.selection_set(0)
        self._select()

    def _select(self) -> None:
        picked = self.players.curselection()
        tags = [p.tag for p in self.c.setup.players]
        self.selected = tags[picked[0]] if picked and picked[0] < len(tags) else None
        self._loading = True
        try:
            if self.selected is None:
                for var in (self.v_model_kind, self.v_model, self.v_url, self.v_cap,
                            self.v_price_in, self.v_price_out):
                    var.set("")
                self.instruction.delete("1.0", "end")
                return
            p = self.c.player(self.selected)
            choice = setup_model.BY_KEY[p.provider]
            self.v_model_kind.set(self.key_to_label[p.provider])
            self.model_box.configure(values=list(choice.models))
            self.v_model.set(p.model)
            self.v_url.set(p.base_url)
            self.v_cap.set("" if p.max_usd is None else f"{p.max_usd:g}")
            self.v_price_in.set("" if p.usd_per_m_input is None else f"{p.usd_per_m_input:g}")
            self.v_price_out.set("" if p.usd_per_m_output is None else f"{p.usd_per_m_output:g}")
            self.instruction.delete("1.0", "end")
            self.instruction.insert("1.0", p.instruction)
            self.instruction.edit_modified(False)
            self._show_fields(choice)
        finally:
            self._loading = False

    def _show_fields(self, choice) -> None:
        def show(widgets, on):
            for w in widgets:
                w.grid() if on else w.grid_remove()
        show((self.url_label, self.url_entry), choice.needs_url)
        show((self.cap_label, self.cap_entry, self.price_label, self.price_frame), choice.paid)

    def _kind_changed(self) -> None:
        if not self.selected or self._loading:
            return
        self.c.set_provider(self.selected, self.label_to_key[self.v_model_kind.get()])
        self._reload_players(select=self.selected)

    def _num(self, text: str) -> float | None:
        try:
            return float(text) if text.strip() else None
        except ValueError:
            return None

    def _store(self) -> None:
        if self._loading or not self.selected:
            return
        self.c.update_player(self.selected, model=self.v_model.get(), base_url=self.v_url.get(),
                             max_usd=self._num(self.v_cap.get()),
                             usd_per_m_input=self._num(self.v_price_in.get()),
                             usd_per_m_output=self._num(self.v_price_out.get()))

    def _instruction_edited(self) -> None:
        if self.instruction.edit_modified() and self.selected and not self._loading:
            self.c.update_player(self.selected, instruction=self.instruction.get("1.0", "end").strip())
        self.instruction.edit_modified(False)

    def _options(self) -> None:
        hours = self.v_tele.get().strip()
        self.c.set_options(launch_game=self.v_launch.get(), keep_running=self.v_keep.get(),
                           telemetry_hours=int(hours) if hours.isdigit() else None,
                           until=self.v_until.get().strip(), game_dir=self.v_dir.get().strip())

    def _browse(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(title="Hearts of Iron IV folder")
        if chosen:
            self.v_dir.set(chosen)

    def _take_keys(self) -> None:
        for key, (var, _state) in self.key_vars.items():
            if var.get().strip():
                self.c.set_key(key, var.get())
                var.set("")                       # a saved key is never shown again
        self._key_states()

    def _key_states(self) -> None:
        words = {"saved": "saved (hidden)", "environment": "found on this PC", "missing": "not set"}
        for key, (_var, state) in self.key_vars.items():
            state.configure(text=words[self.c.key_status(key)])

    def _save(self) -> None:
        self._take_keys()
        self._options()
        try:
            path = self.c.save()
        except OSError as exc:
            self._say(f"Could not save: {exc.strerror or exc}")
            return
        self._say(f"Saved to {path}", bad=False)

    def _start(self) -> None:
        self._take_keys()
        self._options()
        result = self.c.start()
        if not result.ok:
            self._say(" ".join(result.problems))
            return
        self._say("Started. You can close this window; the AI keeps playing.", bad=False)

    def _chat(self) -> None:
        from .chat_gui import ChatModel, ChatWindow, default_model

        model: ChatModel = default_model()
        ChatWindow(model, master=self.root)

    def _tick(self) -> None:
        lines = [f"{s.tag}: {s.text}" for s in self.c.status()]
        tail = self.c.log_tail()
        if tail:
            lines += ["", "Latest from the run:"] + tail
        self.status.configure(state="normal")
        self.status.delete("1.0", "end")
        self.status.insert("end", "\n".join(lines))
        self.status.configure(state="disabled")
        self.root.after(2000, self._tick)


def run() -> int:                                               # pragma: no cover - needs a display
    import tkinter

    try:
        window = SetupWindow(SetupController())
    except tkinter.TclError as exc:
        print(f"setup: no display to open a window on ({exc}). "
              "Use `hoi4-harness start --setup FILE` (docs/setup.md).")
        return 1
    window.root.mainloop()
    return 0
