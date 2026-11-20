"""A model that answers through files: an agent outside the harness plays the turns.

The harness writes each request into a folder and waits; something else (a Claude Code
subagent, a person, any program) reads it and writes the answer back. Nothing here calls
a model or needs a key, so the spend the budget sees is zero: whoever answers pays in
their own currency (a Claude Code subscription, for instance).

The folder, one request at a time:

* ``system.md`` -- the system prompt, rewritten only when it changes.
* ``request.json`` -- ``{"seq", "tools", "messages"}``: the whole conversation as the
  provider would send it, in the provider-neutral shape.
* ``request.md`` -- the same, for a reader: the tools in one line each, then the turns.
* ``response.json`` -- written by the answerer: ``{"seq": N, "text": "...", "calls":
  [{"name": "...", "arguments": {...}}]}``. ``hoi4-harness mailbox answer`` checks it
  first and writes it atomically; the provider takes only a response whose ``seq``
  matches the pending request, so a stale file from an earlier turn is never read
  as this one.
* ``closed`` -- written when the run ends, so a waiting answerer can stop.

A bad tool name or bad arguments are not refused here: the harness validates every call
and returns the error as a tool result, exactly as it does for a real model.
"""

from __future__ import annotations

import itertools
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from ...actions.registry import ToolSpec
from .base import LLMClient, LLMResponse, Msg, ToolCall, Usage

REQUEST_JSON, REQUEST_MD, RESPONSE_JSON = "request.json", "request.md", "response.json"
SYSTEM_MD, CLOSED = "system.md", "closed"


class MailboxTimeout(RuntimeError):
    """Nobody answered within the configured time."""


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _msg_dict(msg: Msg) -> dict:
    out: dict = {"role": msg.role}
    if msg.text:
        out["text"] = msg.text
    if msg.tool_calls:
        out["calls"] = [{"name": c.name, "arguments": c.arguments} for c in msg.tool_calls]
    if msg.tool_results:
        out["results"] = [{"name": r.name, "content": r.content, "error": r.is_error}
                          for r in msg.tool_results]
    return out


def _params_line(spec: ToolSpec) -> str:
    props = (spec.parameters or {}).get("properties", {}) or {}
    required = set((spec.parameters or {}).get("required", []) or [])
    parts = []
    for name, schema in props.items():
        kind = schema.get("type", "any")
        if "enum" in schema:
            kind = "|".join(str(v) for v in schema["enum"][:12])
        parts.append(f"{name}{'' if name in required else '?'}: {kind}")
    return ", ".join(parts)


def render_markdown(seq: int, tools: list[ToolSpec], messages: list[Msg]) -> str:
    lines = [f"# Request {seq}", "",
             "Answer with `hoi4-harness mailbox answer` (JSON: text + calls). "
             "The system prompt is in system.md.", "", "## Tools", ""]
    for spec in tools:
        first = (spec.description or "").strip().splitlines()[0] if spec.description else ""
        lines.append(f"- `{spec.name}({_params_line(spec)})`: {first}")
    lines += ["", "## Conversation", ""]
    for i, msg in enumerate(messages):
        lines.append(f"### {i + 1}. {msg.role}")
        if msg.text:
            lines += ["", msg.text.rstrip(), ""]
        for call in msg.tool_calls:
            lines.append(f"- call `{call.name}` {json.dumps(call.arguments, ensure_ascii=False)}")
        for res in msg.tool_results:
            tag = "ERROR " if res.is_error else ""
            lines.append(f"- result {tag}`{res.name}`: {res.content.strip()}")
        lines.append("")
    return "\n".join(lines)


