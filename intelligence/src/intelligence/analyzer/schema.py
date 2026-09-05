"""Dependency-free validation for model-produced intelligence analyses.

The analyzer is deliberately strict at this boundary.  Model output is untrusted
JSON until :func:`validate_analysis` has returned an ``AnalysisResult``.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


class AnalysisValidationError(ValueError):
    """Raised when a model response does not satisfy the analysis contract."""


@dataclass(frozen=True)
class AnalysisEvidence:
    url: str
    claim: str


@dataclass(frozen=True)
class AnalysisResult:
    summary: str
    key_change: str
    why_it_matters: str
    company_impact: str
    importance: int
    confidence: float
    topics: tuple[str, ...]
    watch_next: tuple[str, ...]
    evidence: tuple[AnalysisEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "key_change": self.key_change,
            "why_it_matters": self.why_it_matters,
            "company_impact": self.company_impact,
            "importance": self.importance,
            "confidence": self.confidence,
            "topics": list(self.topics),
            "watch_next": list(self.watch_next),
            "evidence": [
                {"url": evidence.url, "claim": evidence.claim}
                for evidence in self.evidence
            ],
        }


_REQUIRED = {
    "summary",
    "key_change",
    "why_it_matters",
    "company_impact",
    "importance",
    "confidence",
    "topics",
    "watch_next",
    "evidence",
}


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise AnalysisValidationError(f"{field} must be an array of strings")
    values = tuple(_non_empty_string(item, f"{field}[]") for item in value)
    if len(set(values)) != len(values):
        raise AnalysisValidationError(f"{field} must not contain duplicates")
    return values


def _public_http_url(value: Any, field: str) -> str:
    url = _non_empty_string(value, field)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AnalysisValidationError(f"{field} must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise AnalysisValidationError(f"{field} must not contain credentials")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname in {"localhost", "0.0.0.0"} or hostname.endswith((".local", ".internal")):
        raise AnalysisValidationError(f"{field} must refer to a public host")
    try:
        address = ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise AnalysisValidationError(f"{field} must refer to a public host")
    return url


def validate_analysis(payload: Mapping[str, Any], *, reject_unknown: bool = True) -> AnalysisResult:
    """Validate and normalize one structured model response.

    Boolean values are rejected for numeric fields because ``bool`` is an
    ``int`` subclass in Python and would otherwise silently pass validation.
    """

    if not isinstance(payload, Mapping):
        raise AnalysisValidationError("analysis must be a JSON object")
    missing = sorted(_REQUIRED - payload.keys())
    if missing:
        raise AnalysisValidationError(f"missing required fields: {', '.join(missing)}")
    if reject_unknown:
        unknown = sorted(payload.keys() - _REQUIRED)
        if unknown:
            raise AnalysisValidationError(f"unknown fields: {', '.join(unknown)}")

    importance = payload["importance"]
    if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 5:
        raise AnalysisValidationError("importance must be an integer from 1 to 5")

    confidence = payload["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise AnalysisValidationError("confidence must be a number from 0 to 1")
    confidence = float(confidence)
    if not 0.0 <= confidence <= 1.0:
        raise AnalysisValidationError("confidence must be a number from 0 to 1")

    evidence_value = payload["evidence"]
    if isinstance(evidence_value, (str, bytes)) or not isinstance(evidence_value, Sequence):
        raise AnalysisValidationError("evidence must be a non-empty array")
    evidence: list[AnalysisEvidence] = []
    for index, item in enumerate(evidence_value):
        if not isinstance(item, Mapping):
            raise AnalysisValidationError(f"evidence[{index}] must be an object")
        if set(item.keys()) != {"url", "claim"}:
            raise AnalysisValidationError(f"evidence[{index}] must contain only url and claim")
        evidence.append(
            AnalysisEvidence(
                url=_public_http_url(item["url"], f"evidence[{index}].url"),
                claim=_non_empty_string(item["claim"], f"evidence[{index}].claim"),
            )
        )
    if not evidence:
        raise AnalysisValidationError("evidence must contain at least one source")

    return AnalysisResult(
        summary=_non_empty_string(payload["summary"], "summary"),
        key_change=_non_empty_string(payload["key_change"], "key_change"),
        why_it_matters=_non_empty_string(payload["why_it_matters"], "why_it_matters"),
        company_impact=_non_empty_string(payload["company_impact"], "company_impact"),
        importance=importance,
        confidence=confidence,
        topics=_string_tuple(payload["topics"], "topics"),
        watch_next=_string_tuple(payload["watch_next"], "watch_next"),
        evidence=tuple(evidence),
    )
