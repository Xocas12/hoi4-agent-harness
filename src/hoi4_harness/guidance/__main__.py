"""``python -m hoi4_harness.guidance --write-skill``: regenerate the Claude Code skill."""

import sys
from pathlib import Path

from . import write_skill

if __name__ == "__main__":
    if "--write-skill" not in sys.argv[1:]:
        print("usage: python -m hoi4_harness.guidance --write-skill  (run from the repo root)")
        sys.exit(2)
    print(f"wrote {write_skill(Path.cwd())}")
