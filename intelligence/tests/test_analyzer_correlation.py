from __future__ import annotations

from intelligence.analyzer import CorrelationEvent, correlate_events, validate_analysis


def event(item_id: str, target: str, topics: list[str], tags: tuple[str, ...] = ()) -> CorrelationEvent:
    analysis = validate_analysis(
        {
            "summary": "摘要",
            "key_change": "变化",
            "why_it_matters": "原因",
            "company_impact": "影响",
            "importance": 4,
            "confidence": 0.8,
            "topics": topics,
            "watch_next": [],
            "evidence": [{"url": f"https://example.com/{item_id}", "claim": "证据"}],
        }
    )
    return CorrelationEvent(item_id, target, tags, analysis)


def test_correlation_requires_at_least_two_distinct_events() -> None:
    candidates = correlate_events(
        [event("1", "Composio", ["MCP"]), event("2", "OpenAI", ["Agents"])]
    )
    assert candidates == ()


def test_correlation_groups_target_tags_and_topics_deterministically() -> None:
    candidates = correlate_events(
        [
            event("2", "OpenAI", ["MCP"], ("科技大厂",)),
            event("1", "Anthropic", ["MCP"], ("科技大厂",)),
        ]
    )
    assert [(item.dimension, item.key) for item in candidates] == [
        ("tag", "科技大厂"),
        ("topic", "MCP"),
    ]
    assert all(item.item_ids == ("1", "2") for item in candidates)
    assert candidates[0].evidence_urls == (
        "https://example.com/1",
        "https://example.com/2",
    )
