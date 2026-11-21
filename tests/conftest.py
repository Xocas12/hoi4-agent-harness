"""Test-wide safety: no test may send real keyboard or mouse input.

The suite runs on machines where the game, an editor and a browser are open.
A test that reaches the real input layer types into whichever window is in
front -- that happened twice before this existed. Real input fails the test
instead, and pyautogui behaves as if it were not installed.
"""

import sys

import pytest

from hoi4_harness.adapters import keys as _keys

_REAL_SENDINPUT = _keys._sendinput


@pytest.fixture
def real_sendinput():
    """The real sender, for the one test that checks its off-Windows refusal --
    which raises before touching SendInput."""
    return _REAL_SENDINPUT


class RealInputInTest(AssertionError):
    pass


@pytest.fixture(autouse=True)
def _no_real_input(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise RealInputInTest("a test tried to send a real keystroke; inject keys= or gui=")

    monkeypatch.setattr("hoi4_harness.adapters.keys._sendinput", refuse)
    monkeypatch.setitem(sys.modules, "pyautogui", None)   # `import pyautogui` -> ImportError


@pytest.fixture(autouse=True)
def _no_network_no_shared_cache(monkeypatch, tmp_path):
    """The Workshop brief reads a disk cache and, when allowed, the network. A
    test gets an empty cache of its own, and a fetch that was not injected fails
    the test instead of reaching steamcommunity.com."""
    monkeypatch.setenv("HOI4_CACHE_DIR", str(tmp_path / "hoi4-cache"))

    def refuse(*_args, **_kwargs):
        raise AssertionError("a test tried to fetch a Workshop page; inject opener=")

    monkeypatch.setattr("hoi4_harness.modcontext.workshop._default_open", refuse)


@pytest.fixture(autouse=True)
def _no_real_display(monkeypatch):
    """The click geometry is read from the user's settings and the live window; a
    test pins it (or injects the readers) instead of depending on this machine."""
    monkeypatch.setattr("hoi4_harness.adapters.input_driver.default_display_settings", lambda: None)
    monkeypatch.setattr("hoi4_harness.adapters.input_driver.client_rect", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_skill_home(monkeypatch, tmp_path):
    """Skill packs are read from ~/.hoi4-harness/skills; a test gets an empty home of its own."""
    monkeypatch.setenv("HOI4_HARNESS_HOME", str(tmp_path / "hoi4-harness-home"))
