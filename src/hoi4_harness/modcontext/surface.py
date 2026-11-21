"""The action surface of a playset: what a player can do, read from the merged files.

Everything here is read through ``Playset.layered``, so load order and
``replace_path`` apply exactly as they do for the identifier index. Nothing is
evaluated: a condition is kept as the script the game would run (and as a short
readable line for people), because only the game can say whether ``available``
holds in a given position.

Custom windows (``common/scripted_guis``) are read in ``gui.py``: a button's click is
script with a trigger, so it can be pressed through the inbox the way the game's AI
presses it (#136). The ``interface`` files are only scanned for button labels.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from ..identifiers import IdentifierIndex, Playset
from . import script as S

#: Folders deliberately not read (none since #136 read the custom GUIs).
EXCLUDED_FOLDERS: tuple[str, ...] = ()

#: Features the game gates item by item, not as a whole: ``(label, folder)``. Each item carries its
#: own ``allowed``/``visible`` with ``has_dlc`` (docs/dlc.md), so what a country has is counted.
ITEM_FEATURES: dict[str, tuple[str, str]] = {
    "special_projects": ("Special projects", "common/special_projects/projects"),
    "mios": ("Military industrial organizations", "common/military_industrial_organization/organizations"),
}

_TARGET_KEYS = {"target_trigger", "targets", "targets_dynamic", "target_array",
                "target_root_trigger", "target_start_state"}


# --- localisation -------------------------------------------------------------


def _clean(value: str) -> str:
    value = re.sub(r"§.", "", value).replace("\\n", " ").replace("\\t", " ")
    # Flags, [Scope.GetName] and $KEY$ references are filled in by the game at
    # draw time; the text without them is the readable part.
    value = re.sub(r"\[[^\]]*\]|\$[\w.]+\$|£\w+", "", value)
    return re.sub(r"\s+", " ", value).strip()


def english(playset: Playset, wanted: set[str], clean=_clean) -> dict[str, str]:
    """English text for the keys in ``wanted`` only, layered as the identifier
    index layers names: the first definition wins, except from a ``replace/``
    folder, which wins over it. One pass over the files. ``clean`` turns the file's text into
    the value kept; the default drops colour codes and placeholders, and the event matcher
    passes the identity, because it needs to know where a placeholder stood."""
    names: dict[str, str] = {}
    if not wanted:
        return names
    files = [p for p in playset.layered("localisation", "*.yml") if "english" in p.as_posix().lower()]
    files.sort(key=lambda path: "replace" in path.parent.parts)
    line_re = re.compile(r'^\s*([\w.\-]+):\d*\s*"(.*)"')
    for path in files:
        replacing = "replace" in path.parent.parts
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            colon = line.find(":")
            if colon < 0 or line[:colon].strip() not in wanted:
                continue
            match = line_re.match(line)
            if match and (replacing or match.group(1) not in names):
                names[match.group(1)] = clean(match.group(2))
    return names


# --- records ------------------------------------------------------------------


@dataclass
class Category:
    id: str
    source: str
    priority: int = 0
    allowed: list = field(default_factory=list, repr=False)
    visible: list = field(default_factory=list, repr=False)
    #: The category is drawn by a custom GUI (``gui.py``); its decisions are
    #: still ordinary decisions.
    scripted_gui: str | None = None


@dataclass
class Decision:
    id: str
    category: str
    source: str
    #: ``decision``, ``targeted`` (needs a country or state) or ``mission`` (the
    #: game activates it; nobody takes it).
    kind: str = "decision"
    cost: float | None = None
    #: The cost when it is not a plain number (a variable or script value).
    cost_raw: str | None = None
    custom_cost: bool = False
    #: The localisation key of the custom cost's text (``custom_cost_text``), when it has one.
    custom_cost_text: str | None = None
    #: Resources the decision's own effects take (a negative ``add_command_power``,
    #: ``add_manpower`` or ``add_*_experience``): the cost a custom cost stands for.
    pays: tuple[str, ...] = ()
    days_remove: int | None = None
    days_re_enable: int | None = None
    fire_only_once: bool = False
    priority: int = 0
    targets: list[str] = field(default_factory=list)
    targets_dynamic: bool = False
    allowed: list = field(default_factory=list, repr=False)
    visible: list = field(default_factory=list, repr=False)
    available: list = field(default_factory=list, repr=False)
    target_trigger: list = field(default_factory=list, repr=False)
    custom_cost_trigger: list = field(default_factory=list, repr=False)
    #: No effect block of its own (an information line or a spacer in a custom
    #: window): taking it changes nothing.
    inert: bool = False
    #: ``available`` is ``always = no`` at its top level: it is drawn, never takeable
    #: (a timer, a spacer, an information line).
    blocked: bool = False

    @property
    def takeable(self) -> bool:
        return self.kind != "mission" and not self.inert and not self.blocked

    def describe(self, names: dict[str, str] | None = None, tag: str | None = None) -> dict:
        names = names or {}
        out = {
            "id": self.id, "category": self.category, "kind": self.kind, "source": self.source,
            "name": names.get(self.id, ""), "description": names.get(f"{self.id}_desc", ""),
            "cost": self.cost, "days_remove": self.days_remove,
            "days_re_enable": self.days_re_enable, "fire_only_once": self.fire_only_once,
            "visible": S.readable(self.visible), "available": S.readable(self.available),
        }
        if self.cost_raw is not None:
            out["cost_raw"] = self.cost_raw
        if self.inert:
            out["inert"] = True
        if self.blocked:
            out["blocked"] = True
        if self.custom_cost:
            out["custom_cost"] = S.readable(self.custom_cost_trigger)
        if self.kind == "targeted":
            out["targets"] = self.targets or ("dynamic" if self.targets_dynamic else [])
            out["target_trigger"] = S.readable(self.target_trigger)
        out["allowed"] = S.readable(self.allowed)
        return {k: v for k, v in out.items() if v not in ("", None, [])}


@dataclass
class FocusInfo:
    id: str
    name: str = ""
    cost: float | None = None
    #: All groups must be met; any focus within a group meets it.
    prerequisites: list[list[str]] = field(default_factory=list)
    mutually_exclusive: list[str] = field(default_factory=list)
    available: str = ""
    bypass: str = ""

    def to_dict(self) -> dict:
        out = {"id": self.id, "name": self.name, "cost_weeks": self.cost,
               "prerequisites": self.prerequisites, "mutually_exclusive": self.mutually_exclusive,
               "available": self.available, "bypass": self.bypass}
        return {k: v for k, v in out.items() if v not in ("", None, [])}


@dataclass
class ActionSurface:
    """What ``tag`` can do in this playset."""

    tag: str
    playset: str
    categories: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    focus: dict = field(default_factory=dict)
    new_technologies: list[dict] = field(default_factory=list)
    spirits: list[dict] = field(default_factory=list)
    laws: list[dict] = field(default_factory=list)
    advisors: list[dict] = field(default_factory=list)
    starting_ideas: list[str] = field(default_factory=list)
    mechanics: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)
    excluded: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "tag", "playset", "counts", "categories", "decisions", "focus", "new_technologies",
            "spirits", "laws", "advisors", "starting_ideas", "mechanics", "events", "excluded")}


# --- the context --------------------------------------------------------------


class ModContext:
    """Reads a playset's action surface on demand and keeps what it read."""

    def __init__(self, index: IdentifierIndex):
        self.index = index
        self.playset = index.playset
        #: The active DLC names, or None when unknown (dlc.py): ``has_dlc`` is then left alone.
        self.dlcs = self.playset.dlcs
        self._cache: dict[str, object] = {}
        self._roots = [(str(self.playset.game_dir).replace("\\", "/"), "vanilla")] + [
            (str(m.path).replace("\\", "/"), m.name) for m in self.playset.mods]

    # --- plumbing -------------------------------------------------------------

    def _source(self, path: Path) -> str:
        text = str(path).replace("\\", "/")
        best = "vanilla"
        for root, name in self._roots:
            if text.startswith(root + "/"):
                best = name
        return best

    def _files(self, folder: str, pattern: str = "*.txt") -> list[Path]:
        if any(folder == x or folder.startswith(x + "/") for x in EXCLUDED_FOLDERS):
            return []
        return self.playset.layered(folder, pattern)

    @staticmethod
    def _read(path: Path) -> list:
        return S.parse(path.read_text(encoding="utf-8-sig", errors="replace"))

    def _memo(self, key: str, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    def names(self, keys: set[str]) -> dict[str, str]:
        have: dict[str, str] = self._cache.setdefault("loc", {})  # type: ignore[assignment]
        missing = keys - have.keys() - self._cache.setdefault("loc_missed", set())  # type: ignore[operator]
        if missing:
            found = english(self.playset, missing)
            have.update(found)
            self._cache["loc_missed"] |= missing - found.keys()  # type: ignore[operator]
        return have

    # --- decisions ------------------------------------------------------------

    def categories(self) -> dict[str, Category]:
        return self._memo("categories", self._build_categories)  # type: ignore[return-value]

    def _build_categories(self) -> dict[str, Category]:
        found: dict[str, Category] = {}
        for path in self._files("common/decisions/categories"):
            for key, _op, body in self._read(path):
                if not isinstance(body, list) or key is None:
                    continue
                found[key] = Category(
                    id=key, source=self._source(path),
                    priority=int(S.number(S.text(body, "priority")) or 0),
                    allowed=S.block(body, "allowed") or [], visible=S.block(body, "visible") or [],
                    scripted_gui=S.text(body, "scripted_gui"))
        return found

    def decisions(self) -> dict[str, Decision]:
        return self._memo("decisions", self._build_decisions)  # type: ignore[return-value]

    def _build_decisions(self) -> dict[str, Decision]:
        found: dict[str, Decision] = {}
        for path in self._files("common/decisions"):
            if "/categories/" in path.as_posix():
                continue
            source = self._source(path)
            for category, _op, body in self._read(path):
                if not isinstance(body, list) or category is None:
                    continue
                for key, _op2, spec in body:
                    if key is None or not isinstance(spec, list):
                        continue
                    found[key] = self._decision(key, category, source, spec)
        return found

    @staticmethod
    def _decision(key: str, category: str, source: str, spec: list) -> Decision:
        raw_cost = S.text(spec, "cost")
        cost = S.number(raw_cost)
        targets: list[str] = []
        for value in S.blocks(spec, "targets"):
            targets += S.bare(value)
        kind = "decision"
        if S.get(spec, "days_mission_timeout") is not None:
            kind = "mission"
        elif any(n[0] in _TARGET_KEYS for n in spec):
            kind = "targeted"
        effects = [S.block(spec, k) for k in ("complete_effect", "remove_effect", "timeout_effect")]
        days = S.number(S.text(spec, "days_remove"))
        again = S.number(S.text(spec, "days_re_enable"))
        return Decision(
            id=key, category=category, source=source, kind=kind, cost=cost,
            cost_raw=raw_cost if raw_cost is not None and cost is None else None,
            custom_cost=S.get(spec, "custom_cost_trigger") is not None
            or S.get(spec, "custom_cost_text") is not None,
            custom_cost_text=S.text(spec, "custom_cost_text"),
            pays=_paid_resources([n for e in effects if e for n in e]),
            days_remove=int(days) if days is not None else None,
            days_re_enable=int(again) if again is not None else None,
            fire_only_once=S.text(spec, "fire_only_once") == "yes",
            priority=int(S.number(S.text(spec, "priority")) or 0),
            targets=targets, targets_dynamic=S.text(spec, "targets_dynamic") == "yes"
            or S.get(spec, "target_array") is not None,
            allowed=S.block(spec, "allowed") or [], visible=S.block(spec, "visible") or [],
            available=S.block(spec, "available") or [],
            target_trigger=S.block(spec, "target_trigger") or [],
            custom_cost_trigger=S.block(spec, "custom_cost_trigger") or [],
            inert=not any(effects),
            blocked=any(k == "always" and v == "no" for k, _o, v in (S.block(spec, "available") or [])))

    def decision_for(self, tag: str) -> list[Decision]:
        """The decisions ``tag`` can have, by the tag tests in ``allowed`` (its own
        and its category's): everything not ruled out, which is a superset of what
        is on screen at any moment."""
        categories = self.categories()
        tags = self.index.tags
        out = []
        for decision in self.decisions().values():
            category = categories.get(decision.category)
            # ``allowed`` and ``visible`` both hide a decision, so a tag test in
            # either rules a country out.
            blocks = [decision.allowed, decision.visible]
            if category is not None:
                blocks += [category.allowed, category.visible]
            verdicts = [S.tag_truth(b, tag, self.dlcs) for b in blocks]
            if False in verdicts:
                continue
            if self.decision_needs(decision):
                continue                       # its own available test needs a DLC that is off
            if any(k == "is_debug" and v == "yes" for b in (decision.visible, *blocks[2:])
                   for k, _o, v in b):
                continue                       # drawn only in debug mode
            named = [S.mentions(b, {"tag", "original_tag"}) and v is True
                     for b, v in zip(blocks, verdicts, strict=True)]
            if not any(named):
                # No tag test says yes: an id or a category prefixed with another
                # country's tag (``BEL_..``) belongs to that country.
                for name in (decision.id, decision.category):
                    owner = name[:3]
                    if name[3:4] == "_" and owner != tag and owner in tags:
                        break
                else:
                    owner = None
                if owner:
                    continue
            out.append(decision)
        out.sort(key=lambda d: (not d.category.startswith(tag + "_"), d.category, -d.priority, d.id))
        return out

    def named_for(self, decision: Decision, tag: str) -> bool:
        """A tag test in the decision's or its category's conditions names ``tag``:
        it was written for this country, not for everyone."""
        category = self.categories().get(decision.category)
        blocks = [decision.allowed, decision.visible]
        if category is not None:
            blocks += [category.allowed, category.visible]
        return any(S.mentions(b, {"tag", "original_tag"}) and S.tag_truth(b, tag, self.dlcs) is True
                   for b in blocks)

    def needs(self, *blocks) -> list[str]:
        """The DLCs the conditions in ``blocks`` test with ``has_dlc`` that are off, when those
        tests alone rule the item out; empty when it stands (or the active set is unknown)."""
        if self.dlcs is None:
            return []
        out: list[str] = []
        for block in blocks:
            if block and S.dlc_truth(block, self.dlcs) is False:
                out += [n for n in S.dlc_names(block) if n not in self.dlcs and n not in out]
        return out

    def decision_needs(self, decision: Decision) -> list[str]:
        """The inactive DLCs that rule ``decision`` out through its ``allowed``, ``visible`` or
        ``available`` blocks or its category's."""
        category = self.categories().get(decision.category)
        blocks = [decision.allowed, decision.visible, decision.available]
        if category is not None:
            blocks += [category.allowed, category.visible]
        return self.needs(*blocks)

    def find_decision(self, decision_id: str) -> Decision | None:
        return self.decisions().get(decision_id)

    # --- custom windows (scripted GUIs, #136) -------------------------------------

    def guis(self) -> dict:
        """Every scripted GUI window in the playset, by id, with its category link."""
        from .gui import parse_gui

        def build() -> dict:
            found = {}
            for path in self._files("common/scripted_guis"):
                source = self._source(path)
                for key, _op, body in self._read(path):
                    if key != "scripted_gui" or not isinstance(body, list):
                        continue
                    for name, _o, spec in body:
                        if name and isinstance(spec, list):
                            found[name] = parse_gui(name, spec, source)
            for category in self.categories().values():
                if category.scripted_gui in found:
                    found[category.scripted_gui].category = category.id
            return found

        return self._memo("guis", build)  # type: ignore[return-value]

    def scripted_effects(self) -> dict[str, list]:
        """Every scripted effect's body, by name (what a click calls)."""
        def build() -> dict:
            found: dict[str, list] = {}
            for path in self._files("common/scripted_effects"):
                for key, _op, body in self._read(path):
                    if key and isinstance(body, list):
                        found[key] = body
            return found

        return self._memo("scripted_effects", build)  # type: ignore[return-value]

    def _gui_guard(self, gui) -> list:
        category = self.categories().get(gui.category or "")
        return [*category.allowed, *category.visible] if category is not None else []

    def guis_for(self, tag: str) -> list:
        """The windows ``tag`` can see, by the tag tests in their conditions (as for
        decisions): a superset of what is on screen at any moment."""
        tags = self.index.tags
        out = []
        for gui in self.guis().values():
            blocks = [gui.visible, gui.ai_enabled or [], self._gui_guard(gui)]
            verdicts = [S.tag_truth(b, tag, self.dlcs) for b in blocks]
            if False in verdicts:
                continue
            if not any(v is True for v in verdicts):
                owner = gui.id[:3]
                if gui.id[3:4] == "_" and owner != tag and owner in tags:
                    continue
            if gui.buttons:
                out.append(gui)
        out.sort(key=lambda g: (not g.id.startswith(tag + "_"), g.id))
        return out

    def click_refusal(self, gui, button, tag: str, allow_global: bool = False) -> str:
        from .gui import refusal

        memo: dict = self._cache.setdefault("click_refusals", {})  # type: ignore[assignment]
        key = (gui.id, button.key, tag, allow_global)
        if key not in memo:
            gone = self.needs(gui.visible, button.visible, button.enabled)
            if gone:
                from ..dlc import needs_message

                memo[key] = needs_message(f"Button '{button.key}'", gone)
            else:
                memo[key] = refusal(gui, button, self.scripted_effects(), set(self.index.tags), tag,
                                    allow_global, self._gui_guard(gui))
        return memo[key]

    def find_button(self, gui_id: str, button: str):
        gui = self.guis().get(gui_id)
        if gui is None:
            return None, None
        return gui, gui.buttons.get(button) or gui.buttons.get(f"{button}_click")

    def plan_click(self, gui_id: str, button: str, country: str, *, effect_command="eval_effect",
                   console: bool = True, allow_global: bool = False):
        from .gui import plan_click

        gui, found = self.find_button(gui_id, button)
        if gui is None or found is None:
            return None
        return plan_click(gui, found, country, scripted=self.scripted_effects(),
                          tags=set(self.index.tags), effect_command=effect_command,
                          allow_global=allow_global, console=console,
                          extra_guard=self._gui_guard(gui))

    def gui_labels(self) -> dict[str, str]:
        """Element name -> the localisation key its interface file shows (button text or
        tooltip). A scan, not a parse: ``name = "x"`` then the first text key after it."""
        def build() -> dict:
            labels: dict[str, str] = {}
            rx = re.compile(r'\b(name|buttonText|text|pdx_tooltip)\s*=\s*"?([^"\s}]+)"?')
            for path in self.playset.layered("interface", "*.gui"):
                current = None
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                for key, value in rx.findall(text):
                    if key == "name":
                        current = value
                    elif current and current not in labels:
                        labels[current] = value
            return labels

        return self._memo("gui_labels", build)  # type: ignore[return-value]

    def _button_names(self, buttons) -> dict[str, str]:
        labels = self.gui_labels()
        keys: set[str] = set()
        for b in buttons:
            keys |= {b.element, f"{b.element}_tt", f"{b.element}_tooltip", labels.get(b.element, "")}
        names = self.names({k for k in keys if k})
        out = {}
        for b in buttons:
            for key in (labels.get(b.element, ""), b.element, f"{b.element}_tt", f"{b.element}_tooltip"):
                if key and names.get(key):
                    out[b.key] = names[key]
                    break
        return out

    def list_gui_buttons(self, tag: str, gui: str | None = None, query: str | None = None,
                         limit: int = 40, include_ui: bool = False) -> dict:
        """The custom-window buttons ``tag`` can press, by window: label, kind, the game AI's
        weighting, and why the harness cannot press the others."""
        from .gui import ui_only, visible_flags

        windows = self.guis_for(tag)
        if gui:
            windows = [g for g in windows if g.id == gui]
        scripted = self.scripted_effects()
        openers: dict[str, list[str]] = {}
        everything = list(self.guis().values())
        for w in windows:
            flags = visible_flags(w.visible)
            if flags:
                openers[w.id] = [f"{o.id}/{b.key}" for o in everything for b in o.buttons.values()
                                 if any(f"set_country_flag = {f}" in S.inline(b.effect)
                                        for f in flags)][:3]
        rows, total = [], 0
        needle = (query or "").lower()
        # One localisation pass for every window: a pass per window is a minute on TNO.
        names = self._button_names([b for w in windows for b in w.buttons.values()])
        for w in windows:
            buttons = []
            for b in w.buttons.values():
                ui = ui_only(b.effect, scripted)
                if ui and not include_ui and not gui:
                    continue
                label = names.get(b.key, "")
                if needle and not any(needle in text.lower() for text in (b.key, label, w.id)):
                    continue
                total += 1
                why = self.click_refusal(w, b, tag)
                row = {"button": b.key, "label": label, "kind": "window" if ui else "action"}
                if why:
                    row["cannot_press"] = why
                if b.enabled:
                    row["enabled_when"] = S.readable(b.enabled)
                if b.ai_will_do is not None:
                    row["game_ai_weight"] = S.readable(b.ai_will_do)
                buttons.append({k: v for k, v in row.items() if v != ""})
            if not buttons:
                continue
            entry = {"window": w.id, "context": w.context, "source": w.source, "buttons": buttons}
            if w.visible:
                entry["open_when"] = S.readable(w.visible)
            if openers.get(w.id):
                entry["opened_by"] = openers[w.id]
            if w.category:
                entry["decision_category"] = w.category
            if w.ai_enabled is not None:
                entry["game_ai_uses_it"] = S.readable(w.ai_enabled) or "yes"
            rows.append(entry)
        shown, count = [], 0
        for entry in rows:
            if count >= max(1, limit):
                break
            take = entry["buttons"][:max(1, limit) - count]
            count += len(take)
            shown.append({**entry, "buttons": take})
        return {"country": tag, "windows": len(rows), "matching_buttons": total,
                "shown_buttons": count, "list": shown,
                "how": "click_gui_button(gui=<window>, button=<button>) presses one in your "
                       "country's scope; the game checks open_when and enabled_when first"}

    def check_click(self, call, country: str):
        """None when ``click_gui_button`` names a window and button this country has."""
        from ..types import ActionResult

        gui_id = str(call.arguments.get("gui", ""))
        button = str(call.arguments.get("button", ""))
        if not self.guis():
            return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                message=f"This playset ({self.playset.name}) has no custom windows.",
                                error_kind="invalid_args")
        guis = {g.id: g for g in self.guis_for(country)}
        if gui_id not in guis and gui_id in self.guis():
            window = self.guis()[gui_id]
            gone = self.needs(window.visible, window.ai_enabled or [], self._gui_guard(window))
            if gone:
                from ..dlc import needs_message

                return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                    message=needs_message(f"Window '{gui_id}'", gone) + ".",
                                    error_kind="invalid_args")
        if gui_id not in guis:
            why = (f"'{gui_id}' is a window, but not one {country} has" if gui_id in self.guis()
                   else f"There is no custom window '{gui_id}' in this playset")
            return self.index._reject(call, why, gui_id, guis or self.guis())
        window = guis[gui_id]
        if button in window.buttons or f"{button}_click" in window.buttons:
            return None
        return self.index._reject(call, f"'{gui_id}' has no button '{button}'", button,
                                  window.buttons)

    # --- features gated item by item -----------------------------------------------

    def item_feature(self, key: str, tag: str) -> dict:
        """How many items of a feature (``ITEM_FEATURES``) ``tag`` has with the active DLCs, out of
        those the tag alone allows, and the DLCs that would add some. ``exists`` is None when the
        playset has no such folder (the feature is not described), else whether any item stands."""
        def build() -> dict:
            label, folder = ITEM_FEATURES[key]
            total = standing = 0
            wanted: list[str] = []
            for path in self._files(folder):
                for item, _op, body in self._read(path):
                    if not item or item.startswith("@") or not isinstance(body, list):
                        continue
                    blocks = [S.block(body, "allowed") or [], S.block(body, "visible") or []]
                    if any(S.tag_truth(b, tag) is False for b in blocks):
                        continue
                    total += 1
                    if not self.needs(*blocks):
                        standing += 1
                    else:
                        wanted += [n for b in blocks for n in S.dlc_names(b) if n not in wanted]
            return {"label": label, "total": total, "available": standing,
                    "exists": None if not total else standing > 0,
                    "needs": [] if standing else wanted}

        return self._memo(f"feature:{key}:{tag}", build)  # type: ignore[return-value]

    def feature_gap(self, key: str, tag: str) -> str:
        """``<feature> needs the <DLC> DLC, which is not active`` when ``tag`` has none of the
        feature's items with the active DLCs, else an empty string."""
        if self.dlcs is None:
            return ""
        info = self.item_feature(key, tag)
        if info["exists"] is not False:
            return ""
        from ..dlc import needs_message

        return needs_message(info["label"], info["needs"])

    # --- balance of power --------------------------------------------------------

    def balances(self) -> dict:
        """Every balance of power bar in the playset (``common/bop``), by id."""
        from .bop import parse_balance

        def build() -> dict:
            found = {}
            for path in self._files("common/bop"):
                source = self._source(path)
                for key, _op, body in self._read(path):
                    if key and isinstance(body, list):
                        found[key] = parse_balance(key, body, source)
            return found

        return self._memo("balances", build)  # type: ignore[return-value]

    def balance_needs(self, bar_id: str) -> str | None:
        """The DLC a balance of power bar belongs to when that DLC is off, else None
        (``dlc.BOP_DLC``: the script that sets the bar up is gated on it)."""
        from ..dlc import BOP_DLC

        name = BOP_DLC.get(bar_id)
        return name if name and self.dlcs is not None and name not in self.dlcs else None

    def balance_lines(self, readings) -> list[str]:
        """The brief's lines for the bars the mod logged (``GameState.power_balances``)."""
        from .bop import brief_line

        bars = self.balances()
        readings = [r for r in readings if self.balance_needs(r.id) is None]
        keys: set[str] = set()
        for reading in readings:
            if reading.id in bars:
                keys |= bars[reading.id].keys()
        names = self.names(keys) if keys else {}
        return [brief_line(bars.get(r.id), r.id, r.value, r.weekly, names) for r in readings]

    # --- focus tree ------------------------------------------------------------

    def focus_details(self, tag: str) -> dict[str, FocusInfo]:
        """Prerequisites, exclusions and conditions of every focus ``tag`` can ever
        take, re-read from the files that define its trees."""
        def build() -> dict[str, FocusInfo]:
            wanted = set(self.index.focuses_for(tag))
            tree_ids = [t.id for t in self.index.trees_for(tag) if t.id]
            if not wanted:
                return {}
            id_rx = re.compile(r"\bid\s*=\s*\"?(?:" + "|".join(re.escape(t) for t in tree_ids) + r")\"?\s")
            infos: dict[str, FocusInfo] = {}
            for path in self._files("common/national_focus"):
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                if not ((tree_ids and id_rx.search(text)) or "shared_focus" in text):
                    continue
                for key, _op, body in S.parse(text):
                    if not isinstance(body, list):
                        continue
                    if key == "focus_tree" and S.text(body, "id") in tree_ids:
                        for entry in S.blocks(body, "focus"):
                            self._focus_info(entry, wanted, infos)
                    elif key == "shared_focus":
                        self._focus_info(body, wanted, infos)
            return infos

        return self._memo(f"focus:{tag}", build)  # type: ignore[return-value]

    @staticmethod
    def _focus_info(body: list, wanted: set[str], infos: dict[str, FocusInfo]) -> None:
        fid = S.text(body, "id")
        if not fid or fid not in wanted:
            return
        groups = []
        for prereq in S.blocks(body, "prerequisite"):
            members = [v for k, _o, v in prereq if k == "focus" and isinstance(v, str)]
            if members:
                groups.append(members)
        exclusive = []
        for block in S.blocks(body, "mutually_exclusive"):
            exclusive += [v for k, _o, v in block if k == "focus" and isinstance(v, str)]
        infos[fid] = FocusInfo(
            id=fid, cost=S.number(S.text(body, "cost")), prerequisites=groups,
            mutually_exclusive=exclusive,
            available=S.readable(S.block(body, "available") or []),
            bypass=S.readable(S.block(body, "bypass") or []))

    def available_focuses(self, tag: str, completed=()) -> list[str]:
        """Focuses the tree allows next: prerequisites met and no completed focus
        excludes them. ``available`` is the game's call and is shown, not judged."""
        done = set(completed)
        details = self.focus_details(tag)
        out = []
        for fid in self.index.available_focuses(tag, done):
            info = details.get(fid)
            if info and set(info.mutually_exclusive) & done:
                continue
            out.append(fid)
        return sorted(out)

    # --- technologies ----------------------------------------------------------

    def _technologies(self, playset: Playset) -> dict[str, dict]:
        found: dict[str, dict] = {}
        for path in playset.layered("common/technologies"):
            for key, _op, body in self._read(path):
                if key != "technologies" or not isinstance(body, list):
                    continue
                for tech, _o, spec in body:
                    if tech is None or tech.startswith("@") or not isinstance(spec, list):
                        continue
                    folder = S.block(spec, "folder")
                    found[tech] = {
                        "id": tech, "folder": S.text(folder, "name") if folder else None,
                        "start_year": S.text(spec, "start_year"),
                        "research_cost": S.text(spec, "research_cost"),
                    }
        return found

    def new_technologies(self) -> list[dict]:
        """Technologies this playset defines that the base game does not."""
        def build() -> list[dict]:
            vanilla = set(self._memo("vanilla_techs",
                                     lambda: self._technologies(Playset(self.playset.game_dir))))
            ours = self._technologies(self.playset)
            return [ours[t] for t in sorted(ours) if t not in vanilla]

        return self._memo("new_techs", build)  # type: ignore[return-value]

    # --- ideas, laws, advisors -------------------------------------------------

    def ideas(self) -> dict:
        """``{"spirits": [...], "laws": {group: [options]}}`` from ``common/ideas``."""
        def build() -> dict:
            spirits, laws = [], defaultdict(list)
            for path in self._files("common/ideas"):
                for key, _op, body in self._read(path):
                    if key != "ideas" or not isinstance(body, list):
                        continue
                    for category, _o, group in body:
                        if category is None or not isinstance(group, list):
                            continue
                        is_law = S.text(group, "law") == "yes"
                        if S.text(group, "designer") == "yes" or category == "hidden_ideas":
                            continue
                        for idea, _o2, spec in group:
                            if idea is None or not isinstance(spec, list) or idea in (
                                    "law", "use_list_view", "designer"):
                                continue
                            record = {"id": idea, "category": category,
                                      "cost": S.number(S.text(spec, "cost")),
                                      "allowed": spec_allowed(spec),
                                      "default": S.text(spec, "default") == "yes"}
                            if is_law:
                                record["visible"] = S.block(spec, "visible") or []
                                record["available"] = S.block(spec, "available") or []
                                record["allowed_to_remove"] = S.block(spec, "allowed_to_remove") or []
                            (laws[category] if is_law else spirits).append(record)
            return {"spirits": spirits, "laws": dict(laws)}

        return self._memo("ideas", build)  # type: ignore[return-value]

    def find_law(self, law_id: str) -> tuple[dict, list[dict]] | None:
        """``(law, every law of its group)`` for an idea id, or None when it is no law."""
        for options in self.ideas()["laws"].values():
            for record in options:
                if record["id"] == law_id:
                    return record, options
        return None

    def plan_law(self, law_id: str, country: str, effect_command: str = "eval_effect",
                 console: bool = True):
        from ..policy_effects import plan_law

        found = self.find_law(law_id)
        if found is None:
            return None
        return plan_law(found[0], found[1], country, effect_command=effect_command, console=console,
                        dlcs=self.dlcs)

    def occupation_laws(self) -> dict[str, dict]:
        """``common/occupation_laws``: id -> its visible and available blocks."""
        def build() -> dict[str, dict]:
            laws: dict[str, dict] = {}
            for path in self._files("common/occupation_laws"):
                for key, _op, body in self._read(path):
                    if key is None or not isinstance(body, list):
                        continue
                    laws[key] = {"id": key, "visible": S.block(body, "visible") or [],
                                 "available": S.block(body, "available") or [],
                                 "default": S.text(body, "starting_law") == "yes"}
            return laws

        return self._memo("occupation_laws", build)  # type: ignore[return-value]

    def plan_occupation_law(self, law_id: str, country: str, occupied: str | None = None,
                            effect_command: str = "eval_effect", console: bool = True,
                            state: str | None = None):
        from ..policy_effects import plan_occupation_law

        law = self.occupation_laws().get(law_id)
        if law is None:
            return None
        return plan_occupation_law(law, country, occupied, effect_command=effect_command,
                                   console=console, state=state, dlcs=self.dlcs)

    def advisors(self, tag: str) -> list[dict]:
        def build() -> list[dict]:
            out = []
            for path in self._files("common/characters"):
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                if "advisor" not in text:
                    continue
                for key, _op, body in S.parse(text):
                    if key != "characters" or not isinstance(body, list):
                        continue
                    for char, _o, spec in body:
                        if char is None or not isinstance(spec, list):
                            continue
                        for advisor in S.blocks(spec, "advisor"):
                            allowed = S.block(advisor, "allowed") or []
                            if S.tag_truth(allowed, tag, self.dlcs) is not True:
                                continue
                            if self.needs(S.block(advisor, "visible") or [],
                                          S.block(advisor, "available") or []):
                                continue
                            out.append({"character": char, "name_key": S.text(spec, "name") or char,
                                        "slot": S.text(advisor, "slot"),
                                        "token": S.text(advisor, "idea_token"),
                                        "cost": S.number(S.text(advisor, "cost"))})
            return sorted(out, key=lambda a: (a["slot"] or "", a["character"]))

        return self._memo(f"advisors:{tag}", build)  # type: ignore[return-value]

    def advisor_conditions(self, character: str, slot: str | None = None,
                           token: str | None = None) -> dict[str, list]:
        """The ``allowed``, ``visible`` and ``available`` blocks of one advisor role of a
        character (the last file that defines the character wins, as in the game), as parsed
        nodes. The role is the one with this ``slot`` and ``idea_token`` when given."""
        found: dict[str, list] = {"allowed": [], "visible": [], "available": []}
        for path in self._files("common/characters"):
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if character not in text:
                continue
            for key, _op, body in S.parse(text):
                if key != "characters" or not isinstance(body, list):
                    continue
                for char, _o, spec in body:
                    if char != character or not isinstance(spec, list):
                        continue
                    found = {"allowed": [], "visible": [], "available": []}
                    for advisor in _advisor_nodes(spec):
                        if slot and S.text(advisor, "slot") != slot:
                            continue
                        if token and S.text(advisor, "idea_token") != token:
                            continue
                        for name in found:
                            found[name] = S.block(advisor, name) or []
                        break
        return found

    def start_date(self) -> str:
        """The default bookmark's date (``1936.1.1``), or the first one's."""
        def build() -> str:
            dates = []
            for path in self._files("common/bookmarks"):
                for key, _op, body in self._read(path):
                    if key != "bookmarks" or not isinstance(body, list):
                        continue
                    for _k, _o, mark in body:
                        if isinstance(mark, list) and S.text(mark, "date"):
                            dates.append((S.text(mark, "default") == "yes", S.text(mark, "date")))
            default = [d for flag, d in dates if flag]
            return (default or [d for _f, d in dates] or ["1936.1.1.12"])[0]

        return self._memo("start_date", build)  # type: ignore[return-value]

    def starting_ideas(self, tag: str) -> list[str]:
        """``add_ideas`` in the country's history file, up to the start date."""
        def build() -> list[str]:
            start = _date_key(self.start_date())
            ideas: list[str] = []
            for path in self._files("history/countries"):
                if not re.match(rf"{tag}(?:\s|\.|_)", path.name):
                    continue
                for key, _op, value in self._read(path):
                    if key == "add_ideas":
                        ideas += _idea_names(value)
                    elif key and re.match(r"\d{4}\.\d+\.\d+", key) and isinstance(value, list) \
                            and _date_key(key) <= start:
                        for k2, _o2, v2 in value:
                            if k2 == "add_ideas":
                                ideas += _idea_names(v2)
            return ideas

        return self._memo(f"start_ideas:{tag}", build)  # type: ignore[return-value]

    # --- what is different -----------------------------------------------------

    def _ids(self, playset: Playset, folder: str, container: str) -> set[str]:
        found: set[str] = set()
        for path in playset.layered(folder):
            for key, _op, body in self._read(path):
                if key == container and isinstance(body, list):
                    found |= {k for k, _o, v in body if k and isinstance(v, list)}
        return found

    def mechanics(self) -> dict:
        """Ids a mod adds that the base game has not: ideologies, resources,
        buildings, on_actions. ``dropped`` counts base ids the playset lost."""
        def build() -> dict:
            vanilla_set = Playset(self.playset.game_dir)
            out = {}
            for label, folder, container in (
                    ("ideologies", "common/ideologies", "ideologies"),
                    ("resources", "common/resources", "resources"),
                    ("buildings", "common/buildings", "buildings"),
                    ("on_actions", "common/on_actions", "on_actions")):
                base = self._ids(vanilla_set, folder, container)
                ours = self._ids(self.playset, folder, container) if self.playset.mods else base
                out[label] = {"new": sorted(ours - base), "dropped": len(base - ours),
                              "total": len(ours)}
            out["replaced_folders"] = sorted({r for m in self.playset.mods for r in m.replace_paths})
            return out

        return self._memo("mechanics", build)  # type: ignore[return-value]

    def event_chains(self, tag: str | None = None) -> dict:
        """Event namespaces and how many events each holds (events are not actions)."""
        def build() -> dict:
            counts: dict[str, int] = defaultdict(int)
            id_rx = re.compile(r"^\s*id\s*=\s*([A-Za-z_][\w]*)\.\d+", re.M)
            for path in self._files("events"):
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                for namespace in id_rx.findall(text):
                    counts[namespace] += 1
            return dict(counts)

        counts = self._memo("events", build)
        mine = {}
        if tag:
            low = tag.lower()
            mine = {n: c for n, c in counts.items()  # type: ignore[union-attr]
                    if n.lower() == low or n.lower().startswith(low + "_") or n.lower().startswith(low)}
        top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12]  # type: ignore[union-attr]
        return {"namespaces": len(counts), "events": sum(counts.values()),  # type: ignore[arg-type,union-attr]
                "largest": dict(top), "country": dict(sorted(mine.items()))}

    # --- the whole picture -----------------------------------------------------

    def surface(self, tag: str) -> ActionSurface:
        tag = tag.upper()
        decisions = self.decision_for(tag)
        categories = self.categories()
        used = sorted({d.category for d in decisions})
        ideas = self.ideas()
        spirits = [i for i in ideas["spirits"]
                   if not self.needs(i["allowed"])
                   and (i["id"].startswith(tag + "_") or S.tag_truth(i["allowed"], tag, self.dlcs) is True)]
        laws = []
        for group, options in sorted(ideas["laws"].items()):
            relevant = [o for o in options if S.tag_truth(o["allowed"], tag, self.dlcs) is not False
                        and not self.needs(o.get("visible") or [], o.get("available") or [])]
            if relevant:
                laws.append({"group": group, "options": relevant})
        trees = self.index.trees_for(tag)
        details = self.focus_details(tag)
        roots = self.available_focuses(tag)
        advisors = self.advisors(tag)
        new_techs = self.new_technologies()

        keys: set[str] = set()
        for d in decisions:
            keys |= {d.id, f"{d.id}_desc"}
        keys |= set(used)
        keys |= set(details) | {t.id for t in trees}
        keys |= {t["id"] for t in new_techs}
        keys |= {i["id"] for i in spirits}
        for law in laws:
            keys |= {o["id"] for o in law["options"]} | {law["group"]}
        keys |= {a["token"] for a in advisors if a["token"]} | {a["name_key"] for a in advisors}
        names = self.names(keys)

        surface = ActionSurface(tag=tag, playset=self.playset.name)
        surface.categories = [
            {"id": c, "name": names.get(c, ""),
             "decisions": sum(1 for d in decisions if d.category == c),
             **({"custom_gui": categories[c].scripted_gui} if c in categories
                and categories[c].scripted_gui else {})} for c in used]
        surface.decisions = [d.describe(names, tag) for d in decisions]
        surface.focus = {
            "trees": [t.id for t in trees], "focuses": len(details),
            "available_now": [{"id": f, "name": names.get(f, "")} for f in roots],
            "details": [{**info.to_dict(), "name": names.get(fid, "")}
                        for fid, info in sorted(details.items())],
        }
        surface.new_technologies = [{**t, "name": names.get(t["id"], "")} for t in new_techs]
        surface.spirits = [{"id": i["id"], "name": names.get(i["id"], ""), "category": i["category"]}
                           for i in spirits]
        surface.laws = [{"group": law["group"], "name": names.get(law["group"], ""),
                         "options": [{"id": o["id"], "name": names.get(o["id"], ""), "cost": o["cost"]}
                                     for o in law["options"]]} for law in laws]
        surface.advisors = [{**a, "name": names.get(a["token"] or "", names.get(a["name_key"], ""))}
                            for a in advisors]
        surface.starting_ideas = self.starting_ideas(tag)
        surface.mechanics = self.mechanics()
        surface.events = self.event_chains(tag)
        excluded = []
        for folder in EXCLUDED_FOLDERS:
            count = sum(1 for _ in self.playset.layered(folder))
            excluded.append(f"{folder} ({count} files, custom GUIs)")
        surface.excluded = excluded
        windows = self.guis_for(tag)
        pressable = [b for g in windows for b in g.buttons.values() if not self.click_refusal(g, b, tag)]
        surface.counts = {
            "gui_windows": len(windows), "gui_buttons": sum(len(g.buttons) for g in windows),
            "gui_buttons_pressable": len(pressable),
            "decisions": len(decisions), "decision_categories": len(used),
            "takeable_decisions": sum(1 for d in decisions if d.takeable),
            "missions": sum(1 for d in decisions if d.kind == "mission"),
            "never_takeable": sum(1 for d in decisions if d.kind != "mission" and not d.takeable),
            "decisions_playset_total": len(self.decisions()),
            "categories_playset_total": len(categories),
            "focus_trees": len(trees), "focuses": len(details),
            "new_technologies": len(new_techs), "national_spirits": len(spirits),
            "law_groups": len(laws), "advisors": len(advisors),
        }
        return surface


    # --- using it: validation, queries ----------------------------------------

    def plan_take(self, decision_id: str, country: str, target: str | None = None,
                  effect_command: str = "eval_effect"):
        from .execute import DecisionPlan, plan_decision

        decision = self.find_decision(decision_id)
        if decision is None:
            return None
        plan = plan_decision(decision, country, target, effect_command)
        assert isinstance(plan, DecisionPlan)
        return plan

    def check_take(self, call, country: str):
        """None when ``take_decision`` names a decision this country can have;
        otherwise a rejection with the nearest real ids, in the index's style."""
        from ..types import ActionResult

        decision_id = str(call.arguments.get("decision_id", ""))
        decision = self.find_decision(decision_id)
        mine = {d.id for d in self.decision_for(country)}
        if decision is not None and decision_id in mine:
            return None
        if not self.decisions():
            return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                message=f"This playset ({self.playset.name}) defines no decisions.",
                                error_kind="invalid_args")
        if decision is not None:
            gone = self.decision_needs(decision)
            if gone:
                from ..dlc import needs_message

                return ActionResult(ok=False, action=call.name, call_id=call.call_id,
                                    message=needs_message(f"Decision '{decision_id}'", gone) + ".",
                                    error_kind="invalid_args")
        why = (f"'{decision_id}' is a decision, but not one {country} can have" if decision
               else f"There is no decision '{decision_id}' in this playset ({self.playset.name})")
        return self.index._reject(call, why, decision_id, mine or self.decisions())

    def list_decisions(self, tag: str, category: str | None = None, query: str | None = None,
                       limit: int = 25) -> dict:
        """Decisions ``tag`` can have, filtered; one line each plus how many more."""
        rows = [d for d in self.decision_for(tag) if d.takeable]
        if category:
            rows = [d for d in rows if d.category == category]
        wanted = {d.id for d in rows} | {f"{d.id}_desc" for d in rows} | {d.category for d in rows}
        names = self.names(wanted)
        if query:
            needle = query.lower()
            rows = [d for d in rows if needle in d.id.lower()
                    or needle in names.get(d.id, "").lower() or needle in d.category.lower()]
        shown = rows[:max(1, limit)]
        return {"country": tag, "matching": len(rows), "shown": len(shown),
                "decisions": [{"id": d.id, "name": names.get(d.id, ""), "category": d.category,
                               "kind": d.kind, "cost": d.cost, "days": d.days_remove} for d in shown]}

    def describe(self, ident: str, tag: str) -> dict | None:
        """Everything known about one id: a decision, a focus of ``tag``'s tree,
        a technology the mod adds, an idea. None when it is none of them."""
        decision = self.find_decision(ident)
        if decision is not None:
            names = self.names({decision.id, f"{decision.id}_desc", decision.category})
            out = {"type": "decision", **decision.describe(names, tag)}
            plan = self.plan_take(ident, tag, decision.targets[0] if decision.targets else None)
            if plan is not None:
                out["headless"] = ("take_decision (guarded by its own conditions and cost)"
                                   if plan.executable else f"observe-only: {plan.reason}")
                if plan.executable and plan.unchecked:
                    out["unchecked"] = plan.unchecked
            return out
        window = self.guis().get(ident.split("/")[0])
        if window is not None:
            wanted = ident.split("/")[1] if "/" in ident else None
            data = self.list_gui_buttons(tag, window.id, limit=60, include_ui=True)
            if wanted:
                for entry in data["list"]:
                    entry["buttons"] = [b for b in entry["buttons"]
                                        if b["button"] in (wanted, f"{wanted}_click")]
                _gui, button = self.find_button(window.id, wanted)
                if button is not None:
                    data["click_effect"] = S.readable(button.effect, limit=10, width=400)
            return {"type": "custom_window", **data}
        bar = self.balances().get(ident)
        if bar is not None:
            from .bop import describe as describe_bar

            return describe_bar(bar, self.names(bar.keys()))
        focus = self.focus_details(tag).get(ident)
        if focus is not None:
            names = self.names({ident, f"{ident}_desc"})
            return {"type": "focus", **{**focus.to_dict(), "name": names.get(ident, ""),
                                         "description": names.get(f"{ident}_desc", "")},
                    "headless": "set_national_focus"}
        tech = next((t for t in self.new_technologies() if t["id"] == ident), None)
        if tech is not None:
            names = self.names({ident, f"{ident}_desc"})
            return {"type": "technology", **tech, "name": names.get(ident, ""),
                    "description": names.get(f"{ident}_desc", ""), "headless": "observe-only here: "
                    "start_research needs the tech tree's clicks"}
        for group in (self.ideas()["spirits"], *self.ideas()["laws"].values()):
            for idea in group:
                if idea["id"] == ident:
                    names = self.names({ident, f"{ident}_desc"})
                    law = "available" in idea
                    return {"type": "law" if law else "idea", **idea,
                            "allowed": S.readable(idea["allowed"]),
                            **({"visible": S.readable(idea["visible"]),
                                "available": S.readable(idea["available"]),
                                "allowed_to_remove": S.readable(idea["allowed_to_remove"])}
                               if law else {}),
                            "name": names.get(ident, ""), "description": names.get(f"{ident}_desc", ""),
                            "headless": "change_law (guarded by its own conditions and its cost)"
                                        if law else "observe-only: ideas are granted by focuses, "
                                        "events and decisions"}
        return None

