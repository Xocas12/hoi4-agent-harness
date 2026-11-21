"""Which window is actually focused.

Synthetic input goes wherever the focus is. If the game loses focus -- you
alt-tab, a launcher steals it, an update popup appears -- the harness types army
hotkeys into a browser or an editor. ``pyautogui.FAILSAFE`` catches a runaway
mouse; nothing catches misdirected keystrokes.

So input is gated on the focused window's title. The rule this module exists to
enforce: **an unimplemented platform reports "unsupported", never "fine"**. A
guard that always passes is worse than no guard, because it is trusted.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


class WindowCheckUnsupported(RuntimeError):
    """No way to read the focused window on this platform."""


@dataclass
class FocusCheck:
    focused: bool
    title: str | None
    supported: bool = True
    reason: str = ""


def active_window_title() -> str | None:
    """Title of the focused window, or None if there isn't one.

    Raises :class:`WindowCheckUnsupported` where it cannot be determined.
    """
    if sys.platform == "win32":
        return _windows_title()
    if sys.platform == "darwin":
        return _macos_title()
    if sys.platform.startswith("linux"):
        return _linux_title()
    raise WindowCheckUnsupported(f"no focused-window check for {sys.platform}")


def _windows_title() -> str | None:
    import ctypes

    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return None
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value or None


def _macos_title() -> str | None:
    script = (
        'tell application "System Events" to get name of first application process '
        "whose frontmost is true"
    )
    try:
        out = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WindowCheckUnsupported(f"osascript unavailable: {exc}") from exc
    if out.returncode != 0:
        raise WindowCheckUnsupported(f"osascript failed: {out.stderr.strip()}")
    return out.stdout.strip() or None


def _linux_title() -> str | None:
    for command in (["xdotool", "getactivewindow", "getwindowname"], ["xprop", "-root"]):
        try:
            out = subprocess.run(command, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and command[0] == "xdotool":
            return out.stdout.strip() or None
    raise WindowCheckUnsupported(
        "install xdotool for the focused-window check, or pass --no-window-guard "
        "if you accept that input may go to the wrong window"
    )


def check_focus(expected_title: str) -> FocusCheck:
    """Is a window whose title contains ``expected_title`` focused?

    Matching is a case-insensitive substring, because the real title carries the
    version ("Hearts of Iron IV v1.16.4") and a launcher may add its own suffix.
    """
    if not expected_title.strip():
        # An empty title is a substring of every title: the guard would pass
        # whatever window has focus.
        return FocusCheck(False, None, reason="no window title is configured to look for")
    try:
        title = active_window_title()
    except WindowCheckUnsupported as exc:
        return FocusCheck(focused=False, title=None, supported=False, reason=str(exc))

    if title is None:
        return FocusCheck(False, None, reason="no window is focused")
    if expected_title.strip().lower() in title.lower():
        return FocusCheck(True, title)
    return FocusCheck(False, title, reason=f"focused window is {title!r}")


def take_focus(title: str) -> bool:
    """Ask Windows to bring the visible top-level window whose title contains ``title`` to
    the front. Used only when ``campaign_take_focus`` is on; True if one was found and the
    request was made. Sends no key. Windows only: False elsewhere."""
    if sys.platform != "win32" or not title.strip():
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            if title.strip().lower() in buffer.value.lower():
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(callback_type(visit), 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)           # SW_RESTORE
    # Windows only lets the foreground thread hand the focus over; attaching to its input
    # queue for the call is the sanctioned way, and sends no key.
    foreground = user32.GetForegroundWindow()
    this_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
    attached = bool(fg_thread and fg_thread != this_thread
                    and user32.AttachThreadInput(this_thread, fg_thread, True))
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(this_thread, fg_thread, False)
    return True


def client_rect() -> tuple[int, int, int, int] | None:
    """The focused window's client area as ``(x, y, width, height)`` in screen
    pixels, or None where it cannot be read (not Windows, no window).

    The size the game draws its interface into: a borderless window is the whole
    display, a windowed one excludes the title bar and frame. Windows only so far;
    UNVERIFIED live (docs/live-session.md)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()      # pixels, not the scaled values a 125% desktop reports
    except (AttributeError, OSError):
        pass
    handle = user32.GetForegroundWindow()
    if not handle:
        return None
    rect, origin = wintypes.RECT(), wintypes.POINT(0, 0)
    if not user32.GetClientRect(handle, ctypes.byref(rect)) or \
            not user32.ClientToScreen(handle, ctypes.byref(origin)):
        return None
    return (origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)


