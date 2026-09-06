"""Project-owned Codex defaults, independent of a host's global model settings.

Only explicit INTELLIGENCE_* overrides apply. Invalid overrides fail before a
model process is started; there is no automatic fallback to a more costly model.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

PROJECT_CONFIG = Path(__file__).resolve().parents[2] / "multica" / "config.yaml"
ALLOWED_MODELS = frozenset({"gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol"})
ALLOWED_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})


@dataclass(frozen=True)
class CodexModelConfig:
    model: str
    reasoning_effort: str
    provider: str = "openai"

    def argv(self) -> list[str]:
        return ["--model", self.model, "-c", 'model_provider="openai"',
                "-c", f'model_reasoning_effort="{self.reasoning_effort}"']


def resolve_codex_model(*, role: str | None = None,
                        environ: Mapping[str, str] | None = None,
                        config_path: Path = PROJECT_CONFIG) -> CodexModelConfig:
    if role not in (None, "mcp"):
        raise ValueError("unsupported Codex role")
    env = os.environ if environ is None else environ
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("cannot read project Multica model configuration") from exc
    agent = config.get("agent") if isinstance(config, dict) else None
    if not isinstance(agent, dict):
        raise ValueError("project Multica configuration requires agent settings")

    def value(suffix: str, default: object, allowed: frozenset[str]) -> str:
        if not isinstance(default, str) or default not in allowed:
            raise ValueError(f"project Multica agent has invalid {suffix.lower()}")
        key = f"INTELLIGENCE_CODEX_{suffix}"
        selected = env.get(key, default)
        # Validate even a shadowed global override so stale mistakes are visible.
        if selected not in allowed:
            raise ValueError(f"{key} must be one of: {', '.join(sorted(allowed))}")
        if role:
            key = f"INTELLIGENCE_{role.upper()}_{suffix}"
            selected = env.get(key, selected)
            if selected not in allowed:
                raise ValueError(f"{key} must be one of: {', '.join(sorted(allowed))}")
        return selected

    return CodexModelConfig(value("MODEL", agent.get("model"), ALLOWED_MODELS),
                            value("REASONING_EFFORT", agent.get("thinking_level"), ALLOWED_EFFORTS))
