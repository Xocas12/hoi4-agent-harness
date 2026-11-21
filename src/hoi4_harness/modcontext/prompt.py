"""The "This playset" section of the system prompt.

It sits in the cached prefix, so it is deterministic: sorted, no dates, nothing
that changes between turns, and cut to a token budget by dropping its lowest-value
lines first. The full lists stay out of the prompt on purpose; the model asks for
them with ``list_decisions`` and ``describe_action``.
"""

from __future__ import annotations

from pathlib import Path

from ..observation.tokens import count_tokens
from .surface import ModContext
from .workshop import playset_briefs

DEFAULT_BUDGET = 900
HEADER = "## This playset"


def _decision_lines(ctx: ModContext, country: str, tools: bool) -> list[tuple[int, str]]:
    rows = ctx.decision_for(country)
    live = [d for d in rows if d.takeable]
    categories = ctx.categories()
    counts: dict[str, int] = {}
    for d in live:
        counts[d.category] = counts.get(d.category, 0) + 1
    out: list[tuple[int, str]] = []
    if not live:
        return [(5, f"{country} has no decisions in this playset.")]
    own = {d.category for d in live if ctx.named_for(d, country)}
    top = sorted(counts, key=lambda c: (c not in own, -(categories[c].priority if c in categories else 0), c))
    keys = set(top)
    # Worth a look first: written for this country, then by category and decision priority.
    ordered = sorted(live, key=lambda d: (not ctx.named_for(d, country),
                                          -(categories[d.category].priority
                                            if d.category in categories else 0), -d.priority, d.id))
    pick = [d for d in ordered if d.kind == "decision"][:8]
    keys |= {d.id for d in pick}
    names = ctx.names(keys)
    cats = ", ".join(f"{names.get(c) or c} ({counts[c]})" for c in top[:8])
    more = f", +{len(top) - 8} more" if len(top) > 8 else ""
    out.append((3, f"Decisions for {country}: {len(live)} in {len(counts)} categories: {cats}{more}."))
    if pick:
        bits = []
        for d in pick:
            cost = f" {d.cost:g}pp" if d.cost else ""
            bits.append(f"{d.id}{cost}" + (f" ({names[d.id]})" if names.get(d.id) else ""))
        out.append((6, "Decisions to start from (use list_decisions for the rest): "
                    + "; ".join(bits) + "."))
    if tools:
        out.append((2, "Take one with take_decision(decision_id); describe_action(id) shows its "
                       "conditions, cost and whether the harness can take it. Missions are the game's "
                       "to activate and cannot be taken."))
        windows = ctx.guis_for(country)
        if windows:
            out.append((4, f"Custom windows for {country}: {len(windows)} "
                           f"({', '.join(w.id for w in windows[:6])}"
                           + (", ..." if len(windows) > 6 else "")
                           + "). list_gui_buttons shows their buttons; click_gui_button presses "
                           "one, checked by the window's own conditions."))
    return out


def playset_section(index, country: str | None = None, *, tools: bool = True,
                    max_tokens: int = DEFAULT_BUDGET, cache_dir: Path | None = None,
                    offline: bool = True, opener=None) -> str:
    """The section, or an empty string for a vanilla playset with no country.

    ``offline`` keeps it from touching the network: a mod with no cached page then
    falls back to its descriptor and readme."""
    ctx = index.mod_context()
    playset = index.playset
    country = (country or "").upper() or None
    # (priority, text): lower numbers are cut last.
    lines: list[tuple[int, str]] = []
    mods = [m for m in playset.mods if m.name.strip().lower() not in {"llm bridge", "llm_bridge"}]
    if mods:
        lines.append((0, f"Mods, in load order: {', '.join(m.name for m in mods)}."))
        for brief in playset_briefs(mods, cache_dir=cache_dir, offline=offline, opener=opener,
                                    per_mod_words=110, total_words=300):
            lines.append((1, f"World ({brief.name}): {brief.text}"))
    start = ctx.start_date()
    lines.append((2, f"The campaign's default start is {start.rsplit('.', 1)[0]}."))
    if country:
        tag = country
        trees = index.trees_for(tag)
        focuses = ctx.focus_details(tag)
        if trees:
            roots = ctx.available_focuses(tag)
            first = [r for r in roots if r in trees[0].focuses]
            roots = first or roots
            names = ctx.names(set(roots[:6]))
            shown = ", ".join(f"{r}" + (f" ({names[r]})" if names.get(r) else "") for r in roots[:6])
            lines.append((1, f"{tag}'s focus tree: {', '.join(t.id for t in trees)}, "
                             f"{len(focuses)} focuses. It opens with: {shown or 'nothing'}."))
        lines += _decision_lines(ctx, tag, tools)
        advisors = ctx.advisors(tag)
        if advisors:
            lines.append((5, f"{tag} has {len(advisors)} advisor candidates (hire_advisor)."))
    if mods:
        mech = ctx.mechanics()
        new = []
        for label in ("ideologies", "resources", "buildings"):
            ids = mech[label]["new"]
            if ids:
                new.append(f"{label}: {', '.join(ids[:8])}" + (f" (+{len(ids) - 8})" if len(ids) > 8 else ""))
        techs = ctx.new_technologies()
        if techs:
            folders = sorted({t["folder"] for t in techs if t["folder"]})
            new.append(f"{len(techs)} technologies the base game lacks"
                       + (f" in {', '.join(folders[:6])}" if folders else ""))
        if new:
            lines.append((4, "What is new here: " + "; ".join(new) + "."))
        if len(mech["replaced_folders"]) > 20:
            lines.append((4, "This is a total conversion: most base-game content is replaced, "
                             "so do not assume vanilla's countries, trees or events."))
    if not mods and not country:
        return ""
    return _fit(lines, max_tokens)


def _fit(lines: list[tuple[int, str]], max_tokens: int) -> str:
    def render(parts):
        return HEADER + "\n" + "\n".join(text for _p, text in parts)

    parts = list(lines)
    while len(parts) > 1 and count_tokens(render(parts))[0] > max_tokens:
        # Drop the highest priority number; among equals, the latest line.
        worst = max(range(len(parts)), key=lambda i: (parts[i][0], i))
        del parts[worst]
    text = render(parts)
    if count_tokens(text)[0] > max_tokens:
        words = text.split(" ")
        while words and count_tokens(" ".join(words))[0] > max_tokens:
            words = words[: max(1, int(len(words) * 0.9))]
            if len(words) == 1:
                break
        text = " ".join(words)
    return text if parts else ""
