"""Turn the catalog into tool specs, and validate incoming calls against it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..types import ActionCall, ActionResult
from . import catalog
from .validate import validate


@dataclass(frozen=True)
class ToolSpec:
    """Provider-neutral tool definition.

    Each LLM provider translates this into its own wire format; nothing above
    this layer knows whether it is talking to Anthropic, OpenAI or Gemini.
    """

    name: str
    description: str
    parameters: dict[str, Any]


def tool_specs(allowed: set[str] | None = None) -> list[ToolSpec]:
    """Tool specs for the actions this adapter can actually perform.

    Filtering matters for cost as well as correctness: the tool block is part of
    the cached prefix on every request, and offering a tool the adapter will only
    reject teaches the model nothing except to distrust the catalog.
    """
    return [
        ToolSpec(spec.name, spec.description, spec.parameters)
        for spec in catalog.ACTIONS
        if allowed is None or spec.name in allowed
    ]


def check(call: ActionCall, allowed: set[str] | None = None) -> ActionResult | None:
    """Validate one call. Returns ``None`` when the call is fine."""
    spec = catalog.get(call.name)
    if spec is None:
        close = [n for n in catalog.names() if n.startswith(call.name[:4])]
        hint = f" Did you mean: {', '.join(close)}?" if close else ""
        return ActionResult(
            ok=False,
            action=call.name,
            call_id=call.call_id,
            message=f"No such action '{call.name}'.{hint}",
            error_kind="invalid_action",
        )
    if allowed is not None and call.name not in allowed:
        return ActionResult(
            ok=False,
            action=call.name,
            call_id=call.call_id,
            message=f"'{call.name}' is not available with the current adapter.",
            error_kind="unsupported",
        )
    errors = validate(call.arguments, spec.parameters)
    if errors:
        return ActionResult(
            ok=False,
            action=call.name,
            call_id=call.call_id,
            message="; ".join(errors),
            error_kind="invalid_args",
        )
    return None


def needs_confirmation(call: ActionCall) -> bool:
    spec = catalog.get(call.name)
    return bool(spec and spec.requires_confirmation)
