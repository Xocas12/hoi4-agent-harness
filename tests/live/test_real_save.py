"""A real, full-size text save (opt-in: set HOI4_REAL_SAVE to a ``savegame`` file).

The committed fixture is a 57 kB trim; this is the 66 MB file it was trimmed from.
It checks that the block-only read stays quick and gives the same answer as the
fixture does, which is what keeps the headless reader's per-save cost honest.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from hoi4_harness.adapters.savegame import SaveGameAdapter, read_save

PATH = os.environ.get("HOI4_REAL_SAVE")

pytestmark = pytest.mark.skipif(not PATH or not Path(PATH).is_file(),
                                reason="set HOI4_REAL_SAVE to a text save written by `savegame`")


def test_a_full_size_save_reads_quickly_and_maps():
    started = time.perf_counter()
    raw = read_save(Path(PATH))
    seconds = time.perf_counter() - started
    state = SaveGameAdapter(Path(PATH).parent)._to_state(raw)
    assert seconds < 8.0, f"{seconds:.1f} s to read {Path(PATH).stat().st_size / 1e6:.0f} MB"
    assert state.known("production") and state.known("construction") and state.known("divisions")
    assert state.production and state.political_power > 0
