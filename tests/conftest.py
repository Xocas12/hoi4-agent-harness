"""Test-wide safety: no test may send real keyboard or mouse input.

The suite runs on machines where the game, an editor and a browser are open.
A test that reaches the real input layer types into whichever window is in
front -- that happened twice before this existed. Real input fails the test
instead, and pyautogui behaves as if it were not installed.
"""

import sys
import tempfile
from pathlib import Path

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
def _own_harness_home(monkeypatch, tmp_path_factory):
    """Nothing in a test may touch the real ~/.hoi4-harness (setup.json, players.json, chat)."""
    home = tmp_path_factory.mktemp("harness-home")
    monkeypatch.setenv("HOI4_HARNESS_HOME", str(home))
    monkeypatch.delenv("HOI4_CHAT", raising=False)


@pytest.fixture(autouse=True)
def _no_machine_dlc_state(monkeypatch):
    """The active DLC list is this machine's (the game's logs/system.log, written by every launch)
    and the console channel keeps the last playset's list in a module global. A test sees
    neither unless it builds them: a live launch on 2026-11-22 turned a fixture test's 7 takeable
    decisions into 6, and a channel built by one test folded another's quoted DLC names."""
    from hoi4_harness import dlc
    from hoi4_harness.adapters import console

    monkeypatch.setattr(console, "ACTIVE_DLCS", None)
    real = dlc.read_game_log
    tests_dir = Path(__file__).resolve().parent
    temp_dir = Path(tempfile.gettempdir()).resolve()

    def only_test_dirs(user_dir):
        if user_dir is None:
            return None
        where = Path(user_dir).resolve()
        if tests_dir in where.parents or temp_dir in where.parents or where == tests_dir:
            return real(user_dir)
        return None

    monkeypatch.setattr(dlc, "read_game_log", only_test_dirs)
