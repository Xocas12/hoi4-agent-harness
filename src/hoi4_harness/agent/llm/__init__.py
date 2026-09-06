"""LLM providers. Any model, one interface."""

from __future__ import annotations

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

PROVIDERS = ("anthropic", "openai", "google", "scripted")


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
    if provider == "scripted":
        return ScriptedClient(model=config.model or "scripted")
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