# --- the keyboard layout the game window types with --------------------------------------------
#
# The scan codes the harness sends (keys.ScanCodeKeys) are the US layout's: `grave` opens the
# console, `minus` with shift is `_`. Under another layout the same physical keys mean other
# characters. Seen live 2026-11-21 with a Spanish/Galician layout on the game window: the console
# key typed `º` and the console received `ºrun llmb?turn.txt` ("Unknown command"), so nothing the
# console channel sent ever ran. Windows keeps the input layout per window, so the game can be on
# a different layout than the terminal that started the harness.

#: The US English keyboard layout handle (language 0x0409, layout 0x0409).
US_LAYOUT = 0x04090409
WM_INPUTLANGCHANGEREQUEST = 0x0050


class LayoutUnavailable(RuntimeError):
    """The game window types with another layout and the US one cannot be selected."""


def foreground_layout() -> int | None:
    """The keyboard layout handle of the focused window's thread (Windows), else None."""
    if sys.platform != "win32":
        return None
    import ctypes

    user32 = ctypes.windll.user32
    handle = user32.GetForegroundWindow()
    if not handle:
        return None
    thread = user32.GetWindowThreadProcessId(handle, None)
    return user32.GetKeyboardLayout(thread) & 0xFFFFFFFF


def installed_layouts() -> list[int]:
    if sys.platform != "win32":
        return []
    import ctypes

    user32 = ctypes.windll.user32
    count = user32.GetKeyboardLayoutList(0, None)
    handles = (ctypes.c_void_p * count)()
    user32.GetKeyboardLayoutList(count, handles)
    return [(h or 0) & 0xFFFFFFFF for h in handles]


def request_layout(layout: int) -> None:
    """Ask the focused window to switch its input layout (it applies it asynchronously)."""
    import ctypes

    user32 = ctypes.windll.user32
    user32.PostMessageW(user32.GetForegroundWindow(), WM_INPUTLANGCHANGEREQUEST, 0, layout)


def ensure_us_layout(*, read=foreground_layout, installed=installed_layouts,
                     request=request_layout, sleep=None, tries: int = 10) -> str | None:
    """Make the focused window type with the US layout. Returns a line for the log when it
    switched, None when it already was (or the platform has no per-window layout). Raises
    LayoutUnavailable when the US layout is not installed or the switch does not take."""
    current = read()
    if current is None or current & 0xFFFF == US_LAYOUT & 0xFFFF:
        return None
    if not any(h & 0xFFFF == US_LAYOUT & 0xFFFF for h in installed()):
        raise LayoutUnavailable(
            f"the game window types with keyboard layout {current:#010x}, the harness sends US "
            "scan codes (the console key would type another character), and no US English layout "
            "is installed: add one in Windows settings (Time & language, Language, Keyboard)")
    import time

    pause = sleep or time.sleep
    request(US_LAYOUT)
    for _ in range(tries):
        pause(0.05)
        now = read()
        if now is not None and now & 0xFFFF == US_LAYOUT & 0xFFFF:
            return (f"keyboard layout of the game window switched from {current:#010x} to US "
                    "(the harness types US scan codes)")
    raise LayoutUnavailable(f"asked the game window to switch from layout {current:#010x} to US "
                            "and it did not: switch it by hand (Win+Space) and start again")
