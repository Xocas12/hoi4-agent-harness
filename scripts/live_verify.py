"""Live verification: send actions through the real path (env -> console channel -> inbox) and print
what the game confirms, or print the brief the LLM would get. Needs the game running in -debug_mode
with the installed mod, and a country that reports to the log.

    python scripts/live_verify.py brief --country SPR
    python scripts/live_verify.py act --country SPR '{"name": "take_decision", "arguments": {...}}' ...
    python scripts/live_verify.py query --country SPR '{"name": "inspect_world", ...}'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from hoi4_harness.adapters import build_adapter  # noqa: E402
from hoi4_harness.config import HarnessConfig  # noqa: E402
from hoi4_harness.env import HOI4Env  # noqa: E402
from hoi4_harness.types import ActionCall  # noqa: E402

GAME = Path("C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV")


def build(country: str, run_dir: Path) -> HOI4Env:
    config = HarnessConfig()
    config.adapter = "headless"
    config.headless_reader = "logtail"
    config.console_transport = "inbox"
    config.dry_run = False
    config.country = country
    config.game_dir = GAME
    config.mod_context = True
    config.observe_world = True
    config.run_dir = run_dir
    config.inbox_ack_seconds = 90.0
    index = config.build_index()
    env = HOI4Env(build_adapter(config), config, index)
    from hoi4_harness.world import build_observer

    env.attach_world(build_observer(config, index))
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("verb", choices=["brief", "act", "query"])
    parser.add_argument("calls", nargs="*")
    parser.add_argument("--country", required=True)
    parser.add_argument("--run-dir", default=str(REPO / "runs" / "live-verify"))
    args = parser.parse_args()
    env = build(args.country.upper(), Path(args.run_dir))
    if args.verb == "brief":
        print(env.observe().brief)
        return 0
    for raw in args.calls:
        spec = json.loads(raw)
        call = ActionCall(spec["name"], spec.get("arguments", {}))
        result = env.query(call) if args.verb == "query" else env.act(call, "")
        print(json.dumps({"action": call.name, "ok": result.ok, "kind": result.error_kind,
                          "message": result.message}, ensure_ascii=False)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