class MailboxClient(LLMClient):
    name = "mailbox"

    def __init__(self, directory: str | Path, model: str = "mailbox", *,
                 timeout_seconds: float | None = None, poll_seconds: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 on_wait: Callable[[], None] | None = None):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.model = model or "mailbox"
        self.timeout_seconds = timeout_seconds
        self.poll_seconds, self.sleep, self.clock, self.on_wait = poll_seconds, sleep, clock, on_wait
        self._ids = itertools.count(1)
        self.seq = self._last_seq()
        (self.dir / CLOSED).unlink(missing_ok=True)

    def _last_seq(self) -> int:
        """Carry the numbering across a resumed run, so an old answer never matches."""
        best = 0
        for name in (REQUEST_JSON, RESPONSE_JSON):
            try:
                best = max(best, int(json.loads((self.dir / name).read_text("utf-8"))["seq"]))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return best

    def complete(self, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None,
                 max_tokens: int | None = None) -> LLMResponse:
        self.seq += 1
        tools = list(tools or [])
        system_path = self.dir / SYSTEM_MD
        try:
            same = system_path.read_text(encoding="utf-8") == system
        except OSError:
            same = False
        if not same:
            _atomic_write(system_path, system)
        payload = {"seq": self.seq,
                   "tools": [{"name": t.name, "description": t.description,
                              "parameters": t.parameters} for t in tools],
                   "messages": [_msg_dict(m) for m in messages]}
        _atomic_write(self.dir / REQUEST_MD, render_markdown(self.seq, tools, messages))
        _atomic_write(self.dir / REQUEST_JSON, json.dumps(payload, ensure_ascii=False, indent=1))
        return self._await(self.seq)

    def _await(self, seq: int) -> LLMResponse:
        started = self.clock()
        path = self.dir / RESPONSE_JSON
        while True:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
            if isinstance(data, dict) and data.get("seq") == seq:
                return self._response(data)
            if self.timeout_seconds is not None and self.clock() - started >= self.timeout_seconds:
                raise MailboxTimeout(f"no answer to mailbox request {seq} in "
                                     f"{self.timeout_seconds / 60:.0f} minutes ({self.dir})")
            if self.on_wait:
                self.on_wait()
            self.sleep(self.poll_seconds)

    def _response(self, data: dict) -> LLMResponse:
        calls = []
        for raw in data.get("calls") or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            args = raw.get("arguments") or {}
            calls.append(ToolCall(id=f"mailbox-{self.seq}-{next(self._ids)}",
                                  name=str(raw["name"]),
                                  arguments=args if isinstance(args, dict) else {}))
        return LLMResponse(text=str(data.get("text") or ""), tool_calls=calls,
                           stop_reason="tool_use" if calls else "end_turn",
                           usage=Usage(), model=self.model)

    def close(self) -> None:
        try:
            _atomic_write(self.dir / CLOSED, "the run has ended\n")
        except OSError:
            pass


# --- the answering side ----------------------------------------------------------------


def pending(directory: str | Path) -> dict | None:
    """The request waiting for an answer, or None (none yet, or already answered)."""
    d = Path(directory)
    try:
        req = json.loads((d / REQUEST_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        answered = json.loads((d / RESPONSE_JSON).read_text(encoding="utf-8")).get("seq")
    except (OSError, ValueError, AttributeError):
        answered = None
    return None if answered == req.get("seq") else req


def wait_for_request(directory: str | Path, timeout_seconds: float | None = None, *,
                     poll_seconds: float = 1.0, sleep: Callable[[float], None] = time.sleep,
                     clock: Callable[[], float] = time.monotonic) -> tuple[str, dict | None]:
    """Block until a request is pending: ("request", req), ("closed", None) or ("timeout", None)."""
    d, started = Path(directory), clock()
    while True:
        req = pending(d)
        if req is not None:
            return "request", req
        if (d / CLOSED).exists():
            return "closed", None
        if timeout_seconds is not None and clock() - started >= timeout_seconds:
            return "timeout", None
        sleep(poll_seconds)


def check_answer(req: dict, answer: dict) -> list[str]:
    """Problems with an answer, before it is handed over. Empty: fine."""
    problems: list[str] = []
    if not isinstance(answer, dict):
        return ["the answer must be a JSON object with \"text\" and \"calls\""]
    tools = {t["name"]: t for t in req.get("tools", [])}
    calls = answer.get("calls", [])
    if not isinstance(calls, list):
        return ["\"calls\" must be a list"]
    for i, call in enumerate(calls):
        if not isinstance(call, dict) or "name" not in call:
            problems.append(f"call {i + 1}: needs a \"name\"")
            continue
        name = call["name"]
        if name not in tools:
            problems.append(f"call {i + 1}: no tool named {name!r}; offered: {', '.join(sorted(tools))}")
            continue
        args = call.get("arguments", {})
        if not isinstance(args, dict):
            problems.append(f"call {i + 1} ({name}): \"arguments\" must be an object")
            continue
        params = tools[name].get("parameters") or {}
        for key in params.get("required", []) or []:
            if key not in args:
                problems.append(f"call {i + 1} ({name}): missing required argument {key!r}")
        props = params.get("properties") or {}
        for key, value in args.items():
            enum = (props.get(key) or {}).get("enum")
            if enum and value not in enum:
                problems.append(f"call {i + 1} ({name}): {key}={value!r} is not one of {enum}")
    return problems


def write_answer(directory: str | Path, answer: dict) -> tuple[int | None, list[str]]:
    """Check an answer against the pending request and hand it over. Returns (seq, problems)."""
    req = pending(directory)
    if req is None:
        return None, ["no request is waiting for an answer"]
    problems = check_answer(req, answer)
    if problems:
        return req.get("seq"), problems
    body = {"seq": req["seq"], "text": answer.get("text", ""), "calls": answer.get("calls", [])}
    _atomic_write(Path(directory) / RESPONSE_JSON, json.dumps(body, ensure_ascii=False))
    return req["seq"], []
