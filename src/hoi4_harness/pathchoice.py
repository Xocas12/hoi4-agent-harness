"""The paths open to a country before a campaign starts, and the choice of one (#160).

Two things come from the playset's files:

* the **AI behaviour rules**: ``common/game_rules/*.txt`` defines, per country, a rule such
  as ``GER_ai_behavior`` whose options (``DEFAULT``, ``FASCIST``, ``KAISER`` ...) steer the
  game's AI down one route of the focus tree when Historical AI Focuses is off. Names and
  descriptions are the English localisation's;
* a **focus tree summary**: the branches (what hangs off each root focus), and inside each
  the focuses that commit the country to something: an ideology change, a war goal, a
  puppet, a faction.

``choose_path`` shows both to the model once, reads back ``{"choice": ..., "reason": ...}``
and returns a ``PathChoice`` the run keeps. Where the playset has no rule for the country
(a mod, a minor) the choice is a plain path name and steers the guidance only.

Nothing here touches the game.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import asdict, dataclass, field

from .adapters.savegame import parse_clausewitz
from .identifiers import IdentifierIndex, _as_list, _uncommented

AI_GROUP = "RULE_GROUP_AI_BEHAVIOR"

#: What a completion reward does that commits the country to a path, by the effect that does it.
COMMIT_KEYS = {
    "set_politics": "ideology", "add_popularity": "ideology", "set_party_rank": "ideology",
    "create_wargoal": "war goal", "declare_war_on": "war", "start_civil_war": "war",
    "annex_country": "war goal",
    "puppet": "puppet", "set_autonomy": "puppet", "release_puppet": "puppet",
    "create_faction": "faction", "add_to_faction": "faction", "join_faction": "faction",
    "add_to_war": "war",
}
#: Rewards worth counting as what a branch "gets".
GAIN_KEYS = ("add_ideas", "add_research_slot", "add_political_power", "add_war_support",
             "add_stability", "add_tech_bonus", "army_experience", "add_building_construction",
             "country_event", "unlock_decision_tooltip", "add_manpower", "add_timed_idea")


@dataclass
class RuleOption:
    name: str
    text: str = ""
    desc: str = ""
    required_dlc: str = ""
    default: bool = False


@dataclass
class AiRule:
    token: str
    title: str
    options: list[RuleOption] = field(default_factory=list)

    @property
    def default(self) -> RuleOption | None:
        return next((o for o in self.options if o.default), self.options[0] if self.options else None)

    def option(self, name: str) -> RuleOption | None:
        wanted = str(name).strip().lower()
        return next((o for o in self.options if o.name.lower() == wanted
                     or (o.text and o.text.lower() == wanted)), None)


@dataclass
class Commit:
    focus: str
    name: str
    kind: str                 # ideology | war goal | war | puppet | faction
    detail: str = ""


@dataclass
class Branch:
    root: str
    name: str
    focuses: int = 0
    commits: list[Commit] = field(default_factory=list)
    gains: dict[str, int] = field(default_factory=dict)


@dataclass
class PathMenu:
    country: str
    rules: list[AiRule] = field(default_factory=list)
    trees: list[str] = field(default_factory=list)
    branches: list[Branch] = field(default_factory=list)
    #: ``focus: [focuses it excludes]`` for the focuses that commit to something.
    exclusive: dict[str, list[str]] = field(default_factory=dict)

    @property
    def rule(self) -> AiRule | None:
        return self.rules[0] if self.rules else None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PathChoice:
    country: str
    rule: str | None          # the rule token, None when the playset has none for the country
    option: str               # the option name, or the model's own path name without a rule
    reason: str
    source: str = "model"     # model | default (the reply could not be read)

    def to_dict(self) -> dict:
        return asdict(self)


# --- reading the files ---------------------------------------------------------


def _localisation(index: IdentifierIndex, wanted: set[str]) -> dict[str, str]:
    """English text for ``wanted`` keys; a later (or ``replace/``) definition wins, as in the game."""
    found: dict[str, str] = {}
    files = [p for p in index.playset.layered("localisation", "*.yml")
             if "english" in p.as_posix().lower()]
    files.sort(key=lambda p: "replace" in p.parent.parts)
    for path in files:
        replacing = "replace" in path.parent.parts
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for key, value in re.findall(r'^\s*([\w.]+):\d*\s*"(.*)"', text, flags=re.M):
            if key in wanted and (replacing or key not in found):
                found[key] = value
    return found


def _clean(text: str) -> str:
    """Localisation markup out: ``@GER`` flags, ``§Y`` colour codes, ``\\n`` breaks."""
    text = re.sub(r"§.", "", text.replace("§!", ""))
    text = re.sub(r"@[A-Z0-9]{3}\s*", "", text).replace("\\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def _rule_blocks(index: IdentifierIndex) -> dict[str, dict]:
    rules: dict[str, dict] = {}
    for path in index.playset.layered("common/game_rules"):
        try:
            parsed = parse_clausewitz(_uncommented(path))
        except Exception:  # noqa: BLE001 - one unreadable file must not hide the rest
            continue
        for token, block in parsed.items():
            if isinstance(block, dict):
                rules[token] = block        # a later file (a mod) replaces the rule, as in the game
    return rules


def ai_rules(index: IdentifierIndex, tag: str) -> list[AiRule]:
    """The AI behaviour rules defined for ``tag`` (usually one: ``<TAG>_ai_behavior``)."""
    tag = tag.upper()
    blocks = {tok: b for tok, b in _rule_blocks(index).items()
              if tok.upper().startswith(tag + "_")
              and (str(b.get("group", "")).strip('"') == AI_GROUP
                   or tok.lower().endswith("ai_behavior"))}
    keys: set[str] = set()
    for block in blocks.values():
        keys.add(str(block.get("name", "")).strip('"'))
        for entry in [*_as_list(block.get("default")), *_as_list(block.get("option"))]:
            if isinstance(entry, dict):
                keys |= {str(entry.get(k, "")).strip('"') for k in ("text", "desc")}
    loc = _localisation(index, keys - {""})
    rules = []
    for token, block in sorted(blocks.items()):
        title_key = str(block.get("name", "")).strip('"')
        rule = AiRule(token=token, title=_clean(loc.get(title_key, title_key or token)))
        for is_default, entries in ((True, _as_list(block.get("default"))),
                                    (False, _as_list(block.get("option")))):
            for entry in entries:
                if not isinstance(entry, dict) or "name" not in entry:
                    continue
                text_key = str(entry.get("text", "")).strip('"')
                desc_key = str(entry.get("desc", "")).strip('"')
                rule.options.append(RuleOption(
                    name=str(entry["name"]).strip('"'),
                    text=_clean(loc.get(text_key, text_key)),
                    desc=_clean(loc.get(desc_key, desc_key)) if desc_key else "",
                    required_dlc=str(entry.get("required_dlc", "")).strip('"'),
                    default=is_default))
        rules.append(rule)
    return rules


def _walk(value):
    """Every (key, value) pair inside a parsed block, however deep."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if key == "_items":
                continue
            for item in _as_list(inner):
                yield key, item
                yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _commits(focus_id: str, name: str, reward) -> list[Commit]:
    found: list[Commit] = []
    for key, value in _walk(reward):
        kind = COMMIT_KEYS.get(key)
        if kind is None:
            continue
        detail = ""
        if isinstance(value, dict):
            detail = " ".join(str(value[k]) for k in ("ruling_party", "ideology", "target", "type")
                              if k in value)
        elif isinstance(value, str):
            detail = value.strip('"')
        found.append(Commit(focus_id, name, kind, detail))
    # one line per (kind, detail): a focus that sets three ideologies' popularity is one commit each
    seen, unique = set(), []
    for commit in found:
        if (commit.kind, commit.detail) not in seen:
            seen.add((commit.kind, commit.detail))
            unique.append(commit)
    return unique


