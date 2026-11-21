"""What the setup window edits, with no window in it (``setup_gui.py`` is only widgets).

A :class:`Setup` is the whole answer to "who plays what": the LLM countries (tag, model, standing
instruction, spend cap), the API keys, and the run options. It round-trips through
``~/.hoi4-harness/setup.json`` (``HOI4_HARNESS_HOME`` moves it) and is what
``hoi4-harness start --setup FILE`` runs, so the window and the command line do the same thing.

Keys are the one secret here. They are stored per provider in setup.json (written readable by the
person alone where the OS allows), never printed or logged, and reach a run only through the child
process's environment (``ANTHROPIC_API_KEY`` and so on). The copy of the setup a launch hands the
child (``run copy``) has no keys in it, and the child's argv names that file, not a key.

Several countries are ONE process (``native --rival A,B``), not one process each: orders reach the
game through a single inbox file (inbox.py) that each process would rewrite from its own list.
Each country keeps its own model, instruction and budget through ``HarnessConfig.rival_players``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import cadence
from .home import home_dir

DEFAULT_STEAM_DIR = Path("C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV")
SETUP_FILE = "setup.json"
LOCAL_KEY_ENV = "HOI4_LOCAL_API_KEY"
DEFAULT_CAP_USD = 5.0
_TAG = re.compile(r"^[A-Z0-9]{3}$")


@dataclass(frozen=True)
class ProviderChoice:
    """One line of the model dropdown, in the words a non-technical person reads."""

    key: str                  # what setup.json stores
    label: str
    provider: str             # the harness provider it runs on
    key_env: str | None       # the environment variable the child's key travels in
    paid: bool
    models: tuple[str, ...]
    needs_url: bool = False


CHOICES: tuple[ProviderChoice, ...] = (
    ProviderChoice("claude-cli", "Claude Code (your subscription, no key)", "claude-cli", None,
                   False, ("haiku", "sonnet", "opus")),
    ProviderChoice("anthropic", "Anthropic API key", "anthropic", "ANTHROPIC_API_KEY", True,
                   ("claude-opus-5", "claude-sonnet-5-5")),
    ProviderChoice("openai", "OpenAI API key", "openai", "OPENAI_API_KEY", True,
                   ("gpt-5", "gpt-5-mini")),
    ProviderChoice("google", "Google API key", "google", "GOOGLE_API_KEY", True,
                   ("gemini-2.5-pro", "gemini-2.5-flash")),
    ProviderChoice("local", "Local model (Ollama, LM Studio, ...)", "openai", LOCAL_KEY_ENV, False,
                   ("llama3.1", "qwen2.5"), needs_url=True),
)
BY_KEY = {c.key: c for c in CHOICES}
DEFAULT_LOCAL_URL = "http://localhost:11434/v1"


def choice_for(key: str) -> ProviderChoice:
    try:
        return BY_KEY[key]
    except KeyError:
        raise ValueError(f"unknown model choice {key!r}; known: {', '.join(BY_KEY)}") from None


def default_model(key: str) -> str:
    return choice_for(key).models[0]


@dataclass
class PlayerSetup:
    tag: str
    provider: str = "claude-cli"        # a ProviderChoice.key
    model: str = ""                     # empty: the choice's first model
    base_url: str = ""                  # local models only
    max_usd: float | None = None        # paid providers only; None: no cap
    instruction: str = ""
    usd_per_m_input: float | None = None     # what the cap counts with (the provider's price sheet)
    usd_per_m_output: float | None = None

    def resolved_model(self) -> str:
        return self.model.strip() or default_model(self.provider)


@dataclass
class Setup:
    players: list[PlayerSetup] = field(default_factory=list)
    #: provider choice key -> API key. Only ever written to setup.json; see the module docstring.
    keys: dict[str, str] = field(default_factory=dict)
    launch_game: bool = False           # start hoi4.exe with -debug_mode (needs game_dir)
    game_dir: str = ""
    telemetry_hours: int | None = None
    keep_running: bool = False
    until: str = ""
    my_country: str = ""                # the person's own tag, so the chat window can label it

    # --- file ---------------------------------------------------------------

    def to_dict(self, include_keys: bool = True) -> dict:
        data = asdict(self)
        if not include_keys:
            data["keys"] = {}
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Setup:
        players = []
        for raw in data.get("players", []):
            known = {k: raw[k] for k in PlayerSetup.__dataclass_fields__ if k in raw}
            player = PlayerSetup(**known)
            player.tag = str(player.tag).strip().upper()
            players.append(player)
        keys = {str(k): str(v) for k, v in (data.get("keys") or {}).items() if v}
        hours = data.get("telemetry_hours")
        return cls(players=players, keys=keys, launch_game=bool(data.get("launch_game")),
                   game_dir=str(data.get("game_dir") or ""),
                   telemetry_hours=int(hours) if hours not in (None, "") else None,
                   keep_running=bool(data.get("keep_running")), until=str(data.get("until") or ""),
                   my_country=str(data.get("my_country") or "").strip().upper())

    def save(self, path: Path | None = None) -> Path:
        return write_private(Path(path) if path else default_path(),
                             json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path | None = None) -> Setup:
        path = Path(path) if path else default_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise FileNotFoundError(f"no setup file at {path}: {exc.strerror or exc}") from None
        except ValueError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from None
        if not isinstance(data, dict):
            raise ValueError(f"{path} must hold a JSON object")
        return cls.from_dict(data)

    # --- checks -------------------------------------------------------------

    def key_available(self, choice_key: str, environ=None) -> bool:
        choice = choice_for(choice_key)
        if choice.key_env is None or choice.key == "local":
            return True
        env = os.environ if environ is None else environ
        return bool(self.keys.get(choice_key) or env.get(choice.key_env))

    def problems(self, environ=None) -> list[str]:
        """What would stop this setup from running, in words for the person. Empty: ready."""
        out: list[str] = []
        if not self.players:
            return ["Add at least one country for an AI to play."]
        seen: set[str] = set()
        for p in self.players:
            where = f"{p.tag or '(no tag)'}: "
            if not _TAG.match(p.tag):
                out.append(where + "a country tag is three letters or digits, like POL or GER.")
            elif p.tag in seen:
                out.append(where + "listed twice.")
            seen.add(p.tag)
            if p.tag and p.tag == self.my_country:
                out.append(where + "that is your own country; the AI plays a different one.")
            if p.provider not in BY_KEY:
                out.append(where + f"unknown model choice {p.provider!r}.")
                continue
            choice = BY_KEY[p.provider]
            if not self.key_available(p.provider, environ):
                out.append(where + f"no {choice.label.split(' (')[0]} saved. Paste it under API keys.")
            if choice.needs_url and not p.base_url.strip():
                out.append(where + "a local model needs its address, like " + DEFAULT_LOCAL_URL)
            if choice.needs_url and not p.model.strip():
                out.append(where + "a local model needs the model's name, like llama3.1.")
            if p.max_usd is not None:
                if not choice.paid:
                    out.append(where + "a spend cap only applies to paid API keys.")
                elif p.max_usd <= 0:
                    out.append(where + "the spend cap must be above zero (or empty for none).")
                elif not (p.usd_per_m_input or p.usd_per_m_output):
                    out.append(where + "a spend cap needs the price per million tokens (from your "
                               "provider's price page), or it would count nothing.")
        if self.telemetry_hours is not None:
            try:
                cadence.validate(self.telemetry_hours)
            except ValueError as exc:
                out.append(str(exc))
        if self.until and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.until):
            out.append("Stop date must look like 1941-06-22.")
        if self.launch_game and not self.game_dir:
            out.append("To start the game for you, the game folder is needed.")
        return out

    # --- what it means for a run --------------------------------------------

    def players_config(self) -> dict[str, dict]:
        """``HarnessConfig.rival_players``: each country's settings, keyless."""
        out = {}
        for p in self.players:
            choice = BY_KEY[p.provider]
            row: dict = {"provider": choice.provider, "model": p.resolved_model(),
                         "base_url": p.base_url.strip() if choice.needs_url else "",
                         "max_usd": p.max_usd if choice.paid else None,
                         "instruction": p.instruction.strip()}
            if choice.key == "local":
                row["api_key_env"] = LOCAL_KEY_ENV
            if p.usd_per_m_input is not None:
                row["usd_per_m_input"] = p.usd_per_m_input
            if p.usd_per_m_output is not None:
                row["usd_per_m_output"] = p.usd_per_m_output
            out[p.tag] = row
        return out

    def apply_to(self, config) -> None:
        """Lay this setup over a HarnessConfig (``--setup`` runs this before the flags)."""
        tags = [p.tag for p in self.players]
        config.rival = ",".join(tags)
        config.rival_players = self.players_config()
        caps = [p.max_usd for p in self.players if BY_KEY[p.provider].paid]
        if caps and len(caps) == len(self.players) and all(c is not None for c in caps):
            config.budget.max_usd = sum(caps)
        if self.telemetry_hours is not None:
            config.telemetry_hours = int(self.telemetry_hours)
        if self.game_dir:
            config.game_dir = Path(self.game_dir)

    def key_environment(self, base: dict | None = None) -> dict[str, str]:
        """The environment variables a child needs for its keys: ``{ENV_NAME: key}`` for each
        provider in use that has one saved. A local model gets a placeholder (the SDK wants one)."""
        out: dict[str, str] = {}
        for p in self.players:
            choice = BY_KEY[p.provider]
            if choice.key == "local":
                out[LOCAL_KEY_ENV] = self.keys.get("local") or "not-needed"
            elif choice.key_env and self.keys.get(choice.key):
                out[choice.key_env] = self.keys[choice.key]
        return out

    # --- commands -----------------------------------------------------------

    def native_args(self) -> list[str]:
        """The command line that does what this setup does, with every field as a flag. No key
        in it: those come from the environment. (``start --setup FILE`` is the short form.)"""
        args = ["native", "--rival", ",".join(p.tag for p in self.players)]
        for p in self.players:
            row = self.players_config()[p.tag]
            args += ["--player-provider", f"{p.tag}={row['provider']}",
                     "--player-model", f"{p.tag}={row['model']}"]
            if row["base_url"]:
                args += ["--player-base-url", f"{p.tag}={row['base_url']}"]
            if row.get("api_key_env"):
                args += ["--player-key-env", f"{p.tag}={row['api_key_env']}"]
            if p.max_usd is not None and BY_KEY[p.provider].paid:
                args += ["--player-max-usd", f"{p.tag}={p.max_usd}"]
            if p.usd_per_m_input is not None or p.usd_per_m_output is not None:
                args += ["--player-price", f"{p.tag}={p.usd_per_m_input or 0},{p.usd_per_m_output or 0}"]
            if row["instruction"]:
                args += ["--player-instruction", f"{p.tag}={row['instruction']}"]
        if self.launch_game:
            args.append("--launch")
        if self.game_dir:
            args += ["--game-dir", self.game_dir]
        if self.telemetry_hours is not None:
            args += ["--telemetry-hours", str(self.telemetry_hours)]
        if self.keep_running:
            args.append("--keep-running")
        if self.until:
            args += ["--until", self.until]
        return args


