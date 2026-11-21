"""Custom windows (``common/scripted_guis``): the buttons a player can press, and pressing them in script.

A scripted GUI button is a block of script (``effects = { <element>_click = { ... } }``) with a
trigger saying when it can be clicked (``triggers = { <element>_click_enabled = { ... } }``),
inside a window with its own ``visible`` trigger. The game's own AI presses these buttons without
seeing them, through the window's ``ai_weights``. The harness does the same through the inbox:

    if = { limit = { <window visible> <element visible> <click enabled> }  <click effect>  log }

run in the country's scope, so the game decides whether the click is allowed, exactly as the
screen would, and the log line is written only when it was (``LLMB|v5|click|<nonce>``).

What cannot be pressed faithfully is refused with the reason, never approximated:

* a window scoped to something other than the player (a selected state or country, a map icon,
  a diplomatic action): the harness has no selection to give it;
* a button whose script reads a list row (``v``/``i`` or the list's own value names), or
  ``FROM``/``PREV``, which have no value outside the window;
* a button whose script reaches beyond the country: global flags or variables, every/random
  country or state, another country's scope. An LLM rival pressing Expert AI's "limit research"
  would change the person's game, so these need ``allow_global`` (off by default).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field

from . import script as S

#: Contexts whose scope is the player's own country.
PLAYER_CONTEXTS = frozenset({"player_context", "decision_category"})
_COMBOS = ("right", "alt", "control", "shift")
#: Effects that change the world beyond the country pressing the button.
GLOBAL_EFFECTS = frozenset({
    "set_global_flag", "clr_global_flag", "modify_global_flag",
    "every_country", "random_country", "every_other_country", "random_other_country",
    "every_possible_country", "every_state", "random_state", "set_rule",
})
#: Effects that only switch what a window shows.
UI_EFFECTS = frozenset({"set_country_flag", "clr_country_flag", "if", "else", "else_if", "limit",
                        "hidden_effect", "custom_effect_tooltip", "effect_tooltip", "log"})
_UI_SCRIPTED = re.compile(r"(refresh|gui|update_ui|redraw)", re.I)
_TAG = re.compile(r"^[A-Z][A-Z0-9]{2}$")
_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")
#: Console line budget, as for decisions; the inbox has no console, so it allows more.
CONSOLE_LINE = 6000
INBOX_LINE = 60000
_LOOKUP_DEPTH = 8


@dataclass
class GuiButton:
    gui: str
    element: str
    combo: str = ""
    effect: list = field(default_factory=list, repr=False)
    enabled: list = field(default_factory=list, repr=False)
    visible: list = field(default_factory=list, repr=False)
    ai_will_do: list | None = field(default=None, repr=False)

    @property
    def key(self) -> str:
        return f"{self.element}_{self.combo}_click" if self.combo else f"{self.element}_click"


@dataclass
class Gui:
    id: str
    source: str
    context: str = "player_context"
    window: str | None = None
    visible: list = field(default_factory=list, repr=False)
    ai_enabled: list | None = field(default=None, repr=False)
    #: The names a dynamic list gives its row's value and index (``v`` and ``i`` by default).
    list_names: set[str] = field(default_factory=set)
    buttons: dict[str, GuiButton] = field(default_factory=dict)
    #: The decision category the window is attached to (``decision_category`` context).
    category: str | None = None


def _split_click(key: str) -> tuple[str, str] | None:
    """``foo_alt_right_click`` -> (``foo``, ``alt_right``)."""
    if not key.endswith("_click"):
        return None
    parts = key[:-len("_click")].split("_")
    combo: list[str] = []
    while len(parts) > 1 and parts[-1] in _COMBOS:
        combo.insert(0, parts.pop())
    return "_".join(parts), "_".join(combo)


def parse_gui(key: str, body: list, source: str) -> Gui:
    gui = Gui(id=key, source=source, context=S.text(body, "context_type") or "player_context",
              window=S.text(body, "window_name"), visible=S.block(body, "visible") or [],
              ai_enabled=S.block(body, "ai_enabled"))
    for _name, _op, spec in S.block(body, "dynamic_lists") or []:
        if isinstance(spec, list):
            gui.list_names |= {S.text(spec, "value") or "v", S.text(spec, "index") or "i"}
    triggers = S.block(body, "triggers") or []
    weights = S.block(body, "ai_weights") or []
    for name, _op, effect in S.block(body, "effects") or []:
        if not name or not isinstance(effect, list):
            continue
        split = _split_click(name)
        if split is None:
            continue
        element, combo = split
        weight = S.block(weights, name)
        gui.buttons[name] = GuiButton(
            gui=key, element=element, combo=combo, effect=effect,
            enabled=S.block(triggers, f"{name}_enabled") or [],
            visible=S.block(triggers, f"{element}_visible") or [],
            ai_will_do=(S.block(weight, "ai_will_do") if weight else None))
    return gui


# --- reading what a click does ------------------------------------------------------------------


@dataclass
class Reach:
    """What a click's script touches, following the scripted effects it calls."""

    global_: list[str] = field(default_factory=list)
    reads: set[str] = field(default_factory=set)
    keys: set[str] = field(default_factory=set)
    sets_flags: set[str] = field(default_factory=set)
    opaque: list[str] = field(default_factory=list)


