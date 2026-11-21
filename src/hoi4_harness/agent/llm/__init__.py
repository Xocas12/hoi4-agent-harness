"""LLM providers. Any model, one interface."""

from __future__ import annotations

import os

from ...config import LLMConfig
from .base import LLMClient, LLMResponse, Msg, ToolCall, ToolResult, Usage
from .scripted import ScriptedClient

__all__ = [
    "LLMClient",
    "LLMResponse",
    "Msg",
    "PROVIDERS",
    "ScriptedClient",
    "ToolCall",
    "ToolResult",
    "Usage",
    "build_llm",
]

PROVIDERS = ("anthropic", "openai", "google", "scripted", "mailbox", "claude-cli")


def build_llm(config: LLMConfig) -> LLMClient:
    """Construct the client for ``config.provider``. Vendor SDKs load lazily."""
    provider = config.provider.strip().lower()
    kwargs = dict(
        model=config.model,
        max_tokens=config.max_tokens,
        effort=config.effort,
        base_url=config.base_url,
        temperature=config.temperature,
        cache_prefix=config.cache_prefix,
    )
    if config.api_key_env:
        # The key lives in a variable the user named, e.g. an OpenRouter key
        # beside an OpenAI one. A name that resolves to nothing is an error:
        # falling back to the vendor's default variable would bill another key.
        key = os.environ.get(config.api_key_env)
        if not key:
            raise ValueError(f"api_key_env names {config.api_key_env}, which is not set")
        kwargs["api_key"] = key
    if provider == "scripted":
        script = config.extra.get("script")
        if script:
            return ScriptedClient.from_file(script, model=config.model or "scripted")
        return ScriptedClient(model=config.model or "scripted")
    if provider == "mailbox":
        from .mailbox import MailboxClient

        directory = config.extra.get("mailbox_dir") or "mailbox"
        timeout = config.extra.get("mailbox_timeout_minutes")
        return MailboxClient(directory, model=config.model or "mailbox",
                             timeout_seconds=float(timeout) * 60 if timeout else None)
    if provider == "claude-cli":
        from .claude_cli import ClaudeCliClient

        timeout = config.extra.get("cli_timeout_seconds")
        return ClaudeCliClient(model=config.model or "haiku", cli=config.extra.get("cli_path"),
                               timeout_seconds=float(timeout) if timeout else 300.0,
                               workdir=config.extra.get("cli_workdir"))
    if provider == "anthropic":
        from .anthropic_provider import AnthropicClient

        return AnthropicClient(**kwargs)
    if provider in {"openai", "ollama", "openrouter", "vllm", "local"}:
        from .openai_provider import OpenAIClient

        return OpenAIClient(**kwargs)
    if provider in {"google", "gemini"}:
        from .google_provider import GoogleClient

        return GoogleClient(**kwargs)
    raise ValueError(f"Unknown provider {provider!r}. Known: {', '.join(PROVIDERS)}")
