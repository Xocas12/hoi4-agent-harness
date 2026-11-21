"""The events of a playset: title, description, options and what each option does, and the
matcher that says which of them a window on the screen is.

Read from ``events/*.txt`` and the English localisation through the same layering as everything
else in ``modcontext`` (``Playset.layered``), nothing evaluated. ``EventCatalog.identify`` takes
the text a screen reader (``adapters/ocr.py``) found in an event window and returns the playset
event it most probably is, with a confidence in [0, 1], the runner-up and the margin to it, so a
caller can refuse to act on a guess.

Why matching text and not reading an id: the game gives the mod no on_action for an event
reaching a country (``common/on_actions/_documentation.md`` lists none, and a mod cannot add to
a vanilla event's ``immediate``), the save has no pending-event list, and the log does not print
events. The window's own text is the only witness. docs/events.md has the whole argument.

Matching works on words the localisation fixes. A title such as ``[FROM.GetNameDef] demands
Danzig`` has a placeholder the game fills in, so each text is kept as fragments (the literal runs
between placeholders) and a fragment is scored by how well it fits, approximately, anywhere in
the screen text. Typos from the OCR cost a little similarity, not the match.
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from dataclasses import dataclass, field

from . import script as S
from .surface import ModContext, english

EVENT_KINDS = ("country_event", "news_event", "state_event", "unit_leader_event",
               "operative_leader_event")
#: Keys of an option block that are not effects.
_OPTION_META = {"name", "trigger", "ai_chance", "original_recipient_only", "highlight_states",
                "tooltip", "custom_tooltip"}
#: Below this a fragment says nothing (``the``, ``of``): it is not scored.
MIN_FRAGMENT = 4
#: Confidence needed to say an event is identified, and how far ahead of the runner-up.
IDENTIFIED = 0.80
CLEAR_MARGIN = 0.05
SURE = 0.92


# --- text ------------------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Lower case, accents folded, everything that is not a letter or digit a single space."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def fragments(raw: str) -> list[str]:
    """The literal runs of a localised string, folded. ``[ROOT.GetName] sues for peace`` gives
    ``["sues for peace"]``; a string that is all placeholder gives nothing."""
    text = re.sub(r"§.", "", raw).replace("\\n", " ").replace("\\t", " ")
    text = re.sub(r"\[[^\]]*\]|\$[\w.|]+\$|£\w+", "\x00", text)
    runs = [_fold(part) for part in text.split("\x00")]
    return [r for r in runs if len(r) >= MIN_FRAGMENT]


def display(raw: str) -> str:
    """The string as a person reads it, placeholders dropped (as ``ModContext.names``)."""
    text = re.sub(r"§.", "", raw).replace("\\n", " ").replace("\\t", " ")
    text = re.sub(r"\[[^\]]*\]|\$[\w.|]+\$|£\w+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def partial(needle: str, haystack: str) -> float:
    """How well ``needle`` fits somewhere inside ``haystack``, 0 to 1 (both folded): the best
    similarity of the needle to a window of the haystack about its length."""
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    n = len(needle)
    if len(haystack) <= n:
        return _ratio(needle, haystack)
    best = 0.0
    step = max(1, n // 5)
    spots = list(range(0, len(haystack) - n + 1, step))
    if spots[-1] != len(haystack) - n:
        spots.append(len(haystack) - n)
    for start in spots:
        for width in (n - 2, n, n + 2):
            if width < 1:
                continue
            best = max(best, _ratio(needle, haystack[start:start + width]))
            if best >= 0.999:
                return best
    return best


def fit(texts: list[str], haystack: str) -> float | None:
    """The weighted fit of a localised string's fragments in ``haystack``; None when the string
    has no literal text to score (it is all placeholder)."""
    runs = [r for t in texts for r in fragments(t)]
    if not runs:
        return None
    total = sum(len(r) for r in runs)
    return sum(len(r) * partial(r, haystack) for r in runs) / total


# --- records ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class EventOption:
    index: int                       # 1-based, in file order
    key: str                         # the localisation key of ``name``
    text_raw: str = ""
    text: str = ""
    trigger: list = field(default_factory=list, compare=False)
    effects: list = field(default_factory=list, compare=False)
    ai_chance: str = ""
    fires: tuple[str, ...] = ()
    #: False when the option's own tag test says this country never sees it, None when its
    #: trigger needs the game's state to answer (or there is none: always shown).
    shown: bool | None = None

    def describe(self) -> dict:
        out = {"option": self.index, "key": self.key, "text": self.text or self.key,
               "effects": S.readable(self.effects, limit=14, width=420) or "(no effect)"}
        if self.trigger:
            out["shown_if"] = S.readable(self.trigger, limit=8, width=300)
            out["shown"] = "yes" if self.shown is True else "no" if self.shown is False else "unknown"
        if self.fires:
            out["fires_events"] = list(self.fires)
        if self.ai_chance:
            out["ai_chance"] = self.ai_chance
        return out


@dataclass(frozen=True)
class EventInfo:
    id: str
    kind: str
    title_keys: tuple[str, ...]
    desc_keys: tuple[str, ...]
    options: tuple[EventOption, ...]
    source: str = ""
    triggered_only: bool = False
    major: bool = False
    hidden: bool = False
    fire_once: bool = False
    title_raw: tuple[str, ...] = ()
    desc_raw: tuple[str, ...] = ()
    #: Per description variant: the condition that picks it, readable ("" when none).
    desc_when: tuple[str, ...] = ()

    @property
    def title(self) -> str:
        if self.title_raw:
            return display(self.title_raw[0])
        return self.title_keys[0] if self.title_keys else ""

    def describe(self, *, tag: str | None = None) -> dict:
        out = {"id": self.id, "kind": self.kind, "title": self.title,
               "description": [display(d) for d in self.desc_raw] or list(self.desc_keys)}
        if len(self.desc_raw) > 1:
            out["description_when"] = [w or "otherwise" for w in self.desc_when]
        out["options"] = [o.describe() for o in self.options]
        return out


@dataclass(frozen=True)
class Identification:
    """What a window's text most probably is."""
    event: EventInfo | None
    confidence: float
    margin: float
    runner_up: str = ""
    candidates: tuple[tuple[str, float], ...] = ()
    #: Per option index: how well its text was found on the screen.
    option_scores: dict = field(default_factory=dict, compare=False)
    title_score: float | None = None

    @property
    def identified(self) -> bool:
        if self.event is None:
            return False
        return self.confidence >= SURE or (self.confidence >= IDENTIFIED
                                           and self.margin >= CLEAR_MARGIN)

    def to_dict(self) -> dict:
        return {"event": self.event.id if self.event else None,
                "confidence": round(self.confidence, 3), "margin": round(self.margin, 3),
                "identified": self.identified, "runner_up": self.runner_up,
                "candidates": [{"id": i, "confidence": round(c, 3)} for i, c in self.candidates]}