def reach(nodes, scripted: dict[str, list], tags: set[str], own: str,
          out: Reach | None = None, depth: int = 0, seen: frozenset = frozenset()) -> Reach:
    out = out or Reach()
    for key, _op, value in nodes:
        if key in (None, "limit"):
            # A limit is a trigger: it reads other countries without changing them.
            if isinstance(value, list):
                out.reads |= _words(value)
            continue
        out.keys.add(key)
        if key in GLOBAL_EFFECTS:
            out.global_.append(key)
        elif key.startswith("global.") or (isinstance(value, str) and value.startswith("global.")):
            out.global_.append(f"the global variable {key}")
        elif isinstance(value, list) and any(
                isinstance(k, str) and k.startswith("global.") for k, _o, _v in value):
            out.global_.append(f"a global variable in {key}")
        elif _TAG.match(key) and key in tags and key != own:
            out.global_.append(f"{key}'s scope")
        if key == "set_country_flag" and isinstance(value, str):
            out.sets_flags.add(value)
        if isinstance(value, list):
            reach(value, scripted, tags, own, out, depth, seen)
        if key in scripted and value == "yes":
            if key in seen or depth >= _LOOKUP_DEPTH:
                out.opaque.append(key)
            else:
                reach(scripted[key], scripted, tags, own, out, depth + 1, seen | {key})
    out.reads |= _words(nodes)
    return out


def _words(nodes) -> set[str]:
    found: set[str] = set()
    for key, _op, value in nodes:
        if key:
            found.add(key)
            found |= {part for part in key.split(".") if part}
            found |= {m for m in re.findall(r"\b([A-Za-z_]\w*)", key)}
        if isinstance(value, list):
            found |= _words(value)
        elif isinstance(value, str):
            found.add(value)
            found |= {m for m in re.findall(r"\b([A-Za-z_]\w*)", value)}
    return found


#: Blocks that do not change scope: PREV inside them is still whatever it was outside.
_SAME_SCOPE = frozenset({"if", "else", "else_if", "limit", "AND", "OR", "NOT", "hidden_effect",
                         "custom_effect_tooltip", "effect_tooltip", "random_list", "count_triggers"})


def unscoped_prev(nodes) -> bool:
    """PREV used where it means "the scope before the window's": at the top of the script, not
    inside a scope change (``every_owned_state = { ... PREV ... }`` is the country again)."""
    for key, _op, value in nodes:
        if key == "PREV" or value == "PREV":
            return True
        if isinstance(value, list) and (key is None or key in _SAME_SCOPE) and unscoped_prev(value):
            return True
        if isinstance(value, list) and key not in _SAME_SCOPE and key is not None:
            # A scope change: PREV inside is defined. A PREV.PREV chain still climbs out.
            if any(isinstance(v, str) and v.startswith("PREV.PREV") for _k, _o, v in value):
                return True
    return False


def ui_only(effect: list, scripted: dict[str, list], depth: int = 0) -> bool:
    """The click only switches what a window shows: country flags and redraws."""
    if not effect:
        return False
    for key, _op, value in effect:
        if key is None:
            continue
        if key in scripted and value == "yes":
            if _UI_SCRIPTED.search(key):
                continue
            if depth >= 3 or not ui_only(scripted[key], scripted, depth + 1):
                return False
            continue
        if key not in UI_EFFECTS:
            return False
        if isinstance(value, list) and key != "limit" and not ui_only(value, scripted, depth):
            return False
    return True


