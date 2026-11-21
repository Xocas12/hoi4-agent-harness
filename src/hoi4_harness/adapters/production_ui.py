"""Production by click: edit a line that exists, open one only when there is none (#112).

**NOT live-verified.** Built against the fake game (``fakegame.py``, ``production_ui``) to the
standard of ``battle_plans.py``: refusals before any input, recorded points, a dry run, the
driver's guards. ``docs/live-session.md`` section 14b is the short live session that settles it.

Why this exists. The console's ``add_equipment_production`` DUPLICATES a line instead of
changing it (seen live on 1.19.5), so over the console ``set_production`` is only safe for
equipment with no line, and it refuses for the rest. A player edits the line on the production
screen: pick its row, press + or - on the factory count, or change its equipment. Nothing in the
interface files gives those positions (they are rows of a scrolling list), so every point is
RECORDED by a person (``hoi4-harness calibrate``), never computed.

What is read first. The text save (``log+save`` / ``savegame`` reader, ``state.production``):
one ``ProductionLine`` per ``military_lines`` entry, in the order the game lists them. Row N of
the screen is taken to be the Nth entry (assumption P1, checked live). A reader that does not see
production gets no production click path at all, because a click on a row it cannot name would be
a guess.

The actions (``set_production`` keeps its name and its arguments):

``set_production(equipment, factories)``
    A line for ``equipment`` exists: select its row and click ``+`` or ``-`` until it holds
    ``factories`` (0 clicks the line's remove button). No line: the console's
    ``add_equipment_production`` opens one (it is faithful for that case, seen live), so no
    point is needed for a new line.
``change_production_equipment(from_equipment, to_equipment)``
    Select the row of ``from_equipment``, press the equipment button, pick ``to_equipment`` in
    the list. Refused when ``to_equipment`` already has a line (that would be a duplicate: edit
    that one with ``set_production``).

The click path, as assumptions (P):

  P1 row N of the production screen is the Nth line of the save;
  P2 pressing the research hotkey and then the production one always leaves the production
     screen open (the hotkeys toggle; the research script relies on the same fact);
  P3 clicking a row selects that line; ``+`` / ``-`` act on the selected line, one factory per
     click; the remove button deletes the selected line;
  P4 the equipment button opens a list whose entries can be clicked by equipment.

A point that is not recorded is named in the refusal and nothing is sent. The result of a live
action is verified against a fresh save: ``done and verified``, ``rejected`` (the game did not
change) or ``unverified`` (no save could be read).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field

from ..types import ActionCall, ActionResult, GameState

#: Rows of the production screen a person can record a point for.
MAX_ROWS = 8
#: ``+`` / ``-`` clicks one call may make (a bigger change is done in two calls).
MAX_CLICKS = 40

PLUS = "production.factories.plus"
MINUS = "production.factories.minus"
REMOVE = "production.line.remove"
CHANGE = "production.equipment.change"
ROW = "production.line.{n}"
PICK = "production.equipment.{equipment}"
#: Per-row buttons. The 1.20 production screen has a - , + and remove button on EVERY line
#: (seen live 2026-11-21), so a shared PLUS/MINUS/REMOVE only ever reaches row 1. When a row's
#: own button is recorded it is clicked directly, without selecting the row first (a click on
#: the row opens the equipment chooser).
ROW_BUTTON = "production.line.{n}.{button}"

#: The actions this module performs, and the one that exists only because of it.
PRODUCTION_ACTIONS = ("set_production", "change_production_equipment")
EQUIPMENT_ACTION = "change_production_equipment"


def row_point(n: int) -> str:
    return ROW.format(n=n)


def pick_point(equipment: str) -> str:
    return PICK.format(equipment=equipment)


def row_button(n: int, button: str) -> str:
    return ROW_BUTTON.format(n=n, button=button)


def _button_steps(row: int, shared: str, button: str, clicks: int,
                  coordinates: dict) -> tuple[list[str], tuple[tuple[str, str], ...]]:
    """The points needed and the gestures for ``clicks`` presses of a row's button: the row's
    own button when recorded, else select the row and press the shared one."""
    own = row_button(row, button)
    if own in coordinates:
        return [own], tuple(("click", own) for _ in range(clicks))
    return ([row_point(row), shared],
            (("click", row_point(row)), ("wait", "0.4"),
             *(("click", shared) for _ in range(clicks))))


def edit_points(rows: int = 1) -> list[str]:
    """What editing a count needs: the first ``rows`` row(s) and the two counters."""
    return [*(row_point(n) for n in range(1, rows + 1)), PLUS, MINUS]


def calibrate_line(points: list[str]) -> str:
    return "hoi4-harness calibrate " + " ".join(points)


class ProductionRefused(Exception):
    """The production action cannot be performed as asked; nothing was sent."""


@dataclass(frozen=True)
class ProductionOrder:
    action: str
    kind: str                       # edit | remove | add | equipment
    equipment: str
    #: The row (1-based) the line is on, 0 for a line that does not exist yet.
    row: int = 0
    delta: int = 0
    target: int = 0
    to_equipment: str | None = None
    #: What the screen shows for the line now, for the check afterwards.
    current: int = 0
    note: str = ""
    #: The (kind, value) gestures: press | wait | click.
    steps: tuple[tuple[str, str], ...] = field(default=())

    def describe(self) -> str:
        if self.kind == "add":
            return f"open a {self.equipment} line with {self.target} factories (no line existed)"
        if self.kind == "remove":
            return f"remove the {self.equipment} line (row {self.row})"
        if self.kind == "equipment":
            return (f"change the equipment of row {self.row} from {self.equipment} to "
                    f"{self.to_equipment}")
        return (f"set the {self.equipment} line (row {self.row}) from {self.current} to "
                f"{self.target} factories ({'+' if self.delta > 0 else '-'}{abs(self.delta)})")


def _need(state: GameState) -> None:
    for name in ("production", "military_factories"):
        if not state.known(name):
            raise ProductionRefused(
                f"the reader cannot see {name}, so which row is which line is unknown "
                "(production by click needs a text save)")


def _missing(needed: list[str], coordinates: dict) -> list[str]:
    return [p for p in dict.fromkeys(needed) if p not in coordinates]


def _refuse_missing(action: str, missing: list[str]) -> None:
    if missing:
        raise ProductionRefused(
            f"no recorded point for {', '.join(missing)}: {action} clicks the production screen "
            f"and those positions are not in the interface files. Record them with "
            f"`{calibrate_line(missing)}`")


def _opening() -> tuple[tuple[str, str], ...]:
    # P2: research first, so the production key opens the screen whatever was open.
    return (("press", "research"), ("wait", "0.5"), ("press", "production"), ("wait", "0.8"))


def plan_set(call: ActionCall, state: GameState, coordinates: dict) -> ProductionOrder:
    equipment, target = str(call.arguments["equipment"]), int(call.arguments["factories"])
    if target < 0:
        raise ProductionRefused("factories cannot be negative")
    _need(state)
    rows = [i for i, line in enumerate(state.production) if line.equipment == equipment]
    if not rows:
        if target == 0:
            raise ProductionRefused(f"there is no {equipment} line to remove")
        return ProductionOrder("set_production", "add", equipment, 0, target, target,
                               note="no line for it, so a new one is opened (console effect)")
    index = rows[0]
    row = index + 1
    line = state.production[index]
    dup = (f" ({len(rows)} lines hold {equipment}; the first was edited)" if len(rows) > 1 else "")
    if row > MAX_ROWS:
        raise ProductionRefused(
            f"the {equipment} line is row {row}, past the {MAX_ROWS} rows a point can be "
            "recorded for")
    if target == 0:
        needed, clicks = _button_steps(row, REMOVE, "remove", 1, coordinates)
        _refuse_missing("set_production", _missing(needed, coordinates))
        steps = (*_opening(), *clicks)
        return ProductionOrder("set_production", "remove", equipment, row, -line.factories, 0,
                               current=line.factories, note=dup.strip(), steps=steps)
    delta = target - line.factories
    if delta == 0:
        raise ProductionRefused(f"the {equipment} line already has {target} factories; "
                                "nothing to change")
    if abs(delta) > MAX_CLICKS:
        raise ProductionRefused(f"a change of {abs(delta)} factories is over the {MAX_CLICKS} "
                                "clicks one call makes: change it in steps")
    if delta > 0:
        used = sum(x.factories for x in state.production)
        free = max(state.military_factories - used, 0)
        if delta > free:
            raise ProductionRefused(
                f"{delta} more factories asked for, {free} free ({state.military_factories} "
                f"military, {used} on lines)")
    shared, name = (PLUS, "plus") if delta > 0 else (MINUS, "minus")
    needed, clicks = _button_steps(row, shared, name, abs(delta), coordinates)
    _refuse_missing("set_production", _missing(needed, coordinates))
    steps = (*_opening(), *clicks)
    return ProductionOrder("set_production", "edit", equipment, row, delta, target,
                           current=line.factories, note=dup.strip(), steps=steps)


def plan_equipment(call: ActionCall, state: GameState, coordinates: dict,
                   buildable: Callable[[str], bool] | None = None) -> ProductionOrder:
    source = str(call.arguments["from_equipment"])
    dest = str(call.arguments["to_equipment"])
    _need(state)
    if source == dest:
        raise ProductionRefused(f"from_equipment and to_equipment are both {source}")
    if buildable is not None and not buildable(dest):
        raise ProductionRefused(f"there is no buildable equipment '{dest}' in this playset")
    rows = [i for i, line in enumerate(state.production) if line.equipment == source]
    if not rows:
        raise ProductionRefused(f"there is no {source} line to change")
    if any(line.equipment == dest for line in state.production):
        raise ProductionRefused(
            f"a {dest} line already exists, so this would make a second one: edit that line with "
            "set_production instead")
    row = rows[0] + 1
    if row > MAX_ROWS:
        raise ProductionRefused(f"the {source} line is row {row}, past the {MAX_ROWS} rows a "
                                "point can be recorded for")
    # On 1.20 a click on the line itself opens the equipment chooser; CHANGE is only for a
    # layout that needs a separate button, and is clicked when it is recorded.
    needed = [row_point(row), pick_point(dest)]
    _refuse_missing("change_production_equipment", _missing(needed, coordinates))
    change = (("wait", "0.4"), ("click", CHANGE)) if CHANGE in coordinates else ()
    steps = (*_opening(), ("click", row_point(row)), *change,
             ("wait", "0.8"), ("click", pick_point(dest)))
    return ProductionOrder(EQUIPMENT_ACTION, "equipment", source, row, 0, 0, to_equipment=dest,
                           current=state.production[rows[0]].factories, steps=steps)


def recorded_summary(coordinates: dict) -> dict:
    """What a calibration holds of the production points, for the start log and the check."""
    rows = [n for n in range(1, MAX_ROWS + 1) if row_point(n) in coordinates]
    picks = sorted(k[len("production.equipment."):] for k in coordinates
                   if k.startswith("production.equipment.") and k != CHANGE)
    own = [n for n in range(1, MAX_ROWS + 1) if row_button(n, "plus") in coordinates]
    return {"rows": rows, "row_buttons": own,
            "plus": PLUS in coordinates, "minus": MINUS in coordinates,
            "remove": REMOVE in coordinates, "change": CHANGE in coordinates, "picks": picks}


def missing_for_edit(coordinates: dict) -> list[str]:
    if row_button(1, "plus") in coordinates and row_button(1, "minus") in coordinates:
        return []
    return _missing(edit_points(1), coordinates)


def missing_for_equipment(coordinates: dict) -> list[str]:
    miss = _missing([row_point(1)], coordinates)
    if not recorded_summary(coordinates)["picks"]:
        miss.append(pick_point("<equipment>"))
    return miss


class ProductionExecutor:
    """Performs the production actions through the driver and proves them against a save."""

    actions = PRODUCTION_ACTIONS

    def __init__(self, driver, console, *, saves=None, buildable: Callable[[str], bool] | None = None,
                 sleep: Callable[[float], None] | None = None, settle: float = 0.8):
        self.driver = driver
        self.console = console
        #: The combined reader (``refresh()``, ``fresh_for(date)``), None when there is none.
        self.saves = saves
        self.buildable = buildable
        self._sleep = sleep or driver._sleep
        self.settle = settle
        self.last_order: ProductionOrder | None = None

    def __call__(self, call: ActionCall) -> ActionResult:
        driver = self.driver
        if driver._read_state is None:
            return self._result(call, False, "rejected",
                                "no reader to plan against; nothing was sent")
        state = driver._read_state()
        if (self.saves is not None and not driver.dry_run
                and not self.saves.fresh_for(state.date)):
            self.saves.refresh()           # rows are read from the save: it must be today's
            state = driver._read_state()
        coordinates = driver.config.coordinates
        try:
            if call.name == EQUIPMENT_ACTION:
                order = plan_equipment(call, state, coordinates, self.buildable)
            else:
                order = plan_set(call, state, coordinates)
        except ProductionRefused as exc:
            return self._result(call, False, "rejected",
                                f"refused before any input: {exc}; nothing was sent")
        self.last_order = order
        if order.kind == "add":
            return self._add(call, order)
        if driver.dry_run:
            return self._result(call, False, "not_executed", (
                f"dry run: would {order.describe()}: "
                + "; ".join(f"{k} {v}" for k, v in order.steps)))
        for kind, value in order.steps:
            if kind == "press":
                driver.press(value)
            elif kind == "click":
                driver.click(value)
            else:
                self._sleep(float(value))
        self._sleep(self.settle)
        return self._check(call, order)

    def _add(self, call: ActionCall, order: ProductionOrder) -> ActionResult:
        result = self.console.execute([call])[0]
        if not result.ok:
            return result
        return dataclasses.replace(result, message=f"{result.message} ({order.note})")

    def _check(self, call: ActionCall, order: ProductionOrder) -> ActionResult:
        error = self.saves.refresh() if self.saves is not None else None
        state = self.driver._read_state()
        if error is not None or not state.known("production"):
            why = error or "the reader cannot see production"
            return self._result(call, False, "unverified", (
                f"{order.describe()}: clicks sent, but {why}, so the change could not be "
                "confirmed. Treat it as not done until a later brief shows it."),
                changed={"channel": "mouse"})
        problem = _problem(order, state)
        if problem is None:
            return self._result(call, True, None, f"{order.describe()}: done and verified"
                                + (f" {order.note}" if order.note else ""),
                                changed={"channel": "mouse"})
        return self._result(call, False, "rejected", (
            f"clicks sent but the game did not change: {problem}. The production screen was "
            "probably not in the state the click path expects (is every point recorded at this "
            "resolution and UI scale?)."), changed={"channel": "mouse"})

    @staticmethod
    def _result(call: ActionCall, ok: bool, kind: str | None, message: str,
                changed: dict | None = None) -> ActionResult:
        return ActionResult(ok=ok, action=call.name, call_id=call.call_id, message=message,
                            error_kind=kind, changed=changed or {})


def _problem(order: ProductionOrder, state: GameState) -> str | None:
    lines = state.production
    if order.kind == "remove":
        if any(x.equipment == order.equipment for x in lines):
            return f"a {order.equipment} line is still there"
        return None
    if order.kind == "edit":
        line = next((x for x in lines if x.equipment == order.equipment), None)
        have = line.factories if line else 0
        return None if have == order.target else (
            f"{order.equipment} has {have} factories, not {order.target}")
    here = [x for x in lines if x.equipment == order.to_equipment]
    gone = not any(x.equipment == order.equipment for x in lines)
    if not here:
        return f"there is no {order.to_equipment} line"
    if not gone:
        return f"the {order.equipment} line is still there"
    if here[0].factories != order.current:
        return (f"{order.to_equipment} holds {here[0].factories} factories, the line had "
                f"{order.current}")
    return None


def enable_production(driver, console, **kw) -> ProductionExecutor:
    executor = ProductionExecutor(driver, console, **kw)
    driver.enable_production(executor)
    return executor
