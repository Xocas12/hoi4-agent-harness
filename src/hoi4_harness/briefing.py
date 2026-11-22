"""The briefing agent: a dossier of the world, and an orientation written from it (#165).

``build_dossier`` reads what can be read without the game: the playset's files, a save
(every major country, the player's own country, the neighbours and enemies), the country's
paths (#160), the strategy guide and any skill packs, and a short brief per mod (the Steam
Workshop page first). ``brief`` sends the dossier to a separately configured model once and
gets back an orientation: strengths, weaknesses, opportunities, threats, the viable paths
with their trade-offs and timing windows, the decision points to watch, and what would change
the picture.

The briefing orients; it does not decide. It names no single plan, and it never argues against
an instruction the person gave the country: the playing model chooses, and the person's
instruction (the objective, the country's own instruction) comes first. ``context_block``
is the text the player is given and says so.

Nothing here touches the game.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Headings the briefing is asked for, in order. A reply missing one is still kept and the
#: run says which are missing.
SECTIONS = (
    "Strengths",
    "Weaknesses",
    "Opportunities",
    "Threats",
    "Viable paths",
    "Decision points and what to watch",
    "What would change the picture",
)

#: Strategic resources read from the state history files.
RESOURCES = ("oil", "steel", "aluminium", "rubber", "tungsten", "chromium")

BRIEF_SYSTEM = (
    "You are the briefing officer for a Hearts of Iron IV campaign. Another model will play one "
    "country; you will not. You are given a dossier: the world from a save (the major powers, "
    "factions, the balance of threats around the player's country, its resources), the paths "
    "its focus tree and AI behaviour rule open, the strategy guide, and what the mods change. "
    "Write an orientation for the player, not a plan for it.\n\n"
    "Lay out the situation and the choices. Do NOT recommend one grand strategy and do not "
    "write orders: the player decides. Give it the viable paths with what each asks of the "
    "country, what it gains, what it risks, the dates by which it is open or closed, and what "
    "would make it look wrong. Say plainly where the dossier does not tell you something.\n\n"
    "If the dossier has an instruction or objective for this country, that instruction "
    "stands. Read the situation in its light: say what it implies (what it needs, what it "
    "costs, when it is exposed) and never argue against it or suggest a way around it. With "
    "no instruction, give the paths evenly.\n\n"
    "Use only facts that are in the dossier, the strategy guide or your search results, and "
    "mark anything you are inferring. Answer in markdown with exactly these second-level "
    "headings, in this order: "
    + "; ".join(f"## {s}" for s in SECTIONS) + ". Under 'Viable paths' use a third-level "
    "heading per path, and for each give: what it asks, what it gains, what it risks, the "
    "timing window. Under 'Decision points and what to watch' give dated or triggered points "
    "and the numbers or events to look at. Be specific and compact; about 900 to 1500 words."
)

WEB_NOTE = (
    "You may search the web for the mods listed under 'Mods to look up' whose brief above is "
    "thin, and only for what they change in play. Say which claims came from a search."
)

FRAME = (
    "Briefing (orientation, not orders). A scout wrote this from the save and the game files "
    "on {date}. It lays out the situation and the paths that look open; it names no plan for "
    "you to follow. The choice is yours. If the person gave you an instruction or objective "
    "(above), that comes first and this is read in its light. Trust the situation reports over "
    "it where they disagree."
)


@dataclass
class Dossier:
    country: str
    date: str | None = None
    save: Path | None = None
    sections: list[tuple[str, str]] = field(default_factory=list)
    #: What could not be read, one line each (no save, no map, a mod without a page ...).
    gaps: list[str] = field(default_factory=list)
    #: Mods whose brief is thin, for the web search.
    lookup: list[str] = field(default_factory=list)

    def render(self) -> str:
        parts = [f"# Dossier: {self.country}" + (f", save of {self.date}" if self.date else "")]
        for title, text in self.sections:
            parts.append(f"## {title}\n{text.rstrip()}")
        if self.gaps:
            parts.append("## Gaps in this dossier\n" + "\n".join(f"- {g}" for g in self.gaps))
        return "\n\n".join(parts)

    def titles(self) -> list[str]:
        return [t for t, _ in self.sections]

    def section(self, title: str) -> str:
        return next((text for t, text in self.sections if t == title), "")


@dataclass
class Briefing:
    country: str
    date: str | None
    text: str
    model: str = ""
    missing: list[str] = field(default_factory=list)
    web: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return {"country": self.country, "date": self.date, "model": self.model,
                "missing": self.missing, "web": self.web, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "cost_usd": round(self.cost_usd, 4)}


# --- reading ---------------------------------------------------------------------


def _country_names(index, tags: set[str]) -> dict[str, str]:
    """English country names; only the files named for countries are read."""
    found: dict[str, str] = {}
    if index is None:
        return found
    for path in index.playset.layered("localisation", "*.yml"):
        low = path.as_posix().lower()
        if "english" not in low or "countr" not in low:
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for key, value in re.findall(r'^\s*([A-Z][A-Z0-9]{2}):\d*\s*"(.*)"', text, flags=re.M):
            if key in tags:
                found[key] = value
    return found


def resource_map(index, world) -> dict[str, dict[str, int]]:
    """``{resource: {owner tag: units}}`` from the state history files and the save's owners."""
    out: dict[str, dict[str, int]] = {r: {} for r in RESOURCES}
    if index is None or world is None:
        return out
    for path in index.playset.layered("history/states"):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        ident = re.search(r"^\s*id\s*=\s*(\d+)", text, flags=re.M)
        block = re.search(r"resources\s*=\s*\{([^}]*)\}", text)
        if not ident or not block:
            continue
        state = world.states.get(int(ident.group(1)))
        if state is None or not state.owner:
            continue
        for name, amount in re.findall(r"(\w+)\s*=\s*([\d.]+)", block.group(1)):
            if name in out:
                out[name][state.owner] = out[name].get(state.owner, 0) + int(float(amount))
    return out


