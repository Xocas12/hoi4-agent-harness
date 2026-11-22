"""The tkinter tabs of the setup window: Countries, Settings and Profile and run.

Thin on purpose: every decision (what a field is, what a row writes, what the command line is,
what the preflight says) is in ``settings_model.py`` and tested there. This file only places
widgets and passes text to the model.
"""

from __future__ import annotations

from pathlib import Path

from . import settings_model as sm
from .settings_model import CountryRow, SettingsModel


def build_tabs(notebook, model: SettingsModel):             # pragma: no cover - needs a display
    """Add the three tabs to ``notebook``. Returns the object holding them (for tests and shots)."""
    return _Tabs(notebook, model)


class _Tabs:                                                # pragma: no cover - needs a display
    def __init__(self, notebook, model: SettingsModel):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk, self.m = tk, ttk, model
        self.setters: dict[str, callable] = {}
        self.countries = ttk.Frame(notebook, padding=8)
        self.settings = ttk.Frame(notebook, padding=8)
        self.run = ttk.Frame(notebook, padding=8)
        notebook.add(self.countries, text="Countries")
        notebook.add(self.settings, text="All settings")
        notebook.add(self.run, text="Profile and run")
        self._countries()
        self._settings()
        self._run()
        self.refresh()

    # --- countries --------------------------------------------------------------------------

    def _countries(self) -> None:
        tk, ttk = self.tk, self.ttk
        f = self.countries
        ttk.Label(f, wraplength=950, text=(
            "One line per country. Controller: an LLM plays it, the game's AI plays it, a person "
            "plays it, or nobody. Mode: campaign (your own country, run unattended), rival "
            "(another country beside you) or co-op. Game AI and off lines are not stored in a "
            "profile: a country nobody names is the game's AI.")).pack(anchor="w")
        cols = ("tag", "controller", "mode", "provider", "model", "key", "control", "instruction")
        self.table = ttk.Treeview(f, columns=cols, show="headings", height=8)
        for col in cols:
            self.table.heading(col, text=col)
            self.table.column(col, width=90 if col != "instruction" else 240)
        self.table.pack(fill="x", pady=6)
        self.table.bind("<<TreeviewSelect>>", lambda _e: self._pick_row())
        form = ttk.Frame(f)
        form.pack(fill="x")
        self.rv = {name: tk.StringVar() for name in
                   ("tag", "controller", "mode", "provider", "model", "key_env", "base_url",
                    "control", "max_usd")}
        spec = [("tag", None), ("controller", sm.CONTROLLERS), ("mode", sm.MODES),
                ("provider", _providers()), ("model", None), ("key_env", None),
                ("base_url", None), ("control", None), ("max_usd", None)]
        labels = {"key_env": "key variable", "max_usd": "spend cap (USD)", "base_url": "base URL",
                  "control": "control map"}
        for i, (name, values) in enumerate(spec):
            ttk.Label(form, text=labels.get(name, name)).grid(row=i // 3, column=(i % 3) * 2,
                                                              sticky="e", padx=2, pady=2)
            if values:
                w = ttk.Combobox(form, textvariable=self.rv[name], values=list(values), width=16,
                                 state="readonly")
            else:
                w = ttk.Entry(form, textvariable=self.rv[name], width=18)
            w.grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=2)
        ttk.Label(form, text="instruction").grid(row=3, column=0, sticky="ne")
        self.rinstr = tk.Text(form, height=3, width=70, wrap="word")
        self.rinstr.grid(row=3, column=1, columnspan=5, sticky="we", pady=2)
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="Add / update", command=self._save_row).pack(side="left")
        ttk.Button(bar, text="Remove", command=self._remove_row).pack(side="left", padx=6)
        self.row_msg = ttk.Label(f, wraplength=950, foreground="#a00")
        self.row_msg.pack(anchor="w")
        self.row_key = ttk.Label(f, foreground="#555")
        self.row_key.pack(anchor="w")

    def _pick_row(self) -> None:
        picked = self.table.selection()
        if not picked:
            return
        row = next((r for r in self.m.rows if r.tag == picked[0]), None)
        if row is None:
            return
        for name, var in self.rv.items():
            value = getattr(row, name)
            var.set("" if value is None else str(value))
        self.rinstr.delete("1.0", "end")
        self.rinstr.insert("1.0", row.instruction)
        self.row_key.configure(text=f"key: {sm.key_status(row, self.m.environ)}")

    def _row_from_form(self) -> CountryRow:
        v = {k: var.get().strip() for k, var in self.rv.items()}
        cap = v.pop("max_usd")
        try:
            max_usd = float(cap) if cap else None
        except ValueError:
            raise ValueError("The spend cap is a number of dollars, or empty.") from None
        v["tag"] = v["tag"].upper()
        return CountryRow(**v, instruction=self.rinstr.get("1.0", "end").strip(), max_usd=max_usd)

    def _save_row(self) -> None:
        try:
            row = self._row_from_form()
        except ValueError as exc:
            self.row_msg.configure(text=str(exc))
            return
        rows = [r for r in self.m.rows if r.tag != row.tag] + [row]
        problems = sm.row_problems(rows, self.m.environ)
        self.row_msg.configure(text=" ".join(problems))
        self.m.set_rows(rows)
        self.refresh()

    def _remove_row(self) -> None:
        picked = self.table.selection()
        if picked:
            self.m.remove_row(picked[0])
            self.refresh()

    # --- all settings -----------------------------------------------------------------------

    def _settings(self) -> None:
        tk, ttk = self.tk, self.ttk
        inner = ttk.Notebook(self.settings)
        inner.pack(fill="both", expand=True)
        self.set_msg = ttk.Label(self.settings, foreground="#a00", wraplength=950)
        self.set_msg.pack(anchor="w")
        for group, specs in sm.sections():
            outer = ttk.Frame(inner)
            inner.add(outer, text=group)
            canvas = tk.Canvas(outer, highlightthickness=0)
            bar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
            body = ttk.Frame(canvas, padding=6)
            body.bind("<Configure>", lambda _e, c=canvas: c.configure(scrollregion=c.bbox("all")))
            canvas.create_window((0, 0), window=body, anchor="nw")
            canvas.configure(yscrollcommand=bar.set)
            canvas.pack(side="left", fill="both", expand=True)
            bar.pack(side="right", fill="y")
            for i, spec in enumerate(specs):
                self._field(body, i, spec)

    def _field(self, body, row: int, spec: sm.FieldSpec) -> None:
        tk, ttk = self.tk, self.ttk
        ttk.Label(body, text=spec.path).grid(row=row * 2, column=0, sticky="ne", padx=4, pady=(6, 0))
        if spec.kind == sm.BOOL:
            var = tk.BooleanVar()
            w = ttk.Checkbutton(body, variable=var, command=lambda: self._commit(
                spec, "true" if var.get() else "false"))
            self.setters[spec.path] = lambda t, var=var: var.set(t == "true")
        elif spec.kind == sm.TRISTATE or (spec.kind == sm.CHOICE and not spec.open_choices):
            var = tk.StringVar()
            values = ["", "true", "false"] if spec.kind == sm.TRISTATE else list(spec.choices)
            w = ttk.Combobox(body, textvariable=var, values=values, width=30, state="readonly")
            w.bind("<<ComboboxSelected>>", lambda _e: self._commit(spec, var.get()))
            self.setters[spec.path] = lambda t, var=var: var.set(t)
        elif spec.multiline:
            w = tk.Text(body, height=3, width=70, wrap="word")
            w.bind("<FocusOut>", lambda _e: self._commit(spec, w.get("1.0", "end").rstrip("\n")))

            def put(t, w=w):
                w.delete("1.0", "end")
                w.insert("1.0", t)
            self.setters[spec.path] = put
        else:
            var = tk.StringVar()
            if spec.choices:
                w = ttk.Combobox(body, textvariable=var, values=list(spec.choices), width=40)
            else:
                w = ttk.Entry(body, textvariable=var, width=60)
            w.bind("<Return>", lambda _e: self._commit(spec, var.get()))
            w.bind("<FocusOut>", lambda _e: self._commit(spec, var.get()))
            self.setters[spec.path] = lambda t, var=var: var.set(t)
        w.grid(row=row * 2, column=1, sticky="w", pady=(6, 0))
        default = sm.format_value(spec, spec.default)
        note = f"{spec.help}  (default: {default or 'empty'})"
        ttk.Label(body, text=note, wraplength=760, foreground="#555").grid(
            row=row * 2 + 1, column=1, sticky="w")

    def _commit(self, spec: sm.FieldSpec, text: str) -> None:
        if text == self.m.get_text(spec.path):
            return
        try:
            self.m.set_text(spec.path, text)
        except ValueError as exc:
            self.set_msg.configure(text=str(exc))
            return
        self.set_msg.configure(text="")
        self.refresh()

    # --- profile and run --------------------------------------------------------------------

    def _run(self) -> None:
        tk, ttk = self.tk, self.ttk
        f = self.run
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text="Profile file").pack(side="left")
        self.path_var = tk.StringVar(value=str(self.m.profile_path))
        ttk.Entry(top, textvariable=self.path_var, width=70).pack(side="left", padx=4)
        ttk.Button(top, text="Browse...", command=self._browse).pack(side="left")
        ttk.Button(top, text="Load", command=self._load).pack(side="left", padx=4)
        ttk.Button(top, text="Save", command=self._save).pack(side="left")
        ttk.Label(f, text="The command line these settings stand for (save first; it names the file)"
                  ).pack(anchor="w", pady=(10, 0))
        self.cmd = tk.Text(f, height=3, wrap="word")
        self.cmd.pack(fill="x")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=8)
        ttk.Button(bar, text="Preflight check", command=self._preflight).pack(side="left")
        ttk.Button(bar, text="Start (checks first)", command=self._start).pack(side="left", padx=6)
        self.out = tk.Text(f, height=18, wrap="word", state="disabled")
        self.out.pack(fill="both", expand=True)
        self.run_msg = ttk.Label(f, foreground="#a00", wraplength=950)
        self.run_msg.pack(anchor="w")

    def _say(self, lines: list[str], note: str = "") -> None:
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("end", "\n".join(lines))
        self.out.configure(state="disabled")
        self.run_msg.configure(text=note)

    def _browse(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askopenfilename(title="Profile", filetypes=[("Profile", "*.json *.toml")])
        if chosen:
            self.path_var.set(chosen)
            self._load()

    def _load(self) -> None:
        try:
            self.m.load(Path(self.path_var.get()))
        except (OSError, ValueError, KeyError) as exc:
            self._say([], f"Could not load: {exc}")
            return
        self.refresh()
        self._say([f"Loaded {self.m.profile_path}"])

    def _save(self) -> None:
        self.m.profile_path = Path(self.path_var.get())
        try:
            path = self.m.save()
        except OSError as exc:
            self._say([], f"Could not save: {exc.strerror or exc}")
            return
        self.refresh()
        self._say([f"Saved {path}"])

    def _preflight(self) -> None:
        self._say(self.m.preflight().lines() or ["Nothing to check."])

    def _start(self) -> None:
        self.m.profile_path = Path(self.path_var.get())
        result, plan = self.m.start()
        lines = result.lines()
        if plan is None:
            self._say(lines, "Not started: fix the FAIL lines first.")
        else:
            self._say(lines + ["", f"Started. Log: {plan.log_path}"])
        self.refresh()

    # --- keeping the tabs in step -----------------------------------------------------------

    def refresh(self) -> None:
        for path, setter in self.setters.items():
            setter(self.m.get_text(path))
        for item in self.table.get_children():
            self.table.delete(item)
        for r in self.m.rows:
            short = r.instruction.replace("\n", " ")[:60]
            self.table.insert("", "end", iid=r.tag, values=(
                r.tag, r.controller, r.mode, r.provider, r.model or "(default)",
                sm.key_status(r, self.m.environ), r.control, short))
        self.path_var.set(str(self.m.profile_path))
        self.cmd.delete("1.0", "end")
        self.cmd.insert("1.0", self.m.command())


def _providers() -> tuple[str, ...]:
    from .agent.llm import PROVIDERS

    return tuple(PROVIDERS)
