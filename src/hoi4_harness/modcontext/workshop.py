"""A short world brief per mod, from its Steam Workshop page.

The page is fetched with the standard library, once per mod id, and kept on disk:
a later run reads the cache and sends nothing. What goes in the request is the
public page URL -- the numeric file id from the mod's descriptor -- and a generic
User-Agent. Nothing about the user, the machine, the playset or the run: the same
request anyone's browser makes.

The brief is extractive, not generated: the page's own tagline and opening, the
sentence that names the setting or start year, and the feature-list lines that
mention something a player would act on, cut to a word budget. No model is called.
A mod with no page (no ``remote_file_id``, no network and no cache, a page that is
gone) falls back to its ``descriptor.mod`` and a readme in its folder.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from ..identifiers import Mod

PAGE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={id}"
USER_AGENT = "Mozilla/5.0 (compatible; hoi4-harness-brief/1.0)"
DEFAULT_TTL = 7 * 24 * 3600
TIMEOUT = 15

#: Lines that are about the page, not the world.
_NOISE = re.compile(
    r"discord|patreon|ko-?fi|paypal|subscribe|donat|credits?\b|thanks|special thanks|"
    r"bug|github|youtube|twitter|reddit|steam group|changelog|change log|wiki|tv ?tropes|"
    r"https?://|www\.|\.com|faq|submod|load order|compatib|please (?:read|note)|"
    r"report |join us|follow us|soundtrack|playlist", re.I)
_FEATURE = re.compile(
    r"\b(new|tech|focus|decision|ideolog|law|laws|map|event|events|nation|country|countries|"
    r"economy|mechanic|politic|play|playable|interface|gui|unit|division|navy|air|war)\b", re.I)
_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def cache_dir_default() -> Path:
    env = os.environ.get("HOI4_CACHE_DIR")
    base = Path(env) if env else Path.home() / ".cache" / "hoi4-harness"
    return base / "workshop"


# --- the page -----------------------------------------------------------------


@dataclass
class WorkshopPage:
    title: str = ""
    description: str = ""


class _Page(HTMLParser):
    """Title and description text out of a Workshop page, structure flattened."""

    BLOCK = {"br", "div", "p", "ul", "ol", "li", "h1", "h2", "h3", "h4", "tr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.fallback_title = ""
        self.parts: list[str] = []
        self._depth = 0           # div depth inside the description, 0 outside
        self._in_title = False
        self._in_doc_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "title":
            self._in_doc_title = True
        if "workshopItemTitle" in classes:
            self._in_title = True
        if tag == "div" and (a.get("id") == "highlightContent" and self._depth == 0
                             or self._depth > 0):
            self._depth += 1
        if self._depth:
            if tag in ("script", "style"):
                self._skip += 1
            if tag == "li":
                self.parts.append("\n- ")
            elif tag in self.BLOCK:
                self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_doc_title = False
        if self._in_title and tag == "div":
            self._in_title = False
        if self._depth:
            if tag in ("script", "style") and self._skip:
                self._skip -= 1
            if tag in self.BLOCK and tag != "li":
                self.parts.append("\n")
            if tag == "div":
                self._depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif self._in_doc_title:
            self.fallback_title += data
        elif self._depth and not self._skip:
            self.parts.append(data)


def parse_page(page_html: str) -> WorkshopPage:
    parser = _Page()
    parser.feed(page_html)
    parser.close()
    title = parser.title.strip() or re.sub(
        r"^Steam Workshop::", "", parser.fallback_title.strip())
    return WorkshopPage(title=_squash(title), description=strip_markup("".join(parser.parts)))


_BBCODE = re.compile(r"\[/?(?:b|i|u|h[1-3]|url(?:=[^\]]*)?|img|list|olist|quote|spoiler|strike|"
                     r"code|noparse|hr|table|tr|td|th)\]", re.I)


def strip_markup(text: str) -> str:
    """HTML entities, leftover BBCode and runs of blank lines out of text."""
    text = html.unescape(text)
    text = re.sub(r"\[\*\]", "\n- ", text)
    text = _BBCODE.sub("", text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", text, flags=re.M)
    text = text.replace("**", "")
    lines = [_squash(line) for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _squash(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


# --- fetching and the cache -----------------------------------------------------


def valid_id(file_id: str) -> bool:
    return bool(re.fullmatch(r"\d{4,12}", str(file_id)))


def _default_open(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept-Language": "en"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - https only
        return response.read().decode("utf-8", errors="replace")


def load_page(file_id: str, cache_dir: Path | None = None, ttl: float = DEFAULT_TTL,
              offline: bool = False, opener: Callable[[str], str] | None = None,
              now: Callable[[], float] = time.time) -> tuple[str | None, str]:
    """``(html, how)``: the page and where it came from.

    ``how`` is ``cache`` (fresh enough), ``fetched``, ``stale`` (the network failed,
    an old copy stands in), ``offline`` (asked not to fetch, nothing cached) or
    ``error: ...``. Only the cache directory is written."""
    if not valid_id(file_id):
        return None, f"error: {file_id!r} is not a workshop file id"
    directory = cache_dir or cache_dir_default()
    page_file, meta_file = directory / f"{file_id}.html", directory / f"{file_id}.json"
    cached = None
    fetched_at = 0.0
    if page_file.is_file():
        cached = page_file.read_text(encoding="utf-8", errors="replace")
        try:
            fetched_at = float(json.loads(meta_file.read_text(encoding="utf-8")).get("fetched_at", 0))
        except (OSError, ValueError, AttributeError):
            fetched_at = page_file.stat().st_mtime
    if cached is not None and (offline or now() - fetched_at < ttl):
        return cached, "cache"
    if offline:
        return None, "offline"
    try:
        page = (opener or _default_open)(PAGE_URL.format(id=file_id))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if cached is not None:
            return cached, "stale"
        return None, f"error: {type(exc).__name__}: {exc}"
    if "highlightContent" not in page:
        # An age gate, an error page: not worth caching over a good copy.
        return (cached, "stale") if cached is not None else (None, "error: no description on the page")
    try:
        directory.mkdir(parents=True, exist_ok=True)
        page_file.write_text(page, encoding="utf-8", newline="\n")
        meta_file.write_text(json.dumps({"fetched_at": now(), "id": file_id}), encoding="utf-8")
    except OSError:
        pass          # a read-only cache costs a refetch next time, nothing else
    return page, "fetched"


# --- the brief ---------------------------------------------------------------


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    cut = " ".join(words[:limit])
    last = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: last + 1] if last > len(cut) // 2 else cut.rstrip(",;:") + "..."


def brief(title: str, description: str, max_words: int = 200) -> str:
    """The extractive brief: tagline and opening, the line that places the
    setting in time, a few feature lines."""
    lines = [line for line in description.splitlines() if line.strip()]
    prose = [line for line in lines if not line.startswith("- ") and not _NOISE.search(line)]
    features = [line[2:].strip() for line in lines if line.startswith("- ")]
    features = [f for f in features if not _NOISE.search(f) and _FEATURE.search(f)]

    picked: list[str] = []
    if prose:
        picked += _sentences(prose[0])[:2]
        # The paragraph after a one-line tagline is the opening of the premise.
        if len(prose) > 1 and len(" ".join(picked).split()) < 30:
            picked += _sentences(prose[1])[:2]
        for line in prose[1:]:
            hit = next((x for x in _sentences(line) if _YEAR.search(x) and x not in picked), None)
            if hit:
                picked.append(hit)
                break
    text = " ".join(picked)
    if features:
        shown = [_words(f.rstrip("."), 18) for f in features[:4]]
        text += " Features: " + "; ".join(shown) + "."
    head = f"{title}: " if title else ""
    return _words(head + text.strip(), max_words)


# --- local fallbacks ------------------------------------------------------------


def read_readme(mod_path: Path, limit: int = 20000) -> str:
    for pattern in ("readme*", "README*", "*.md"):
        for path in sorted(mod_path.glob(pattern)):
            if path.is_file() and path.suffix.lower() in ("", ".md", ".txt"):
                return strip_markup(path.read_text(encoding="utf-8-sig", errors="replace")[:limit])
    return ""


def descriptor_summary(mod: Mod) -> str:
    descriptor = mod.path / "descriptor.mod"
    tags: list[str] = []
    if descriptor.is_file():
        text = descriptor.read_text(encoding="utf-8-sig", errors="replace")
        match = re.search(r"tags\s*=\s*\{([^}]*)\}", text, re.S)
        if match:
            tags = re.findall(r'"([^"]+)"', match.group(1))
    out = mod.name
    if mod.version:
        out += f" {mod.version}"
    if tags:
        out += f" ({', '.join(tags)})"
    if mod.replace_paths:
        out += f"; replaces {len(mod.replace_paths)} base folders, a total-conversion pattern" \
            if len(mod.replace_paths) > 20 else f"; replaces {', '.join(mod.replace_paths)}"
    return out


@dataclass
class ModBrief:
    name: str
    file_id: str | None
    text: str
    #: ``steam`` (page), ``readme``, ``descriptor``.
    source: str
    how: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "remote_file_id": self.file_id, "source": self.source,
                "how": self.how, "brief": self.text}


def mod_brief(mod: Mod, cache_dir: Path | None = None, offline: bool = False,
              ttl: float = DEFAULT_TTL, opener=None, max_words: int = 200,
              now: Callable[[], float] = time.time) -> ModBrief:
    how = "no remote_file_id"
    if mod.remote_file_id:
        page_html, how = load_page(mod.remote_file_id, cache_dir, ttl, offline, opener, now)
        if page_html:
            page = parse_page(page_html)
            if page.description:
                return ModBrief(mod.name, mod.remote_file_id,
                                brief(page.title or mod.name, page.description, max_words),
                                "steam", how)
    readme = read_readme(mod.path)
    if readme:
        return ModBrief(mod.name, mod.remote_file_id, brief(mod.name, readme, max_words),
                        "readme", how)
    return ModBrief(mod.name, mod.remote_file_id, descriptor_summary(mod), "descriptor", how)


def playset_briefs(mods, cache_dir: Path | None = None, offline: bool = False,
                   ttl: float = DEFAULT_TTL, opener=None, per_mod_words: int = 200,
                   total_words: int = 500, now: Callable[[], float] = time.time) -> list[ModBrief]:
    """One brief per content mod, in load order, within a total word cap (the
    last ones shrink first). The bridge mod is plumbing and has none."""
    from ..identifiers import _is_bridge

    out = []
    remaining = total_words
    for mod in mods:
        if _is_bridge(mod):
            continue
        limit = max(30, min(per_mod_words, remaining))
        result = mod_brief(mod, cache_dir, offline, ttl, opener, limit, now)
        out.append(result)
        remaining -= len(result.text.split())
    return out