def major_powers(world, limit: int = 10) -> list[str]:
    """The countries with the most factories in the save: the world's major powers."""
    totals: dict[str, int] = {}
    for s in world.states.values():
        if s.owner:
            totals[s.owner] = totals.get(s.owner, 0) + s.civ + s.mil + s.dock
    return [t for t, n in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])) if n][:limit]


def _flag(tag: str, names: dict[str, str]) -> str:
    return f"{tag} ({names[tag]})" if tag in names else tag


def _line_country(world, tag: str, names: dict[str, str]) -> str:
    c = world.countries.get(tag)
    fac = world.factories(tag)
    bits = [f"{_flag(tag, names)}: factories civ {fac['civ']} mil {fac['mil']} dock {fac['dock']}"]
    if c is not None:
        bits.append(f"{len(c.divisions)} divisions")
        full = c.full
        if full is not None:
            pol = full.politics
            parties = ", ".join(f"{k} {v:.0f}%" for k, v in sorted(
                pol.get("parties", {}).items(), key=lambda kv: -kv[1]) if v)
            bits.append(f"ruling {pol.get('ruling_party') or '?'}" + (f" ({parties})" if parties else ""))
            wars = sorted(t for t, r in full.relations.items() if "war" in r)
            if wars:
                bits.append("at war with " + ", ".join(wars))
            fz = sorted(t for t, r in full.relations.items()
                        if r.get("guarantee", {}).get("direction") == "to")
            if fz:
                bits.append("guarantees " + ", ".join(fz))
    faction = world.full.faction_of(tag) if world.full is not None else None
    if faction:
        bits.append(f"faction {faction['template']} (leader {faction['leader']})")
    return "; ".join(bits)


