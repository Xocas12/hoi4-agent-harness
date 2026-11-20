"""Running a campaign: the setup the game needs, the loop, and the log a person reads.

``run_campaign`` is what ``hoi4-harness play --campaign`` calls. Around the ordinary
agent loop it adds exactly what an unattended run of a game the harness does not own
needs:

* the **start**: mark the player's country for the mod (``llmb_harness``), hand it to the
  game's AI with the console's ``human_ai``, and prove from the log that the AI took it
  (the state line's ``ai=``). ``human_ai`` is a *toggle*, so it is sent only when the log
  says the AI is not running the country yet: a resumed run finds it on and leaves it;
* a **supervisor** that looks at every turn (the game process, the AI flag, the war
  events), writes the time series and the ``campaign.log`` line, and takes a recovery
  ``savegame`` every N in-game months;
* the **stops**: the end date, a spent budget, a dead or silent game, a window that never
  comes back. Each ends the run with a reason, the game left paused and resumable.

None of it presses a key that is not the console key, the console's own text, or the
space bar the clock already used; nothing here presses Escape.
"""

from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable
from datetime import date
from pathlib import Path

from ..adapters.base import GameLost
from ..adapters.clock import days_between
from ..adapters.savegame import read_historical_ai
from ..agent.llm.base import LLMClient
from ..agent.loop import AgentLoop, RunReport, Supervisor
from ..agent.memory import Memory
from ..config import HarnessConfig
from ..env import HOI4Env
from ..keepawake import keep_awake_for
from ..observation.render import unnamed_enemies
from ..types import GameState
from .progress import ProgressTracker
from .saves import SaveTracker

CAMPAIGN_LOG = "campaign.log"
STATE_FILE = "campaign_state.json"
#: How many times the log is read for the ``ai=`` flip, and the pause between reads.
VERIFY_TRIES = 6
VERIFY_SECONDS = 2.0


class CampaignStartError(RuntimeError):
    """The campaign could not be set up: the game is not in the state it needs."""


class CampaignLog:
    """``campaign.log``: one human-readable line per turn, and the notes between them.

    Appended to and flushed on every line, so a person tailing it overnight sees the
    run as it is, and a crash loses nothing."""

    def __init__(self, path: Path | None, echo: Callable[[str], None] | None = None):
        self.path, self.echo = path, echo
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def line(self, text: str) -> None:
        stamped = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}"
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(stamped + "\n")
        if self.echo is not None:
            self.echo(text)


