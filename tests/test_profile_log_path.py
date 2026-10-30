"""A profile's log_path stays a str, and the logtail adapter crashes on it."""
import json

from hoi4_harness.adapters import build_adapter
from hoi4_harness.config import HarnessConfig


def test_profile_log_path_is_usable(tmp_path):
    log = tmp_path / "game.log"
    log.write_text("[10:00:00][1936.1.2.12][x.cpp:1] LLMB|v1|state|tag=SWE|pp=1\n")
    profile = tmp_path / "p.json"
    profile.write_text(json.dumps({"adapter": "logtail", "log_path": log.as_posix()}))
    config = HarnessConfig.from_file(profile)
    adapter = build_adapter(config)
    assert adapter.read_state().country == "SWE"