def _focus_blocks(index: IdentifierIndex, tree_ids: set[str]) -> list[dict]:
    """The raw focus blocks of the named trees, plus the shared focuses they list."""
    blocks: list[dict] = []
    shared: dict[str, dict] = {}
    wanted_shared: list[str] = []
    for path in index.playset.layered("common/national_focus"):
        try:
            parsed = parse_clausewitz(_uncommented(path))
        except Exception:  # noqa: BLE001
            continue
        for block in _as_list(parsed.get("shared_focus")):
            if isinstance(block, dict) and "id" in block:
                shared[str(block["id"])] = block
        for tree in _as_list(parsed.get("focus_tree")):
            if isinstance(tree, dict) and str(tree.get("id", "")) in tree_ids:
                blocks = [b for b in blocks if not (b.get("_tree") == tree.get("id"))]
                for entry in _as_list(tree.get("focus")):
                    if isinstance(entry, dict) and "id" in entry:
                        blocks.append({**entry, "_tree": str(tree.get("id"))})
                wanted_shared += [str(s) for s in _as_list(tree.get("shared_focus"))
                                  if isinstance(s, str)]
    blocks += [{**shared[s], "_tree": "shared"} for s in wanted_shared if s in shared]
    return blocks


def _prereqs(block: dict) -> list[str]:
    out: list[str] = []
    for entry in _as_list(block.get("prerequisite")):
        if isinstance(entry, dict):
            out += [str(f) for f in _as_list(entry.get("focus")) if isinstance(f, str)]
    return out