# --- the catalogue ---------------------------------------------------------------------------


def _fires(nodes) -> tuple[str, ...]:
    out: list[str] = []

    def walk(items):
        for key, _op, value in items:
            if key in EVENT_KINDS and isinstance(value, list):
                ident = S.text(value, "id")
                if ident:
                    out.append(ident)
            if isinstance(value, list):
                walk(value)

    walk(nodes)
    return tuple(dict.fromkeys(out))


def _text_keys(nodes, key: str) -> list[tuple[str, list]]:
    """The ``title`` / ``desc`` entries of an event as (loc key, condition) pairs: a bare key, or a
    block ``{ text = KEY trigger = { ... } }`` (``desc`` blocks may also nest ``text`` lists)."""
    out: list[tuple[str, list]] = []
    for k, _op, value in nodes:
        if k != key:
            continue
        if isinstance(value, str):
            out.append((value, []))
        elif isinstance(value, list):
            cond = S.block(value, "trigger") or []
            for entry in S.entries(value, "text"):
                if isinstance(entry[2], str):
                    out.append((entry[2], cond))
    return out


class EventCatalog:
    """The events of one playset, read once, with the text found in the English files."""

    def __init__(self, ctx: ModContext):
        self.ctx = ctx
        self._events: dict[str, EventInfo] | None = None
        self._index: dict[str, set[str]] | None = None
        self._vocab: dict[int, list[str]] = {}

    # --- reading ------------------------------------------------------------------------

    def events(self) -> dict[str, EventInfo]:
        if self._events is None:
            self._events = self._build()
        return self._events

    def _build(self) -> dict[str, EventInfo]:
        parsed: dict[str, tuple] = {}
        keys: set[str] = set()
        for path in self.ctx.playset.layered("events"):
            source = self.ctx._source(path)
            for key, _op, body in self.ctx._read(path):
                if key not in EVENT_KINDS or not isinstance(body, list):
                    continue
                ident = S.text(body, "id")
                if not ident:
                    continue
                titles, descs = _text_keys(body, "title"), _text_keys(body, "desc")
                options = S.blocks(body, "option")
                parsed[ident] = (key, body, titles, descs, options, source)
                keys.update(k for k, _c in titles + descs)
                keys.update(S.text(o, "name") or "" for o in options)
        keys.discard("")
        raw = english(self.ctx.playset, keys, clean=lambda value: value)
        events: dict[str, EventInfo] = {}
        for ident, (kind, body, titles, descs, options, source) in parsed.items():
            opts = []
            for number, block in enumerate(options, start=1):
                name = S.text(block, "name") or ""
                trigger = S.block(block, "trigger") or []
                effects = [n for n in block if n[0] not in _OPTION_META]
                chance = S.block(block, "ai_chance")
                tag = self.ctx_tag
                shown = None if not trigger else (S.tag_truth(trigger, tag) if tag else None)
                opts.append(EventOption(
                    index=number, key=name, text_raw=raw.get(name, ""),
                    text=display(raw.get(name, "")), trigger=trigger, effects=effects,
                    ai_chance=S.readable(chance, limit=3, width=120) if chance else "",
                    fires=_fires(effects), shown=shown))
            events[ident] = EventInfo(
                id=ident, kind=kind,
                title_keys=tuple(k for k, _c in titles), desc_keys=tuple(k for k, _c in descs),
                options=tuple(opts), source=source,
                triggered_only=S.text(body, "is_triggered_only") == "yes",
                major=S.text(body, "major") == "yes", hidden=S.text(body, "hidden") == "yes",
                fire_once=S.text(body, "fire_only_once") == "yes",
                title_raw=tuple(raw[k] for k, _c in titles if k in raw),
                desc_raw=tuple(raw[k] for k, _c in descs if k in raw),
                desc_when=tuple(S.readable(c, limit=4, width=160) if c else ""
                                for k, c in descs if k in raw))
        return events

    #: The country whose option triggers are tested for a tag mismatch; set by ``for_country``.
    ctx_tag: str | None = None

    def for_country(self, tag: str | None) -> EventCatalog:
        """Use ``tag`` to decide options whose trigger is a tag test (rebuilds the records)."""
        tag = tag.upper() if tag else None
        if tag != self.ctx_tag:
            self.ctx_tag = tag
            self._events = None
        return self

    def get(self, ident: str) -> EventInfo | None:
        return self.events().get(ident)

    # --- matching -----------------------------------------------------------------------

    def _words(self, info: EventInfo) -> set[str]:
        words: set[str] = set()
        for text in (*info.title_raw, *(o.text_raw for o in info.options)):
            for run in fragments(text):
                words.update(w for w in run.split() if len(w) >= MIN_FRAGMENT)
        return words

    def _inverted(self) -> dict[str, set[str]]:
        if self._index is None:
            index: dict[str, set[str]] = {}
            for ident, info in self.events().items():
                if info.hidden:
                    continue
                for word in self._words(info):
                    index.setdefault(word, set()).add(ident)
            self._index = index
            for word in index:
                self._vocab.setdefault(len(word), []).append(word)
        return self._index

    def _candidates(self, words: set[str], title_words: set[str], limit: int) -> list[str]:
        index = self._inverted()
        hits: dict[str, float] = {}

        def add(word: str, weight: float) -> None:
            ids = index.get(word, ())
            idf = 1.0 / math.log(2 + len(ids)) if ids else 0.0
            for ident in ids:
                hits[ident] = hits.get(ident, 0.0) + weight * idf

        for word in words:
            if word in index:
                add(word, 1.0)
        # Typos: the title's words that are not in the vocabulary get their nearest ones.
        for word in sorted(title_words - set(index), key=len, reverse=True)[:12]:
            pool = [w for n in (len(word) - 1, len(word), len(word) + 1) for w in self._vocab.get(n, ())]
            for near in difflib.get_close_matches(word, pool, n=2, cutoff=0.82):
                add(near, 0.8)
        ranked = sorted(hits, key=lambda i: (-hits[i], i))
        return ranked[:limit]

    def identify(self, text: str, *, title: str = "", lines: list[str] | None = None,
                 limit: int = 40) -> Identification:
        """The playset event the screen text most probably is.

        ``text`` is everything read in the window; ``title`` the part of it in the title band
        (empty when the band could not be told, then the whole text is used); ``lines`` the
        separate lines, so an option is scored against a line and not against the whole text.
        """
        body = _fold(text)
        head = _fold(title) or body
        folded_lines = [_fold(x) for x in (lines or []) if _fold(x)]
        # An option's name may wrap onto two lines: score pairs of neighbours too.
        pools = folded_lines + [f"{a} {b}" for a, b in zip(folded_lines, folded_lines[1:], strict=False)]
        words = {w for w in body.split() if len(w) >= MIN_FRAGMENT}
        title_words = {w for w in head.split() if len(w) >= MIN_FRAGMENT}
        scored: list[tuple[float, EventInfo, dict, float | None]] = []
        for ident in self._candidates(words, title_words, limit):
            info = self.events()[ident]
            t = fit(list(info.title_raw), head)
            if t is None:
                t = fit(list(info.title_raw), body)
            opt_scores: dict[int, float] = {}
            for option in info.options:
                s = self._option_fit(option, pools, body)
                if s is not None:
                    opt_scores[option.index] = s
            d = None
            if info.desc_raw:
                d = max((x for x in (fit([raw], body) for raw in info.desc_raw) if x is not None),
                        default=None)
            parts, weights = [], []
            if t is not None:
                parts.append(t), weights.append(0.5)
            if opt_scores:
                ranked = sorted(opt_scores.values(), reverse=True)
                seen = max(1, sum(1 for v in ranked if v >= 0.7))
                parts.append(sum(ranked[:seen]) / seen), weights.append(0.3)
            if d is not None:
                parts.append(d), weights.append(0.2)
            if not parts:
                continue
            conf = sum(p * w for p, w in zip(parts, weights, strict=True)) / sum(weights)
            # A title that does not fit is not rescued by options saying "OK".
            if t is not None and t < 0.55:
                conf *= 0.6
            scored.append((conf, info, opt_scores, t))
        scored.sort(key=lambda row: (-row[0], row[1].id))
        if not scored:
            return Identification(None, 0.0, 0.0)
        best = scored[0]
        second = scored[1] if len(scored) > 1 else None
        margin = best[0] - (second[0] if second else 0.0)
        return Identification(
            event=best[1], confidence=best[0], margin=margin,
            runner_up=second[1].id if second else "",
            candidates=tuple((row[1].id, row[0]) for row in scored[:3]),
            option_scores=best[2], title_score=best[3])

    @staticmethod
    def _option_fit(option: EventOption, pools: list[str], body: str) -> float | None:
        runs = fragments(option.text_raw) if option.text_raw else []
        if not runs:
            return None
        whole = _fold(option.text)
        if pools and whole:
            direct = max(partial(whole, p) for p in pools)
            if direct >= 0.85:
                return direct
        return fit([option.text_raw], " ".join(pools) or body)
