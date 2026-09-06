import pytest

from intelligence.model_config import resolve_codex_model


def test_project_default_ignores_global_host_model_environment():
    config = resolve_codex_model(environ={"CODEX_MODEL": "deepseek", "MODEL": "gpt-6"})
    assert config.model == "gpt-5.6-terra"
    assert config.reasoning_effort == "medium"
    assert config.provider == "openai"
    assert config.argv() == ["--model", "gpt-5.6-terra", "-c", 'model_provider="openai"',
                             "-c", 'model_reasoning_effort="medium"']


def test_explicit_role_overrides_win_only_for_that_role():
    env = {"INTELLIGENCE_CODEX_MODEL": "gpt-5.6-terra",
           "INTELLIGENCE_CODEX_REASONING_EFFORT": "high",
           "INTELLIGENCE_MCP_MODEL": "gpt-5.6-luna",
           "INTELLIGENCE_MCP_REASONING_EFFORT": "low"}
    assert resolve_codex_model(environ=env).reasoning_effort == "high"
    capture = resolve_codex_model(role="mcp", environ=env)
    assert capture.model == "gpt-5.6-luna"
    assert capture.reasoning_effort == "low"


@pytest.mark.parametrize("key,value", [
    ("INTELLIGENCE_CODEX_MODEL", ""), ("INTELLIGENCE_CODEX_MODEL", "deepseek"),
    ("INTELLIGENCE_MCP_MODEL", "gpt-6"), ("INTELLIGENCE_MCP_MODEL", " gpt-5.6-terra"),
    ("INTELLIGENCE_CODEX_REASONING_EFFORT", ""), ("INTELLIGENCE_MCP_REASONING_EFFORT", "ultra"),
])
def test_invalid_overrides_fail_clearly(key, value):
    with pytest.raises(ValueError, match=key):
        resolve_codex_model(role="mcp", environ={key: value})


def test_missing_configuration_fails_without_silent_model_fallback(tmp_path):
    with pytest.raises(ValueError, match="cannot read project"):
        resolve_codex_model(config_path=tmp_path / "missing.yaml", environ={})


def test_unknown_role_rejected():
    with pytest.raises(ValueError, match="unsupported Codex role"):
        resolve_codex_model(role="analysis", environ={})