def _exclusive(block: dict) -> list[str]:
    out: list[str] = []
    for entry in _as_list(block.get("mutually_exclusive")):
        if isinstance(entry, dict):
            out += [str(f) for f in _as_list(entry.get("focus")) if isinstance(f, str)]
    return out


def _x(block: dict) -> float:
    try:
        return float(block.get("x", 0))
    except (TypeError, ValueError):
        return 0.0


def focus_branches(index: IdentifierIndex, tag: str) -> tuple[list[str], list[Branch], dict[str, list[str]]]:
    """The tree ids ``tag`` has, its branches, and what the committing focuses exclude.

    A branch is everything reached from one root focus (one with no prerequisite); a focus
    reachable from several roots belongs to the nearest, the leftmost on a tie."""
    trees = index.trees_for(tag.upper())
    blocks = _focus_blocks(index, {t.id for t in trees})
    by_id = {str(b["id"]): b for b in blocks}
    keys = set(by_id) | {str(b["id"]) for b in blocks}
    loc = _localisation(index, keys)
    name_of = lambda fid: _clean(loc.get(fid, fid))  # noqa: E731
    children: dict[str, list[str]] = {fid: [] for fid in by_id}
    for fid, block in by_id.items():
        for parent in _prereqs(block):
            if parent in children:
                children[parent].append(fid)
    roots = sorted((fid for fid, b in by_id.items() if not _prereqs(b)),
                   key=lambda fid: (_x(by_id[fid]), fid))
    owner: dict[str, str] = {r: r for r in roots}
    queue = deque(roots)
    while queue:
        fid = queue.popleft()
        for child in sorted(children.get(fid, []), key=lambda c: (_x(by_id[c]), c)):
            if child not in owner:
                owner[child] = owner[fid]
                queue.append(child)
    branches = {r: Branch(root=r, name=name_of(r)) for r in roots}
    exclusive: dict[str, list[str]] = {}
    for fid, root in owner.items():
        block, branch = by_id[fid], branches[root]
        branch.focuses += 1
        reward = block.get("completion_reward")
        for key, _value in _walk(reward):
            if key in GAIN_KEYS:
                branch.gains[key] = branch.gains.get(key, 0) + 1
        commits = _commits(fid, name_of(fid), reward)
        branch.commits += commits
        if commits and _exclusive(block):
            exclusive[fid] = _exclusive(block)
    return [t.id for t in trees], list(branches.values()), exclusive


def build_menu(index: IdentifierIndex, tag: str) -> PathMenu:
    tree_ids, branches, exclusive = focus_branches(index, tag)
    return PathMenu(country=tag.upper(), rules=ai_rules(index, tag), trees=tree_ids,
                    branches=branches, exclusive=exclusive)


# --- showing it ----------------------------------------------------------------


def _grouped(commits: list[Commit]) -> list[tuple[str, str, dict[str, list[str]]]]:
    """``(focus id, name, {kind: [details]})`` in order, one entry per focus."""
    out: dict[str, tuple[str, str, dict[str, list[str]]]] = {}
    for c in commits:
        entry = out.setdefault(c.focus, (c.focus, c.name, {}))
        detail = c.detail.split()[0] if c.detail else ""
        if detail in ("PREV", "ROOT", "THIS", "FROM"):
            detail = ""
        details = entry[2].setdefault(c.kind, [])
        if detail and detail not in details:
            details.append(detail)
    return list(out.values())


