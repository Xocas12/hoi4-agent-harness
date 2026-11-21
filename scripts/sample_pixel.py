"""Print the colour of the screen pixel under the mouse (for the event-window probe).

    PYTHONPATH=src python scripts/sample_pixel.py --wait 5

Aim the mouse at the pixel while the window is open; after the wait it prints ``x,y`` and
``r,g,b``. Reads one pixel; sends no input and never touches the game
(docs/live-session.md section 14c).
"""

from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=float, default=5.0, help="seconds to aim (default 5)")
    args = parser.parse_args()
    try:
        import mss
        import pyautogui
    except ImportError:
        print("pip install 'hoi4-agent-harness[input]' first")
        return 1
    time.sleep(args.wait)
    x, y = pyautogui.position()
    with mss.mss() as sct:
        pixel = sct.grab({"left": x, "top": y, "width": 1, "height": 1}).pixel(0, 0)
    print(f"x,y = {x},{y}   rgb = {pixel[0]},{pixel[1]},{pixel[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
