"""The Workshop world brief: parsing a recorded page, the cache, and what is sent.

Nothing here reaches the network: ``opener`` is a stand-in for the one function that
would, and the page is a trimmed recording of the public page for mod 2438003901.
"""

from __future__ import annotations

import urllib.error
from pathlib import Path

from hoi4_harness.identifiers import Mod
from hoi4_harness.modcontext import workshop as W

FIX = Path(__file__).parent / "fixtures"
PAGE = (FIX / "workshop" / "tno_2438003901.html").read_text(encoding="utf-8")
ALPHA = FIX / "modctx" / "mods" / "alpha"
BETA = FIX / "modctx" / "mods" / "beta"


class Opener:
    def __init__(self, page=PAGE, error=None):
        self.page, self.error, self.urls = page, error, []

    def __call__(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.page


# --- the page --------------------------------------------------------------------


def test_title_and_description_come_out_of_the_recorded_page():
    page = W.parse_page(PAGE)
    assert page.title == "The New Order: Last Days of Europe"
    assert page.description.startswith("The year is 1962, and old powers and new struggle")
    assert "<" not in page.description and "&amp;" not in page.description
    assert "\n- Thousands of decisions" in page.description          # list items kept as lines
    assert "Discord" in page.description                              # still there; the brief skips it


def test_the_brief_is_extractive_short_and_free_of_page_furniture():
    page = W.parse_page(PAGE)
    text = W.brief(page.title, page.description)
    assert text.startswith("The New Order: Last Days of Europe: The year is 1962")
    assert "Features:" in text and "playable content" in text
    assert len(text.split()) <= 200
    for noise in ("Discord", "github", "http", "Youtube", "wiki"):
        assert noise.lower() not in text.lower()
    assert W.brief(page.title, page.description) == text                 # deterministic


def test_the_word_cap_is_kept_at_a_sentence_end_when_it_can():
    page = W.parse_page(PAGE)
    short = W.brief(page.title, page.description, max_words=40)
    assert len(short.split()) <= 41 and short.endswith((".", "..."))


def test_bbcode_and_markdown_residue_is_stripped():
    assert W.strip_markup("[b]Bold[/b] &amp; [url=http://x]link[/url]\n\n\n## Head\n\n- a") == (
        "Bold & link\n\nHead\n\n- a")


# --- the cache -------------------------------------------------------------------


def test_a_fetch_is_cached_and_the_next_read_sends_nothing(tmp_path):
    opener = Opener()
    page, how = W.load_page("2438003901", tmp_path, opener=opener, now=lambda: 1000.0)
    assert how == "fetched" and page == PAGE
    again, how2 = W.load_page("2438003901", tmp_path, opener=opener, now=lambda: 1000.0 + 3600)
    assert how2 == "cache" and again == PAGE and len(opener.urls) == 1


def test_the_request_names_the_public_page_and_nothing_else(tmp_path):
    opener = Opener()
    W.load_page("2438003901", tmp_path, opener=opener)
    assert opener.urls == ["https://steamcommunity.com/sharedfiles/filedetails/?id=2438003901"]
    assert "Mozilla" in W.USER_AGENT and "hoi4" in W.USER_AGENT.lower()
    assert all(part not in W.USER_AGENT for part in ("\\", "@", "joaqu", "Users"))


def test_an_expired_entry_is_refetched_and_a_failed_refetch_serves_the_old_page(tmp_path):
    W.load_page("2438003901", tmp_path, opener=Opener(), now=lambda: 0.0)
    late = 8 * 24 * 3600.0
    page, how = W.load_page("2438003901", tmp_path, opener=Opener(PAGE + "<!-- new -->"), now=lambda: late)
    assert how == "fetched" and page.endswith("<!-- new -->")
    broken = Opener(error=urllib.error.URLError("offline"))
    page, how = W.load_page("2438003901", tmp_path, opener=broken, now=lambda: 2 * late)
    assert how == "stale" and page.endswith("<!-- new -->")


def test_offline_reads_the_cache_even_when_it_has_expired_and_never_calls_out(tmp_path):
    W.load_page("2438003901", tmp_path, opener=Opener(), now=lambda: 0.0)
    opener = Opener()
    page, how = W.load_page("2438003901", tmp_path, offline=True, opener=opener, now=lambda: 1e9)
    assert how == "cache" and page == PAGE and opener.urls == []
    assert W.load_page("999999", tmp_path, offline=True, opener=opener) == (None, "offline")
    assert opener.urls == []


def test_an_error_page_is_not_cached_over_nothing(tmp_path):
    page, how = W.load_page("2438003901", tmp_path, opener=Opener("<html>age check</html>"))
    assert page is None and how.startswith("error") and not list(tmp_path.iterdir())


def test_only_a_numeric_file_id_is_ever_put_in_a_url(tmp_path):
    opener = Opener()
    for bad in ("../x", "123 456", "abc", "12"):
        page, how = W.load_page(bad, tmp_path, opener=opener)
        assert page is None and how.startswith("error")
    assert opener.urls == []


# --- per mod, with fallbacks --------------------------------------------------------


def test_a_mod_with_a_workshop_id_gets_the_pages_brief(tmp_path):
    result = W.mod_brief(Mod.load(ALPHA), tmp_path, opener=Opener())
    assert result.source == "steam" and result.how == "fetched" and result.file_id == "2438003901"
    assert result.text.startswith("The New Order")


def test_with_no_page_a_mod_falls_back_to_its_readme_then_its_descriptor(tmp_path):
    offline = W.mod_brief(Mod.load(ALPHA), tmp_path, offline=True)
    assert offline.source == "descriptor" and offline.text == "Alpha World 2.0 (Gameplay)"
    readme = W.mod_brief(Mod.load(BETA), tmp_path, offline=True)
    assert readme.source == "readme" and readme.text.startswith("Beta Total: Beta Total")
    assert W.mod_brief(Mod.load(BETA), tmp_path, opener=Opener()).source == "readme"   # no id: no call


def test_a_total_word_cap_shrinks_the_later_mods_and_skips_the_bridge(tmp_path):
    bridge = Mod(path=ALPHA, name="LLM Bridge")
    mods = [Mod.load(ALPHA), bridge, Mod.load(ALPHA)]
    briefs = W.playset_briefs(mods, tmp_path, opener=Opener(), per_mod_words=200, total_words=170)
    assert len(briefs) == 2
    assert len(briefs[0].text.split()) <= 200 and len(briefs[1].text.split()) <= 41
