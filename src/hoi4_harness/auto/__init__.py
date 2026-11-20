"""Deterministic script controllers, one per domain of ``control.py``.

    from hoi4_harness.auto import build_layer
    layer = build_layer(config.control_map(), config, env)
    layer.run(env, state)             # every turn: the script domains act

See docs/modular-control.md. Tested on the simulators only.
"""

from .base import AutoContext, Script
from .layer import SCRIPTS, AutoLayer, build_layer

__all__ = ["SCRIPTS", "AutoContext", "AutoLayer", "Script", "build_layer"]
