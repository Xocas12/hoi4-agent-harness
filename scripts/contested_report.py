"""What the game's AI did to the production lines the model added (issue #161, step 1).

    python scripts/contested_report.py RUN_DIR --saves "<save folder>" [--json]

RUN_DIR holds ``contested_lines.jsonl`` (written by ``add_production_line``); the saves are the
campaign's text saves (set ``campaign_save_months`` low enough to have several in the window).
The logic is in ``hoi4_harness.contested``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hoi4_harness.contested import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