def load_state(run_dir: Path) -> dict:
    """What the run keeps between runs of the harness (``campaign_state.json``)."""
    try:
        return json.loads((Path(run_dir) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(run_dir: Path, **updates) -> None:
    """Merge ``updates`` into the state file; a failed write never ends a run."""
    record = {**load_state(run_dir), **updates}
    try:
        (Path(run_dir) / STATE_FILE).write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        pass


def _save_dir(config: HarnessConfig) -> Path | None:
    if config.save_dir:
        return Path(config.save_dir)
    from ..adapters.savegame import find_save_dir
    return find_save_dir()


RULE_TRIES = 3


def historical_rule_lines(value: bool | None, wanted: str = "any") -> list[str]:
    """The campaign.log lines for the Historical AI Focuses rule: what the save says, and
    a warning when that is not what the profile's ``campaign_historical_ai`` asks for."""
    shown = "unknown (the save does not say)" if value is None else "on" if value else "off"
    lines = [f"historical AI focuses: {shown}"]
    wanted = (wanted or "any").lower()
    if (value is False and wanted == "on") or (value is True and wanted == "off"):
        lines.append(
            f"WARNING: historical AI focuses is {shown} but this profile says "
            f"campaign_historical_ai={wanted}. The AI picks the country's focuses, so this rule "
            "decides the political path and runs are not comparable across it; the run goes on")
    return lines


def _months_between(earlier: str, later: str) -> int:
    try:
        a, b = date.fromisoformat(earlier), date.fromisoformat(later)
    except ValueError:
        return 0
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


class CampaignSupervisor(Supervisor):
    """What the loop calls around each turn of a campaign."""

    def __init__(self, config: HarnessConfig, env: HOI4Env, tracker: ProgressTracker,
                 log: CampaignLog, run_dir: Path, watchdog=None):
        self.config, self.env, self.tracker, self.log = config, env, tracker, log
        self.run_dir, self.watchdog = run_dir, watchdog
        self.loop: AgentLoop | None = None
        self._rule_misses = 0
        self.last_save: str | None = None
        self.saves_taken = 0
        self.saves = SaveTracker(run_dir, _save_dir(config), config.campaign_keep_saves,
                                 log=log.line) if not config.dry_run else None
        saved = load_state(run_dir)
        self.last_save = saved.get("last_recovery_save")
        try:
            self.saves_taken = int(saved.get("recovery_saves", 0))
        except (TypeError, ValueError):
            self.saves_taken = 0

    # --- the loop's hooks ---------------------------------------------------------

    def before_turn(self, state: GameState) -> str | None:
        if self.watchdog is not None:
            self.watchdog.check_process()
        spend = self.loop.budget.summary() if self.loop else None
        turn = self.loop.report.turns if self.loop else 0
        self.tracker.events(state.events, state.date)
        self.tracker.record(state, spend, turn)
        if (self.config.campaign_verify_ai and self.config.campaign_human_ai
                and state.ai_controlled is False):
            return ("the game's AI is no longer running the country (the state line says "
                    "ai=0): human_ai was toggled off, or the game was restarted. Not "
                    "playing on as a human country; resume to set it up again")
        self._recovery_save(state)
        if self.saves is not None:
            self.saves.prune()
            self.note_rule()
        return None

    def after_turn(self, state: GameState, *, woke: bool, reason: str, actions: list[str],
                   tokens: int) -> None:
        if woke:
            self.tracker.reviewed()
        self.log.line(self.turn_line(state, woke, reason, actions, tokens))
        self._write_state(state)

    # --- pieces ------------------------------------------------------------------

    def turn_line(self, state: GameState, woke: bool, reason: str, actions: list[str],
                  tokens: int) -> str:
        """``1938-05-01 wake: scheduled review | wars POL 37% | states 41 (3.8%) ...``"""
        camp = state.campaign
        wars = ", ".join(
            f"{w.against}" + (f" {w.surrender_progress * 100:.0f}%"
                              if w.surrender_progress is not None else "")
            for w in state.wars) or (f"{unnamed_enemies(state)} unnamed"
                                     if unnamed_enemies(state) else "none")
        held = "?"
        if camp is not None and camp.owned_states is not None:
            held = str(camp.owned_states)
            if camp.state_share is not None:
                held += f" ({camp.state_share * 100:.1f}%)"
        spend = self.loop.budget.summary() if self.loop else {}
        cap = f"/{self.config.budget.max_usd:.2f}" if self.config.budget.max_usd else ""
        marker = f"wake: {reason}" if woke else "ai plays"
        acts = ", ".join(actions) if actions else "-"
        return (f"{state.date}  {marker} | wars: {wars} | states {held} | "
                f"divs {camp.divisions if camp and camp.divisions is not None else '?'} | "
                f"${spend.get('usd', 0):.2f}{cap} {spend.get('llm_calls', 0)} calls "
                f"{tokens} tok | {acts}")

    def _recovery_save(self, state: GameState) -> None:
        months = self.config.campaign_save_months
        if not months or self.config.dry_run:
            return
        if self.last_save is not None and _months_between(self.last_save, state.date) < months:
            return
        console = getattr(getattr(self.env.adapter, "writer", None), "router", None)
        console = getattr(console, "console", None)
        if console is None:
            return
        try:
            if self.saves is not None:
                self.saves.before_send(state.date)
            console.send_lines(["savegame"])
        except GameLost:
            raise
        except Exception as exc:  # noqa: BLE001 - a failed recovery save must not end the run
            self.log.line(f"recovery save at {state.date} failed: {type(exc).__name__}: {exc}")
            self.last_save = state.date       # try again in N months, not on every turn
            return
        self.last_save, self.saves_taken = state.date, self.saves_taken + 1
        self.log.line(f"recovery save asked for at {state.date} (#{self.saves_taken})")

    def note_rule(self) -> None:
        """Once the first recovery save is on disk, read the Historical AI Focuses rule
        from its header, log it, and keep it in the run state. A warning, never a stop."""
        if self.saves is None or "historical_ai" in load_state(self.run_dir):
            return
        saves = self.saves.data["saves"]
        if not saves or self.saves.save_dir is None:
            return
        value = read_historical_ai(self.saves.save_dir / saves[0]["name"])
        if value is None:
            self._rule_misses += 1
            if self._rule_misses < RULE_TRIES:
                return                      # the file may still be growing: look again next turn
        save_state(self.run_dir, historical_ai=value)
        for line in historical_rule_lines(value, self.config.campaign_historical_ai):
            self.log.line(line)

    def _write_state(self, state: GameState) -> None:
        save_state(self.run_dir, last_recovery_save=self.last_save,
                   recovery_saves=self.saves_taken, date=state.date)


# --- the start ----------------------------------------------------------------


def begin(env: HOI4Env, config: HarnessConfig, log: CampaignLog,
          sleep: Callable[[float], None] = time.sleep, run_dir: Path | None = None) -> GameState:
    """Put the game in the state a campaign needs, and return what the log then says.

    Pauses the game and reads it (asking the mod for a first line if it has not logged
    one). Then, if the game's AI is not already running the country: mark the player's
    country, toggle ``human_ai``, ask for fresh telemetry, and read it back. A toggle is
    sent at most once, and only on a log that says ``ai=0``: if ``ai=`` is not on the
    line at all the harness cannot tell whether the toggle would turn the AI on or off,
    and refuses to guess."""
    adapter = env.adapter
    console = _console(adapter)
    try:
        env.reset()
    except RuntimeError as exc:
        if not config.country or console is None:
            raise CampaignStartError(
                f"{exc} Pass --country, so the harness can ask the mod for a first line.") from exc
        log.line("no telemetry in game.log yet: asking the mod for a line")
        console.send_lines(console.refresh_lines(config.country.upper()))
        try:
            env.reset()
        except RuntimeError as exc2:
            raise CampaignStartError(
                f"the game logs nothing from the LLM Bridge mod after a request ({exc2}). "
                "Is the mod enabled, and HOI4_LOG_PATH the live game.log?") from exc2
    state = env.read_state()
    tag = (state.country if state.country and state.country != "___" else config.country) or ""
    if not tag:
        raise CampaignStartError("the player's country tag is not known; pass --country")
    if not config.campaign_human_ai:
        log.line("human_ai is off by configuration: the game is not handed to the AI")
        return state
    if state.ai_controlled is True:
        log.line(f"the game's AI already runs {tag} (ai=1): human_ai left as it is")
        return state
    if (not config.campaign_verify_ai and run_dir is not None
            and load_state(run_dir).get("human_ai_sent")):
        # Verification is off, so the log cannot say whether the toggle took; a resumed run
        # that already sent it must not send it again, which would hand the country back.
        log.line("human_ai was sent by an earlier run and verification is off: not sent again")
        return state
    if state.ai_controlled is None:
        raise CampaignStartError(
            "the state line has no ai= field, so the harness cannot tell whether human_ai is "
            "on, and it is a toggle. Is the mod schema v5? (hoi4-harness verify-bridge)")
    if console is None:
        raise CampaignStartError("this adapter has no console to send human_ai through")
    lines = [f"effect {tag} llm_bridge_mark_harness", "human_ai",
             *console.refresh_lines(tag.upper()), f"effect {tag} llm_bridge_emit_campaign"]
    log.line(f"handing {tag} to the game's AI: {'; '.join(lines[:2])}")
    console.send_lines(lines)
    if run_dir is not None:
        save_state(run_dir, human_ai_sent=True)
    for _ in range(VERIFY_TRIES):
        state = env.read_state()
        if state.ai_controlled is True:
            log.line(f"human_ai took: the state line reads ai=1 for {tag}")
            return state
        sleep(VERIFY_SECONDS)
    if config.campaign_verify_ai:
        raise CampaignStartError(
            "human_ai did not take: after the toggle the state line still reads ai=0. The "
            "country is not under the AI (or `is_ai` does not follow the toggle in this build: "
            "see docs/campaign.md, pre-flight 1). Not playing on. It was sent once; do not "
            "send it again by hand without looking at the game first")
    log.line("human_ai sent; the state line still reads ai=0 and verification is off")
    return state


def _console(adapter):
    writer = getattr(adapter, "writer", None)
    return getattr(getattr(writer, "router", None), "console", None)


# --- running ------------------------------------------------------------------


def _check_ready(config: HarnessConfig, planner: LLMClient | None) -> None:
    if config.adapter != "headless":
        raise CampaignStartError("a campaign runs headless (adapter = headless)")
    if config.dry_run:
        raise CampaignStartError(
            "a campaign sends keys: pass --live (against the real game, with it loaded, paused "
            "and in front) or use --fake-game")
    priced = config.planner_enabled and config.planner.provider != "scripted"
    if priced and config.budget.max_usd is None:
        raise CampaignStartError(
            "a campaign spends money unattended: give it a hard cap with --max-usd N")
    if priced and not (config.budget.usd_per_m_input or config.budget.usd_per_m_output):
        raise CampaignStartError(
            "the budget has no prices, so --max-usd would count every call as free: set "
            "budget.usd_per_m_input / usd_per_m_output (the campaign profile does)")


def run_campaign(
    config: HarnessConfig,
    *,
    planner: LLMClient | None = None,
    resume: bool = False,
    quiet: bool = False,
    echo: Callable[[str], None] | None = None,
    env: HOI4Env | None = None,
    until: str | None = None,
    profile: str | Path | None = None,
) -> tuple[RunReport, dict]:
    """Run a campaign to its end date or its first reason to stop.

    Returns the loop's report and a summary dict (``stopped_reason``, the dates, the
    spend, where the files are). Raises ``CampaignStartError`` when the game is not
    ready and ``GameLost`` never: that is a clean stop with a reason. Any other failure is
    written to ``campaign.log`` and the transcript, then raised, so a crash is loud and
    the run can be resumed."""
    _check_ready(config, planner)
    run_dir = Path(config.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    out = echo or (lambda line: None)
    log = CampaignLog(run_dir / CAMPAIGN_LOG, None if quiet else out)
    if profile:
        save_state(run_dir, profile=str(profile))
    # A sleeping or locked PC types into nothing: hold it awake for the whole run (a no-op
    # for a dry run or the fake game), and let go however the run ends.
    with keep_awake_for(config, log.line):
        return _run(config, planner, resume, env, until, run_dir, log)


def _run(config: HarnessConfig, planner: LLMClient | None, resume: bool, env: HOI4Env | None,
         until: str | None, run_dir: Path, log: CampaignLog) -> tuple[RunReport, dict]:
    from ..agent.llm import build_llm
    from ..cli import _env_from_config

    env = env or _env_from_config(config)
    adapter = env.adapter
    end = until or config.campaign_until
    tracker = ProgressTracker(run_dir=run_dir, until=end, max_usd=config.budget.max_usd,
                              objective=_objective_label(config))
    env.brief_hooks.append(tracker.brief_lines)
    watchdog = getattr(adapter, "watchdog", None)
    patient = getattr(adapter, "patient_focus", None)
    if patient is not None:
        patient.log = log.line
    sleep = getattr(getattr(adapter, "fake_game", None), "sleep", None) or time.sleep
    cap = f"${config.budget.max_usd:.2f}" if config.budget.max_usd else "none"
    log.line(f"campaign {'resumed' if resume else 'started'}: {config.country or '?'} until {end}, "
             f"cap {cap}, model {config.planner.model}")
    try:
        begin(env, config, log, sleep=sleep, run_dir=run_dir)
    except (CampaignStartError, GameLost) as exc:
        log.line(f"cannot start: {exc}")
        raise
    if watchdog is not None:
        watchdog.reset()
    save_state(run_dir, save_dir=str(_save_dir(config) or ""))
    supervisor = CampaignSupervisor(config, env, tracker, log, run_dir, watchdog)
    loop = AgentLoop(
        env=env,
        planner=planner if planner is not None
        else (build_llm(config.planner) if config.planner_enabled else None),
        config=config,
        memory=Memory.load(run_dir / "memory.json") if resume else Memory(),
        transcript_path=run_dir / "transcript.jsonl",
        resume=resume,
        progress=None,
        supervisor=supervisor,
    )
    supervisor.loop = loop
    if resume and loop.report.resumed_from:
        log.line(f"resuming: {loop.report.turns} turns already played, "
                 f"${loop.budget.spend.usd:.2f} already spent")
    try:
        report = loop.run(config.turns, until=end)
    except Exception as exc:
        log.line(f"CRASH: {type(exc).__name__}: {exc}")
        loop.transcript.write("crash", error=f"{type(exc).__name__}: {exc}",
                              trace=traceback.format_exc(limit=8))
        raise
    finally:
        loop.memory.save(run_dir / "memory.json")
        if supervisor.saves is not None:
            try:
                supervisor.saves.prune()     # the last save is on disk by now: claim it, trim
                supervisor.note_rule()
            except OSError:
                pass
    try:
        # The last turn's end state, which no turn start saw: the series ends where the run did.
        tracker.record(env.read_state(), loop.budget.summary(), report.turns)
    except Exception as exc:  # noqa: BLE001 - the game may be the reason the run stopped
        log.line(f"no final sample: {type(exc).__name__}: {exc}")
    reason = report.stopped_reason or (
        "reached the end date" if report.reached_end_date else "ran out of turns")
    log.line(f"STOP: {reason} at {report.end_date or '?'}; spent ${loop.budget.spend.usd:.2f}")
    save_state(run_dir, stopped_reason=report.stopped_reason, stopped_at=report.end_date,
               reached_end_date=report.reached_end_date)
    if report.stopped_reason or not report.reached_end_date:
        for line in resume_lines(run_dir, config.save_dir and Path(config.save_dir)
                                 or _save_dir(config)):
            log.line(line)
    summary = {
        "stopped_reason": report.stopped_reason, "reached_end_date": report.reached_end_date,
        "end_date": report.end_date, "start_date": report.start_date, "turns": report.turns,
        "spend": report.spend, "run_dir": str(run_dir),
        "days_played": days_between(report.start_date, report.end_date),
    }
    return report, summary


def resume_command(run_dir: Path, profile: str | None = None) -> str:
    """The exact line that continues a stopped run (docs/campaign.md)."""
    return (f"hoi4-harness play --campaign --live --resume --profile {profile or '<profile.json>'} "
            f"--run-dir {run_dir} --max-usd <N more if the budget was the reason>")


def resume_lines(run_dir: Path, save_dir: Path | None) -> list[str]:
    """Where a stopped run can be picked up: the newest harness save and the command."""
    tracker = SaveTracker(run_dir, save_dir, 1)
    tracker.collect()
    latest = tracker.latest()
    state = load_state(run_dir)
    where = (f"load the recovery save {latest['name']} (taken at {latest.get('date') or '?'}) in "
             f"{save_dir}" if latest else "no recovery save of this run is on disk: "
             "continue the game as it is if it is still open")
    return [f"RESUME: {where}; pause it, bring the game window to the front, then run: "
            + resume_command(run_dir, state.get("profile"))]


def _objective_label(config: HarnessConfig) -> str:
    text = (config.objective or "").strip().partition(":")[0].rstrip(".")
    return text[:80] if text else "conquer the world"
