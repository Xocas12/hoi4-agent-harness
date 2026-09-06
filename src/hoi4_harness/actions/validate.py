"""A small JSON-Schema subset validator.

Deliberately dependency-free and deliberately incomplete: it covers exactly the
constructs the action catalog uses (type, required, enum, min/max, maxLength,
array items, nested objects, additionalProperties). A rejected call costs one
cheap local error string instead of a wasted round trip, so the error messages
matter more than the coverage does -- each one names the field and says what
would have been accepted.
"""

from __future__ import annotations

from typing import Any

TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate(value: Any, schema: dict[str, Any], path: str = "") -> list[str]:
    """Return a list of human-readable problems. Empty means valid."""
    errors: list[str] = []
    where = path or "argument"

    expected = schema.get("type")
    if expected:
        py = TYPES.get(expected)
        # bool is a subclass of int in Python; the game never wants one for the other.
        if expected in {"integer", "number"} and isinstance(value, bool):
            return [f"{where}: expected {expected}, got boolean"]
        if py and not isinstance(value, py):
            return [f"{where}: expected {expected}, got {type(value).__name__}"]

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(str(v) for v in schema["enum"])
        return [f"{where}: {value!r} is not one of [{allowed}]"]

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: {value} is above maximum {schema['maximum']}")

    if isinstance(value, str) and "maxLength" in schema and len(value) > schema["maxLength"]:
        errors.append(f"{where}: longer than {schema['maxLength']} characters")

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], f"{where}[{i}]")

    if isinstance(value, dict) and schema.get("type") == "object":
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{where}: missing required field '{name}'")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    known = ", ".join(props) or "(none)"
                    errors.append(f"{where}: unknown field '{name}'; known fields: {known}")
        for name, sub in props.items():
            if name in value:
                errors += validate(value[name], sub, f"{where}.{name}" if path else name)

    return errors