def visible_flags(nodes) -> set[str]:
    """Country flags a window's ``visible`` needs at its top level (or in AND)."""
    out = set()
    for key, _op, value in nodes:
        if key == "has_country_flag" and isinstance(value, str):
            out.add(value)
        elif key == "AND" and isinstance(value, list):
            out |= visible_flags(value)
    return out


# --- pressing ------------------------------------------------------------------------------------


@dataclass
class ClickPlan:
    gui: Gui
    button: GuiButton
    line: str | None = None
    reason: str = ""
    unchecked: list[str] = field(default_factory=list)
    nonce: str = ""
    ui: bool = False

    @property
    def executable(self) -> bool:
        return self.line is not None


def marker(nonce: str) -> str:
    # No space or '=' inside the quotes: the console splits on both (check_console_line).
    return f"LLMB|v5|click|{nonce}"


def refusal(gui: Gui, button: GuiButton, scripted: dict[str, list], tags: set[str],
            country: str, allow_global: bool = False, extra_guard: list | None = None) -> str:
    """Why this button cannot be pressed faithfully for ``country``; empty when it can."""
    if gui.context not in PLAYER_CONTEXTS:
        return (f"the window's context is {gui.context}: its scope is a selected state or country "
                "(or a map icon), not the player, and the harness has no selection to give it")
    if not (_SAFE.match(gui.id) and _SAFE.match(button.key)):
        return "its id has characters the harness will not write into script"
    effect_reach = reach(button.effect, scripted, tags, country)
    guard = [*gui.visible, *button.visible, *button.enabled, *(extra_guard or [])]
    guard_words = _words(guard) | effect_reach.reads
    if gui.list_names and guard_words & gui.list_names:
        return ("a button in a list row: its script reads the row's value "
                f"({', '.join(sorted(guard_words & gui.list_names))}), which only the drawn row has")
    if "FROM" in guard_words or unscoped_prev([*guard, *button.effect]):
        return ("its script or conditions read FROM (or PREV outside a scope change), which have "
                "no value outside the window")
    if effect_reach.global_ and not allow_global:
        return ("it changes more than this country (" + ", ".join(sorted(set(effect_reach.global_))[:4])
                + "): pressing it would change other players' game. Allowed only with allow_global")
    return ""


def plan_click(gui: Gui, button: GuiButton, country: str, *, scripted: dict[str, list],
               tags: set[str], effect_command: str = "eval_effect", allow_global: bool = False,
               console: bool = True, extra_guard: list | None = None) -> ClickPlan:
    plan = ClickPlan(gui, button)
    why = refusal(gui, button, scripted, tags, country, allow_global, extra_guard)
    if why:
        plan.reason = why
        return plan
    guard = " ".join(S.inline(nodes) for nodes in (extra_guard or [], gui.visible, button.visible,
                                                    button.enabled) if nodes).strip()
    plan.nonce = "c" + secrets.token_hex(5)
    body = (f"if = {{ limit = {{ {guard or 'always = yes'} }} {S.inline(button.effect)} "
            f'log = "{marker(plan.nonce)}" }}')
    line = f"{effect_command} {country} = {{ {body} }}"
    limit = CONSOLE_LINE if console else INBOX_LINE
    if len(line) > limit:
        plan.reason = f"its script makes a {len(line)}-character line (limit {limit})"
        return plan
    if console:
        from ..adapters.console import UnsafeConsoleLine, check_console_line

        try:
            check_console_line(line)
        except UnsafeConsoleLine as exc:
            plan.reason = f"{exc} (the inbox has no such limit: use --transport inbox)"
            return plan
    effect_reach = reach(button.effect, scripted, tags, country)
    plan.line = line
    plan.ui = ui_only(button.effect, scripted)
    plan.unchecked = ["the window being open on screen: its visible trigger is checked, the "
                      "player's having clicked through to it is not"]
    if effect_reach.opaque:
        plan.unchecked.append("scripted effects nested too deep to read: "
                              + ", ".join(sorted(set(effect_reach.opaque))[:3]))
    if effect_reach.global_:
        plan.unchecked.append("it changes more than this country (allow_global is on)")
    return plan
