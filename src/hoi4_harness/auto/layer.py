"""The automation layer: runs the script controllers of a control map each turn.

``AutoLayer.run`` is what the agent loop calls every turn, woken or not, for the domains set to
``script`` (and the army delegation of ``army: game`` with nobody steering). A script's calls go
through ``HOI4Env.act_many(auto=True)``, so they get the same validation, playset check,
confirmation gate and per-turn cap as everything else, but are checked against what the ADAPTER
offers rather than the model's narrowed tool list. A call outside the script's own domain is
dropped here, and so is one the adapter does not offer.

``brief_lines`` is the model's window onto it: one line per automated domain saying what the
script did since the model last woke (``mark_wake`` resets it).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..control import DOMAIN_ACTIONS, DOMAINS, ControlMap
from ..types import GameState
from .army import ArmyDelegate, ArmyScript
from .base import AutoContext, Script
from .construction import ConstructionScript
from .focus import FocusScript
from .politics import PoliticsScript
from .production import ProductionScript
from .research import ResearchScript

SCRIPTS: dict[str, type[Script]] = {
    "focus": FocusScript,
    "research": ResearchScript,
    "construction": ConstructionScript,
    "production": ProductionScript,
    "politics": PoliticsScript,
    "army": ArmyScript,
}


@dataclass
class Report:
    domain: str
    label: str
    done: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    why: str = ""


class AutoLayer:
    def __init__(self, scripts: dict[str, Script], labels: dict[str, str] | None = None):
        #: Domain order is the catalog order: the focus before the research before the builds.
        self.scripts = {d: scripts[d] for d in DOMAINS if d in scripts}
        self.labels = labels or {d: s.name for d, s in self.scripts.items()}
        self.since_wake: dict[str, Report] = {
            d: Report(d, self.labels[d]) for d in self.scripts}
        self.turns = 0

    def __bool__(self) -> bool:
        return bool(self.scripts)

    def run(self, env, state: GameState, objective: str = "", memory=None) -> list:
        """Let each script act. Returns ``[(call, result), ...]`` in the order they ran."""
        self.turns += 1
        offered = env.script_actions
        ran: list = []
        for domain, script in self.scripts.items():
            allowed = (DOMAIN_ACTIONS.get(domain, frozenset()) | script.extra_actions) & offered
            calls = [c for c in script.decide(state, objective, memory) if c.name in allowed]
            report = self.since_wake[domain]
            if not calls:
                report.why = script.idle_note(state)
                continue
            for c, result in zip(calls, env.act_many(calls, state.date, auto=True), strict=False):
                script.feedback(c, result, state)
                (report.done if result.ok else report.refused).append(describe(c, result))
                ran.append((c, result))
        return ran

    def mark_wake(self) -> None:
        for domain in self.scripts:
            self.since_wake[domain] = Report(domain, self.labels[domain])

    def brief_lines(self, state: GameState) -> list[str]:
        lines = []
        for domain, report in self.since_wake.items():
            bits = []
            if report.done:
                bits.append("did " + "; ".join(report.done[-4:]))
            if report.refused:
                bits.append("refused " + "; ".join(report.refused[-3:]))
            if not bits:
                bits.append(report.why or "nothing to do")
            lines.append(f"Automated {domain} ({report.label}): " + ", ".join(bits))
        return lines


def describe(call, result) -> str:
    args = ", ".join(f"{v}" for v in call.arguments.values())
    suffix = "" if result.ok else f" ({result.error_kind or 'refused'})"
    return f"{call.name} {args}{suffix}"


def build_layer(cmap: ControlMap, config, env=None) -> AutoLayer | None:
    """The layer for ``cmap``, or None when no domain needs one."""
    index = getattr(env, "index", None)
    options = dict(getattr(config, "auto_options", {}) or {})
    ctx = AutoContext(config=config, index=index, layer=_layer_loader(config), options=options)
    scripts: dict[str, Script] = {}
    for domain in cmap.script_domains():
        scripts[domain] = SCRIPTS[domain](ctx)
    if cmap.delegated and cmap.steer == "off":
        scripts["army"] = ArmyDelegate(ctx)
    if not scripts:
        return None
    labels = {d: s.label if hasattr(s, "label") else s.name for d, s in scripts.items()}
    return AutoLayer(scripts, labels)


def _layer_loader(config):
    """A loader for the map the army script needs; None without a playset."""
    if getattr(config, "game_dir", None) is None:
        return None
    holder: list = []

    def load():
        if not holder:
            from ..identifiers import Playset
            from ..mapdata import MapLayer

            holder.append(MapLayer.load(Playset.from_paths(config.game_dir, config.mod_dirs),
                                        cache_dir=False))
        return holder[0]

    return load
