"""--provider X without --model keeps the scripted default model 'scripted'."""
from hoi4_harness.cli import _config_from_args, build_parser


def test_provider_flag_gets_provider_default_model(monkeypatch):
    for name in ("HOI4_LLM_PROVIDER", "HOI4_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    args = build_parser().parse_args(["play", "--provider", "anthropic"])
    config = _config_from_args(args)
    assert config.planner.provider == "anthropic"
    assert config.planner.model == "claude-opus-5"   # got: 'scripted'


def test_profile_provider_gets_provider_default_model(monkeypatch, tmp_path):
    for name in ("HOI4_LLM_PROVIDER", "HOI4_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    from hoi4_harness.config import HarnessConfig
    # JSON, not TOML: tomllib is 3.11+, and CI runs 3.10.
    profile = tmp_path / "p.json"
    profile.write_text('{"planner": {"provider": "openai"}}')
    assert HarnessConfig.from_file(profile).planner.model == "gpt-5"


def test_a_chosen_model_survives_a_provider_flag(monkeypatch):
    monkeypatch.setenv("HOI4_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HOI4_LLM_MODEL", "my-local-model")
    args = build_parser().parse_args(["play", "--provider", "anthropic"])
    assert _config_from_args(args).planner.model == "my-local-model"