def _own_section(world, me: str, names: dict[str, str]) -> str:
    c = world.countries.get(me)
    lines = [_line_country(world, me, names)]
    full = c.full if c is not None else None
    if full is None:
        lines.append("The save's detailed picture of this country could not be read"
                     + (f" ({world.full_error})." if world.full_error else "."))
        return "\n".join(lines)
    pol, eco = full.politics, full.economy
    leaders = ", ".join(f"{k}: {v}" for k, v in pol.get("leaders", {}).items())
    if leaders:
        lines.append(f"Leaders by party: {leaders}")
    lines.append(f"Political power {pol.get('political_power', 0)}; elections "
                 f"{'allowed' if pol.get('elections_allowed') else 'not allowed'}")
    lines.append(f"Stability {eco.get('stability_base')}%, war support {eco.get('war_support_base')}% "
                 f"(base); manpower available {eco.get('manpower', {}).get('available', 0):,}")
    if full.ideas.get("laws"):
        lines.append("Laws: " + ", ".join(full.ideas["laws"]))
    if full.ideas.get("spirits"):
        lines.append("National spirits: " + ", ".join(full.ideas["spirits"][:12]))
    if full.ideas.get("advisors"):
        lines.append("Advisors: " + ", ".join(
            f"{a['name']} ({a.get('slot')})" for a in full.ideas["advisors"]))
    ships = sum(f["ship_count"] for f in full.fleets)
    planes = sum(w["planes"] for w in full.air)
    lines.append(f"Navy {ships} ships in {len(full.fleets)} task forces; air {planes} planes")
    held = sorted(full.in_service.items(), key=lambda kv: -kv[1])[:6]
    if held:
        lines.append("Equipment in service: " + ", ".join(f"{k} {v}" for k, v in held))
    stock = sorted(full.stockpile.items(), key=lambda kv: -kv[1])[:6]
    if stock:
        lines.append("Stockpile: " + ", ".join(f"{k} {v}" for k, v in stock))
    fuel = eco.get("fuel") or {}
    if fuel.get("max"):
        lines.append(f"Fuel {fuel.get('stock', 0):,} of {fuel.get('max', 0):,}, "
                     f"net {fuel.get('gain', 0) - fuel.get('cost', 0):+.0f} a day")
    if c.production:
        lines.append("Production lines: " + "; ".join(
            f"{p.equipment if hasattr(p, 'equipment') else p}" for p in c.production[:8]))
    short = sum(d.equipment_missing or 0 for d in c.divisions)
    if short:
        lines.append(f"Divisions short of equipment: {short} items requested and not delivered")
    return "\n".join(lines)


def _threats_section(world, me: str, names: dict[str, str]) -> str:
    mine = world.factories(me)
    mine_div = len(world.countries[me].divisions) if me in world.countries else 0
    lines = [f"{me} for scale: factories civ {mine['civ']} mil {mine['mil']}, {mine_div} divisions"]
    rows = []
    for tag, c in world.countries.items():
        if tag == me:
            continue
        fac = world.factories(tag)
        mil_ratio = f"{fac['mil'] / mine['mil']:.1f}x" if mine["mil"] else "n/a"
        mark = " [AT WAR with " + me + "]" if tag in world.enemies else ""
        rows.append((-(fac["mil"] + fac["civ"]), tag,
                     f"{_flag(tag, names)}: mil factories {fac['mil']} ({mil_ratio} ours), "
                     f"{len(c.divisions)} divisions{mark}"))
    lines += [r for *_x, r in sorted(rows)]
    web = []
    for tag, c in world.countries.items():
        if c.full is None:
            continue
        for other, rel in c.full.relations.items():
            if rel.get("guarantee", {}).get("direction") == "to":
                web.append(f"{tag} guarantees {other}")
    if web:
        lines.append("Guarantee web among the countries read: " + "; ".join(sorted(web)))
    return "\n".join(lines)


