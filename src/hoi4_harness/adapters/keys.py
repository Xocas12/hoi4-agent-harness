"""Keyboard input as hardware scan codes.

HOI4 1.19 ignores the synthetic key events pyautogui sends (it posts virtual-key
codes); a SendInput keystroke carrying a hardware scan code with
``KEYEVENTF_SCANCODE`` and ``wVk=0`` is what the game's own input layer sees as a
real key. Mouse clicks through pyautogui are unaffected and stay where they are.

Everything here goes through one injectable function, ``send(scan_code, up)``, so
the key tables, chord ordering and text typing are unit-testable without a
keyboard. The default sender is Windows-only and says so elsewhere.

Typing text: lowercase letters, digits, space, ``-``, ``'``, ``,`` and ``.`` map to
a scan code; uppercase letters and ``_`` go with shift. Anything else (``ö``, say)
has no scan code on a US layout and cannot be typed: ``type_text`` refuses the
whole string before sending a single key, so a half-typed search never happens.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable

KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

_ROWS = {
    "q": 0x10, "w": 0x11, "e": 0x12, "r": 0x13, "t": 0x14, "y": 0x15, "u": 0x16, "i": 0x17,
    "o": 0x18, "p": 0x19, "a": 0x1E, "s": 0x1F, "d": 0x20, "f": 0x21, "g": 0x22, "h": 0x23,
    "j": 0x24, "k": 0x25, "l": 0x26, "z": 0x2C, "x": 0x2D, "c": 0x2E, "v": 0x2F, "b": 0x30,
    "n": 0x31, "m": 0x32,
}
SCAN_CODES: dict[str, int] = {
    **_ROWS,
    "1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06, "6": 0x07, "7": 0x08, "8": 0x09,
    "9": 0x0A, "0": 0x0B, "-": 0x0C, "backspace": 0x0E, "tab": 0x0F, "enter": 0x1C,
    "ctrl": 0x1D, "shift": 0x2A, "alt": 0x38, "space": 0x39, "escape": 0x01,
    "'": 0x28, ",": 0x33, ".": 0x34,
    "kp_plus": 0x4E, "kp_minus": 0x4A,
    # The key left of 1: the console key on every layout, wherever its legend
    # sits (` on US, ^ on German, ; on Swedish). A scan code is a position.
    "grave": 0x29,
}
ALIASES = {
    "return": "enter", "esc": "escape", "control": "ctrl", "add": "kp_plus",
    "subtract": "kp_minus", "plus": "kp_plus", "minus": "kp_minus", " ": "space",
}
#: What shift turns a typed character into: the key that carries it.
SHIFTED = {"_": "-"}

SendFn = Callable[[int, bool], None]


class UnsupportedPlatform(RuntimeError):
    """Scan-code input needs Windows' SendInput."""


def scan_code(key: str) -> int:
    name = " " if key == " " else key.strip().lower()
    name = ALIASES.get(name, name)
    if name.startswith("0x"):
        # A raw scan code, for a key this table does not name (a rebound console key).
        try:
            code = int(name, 16)
        except ValueError:
            raise ValueError(f"no scan code for key {key!r}") from None
        if not 0 < code < 0x80:
            raise ValueError(f"scan code {key!r} is outside 0x01-0x7F")
        return code
    if name not in SCAN_CODES:
        raise ValueError(f"no scan code for key {key!r}")
    return SCAN_CODES[name]


def parse_chord(spec: str) -> list[int]:
    """``'ctrl+shift+tab'`` -> scan codes, modifiers first, in the order written."""
    if spec == "+":
        return [scan_code("kp_plus")]
    return [scan_code(part) for part in spec.split("+")]


def text_chords(text: str) -> list[list[int]]:
    """One chord per character, or ValueError naming the first untypable one."""
    chords = []
    for char in text:
        if char in SHIFTED:
            chords.append([SCAN_CODES["shift"], SCAN_CODES[SHIFTED[char]]])
        elif char.isascii() and char.isupper():
            chords.append([SCAN_CODES["shift"], SCAN_CODES[char.lower()]])
        elif char in SCAN_CODES or char == " ":
            chords.append([scan_code(char)])
        else:
            raise ValueError(
                f"cannot type {char!r} (U+{ord(char):04X}): it has no scan code; "
                "choose an ASCII-only fragment"
            )
    return chords


def _sendinput(scan: int, up: bool) -> None:  # pragma: no cover - needs Windows
    if sys.platform != "win32":
        raise UnsupportedPlatform(
            "scan-code keyboard input uses Windows SendInput; this platform is not supported"
        )
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ulong_ptr)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ulong_ptr)]

    class _Union(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _Union)]

    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    event = INPUT(type=1, u=_Union(ki=KEYBDINPUT(0, scan, flags, 0, 0)))
    if ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        raise OSError("SendInput did not accept the key event")


class ScanCodeKeys:
    """Presses chords and types text as hardware scan codes."""

    def __init__(self, send: SendFn | None = None, sleep: Callable[[float], None] = time.sleep,
                 key_gap: float = 0.03, hold: float = 0.06, char_gap: float = 0.02):
        self._send = send or _sendinput
        self._sleep = sleep
        self.key_gap, self.hold, self.char_gap = key_gap, hold, char_gap

    def chord(self, spec: str) -> None:
        self._chord(parse_chord(spec))

    def _chord(self, codes: list[int]) -> None:
        for code in codes:
            self._send(code, False)
            self._sleep(self.key_gap)
        self._sleep(self.hold)
        for code in reversed(codes):
            self._send(code, True)
            self._sleep(self.key_gap)

    def type_text(self, text: str) -> None:
        chords = text_chords(text)       # every character checked before any is sent
        for codes in chords:
            self._chord(codes)
            self._sleep(self.char_gap)


class GuiKeys:
    """Keys through a pyautogui-shaped object. Not what reaches HOI4 (see the
    module docstring); it is what a test double and non-game windows use."""

    def __init__(self, gui):
        self._gui = gui

    def chord(self, spec: str) -> None:
        parts = spec.split("+") if spec != "+" else ["+"]
        if len(parts) > 1 and hasattr(self._gui, "hotkey"):
            self._gui.hotkey(*parts)
        else:
            self._gui.press(spec)

    def type_text(self, text: str) -> None:
        self._gui.write(text, interval=0.02)