# --- small helpers -------------------------------------------------------------


def spec_allowed(spec: list) -> list:
    return S.block(spec, "allowed") or []


def _date_key(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text)[:4])


def _idea_names(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    names: list[str] = []
    for key, _op, inner in value:
        if key is None and isinstance(inner, str):
            names.append(inner)
        elif key == "idea" and isinstance(inner, str):
            names.append(inner)
        elif key == "idea" and isinstance(inner, list):
            idea = S.text(inner, "idea")
            if idea:
                names.append(idea)
    return names


def _advisor_nodes(spec: list) -> list[list]:
    """Every ``advisor = { }`` block of a character, including those inside ``instance`` wrappers."""
    out: list[list] = []
    for key, _op, value in spec:
        if key == "advisor" and isinstance(value, list):
            out.append(value)
        elif key == "instance" and isinstance(value, list):
            out += _advisor_nodes(value)
    return out


#: Effects that take a resource other than political power; a negative amount is a price.
_COST_EFFECTS = {"add_command_power": "command power", "add_manpower": "manpower",
                 "add_army_experience": "army experience", "add_navy_experience": "navy experience",
                 "add_air_experience": "air experience"}


def _paid_resources(nodes: list) -> tuple[str, ...]:
    """The resources the effects take (a negative amount of a ``_COST_EFFECTS`` effect, at any depth)."""
    found: list[str] = []
    for key, _op, value in nodes:
        if isinstance(value, list):
            found += _paid_resources(value)
        elif key in _COST_EFFECTS:
            amount = S.number(value)
            if amount is not None and amount < 0 and _COST_EFFECTS[key] not in found:
                found.append(_COST_EFFECTS[key])
    return tuple(dict.fromkeys(found))
