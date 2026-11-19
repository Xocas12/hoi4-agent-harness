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