def render_menu(menu: PathMenu, limit: int | None = None) -> str:
    """The menu as text. ``limit`` caps the committing focuses listed per branch (the prompt
    uses it: the German tree has well over a hundred)."""
    lines = [f"Paths open to {menu.country}"]
    if menu.rule is None:
        lines.append(f"AI behaviour rule: none in this playset for {menu.country} (a path you "
                     "pick steers the guidance only).")
    for rule in menu.rules:
        lines.append(f"AI behaviour rule {rule.token} ({rule.title}), options:")
        for opt in rule.options:
            star = " [default]" if opt.default else ""
            dlc = f" (needs DLC: {opt.required_dlc})" if opt.required_dlc else ""
            lines.append(f"  {opt.name}: {opt.text or opt.name}{star}{dlc}")
            if opt.desc:
                lines.append(f"      {opt.desc}")
    lines.append(f"Focus tree: {', '.join(menu.trees) or 'none'}; {len(menu.branches)} branches")
    for branch in menu.branches:
        label = f"{branch.name} [{branch.root}]" if branch.name != branch.root else branch.root
        lines.append(f"  Branch {label}: {branch.focuses} focuses")
        if branch.gains:
            lines.append("      gets: " + ", ".join(f"{k} x{n}" for k, n in sorted(branch.gains.items())))
        groups = _grouped(branch.commits)
        for focus, name, kinds in groups[:limit]:
            what = "; ".join(f"{kind} {' '.join(details)}".strip() for kind, details in kinds.items())
            lines.append(f"      commits: {name} [{focus}]: {what}")
            if focus in menu.exclusive:
                lines.append(f"          excludes: {', '.join(menu.exclusive[focus])}")
        if limit is not None and len(groups) > limit:
            lines.append(f"      ... {len(groups) - limit} more committing focuses")
    return "\n".join(lines)


def menu_json(menu: PathMenu) -> str:
    return json.dumps(menu.to_dict(), indent=2)


# --- the model's choice --------------------------------------------------------

CHOOSE_SYSTEM = (
    "You are about to play a Hearts of Iron IV campaign for one country, from the 1936 start, "
    "to conquer as much of the world as you can by the end of 1948. Before it starts you choose "
    "which path your country takes. Read the options and answer with one JSON object and "
    'nothing else: {"choice": "<option name>", "reason": "<one or two sentences>"}.')


def choice_prompt(menu: PathMenu, briefing: str | None = None) -> str:
    rule = menu.rule
    if rule is not None:
        ask = (f"Choose one option of {rule.token}, by its name "
               f"({', '.join(o.name for o in rule.options)}).")
    else:
        ask = ("The playset has no AI behaviour rule for this country, so name the path you want "
               "in a few words (a branch or a focus from the list). It steers your own guidance "
               "for the campaign.")
    head = (f"{briefing.strip()}\n\nThe briefing informs your choice; it does not make it.\n\n"
            if briefing else "")
    return f"{head}{render_menu(menu, limit=25)}\n\n{ask}"


def parse_choice(text: str, menu: PathMenu) -> tuple[str, str] | None:
    """``(option or path, reason)`` from the model's reply, or None when it names nothing usable."""
    match = re.search(r"\{.*\}", text or "", flags=re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    pick = str(data.get("choice") or data.get("option") or "").strip()
    reason = str(data.get("reason") or "").strip()
    if not pick:
        return None
    if menu.rule is not None:
        option = menu.rule.option(pick)
        return (option.name, reason) if option else None
    return pick, reason


def choose_path(client, menu: PathMenu, *, max_tokens: int = 2000,
                briefing: str | None = None) -> PathChoice:
    """Ask ``client`` once. An unreadable or invalid reply falls back to the rule's default
    option (or "no path chosen" without a rule) and says so in the reason; it is never asked again."""
    from .agent.llm.base import Msg

    response = client.complete(CHOOSE_SYSTEM, [Msg(role="user", text=choice_prompt(menu, briefing))],
                              tools=None, max_tokens=max_tokens)
    rule = menu.rule
    parsed = parse_choice(getattr(response, "text", "") or "", menu)
    if parsed is not None:
        option, reason = parsed
        return PathChoice(menu.country, rule.token if rule else None, option,
                          reason or "(no reason given)")
    fallback = rule.default.name if rule and rule.default else "no path chosen"
    # Say what came back: the live run of 2026-11-27 fell back with no trace of the reply (600
    # tokens cut Haiku off mid-reasoning, before its JSON). The limit is now 2000.
    text = " ".join((getattr(response, "text", "") or "").split())
    stop = getattr(response, "stop_reason", None)
    seen = f" (stop: {stop}; the reply began: {text[:160]!r})" if text else f" (stop: {stop}; empty reply)"
    return PathChoice(menu.country, rule.token if rule else None, fallback,
                      "the model's reply did not name a valid choice; the rule's default is used"
                      + seen, source="default")


def guidance_text(choice: PathChoice) -> str:
    """The paragraph that goes into the campaign's objective: the path, and why."""
    what = (f"the {choice.option} path ({choice.rule})" if choice.rule else
            f"the path \"{choice.option}\"")
    text = (f"Your country's path, chosen before the campaign began: {what}. Your reason then: "
            f"{choice.reason} Plan the conquest around it: the focuses and wars on that path are "
            "the ones to expect and to point the AI at; do not plan as if the other branches were open.")
    if choice.rule is None:
        text += " (The playset has no rule to set it, so only you hold to it.)"
    return text

