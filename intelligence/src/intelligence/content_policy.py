"""User-owned topic exclusions shared by collection, analysis, and reports."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml


DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "content-policy.yaml"
MATCH_FIELDS = (
    "title",
    "url",
    "canonical_url",
    "content_text",
    "headline",
    "summary",
    "key_change",
    "why_it_matters",
    "company_impact",
    "topics",
    "topics_json",
)


def load_excluded_topics(path: Path | None = None) -> tuple[dict[str, Any], ...]:
    source = path or DEFAULT_POLICY_PATH
    if not source.exists():
        return ()
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    topics = raw.get("excluded_topics", []) if isinstance(raw, Mapping) else []
    result = []
    for topic in topics:
        if not isinstance(topic, Mapping) or not topic.get("slug"):
            continue
        terms = tuple(str(value).casefold().strip() for value in topic.get("terms", []) if str(value).strip())
        if terms:
            result.append({"slug": str(topic["slug"]), "terms": terms,
                           "suppress": tuple(str(value) for value in topic.get("suppress", []))})
    return tuple(result)


def excluded_topic(values: Iterable[Any], *, stage: str, path: Path | None = None) -> str | None:
    text = "\n".join(_text(value) for value in values if value is not None).casefold()
    if not text:
        return None
    for topic in load_excluded_topics(path):
        if stage not in topic["suppress"]:
            continue
        if any(term in text for term in topic["terms"]):
            return str(topic["slug"])
    return None


def excluded_mapping(row: Mapping[str, Any], *, stage: str, path: Path | None = None) -> str | None:
    return excluded_topic((row.get(field) for field in MATCH_FIELDS), stage=stage, path=path)


def excluded_item(item: Any, *, stage: str, path: Path | None = None) -> str | None:
    return excluded_topic(
        (
            getattr(item, "title", None),
            getattr(item, "url", None),
            getattr(item, "canonical_url", None),
            getattr(item, "content_text", None),
        ),
        stage=stage,
        path=path,
    )


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)
