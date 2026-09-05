from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from intelligence.analyzer import AnalysisValidationError, validate_analysis


def valid_payload() -> dict:
    return {
        "summary": "Composio 发布了新能力。",
        "key_change": "新增了一项此前没有的能力。",
        "why_it_matters": "缩短 Agent 集成路径。",
        "company_impact": "推断：Aisa 需要比较接入成本。",
        "importance": 4,
        "confidence": 0.86,
        "topics": ["MCP", "Agent Infrastructure"],
        "watch_next": ["定价", "采用率"],
        "evidence": [{"url": "https://example.com/post", "claim": "发布公告"}],
    }


def test_validate_analysis_returns_immutable_contract() -> None:
    result = validate_analysis(valid_payload())
    assert result.importance == 4
    assert result.confidence == 0.86
    assert result.topics == ("MCP", "Agent Infrastructure")
    assert result.evidence[0].url == "https://example.com/post"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("importance", 0),
        ("importance", 6),
        ("importance", True),
        ("confidence", -0.1),
        ("confidence", 1.1),
        ("confidence", True),
        ("summary", "  "),
        ("topics", "MCP"),
        ("evidence", []),
    ],
)
def test_validate_analysis_rejects_invalid_fields(field: str, value: object) -> None:
    payload = valid_payload()
    payload[field] = value
    with pytest.raises(AnalysisValidationError):
        validate_analysis(payload)


def test_validate_analysis_rejects_credentials_in_evidence_url() -> None:
    payload = valid_payload()
    payload["evidence"] = [{"url": "https://user:secret@example.com/post", "claim": "x"}]
    with pytest.raises(AnalysisValidationError, match="credentials"):
        validate_analysis(payload)


def test_validate_analysis_rejects_unknown_fields() -> None:
    payload = valid_payload()
    payload["model_note"] = "not part of the storage contract"
    with pytest.raises(AnalysisValidationError, match="unknown fields"):
        validate_analysis(payload)


def test_packaged_json_schema_accepts_contract() -> None:
    schema_path = Path(__file__).parents[1] / "src/intelligence/analyzer/analysis.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(valid_payload())