# --- files ---------------------------------------------------------------------------------


def default_path() -> Path:
    return home_dir() / SETUP_FILE


def write_private(path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` readable by the person alone where the OS allows (0600 on
    POSIX; on Windows the inherited access is replaced with the current user's, best effort)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":                              # pragma: no cover - Windows only
        path.write_text(text, encoding="utf-8")
        user = os.environ.get("USERNAME")
        if user:
            try:
                subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                               capture_output=True, timeout=20,
                               creationflags=0x08000000)
            except (OSError, subprocess.SubprocessError):
                pass
        return path
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)                                    # a file that already existed
    return path


def find_install(explicit: str | Path | None = None, environ=None) -> Path | None:
    """The game folder (the one with hoi4.exe and ``common/``): the one named, else HOI4_GAME_DIR,
    else the default Steam folder. None when none of them looks like an install."""
    env = os.environ if environ is None else environ
    candidates = [Path(explicit)] if explicit else []
    if env.get("HOI4_GAME_DIR"):
        candidates.append(Path(env["HOI4_GAME_DIR"]))
    candidates.append(DEFAULT_STEAM_DIR)
    for path in candidates:
        if (path / "common" / "country_tags").is_dir():
            return path
    return None


_TAG_LINE = re.compile(r"^\s*([A-Z0-9]{3})\s*=", re.M)
_NAME_LINE = re.compile(r'^\s*([A-Z0-9]{3}):\d*\s+"(.*)"\s*(?:#.*)?$')


def load_countries(game_dir: Path | None) -> list[tuple[str, str]]:
    """``[(tag, name)]`` from a game folder (country_tags and the English country names), sorted by
    name. Empty when there is no folder: the window then takes a tag as free text."""
    if game_dir is None:
        return []
    game_dir = Path(game_dir)
    tags: list[str] = []
    for path in sorted((game_dir / "common" / "country_tags").glob("*.txt")):
        text = re.sub(r"#.*", "", path.read_text(encoding="utf-8-sig", errors="replace"))
        tags += _TAG_LINE.findall(text)
    names: dict[str, str] = {}
    for path in sorted((game_dir / "localisation" / "english").glob("countries*_l_english.yml")) \
            + sorted((game_dir / "localisation").glob("countries*_l_english.yml")):
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            match = _NAME_LINE.match(line)
            if match:
                names.setdefault(match.group(1), match.group(2))
    seen: dict[str, str] = {}
    for tag in tags:
        seen.setdefault(tag, names.get(tag, tag))
    return sorted(seen.items(), key=lambda kv: (kv[1].lower(), kv[0]))


def filter_countries(countries: list[tuple[str, str]], query: str, limit: int = 200):
    """The searchable list: a tag or a name that contains ``query``, case-insensitively."""
    q = (query or "").strip().lower()
    if not q:
        return countries[:limit]
    hits = [c for c in countries if q in c[0].lower() or q in c[1].lower()]
    hits.sort(key=lambda c: (not c[0].lower().startswith(q), not c[1].lower().startswith(q)))
    return hits[:limit]


# --- launching -----------------------------------------------------------------------------


@dataclass
class Launch:
    """What a Start press runs: argv (no key in it), the child's environment, and where it logs."""

    argv: list[str]
    env: dict[str, str]
    run_copy: Path
    log_path: Path

    def __repr__(self) -> str:      # never show the environment
        return f"Launch(argv={self.argv!r}, log_path={self.log_path!r})"


def build_launch(setup: Setup, home: Path | None = None, python: str | None = None,
                 base_env: dict | None = None, now=time.time) -> Launch:
    """The run for ``setup``: ``hoi4-harness start --setup <key-less copy>``, with the keys in the
    child's environment. Writes the key-less copy and nothing else."""
    home = Path(home) if home else home_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now()))
    run_copy = home / "runs" / f"setup-{stamp}.json"
    write_private(run_copy, json.dumps(setup.to_dict(include_keys=False), indent=2,
                                       ensure_ascii=False))
    env = dict(os.environ if base_env is None else base_env)
    env.update(setup.key_environment())
    env["PYTHONUNBUFFERED"] = "1"
    argv = [python or sys.executable, "-m", "hoi4_harness.cli", "start", "--setup", str(run_copy)]
    return Launch(argv, env, run_copy, home / "logs" / f"run-{stamp}.log")


def launch(setup: Setup, spawn=subprocess.Popen, **kwargs) -> tuple[Launch, int]:
    """Start the run in the background, detached, its output in a log file. Returns the plan and
    the pid. Closing the setup window does not end it."""
    plan = build_launch(setup, **kwargs)
    plan.log_path.parent.mkdir(parents=True, exist_ok=True)
    log = plan.log_path.open("ab")
    options: dict = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
                     "env": plan.env, "close_fds": True}
    if sys.platform == "win32":
        options["creationflags"] = 0x08000000 | 0x00000200 | 0x00000008   # no window, own group
    else:
        options["start_new_session"] = True
    try:
        proc = spawn(plan.argv, **options)
    finally:
        log.close()
    return plan, proc.pid


def tail(path: Path, lines: int = 12) -> list[str]:
    """The last lines of a run's log, for the status box. Never raises."""
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return []


def inject_environment(setup: Setup, environ=None) -> None:
    """In the run itself: put the setup's saved keys into this process's environment, without
    replacing one already there (a launch from the window already passed its own)."""
    env = os.environ if environ is None else environ
    for name, value in setup.key_environment().items():
        env.setdefault(name, value)
