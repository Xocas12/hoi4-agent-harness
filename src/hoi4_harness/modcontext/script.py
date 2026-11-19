"""An order-preserving reader for Clausewitz script, for conditions.

``adapters.savegame.parse_clausewitz`` returns a dict: fine for ids, but it merges
repeated keys and drops the comparison operator, and a decision's ``available``
block has to survive both -- it is read back as text for the player, and written
into a console effect as a guard. So this keeps every entry, in file order, as a
``(key, operator, value)`` tuple whose value is a string or a list of entries.
A bare value inside a block (``{ GER ITA }``) has the key ``None``.

It reads what the game reads and nothing more: comments are cut, a quoted string
is one token, ``@variables`` and ``[...]`` inline maths are left as written.
"""

from __future__ import annotations

import re

Node = tuple  # (key | None, operator, str | list[Node])

_TOKEN = re.compile(r'"[^"\n]*"?|#[^\n]*|[{}]|<=|>=|!=|[=<>]|[^\s{}=<>"#]+')
_OPERATORS = {"=", "<", ">", "<=", ">=", "!="}
_PLAIN = re.compile(r"^[\w.\-:@\[\]/$|]+$")


def parse(text: str) -> list[Node]:
    root: list[Node] = []
    stack = [root]
    current = root
    key: str | None = None
    op = ""
    for token in _TOKEN.findall(text):
        head = token[0]
        if head == "#":
            continue
        if token == "{":
            block: list[Node] = []
            if key is not None:
                current.append((key, op or "=", block))
                key, op = None, ""
            else:
                current.append((None, "", block))
            stack.append(block)
            current = block
        elif token == "}":
            if key is not None:
                current.append((None, "", key))
                key, op = None, ""
            if len(stack) > 1:
                stack.pop()
                current = stack[-1]
        elif token in _OPERATORS:
            if key is not None:
                op = token
        else:
            value = token[1:-1] if head == '"' and token.endswith('"') and len(token) > 1 else token
            if key is not None and op:
                current.append((key, op, value))
                key, op = None, ""
            else:
                if key is not None:
                    current.append((None, "", key))
                key = value
    if key is not None:
        current.append((None, "", key))
    return root


def entries(nodes, key: str) -> list[Node]:
    return [n for n in nodes if n[0] == key]


def get(nodes, key: str, default=None):
    """The first value of ``key`` (a string, or a list of entries for a block)."""
    for n in nodes:
        if n[0] == key:
            return n[2]
    return default


def text(nodes, key: str, default: str | None = None) -> str | None:
    value = get(nodes, key)
    return value if isinstance(value, str) else default


def block(nodes, key: str) -> list[Node] | None:
    value = get(nodes, key)
    return value if isinstance(value, list) else None


def blocks(nodes, key: str) -> list[list[Node]]:
    return [n[2] for n in nodes if n[0] == key and isinstance(n[2], list)]


def bare(nodes) -> list[str]:
    """The bare values of a block (``{ GER ITA }`` gives ``['GER', 'ITA']``)."""
    return [n[2] for n in nodes if n[0] is None and isinstance(n[2], str)]


def number(raw) -> float | None:
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


# --- writing -----------------------------------------------------------------


def _atom(value: str) -> str:
    return value if _PLAIN.match(value) else f'"{value}"'


def inline(nodes) -> str:
    """The entries as one line of script, as the console takes it."""
    parts = []
    for key, op, value in nodes:
        if isinstance(value, list):
            body = f"{{ {inline(value)} }}" if value else "{ }"
            parts.append(body if key is None else f"{key} {op} {body}")
        else:
            parts.append(_atom(value) if key is None else f"{key} {op} {_atom(value)}")
    return " ".join(parts)


def mentions(nodes, words: set[str]) -> bool:
    """Whether any key or value, at any depth, is one of ``words``."""
    for key, _op, value in nodes:
        if key in words:
            return True
        if isinstance(value, list):
            if mentions(value, words):
                return True
        elif value in words:
            return True
    return False


# --- reading it as a player would -------------------------------------------

_SCOPE_WORDS = {"NOT": "not", "OR": "or", "AND": "and"}


def _readable_node(node: Node, depth: int) -> str:
    key, op, value = node
    if key is None:
        return value if isinstance(value, str) else "(...)"
    if isinstance(value, list):
        if key in ("OR", "AND", "NOT", "hidden_trigger", "custom_trigger_tooltip",
                   "if", "limit", "NAND", "NOR"):
            inner = [_readable_node(n, depth + 1) for n in value if n[0] != "tooltip"]
            if key == "NOT":
                return "not (" + ", ".join(inner) + ")" if len(inner) != 1 else "not " + inner[0]
            if key in ("OR", "NOR"):
                body = " or ".join(inner)
                return f"({body})" if key == "OR" else f"none of ({body})"
            if key in ("AND", "NAND"):
                return "(" + " and ".join(inner) + ")"
            return ", ".join(inner)
        if depth >= 2:
            return f"{key}{{...}}"
        inner = ", ".join(_readable_node(n, depth + 1) for n in value)
        return f"{key}{{{inner}}}"
    if op == "=" and value == "yes":
        return key
    if op == "=" and value == "no":
        return f"not {key}"
    return f"{key} {value}" if op == "=" else f"{key} {op} {value}"


def readable(nodes, limit: int = 6, width: int = 160) -> str:
    """A condition block as a short sentence: ``tag GER, not has_war, (a or b)``.

    Only the first ``limit`` entries are written out and the rest counted, so a
    forty-line ``available`` becomes a line a person can take in."""
    shown = [_readable_node(n, 0) for n in nodes[:limit] if n[0] not in ("tooltip", "custom_tooltip")]
    text_ = ", ".join(shown)
    if len(nodes) > limit:
        text_ += f", +{len(nodes) - limit} more"
    if len(text_) > width:
        text_ = text_[: width - 3].rstrip(", ") + "..."
    return text_


def tag_truth(nodes, tag: str) -> bool | None:
    """Whether a condition block holds for country ``tag``, going by the tag
    tests alone: True or False when they decide it, None when anything else
    could. Used to tell which decisions a country can ever have."""
    verdicts = [_node_truth(n, tag) for n in nodes]
    if any(v is False for v in verdicts):
        return False
    if verdicts and all(v is True for v in verdicts):
        return True
    return None


def _node_truth(node: Node, tag: str) -> bool | None:
    key, _op, value = node
    if key in ("tag", "original_tag") and isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9]{2}", value):
        return value == tag
    if key == "always" and isinstance(value, str):
        return value == "yes"
    if not isinstance(value, list):
        return None
    if key in ("AND", "hidden_trigger"):
        return tag_truth(value, tag)
    if key == "OR":
        verdicts = [_node_truth(n, tag) for n in value]
        if any(v is True for v in verdicts):
            return True
        return False if verdicts and all(v is False for v in verdicts) else None
    if key == "NOT":
        inner = tag_truth(value, tag)
        return None if inner is None else not inner
    return None
