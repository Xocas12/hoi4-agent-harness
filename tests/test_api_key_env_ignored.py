"""HOI4_LLM_API_KEY_ENV is parsed into LLMConfig.api_key_env but build_llm never
reads it, so the key in the named variable never reaches the client."""
import sys
import types

from hoi4_harness.agent import llm
from hoi4_harness.config import LLMConfig


def test_api_key_env_is_used(monkeypatch):
    captured = {}

    class Fake:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    fake_mod = types.ModuleType("hoi4_harness.agent.llm.openai_provider")
    fake_mod.OpenAIClient = Fake
    monkeypatch.setitem(sys.modules, "hoi4_harness.agent.llm.openai_provider", fake_mod)
    monkeypatch.setenv("MY_OPENROUTER_KEY", "sk-secret")
    monkeypatch.setenv("HOI4_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HOI4_LLM_API_KEY_ENV", "MY_OPENROUTER_KEY")
    config = LLMConfig.from_env()
    assert config.api_key_env == "MY_OPENROUTER_KEY"
    llm.build_llm(config)
    assert captured.get("api_key") == "sk-secret"
