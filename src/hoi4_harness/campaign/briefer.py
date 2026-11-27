"""The briefing agent inside a campaign (#165): the first briefing before the start, and the
re-briefings the schedule asks for. The reading and the prompts are in ``briefing.py``."""

from __future__ import annotations

import time
from pathlib import Path

from .. import briefing as B

STATE_KEY = "briefing"
#: A re-briefing is not asked for again within this many days of the last one (a war event
#: and a dated entry on the same week are one briefing).
MIN_GAP_DAYS = 30


class BriefingRun:
    """What one run needs to brief: the config, the model (its own, or the planner's), the
    save folder, the log. ``log`` has ``line`` and ``show``; ``state`` is the campaign state
    file's load/save pair."""

    def __init__(self, config, client, env, run_dir: Path, log, load_state, save_state,
                 save_dir: Path | None, opener=None):
        self.config, self.client, self.env = config, client, env
        self.run_dir, self.log = Path(run_dir), log
        self.load_state, self.save_state = load_state, save_state
        self.save_dir, self.opener = save_dir, opener
        self.layer = None
        self._layer_tried = False

    # --- the pieces -------------------------------------------------------------

    def _layer(self):
        if getattr(self.env, "world", None) is not None and getattr(self.env.world, "layer", None):
            return self.env.world.layer
        if not self._layer_tried and self.env.index is not None:
            self._layer_tried = True
            try:
                from ..mapdata import MapLayer

                self.layer = MapLayer.load(self.env.index.playset)
            except Exception as exc:  # noqa: BLE001 - no map files: no neighbours, the rest stands
                self.log.line(f"briefing: no map layer ({type(exc).__name__}: {exc})")
        return self.layer

    def _save(self, tag: str, game_date: str | None) -> Path | None:
        if self.config.briefing_save:
            return Path(self.config.briefing_save)
        from ..world import newest_save

        # Before the first telemetry line the game's date is unknown: bound by the campaign's
        # start, never "any save" (the live run of 2026-11-27 briefed 1936 from a 1941 autosave).
        game_date = game_date or getattr(self.config, "start_date", None)
        found = newest_save(self.save_dir, game_date, tag) if self.save_dir else None
        if found is None and game_date and self.save_dir:
            # Older than the window is still this world; LATER than the game's date is another
            # game (the live run of 2026-11-27 briefed a fresh 1936 start from a 1941 save).
            found = _newest_not_after(Path(self.save_dir), game_date, tag)
        return found

    def _prices(self):
        extra = self.config.briefing.extra or {}
        if "usd_per_m_input" in extra or "usd_per_m_output" in extra:
            return float(extra.get("usd_per_m_input", 0)), float(extra.get("usd_per_m_output", 0))
        return None

    # --- the briefing ------------------------------------------------------------

    def run(self, reason: str, tag: str, game_date: str | None = None) -> tuple[str, str | None] | None:
        """Write a briefing; ``(context text, date)`` or None when it could not be written
        (said in the log; the campaign goes on without it)."""
        if self.env.index is None or not tag:
            self.log.line("briefing skipped: it needs a playset (game_dir) and a country")
            return None
        web_on, why = B.enable_web(self.client, self.config.briefing_web_search)
        if self.config.briefing_web_search:
            self.log.line(f"briefing: {why}")
        previous = None
        last = B.latest_briefing(self.run_dir)
        rec = self.load_state().get(STATE_KEY) or {}
        if last and rec.get("date"):
            previous = (rec["date"], last)
        try:
            save = self._save(tag, game_date)
            if save is None:
                self.log.line("briefing: no save of this country found; the dossier has the "
                              "playset's files only")
            dossier = B.build_dossier(
                self.config, self.env.index, tag, save=save, layer=self._layer() if save else None,
                offline=not web_on, opener=self.opener, previous=previous)
            made = B.brief(self.client, dossier, web=web_on,
                           max_tokens=self.config.briefing.max_tokens, prices=self._prices())
        except Exception as exc:  # noqa: BLE001 - the campaign must not end on its scout
            self.log.line(f"WARNING: the briefing ({reason}) could not be written: "
                          f"{type(exc).__name__}: {exc}; the campaign goes on without it")
            return None
        made.date = made.date or game_date
        path = B.save_briefing(self.run_dir, made, dossier)
        history = list(rec.get("history", []))
        history.append({"date": made.date, "reason": reason, "model": made.model,
                        "input_tokens": made.input_tokens, "output_tokens": made.output_tokens,
                        "cost_usd": round(made.cost_usd, 4), "web": made.web,
                        "written": time.strftime("%Y-%m-%d %H:%M:%S")})
        self.save_state(**{STATE_KEY: {"date": made.date, "file": str(path), "history": history,
                                       "model": made.model}})
        self.log.line(f"briefing written for {tag} ({reason}): {path}; model {made.model}, "
                      f"{made.input_tokens:,} in / {made.output_tokens:,} out"
                      + (f", about ${made.cost_usd:.2f}" if made.cost_usd else "")
                      + "; outside budget.max_usd")
        if made.missing:
            self.log.line("WARNING: the briefing lacks the section(s): " + ", ".join(made.missing))
        self.log.show(f"\n--- Briefing for {tag}, {made.date or 'undated'} ---\n"
                      f"{made.text}\n--- end of briefing ---\n")
        return B.context_block(made.text, made.date), made.date

    def recorded(self) -> tuple[str, str | None] | None:
        """The briefing an earlier run of this directory wrote, as context text."""
        text = B.latest_briefing(self.run_dir)
        if not text:
            return None
        date = (self.load_state().get(STATE_KEY) or {}).get("date")
        return B.context_block(text, date), date


def wars_of(state) -> set[str]:
    return {w.against for w in getattr(state, "wars", [])}


def snapshot(state) -> dict:
    return {"wars": sorted(wars_of(state)), "faction": state.faction}



def _newest_not_after(save_dir: Path, game_date: str, tag: str | None) -> Path | None:
    """The newest text save of ``tag`` dated on or before ``game_date``; None when there is none."""
    import re

    from ..adapters import savegame as sg

    for path in sorted(save_dir.glob("*.hoi4"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            head = path.open("rb").read(4096).decode("utf-8", errors="replace")
        except OSError:
            continue
        if not head.startswith("HOI4txt"):
            continue
        who = re.search(r'\bplayer="?(\w+)', head)
        if tag and who and who.group(1) != tag:
            continue
        when = re.search(r'\bdate="?([\d.]+)', head)
        saved = sg.save_date(when.group(1)) if when else None
        if saved and saved <= game_date:
            return path
    return None
