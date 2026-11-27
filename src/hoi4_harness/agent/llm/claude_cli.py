"""A model behind the Claude Code CLI: unattended play on the person's subscription.

The mailbox provider needs an agent outside the harness to answer every request; this one
is that agent, run by the harness itself. Each turn is one ``claude -p`` call:

* the system prompt goes in ``--system-prompt-file``, the turn (tools and conversation, the
  mailbox's markdown rendering) on stdin, with a closing instruction to answer in the
  mailbox's JSON (``{"text": ..., "calls": [{"name": ..., "arguments": {...}}]}``);
* ``--tools ""`` gives the model no tools of its own (it cannot touch files or the shell),
  ``--setting-sources ""`` and ``--strict-mcp-config`` keep the person's hooks, settings and
  MCP servers out of it, ``--no-session-persistence`` keeps the session list clean;
* it runs in a scratch folder outside any repository, so no CLAUDE.md is picked up;
* on Windows the npm shim (``claude.cmd``) is skipped for the ``claude.exe`` it calls, and the
  process is started with no console window: a cmd window flashing over the game on every
  turn is exactly what a person playing does not want.

No API key is read or needed: the CLI uses whatever login it has. The spend the harness's
budget sees is zero; ``total_cost_usd`` from the CLI is kept on the response for the log.

A reply that is not the JSON asked for is not an error the run dies on: the provider asks
once more with the parse problem, and then returns the text with no calls, which the agent
loop treats like a model that chose to do nothing.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ...actions.registry import ToolSpec
from .base import LLMClient, LLMResponse, Msg, ToolCall, Usage
from .mailbox import render_markdown

ANSWER_INSTRUCTION = (
    "\n\n---\nYou cannot run tools yourself. Answer this request with ONLY one JSON object and "
    "nothing else (no prose before or after, no code fence):\n"
    '{"text": "one or two sentences: why", "calls": [{"name": "<tool>", "arguments": {...}}]}\n'
    "Use only the tools listed above, with their argument names and allowed values. If the "
    "conversation asks you to end the turn with a specific tool (advance_time, end_turn), "
    "include it last."
)

CREATE_NO_WINDOW = 0x08000000


class ClaudeCliError(RuntimeError):
    """The CLI could not be run or returned nothing usable."""


def find_cli(explicit: str | None = None) -> str:
    """The CLI executable: an explicit path, else ``claude`` on PATH (the .exe behind the
    npm shim on Windows)."""
    if explicit:
        return explicit
    found = shutil.which("claude")
    if not found:
        raise ClaudeCliError("the claude CLI is not on PATH (npm i -g @anthropic-ai/claude-code)")
    path = Path(found)
    if path.suffix.lower() in (".cmd", ".ps1", "") and sys.platform == "win32":
        exe = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
    return found


def extract_json(text: str) -> dict:
    """The answer object in ``text``: the whole text, a fenced block, or the outermost braces."""
    text = text.strip()
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        candidates.append(fence.group(1))
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("no JSON object in the reply")


class ClaudeCliClient(LLMClient):
    name = "claude-cli"
    web_search_support = True

    def __init__(self, model: str = "haiku", *, cli: str | None = None,
                 timeout_seconds: float = 300.0, workdir: str | Path | None = None,
                 run: Callable[..., subprocess.CompletedProcess] | None = None,
                 retries: int = 1, **_ignored):
        self.model = model or "haiku"
        self.cli = cli
        self.timeout_seconds = timeout_seconds
        self.workdir = Path(workdir) if workdir else Path(tempfile.gettempdir()) / "hoi4-harness-claude"
        self._run = run or subprocess.run
        self.retries = retries
        self.web = False
        self.plain = False
        self._ids = itertools.count(1)
        self.seq = 0
        self.last_cost_usd = 0.0
        self.total_cost_usd = 0.0

    @contextmanager
    def prose(self, web: bool = False) -> Iterator[None]:
        """Inside the block: the reply as it comes, and WebSearch/WebFetch (only those) when ``web``."""
        before = (self.plain, self.web)
        self.plain, self.web = True, web
        try:
            yield
        finally:
            self.plain, self.web = before

    def _command(self, system_file: Path) -> list[str]:
        tools = "WebSearch,WebFetch" if self.web else ""
        return [find_cli(self.cli), "-p", "--model", self.model, "--output-format", "json",
                "--tools", tools, "--setting-sources", "", "--strict-mcp-config",
                "--no-session-persistence", "--system-prompt-file", str(system_file)]

    def _call(self, system_file: Path, prompt: str) -> tuple[str, dict]:
        kwargs = dict(input=prompt, capture_output=True, text=True, encoding="utf-8",
                      timeout=self.timeout_seconds, cwd=str(self.workdir))
        if sys.platform == "win32":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        env = dict(os.environ)
        # Never let a key in the environment turn subscription play into API billing.
        env.pop("ANTHROPIC_API_KEY", None)
        kwargs["env"] = env
        try:
            proc = self._run(self._command(system_file), **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliError(f"claude -p took longer than {self.timeout_seconds:.0f} s") from exc
        except OSError as exc:
            raise ClaudeCliError(f"could not run the claude CLI: {exc}") from exc
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-400:]
            raise ClaudeCliError(f"claude -p exited {proc.returncode}: {tail}")
        try:
            envelope = json.loads(proc.stdout)
        except ValueError:
            return proc.stdout, {}
        if envelope.get("is_error"):
            raise ClaudeCliError(f"claude -p reported an error: {str(envelope.get('result'))[:400]}")
        return str(envelope.get("result") or ""), envelope

    def complete(self, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None,
                 max_tokens: int | None = None) -> LLMResponse:
        self.seq += 1
        if self.plain:
            return self._complete_plain(system, messages)
        tools = list(tools or [])
        self.workdir.mkdir(parents=True, exist_ok=True)
        system_file = self.workdir / "system.md"
        system_file.write_text(system, encoding="utf-8")
        prompt = render_markdown(self.seq, tools, messages) + ANSWER_INSTRUCTION
        usage, text, problem = Usage(), "", ""
        for attempt in range(self.retries + 1):
            ask = prompt if attempt == 0 else (
                prompt + f"\n\nYour previous reply could not be used ({problem}). Reply with the "
                "JSON object only.")
            text, envelope = self._call(system_file, ask)
            usage = usage + self._usage(envelope)
            try:
                data = extract_json(text)
            except ValueError as exc:
                problem = str(exc)
                continue
            return self._response(data, usage)
        return LLMResponse(text=text, tool_calls=[], stop_reason="end_turn", usage=usage,
                           model=self.model)

    def _complete_plain(self, system: str, messages: list[Msg]) -> LLMResponse:
        """One call, the reply as it comes: a briefing is prose, not a tool-call envelope."""
        self.workdir.mkdir(parents=True, exist_ok=True)
        system_file = self.workdir / "system.md"
        system_file.write_text(system, encoding="utf-8")
        prompt = "\n\n".join(m.text for m in messages if m.text)
        text, envelope = self._call(system_file, prompt)
        return LLMResponse(text=text.strip(), stop_reason="end_turn", usage=self._usage(envelope),
                           model=self.model)

    def _usage(self, envelope: dict) -> Usage:
        raw = envelope.get("usage") or {}
        cost = float(envelope.get("total_cost_usd") or 0.0)
        self.last_cost_usd = cost
        self.total_cost_usd += cost
        uncached = int(raw.get("input_tokens") or 0)
        read = int(raw.get("cache_read_input_tokens") or 0)
        write = int(raw.get("cache_creation_input_tokens") or 0)
        return Usage(input_tokens=uncached + read + write, output_tokens=int(raw.get("output_tokens") or 0),
                     cached_input_tokens=read, cache_write_input_tokens=write)

    def _response(self, data: dict, usage: Usage) -> LLMResponse:
        if "text" not in data and "calls" not in data:
            # An object in some other shape (the system prompt asked for its own): hand it back
            # whole as the text instead of an empty reply the caller cannot read.
            return LLMResponse(text=json.dumps(data), tool_calls=[], stop_reason="end_turn",
                               usage=usage, model=self.model)
        calls = []
        for raw in data.get("calls") or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            args = raw.get("arguments") or {}
            calls.append(ToolCall(id=f"cli-{self.seq}-{next(self._ids)}", name=str(raw["name"]),
                                  arguments=args if isinstance(args, dict) else {}))
        return LLMResponse(text=str(data.get("text") or ""), tool_calls=calls,
                           stop_reason="tool_use" if calls else "end_turn", usage=usage,
                           model=self.model)
