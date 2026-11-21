"""Where the harness keeps its own small files: ``~/.hoi4-harness`` unless ``HOI4_HARNESS_HOME``
says otherwise (tests point it at a temporary folder). Holds ``chat.jsonl``, ``players.json``
(players.py) and ``setup.json`` (setup_model.py)."""

from __future__ import annotations

import os
from pathlib import Path


def home_dir() -> Path:
    env = os.environ.get("HOI4_HARNESS_HOME")
    return Path(env) if env else Path.home() / ".hoi4-harness"
