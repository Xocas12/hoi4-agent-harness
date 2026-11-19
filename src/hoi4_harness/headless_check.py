"""``hoi4-harness headless-check``: what a headless setup can and cannot do, measured.

Each check is one claim the headless path rests on, run against the game (or the
fake) and answered PASS or FAIL with the numbers it saw. Nothing is taken on
trust: every clock claim is the date in the telemetry moving or not moving. The
game must be loaded, paused and in front; the checks leave it paused.

The claims, in the order they are tried (cheapest and least risky first):

1. the log has telemetry and the reader sees it;
2. while paused, the console can make the mod log two fresh state lines (what
   lets an action be verified with the clock stopped);
3. ``gamespeed 3`` starts the clock;
4. the space key stops it;
5. ``gamespeed 0`` stops it, too;
6. ``pause_in_hours`` stops the game by itself after the days asked.

A FAIL names which assumption was wrong; ``docs/headless.md`` says what each one
changes (a different ``headless_clock``, no refresh).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from .adapters.clock import wait_for_days
from .adapters.headless import EXACT_MARGIN_HOURS, HeadlessClock, HeadlessComposite


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def run_checks(adapter: HeadlessComposite, days: int = 7,
               echo: Callable[[str], None] = print) -> list[Check]:
    reader, writer = adapter.reader, adapter.writer
    driver, console = writer.router.driver, writer.router.console
    results: list[Check] = []

    def peek():
        return getattr(reader, "peek_state", reader.read_state)()

    def record(name: str, ok: bool, detail: str) -> None:
        results.append(Check(name, ok, detail))
        echo(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")

    def attempt(name: str, body: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = body()
        except Exception as exc:  # noqa: BLE001 - a check that blew up is a FAIL with the reason
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        record(name, ok, detail)

    def clock(mode: str) -> HeadlessClock:
        return HeadlessClock(driver, console, mode, writer.clock.speed)

    def telemetry() -> tuple[bool, str]:
        state = peek()
        return True, f"{state.country} {state.date}, {state.telemetry_ticks} complete day(s) seen"

    attempt("the log has telemetry", telemetry)
    if not results[-1].ok:
        echo("stopping: without telemetry nothing else can be proved")
        return results

    def start_paused() -> tuple[bool, str]:
        clock("keys").pause()            # looks first; presses nothing on a paused game
        return True, "paused (the date stood still)"

    attempt("the game is paused to begin with", start_paused)

    def refresh() -> tuple[bool, str]:
        seen = peek()          # the reader hands back its live state: copy what is compared
        ticks = seen.telemetry_ticks or 0
        console.refresh()
        seen = peek()
        fresh = (seen.telemetry_ticks or 0) - ticks
        # The refresh lines carry the paused clock's own hour, which can be a day
        # past the last daily line (seen live: last daily line 17 Dec 21:00, game
        # paused at 18 Dec 04:00). What proves "paused" is that the date then
        # stands still, not that it equals the last daily line.
        day = seen.date
        still = not driver._date_moves()
        return fresh >= 2 and still and peek().date == day, (
            f"{fresh} fresh state line(s) (need 2), dated {day}, and the date "
            f"{'stood still' if still else 'moved'} afterwards")

    attempt("refresh: two fresh state lines while paused", refresh)

    def resume_console() -> tuple[bool, str]:
        start = peek().date
        clock("console").resume()
        return True, f"the date moved from {start} to {peek().date}"

    attempt("gamespeed 3 starts the clock", resume_console)

    def pause_key() -> tuple[bool, str]:
        stopped = peek().date
        clock("keys").pause()
        return True, f"paused at {peek().date} (was running from {stopped})"

    attempt("the space key stops it", pause_key)

    def console_cycle() -> tuple[bool, str]:
        clock("console").resume()
        clock("console").pause()
        return True, f"resumed and paused again with gamespeed, at {peek().date}"

    attempt("gamespeed 0 stops it", console_cycle)

    #: How many more times to look for the game to stop after the last day asked
    #: for arrives: each look watches the date for the driver's probe time.
    EXACT_SETTLE_LOOKS = 4

    def exact() -> tuple[bool, str]:
        start = peek().date
        timer = clock("exact")
        timer.hint_days = days
        timer.resume()
        reached = wait_for_days(reader.read_state, days, adapter.poll_seconds,
                                sleep=driver._sleep, start=start)
        # The pause comes EXACT_MARGIN_HOURS after the last day asked for, so the
        # game is still running when that day's line arrives (seen live: the
        # check looked then and called it still running; the game stopped
        # itself about half a day later). Give it until the next day to stop.
        driver._running = None                  # it should have stopped by itself: look
        still = driver._date_moves()
        for _ in range(EXACT_SETTLE_LOOKS):
            if not still:
                break
            still = driver._date_moves()
        reached = peek()
        elapsed = _days(start, reached.date)
        ok = not still and days <= elapsed <= days + 1
        if still:
            timer.pause()
        return ok, (f"asked for {days} days with a {EXACT_MARGIN_HOURS} h margin; the date went "
                    f"{start} -> {reached.date} ({elapsed} days) and the game "
                    f"{'was still running' if still else 'had stopped itself'}")

    attempt("pause_in_hours stops the game by itself", exact)
    try:
        clock("auto").pause()
    except Exception as exc:  # noqa: BLE001
        echo(f"WARNING: could not leave the game paused: {exc}")
    return results