def _resources_section(index, world, me: str) -> str:
    table = resource_map(index, world)
    lines = []
    for res in RESOURCES:
        owners = table.get(res, {})
        mine = owners.get(me, 0)
        top = sorted(owners.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
        lines.append(f"{res}: {me} holds {mine}; largest holders "
                     + ", ".join(f"{t} {n}" for t, n in top)
                     + ("  <- none at home" if not mine else ""))
    lines.append("(Natural resources by state from the history files, assigned to the save's "
                 "owners. Trade, conquest and the game's own consumption are not in it.)")
    return "\n".join(lines)


def _factions_section(world) -> str:
    if world.full is None or not world.full.factions:
        return "none in the save"
    return "\n".join(
        f"{f['template'] or 'faction'}: leader {f['leader']}, ideology {f['ideology']}, members "
        f"{', '.join(f['members'])}" for f in world.full.factions)


def guide_text() -> str:
    from .guidance import load

    return load("strategy")


def read_save_world(index, country: str, save: Path, layer=None, majors: int = 10):
    """The save read for ``country`` plus the world's major powers."""
    from . import world as W
    from .adapters import savegame as sg

    text_world = W.read_world(save, country, layer=layer, also=())
    # a second read, now that the factory table says who the major powers are
    chosen = tuple(t for t in major_powers(text_world, majors) if t != country)
    del sg
    return W.read_world(save, country, layer=layer, also=chosen)


def build_dossier(config, index, country: str, *, save: Path | None = None, layer=None,
                  offline: bool = True, opener=None, cache_dir: Path | None = None,
                  previous: tuple[str, str] | None = None, world=None,
                  majors: int = 10) -> Dossier:
    """Everything the briefing is written from. ``previous`` is ``(date, text)`` of the last
    briefing, for a re-briefing. A part that cannot be read is skipped and named in the gaps."""
    country = country.upper()
    dossier = Dossier(country)
    add = lambda title, text: dossier.sections.append((title, text))  # noqa: E731

    instruction = [t.strip() for t in (getattr(config, "objective", ""),
                                       getattr(config, "instruction", "")) if t and t.strip()]
    add("Objective and instruction for this country",
        "\n\n".join(instruction) if instruction else
        "None given. The player chooses its own course; give the paths evenly.")
    playset = index.playset if index is not None else None
    add("Playset", (playset.name if playset else "unknown")
        + (f"; mods: {', '.join(m.name for m in playset.mods)}" if playset and playset.mods else ""))

    if world is None and save is not None:
        try:
            world = read_save_world(index, country, Path(save), layer, majors)
        except Exception as exc:  # noqa: BLE001 - a save that cannot be read costs the world picture only
            dossier.gaps.append(f"the save {Path(save).name} could not be read: "
                                f"{type(exc).__name__}: {exc}")
    if save is None and world is None:
        dossier.gaps.append("no save: the world picture (countries, factions, balance of "
                            "threats, resources) is missing; only the playset's files are read")
    if layer is None and world is not None:
        dossier.gaps.append("no map layer: neighbours and where divisions stand are not known")
    if world is not None:
        dossier.date = world.date
        dossier.save = Path(save) if save else None
        tags = set(world.countries) | set(major_powers(world, majors))
        names = _country_names(index, tags)
        add(f"The world on {world.date}",
            f"Player in the save: {world.player}. Countries below are the major powers by "
            "industry and those around " + country + ".\n"
            + "\n".join(_line_country(world, t, names) for t in major_powers(world, majors)))
        add(f"Your country: {_flag(country, names)}", _own_section(world, country, names))
        add("Factions", _factions_section(world))
        add(f"Balance of threats around {country}", _threats_section(world, country, names))
        add("Resources", _resources_section(index, world, country))
        if world.full_error:
            dossier.gaps.append(f"part of the save could not be read: {world.full_error}")

    if index is not None:
        from . import pathchoice

        try:
            add("Paths the country's files open (#160)",
                pathchoice.render_menu(pathchoice.build_menu(index, country), limit=15))
        except Exception as exc:  # noqa: BLE001
            dossier.gaps.append(f"paths: {type(exc).__name__}: {exc}")
        mods = _mod_section(index, offline, opener, cache_dir, dossier)
        if mods:
            add("Mods", mods)
    from .skills import skills_section

    try:
        packs = skills_section(config, index)
    except Exception as exc:  # noqa: BLE001
        packs = ""
        dossier.gaps.append(f"skill packs: {type(exc).__name__}: {exc}")
    if packs:
        add("Skill packs in use", packs)
    add("Strategy guide", guide_text())
    if previous:
        add(f"Your last briefing (written {previous[0]})", previous[1])
    if dossier.lookup:
        add("Mods to look up", "\n".join(f"- {m}" for m in dossier.lookup))
    return dossier


def _mod_section(index, offline: bool, opener, cache_dir, dossier: Dossier) -> str:
    from .modcontext.workshop import playset_briefs

    mods = index.playset.mods
    if not mods:
        return ""
    briefs = playset_briefs(mods, cache_dir=cache_dir, offline=offline, opener=opener)
    lines = []
    for b in briefs:
        lines.append(f"- {b.name} [{b.source}]: {b.text}")
        if b.source != "steam":
            dossier.lookup.append(b.name)
    return "\n".join(lines)


# --- the model ---------------------------------------------------------------------


def _missing(text: str) -> list[str]:
    have = {m.group(1).strip().lower() for m in re.finditer(r"^##\s+(.+?)\s*$", text, flags=re.M)}
    return [s for s in SECTIONS if s.lower() not in have]


def brief(client, dossier: Dossier, *, web: bool = False, max_tokens: int = 6000,
          prices: tuple[float, float] | None = None) -> Briefing:
    """Ask ``client`` once. ``web`` has already been negotiated with the client (see
    ``enable_web``); it only changes what the prompt says. An empty reply is an error."""
    from .agent.llm.base import Msg

    system = BRIEF_SYSTEM + ("\n\n" + WEB_NOTE if web and dossier.lookup else "")
    with client.prose(web=web and client.web_search_support):
        response = client.complete(system, [Msg(role="user", text=dossier.render())],
                                   tools=None, max_tokens=max_tokens)
    text = (getattr(response, "text", "") or "").strip()
    if not text:
        raise RuntimeError("the briefing model returned nothing")
    usage = response.usage
    cost = getattr(client, "last_cost_usd", 0.0) or 0.0
    if not cost and prices:
        cost = (usage.input_tokens * prices[0] + usage.output_tokens * prices[1]) / 1_000_000
    return Briefing(dossier.country, dossier.date, text, getattr(response, "model", "") or
                    getattr(client, "model", ""), _missing(text), web,
                    usage.input_tokens, usage.output_tokens, cost)


def enable_web(client, requested: bool) -> tuple[bool, str]:
    """``(on, why)``: web search is on only when asked for and the client has one. A scripted
    or mailbox client never can."""
    if not requested:
        return False, "web search is off"
    if not getattr(client, "web_search_support", False):
        return False, (f"web search is not available for the {getattr(client, 'name', 'this')} "
                       "provider; the briefing goes without it")
    return True, "web search is on"


# --- the player's side -----------------------------------------------------------------


def context_block(briefing_text: str, date: str | None) -> str:
    """The text the player is given each turn, framed as orientation. The standing objective
    and the country's instruction come before it in the prompt."""
    return FRAME.format(date=date or "an earlier date") + "\n\n" + briefing_text.strip()


# --- keeping it ---------------------------------------------------------------------------


def save_briefing(run_dir: Path, briefing: Briefing, dossier: Dossier) -> Path:
    """``briefing.md`` (the current one) and a dated copy and dossier under ``briefings/``."""
    run_dir = Path(run_dir)
    folder = run_dir / "briefings"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (briefing.date or time.strftime("%Y-%m-%d")).replace(".", "-")
    head = f"# Briefing for {briefing.country}, {briefing.date or 'undated'}\n\n"
    body = head + briefing.text.strip() + "\n"
    (folder / f"{stamp}-{briefing.country}.md").write_text(body, encoding="utf-8")
    (folder / f"{stamp}-{briefing.country}-dossier.md").write_text(dossier.render(), encoding="utf-8")
    current = run_dir / "briefing.md"
    current.write_text(body, encoding="utf-8")
    return current


def latest_briefing(run_dir: Path) -> str | None:
    path = Path(run_dir) / "briefing.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return text.partition("\n\n")[2].strip() or None


# --- when to brief again ---------------------------------------------------------------------

EVENTS = ("war", "capitulation", "faction")


def parse_schedule(spec) -> list[str]:
    """``campaign_rebrief``: a list or comma string of ``yearly``, ``halfyearly``, ``YYYY-MM-DD``
    and the events ``war`` (a new enemy), ``capitulation`` (the player's), ``faction`` (joins,
    leaves or changes faction)."""
    items = spec.split(",") if isinstance(spec, str) else list(spec or [])
    out = []
    for item in items:
        item = str(item).strip().lower()
        if not item:
            continue
        if item in ("yearly", "halfyearly", *EVENTS) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", item):
            out.append(item)
        else:
            raise ValueError(f"campaign_rebrief: {item!r} is not yearly, halfyearly, a date "
                             f"YYYY-MM-DD or one of {', '.join(EVENTS)}")
    return out


def _ymd(date: str) -> tuple[int, int, int]:
    y, m, d = (int(x) for x in re.findall(r"\d+", date)[:3])
    return y, m, d


def due(schedule: list[str], *, date: str, last: str | None, seen_wars: set[str],
        wars: set[str], faction_before: str | None, faction_now: str | None,
        capitulated: bool, done_dates: set[str]) -> str | None:
    """The reason to brief again at ``date``, or None. ``last`` is the date of the last
    briefing. Dated entries fire once on the first turn on or after the date."""
    if not schedule or not date:
        return None
    for item in schedule:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", item):
            if item not in done_dates and _ymd(date) >= _ymd(item):
                return f"the date {item} was reached"
        elif item in ("yearly", "halfyearly") and last:
            (y0, m0, _), (y1, m1, _) = _ymd(last), _ymd(date)
            months = (y1 - y0) * 12 + m1 - m0
            if months >= (12 if item == "yearly" else 6):
                return f"{months} months since the last briefing"
        elif item == "war" and wars - seen_wars:
            return "war with " + ", ".join(sorted(wars - seen_wars))
        elif item == "capitulation" and capitulated:
            return "the country capitulated"
        elif item == "faction" and faction_before != faction_now:
            return f"faction changed from {faction_before or 'none'} to {faction_now or 'none'}"
    return None
