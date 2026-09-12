from datetime import datetime, timezone

from intelligence.content_policy import excluded_mapping, load_excluded_topics
from intelligence.reporter.editorial import exclusion_reason


def test_project_policy_excludes_datasette_globally():
    topics = load_excluded_topics()
    assert any(topic["slug"] == "datasette" for topic in topics)
    assert excluded_mapping(
        {"title": "Datasette 1.0 security release", "url": "https://simonwillison.net/post"},
        stage="reports",
    ) == "datasette"
    assert excluded_mapping(
        {"title": "Unrelated agent release", "topics_json": '["MCP"]'},
        stage="reports",
    ) is None


def test_report_editorial_gate_rejects_datasette_before_rendering():
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    row = {
        "title": "datasette-publish-fly 1.4",
        "url": "https://example.com/datasette-publish-fly",
    }
    assert exclusion_reason(row, now, now) == "user_excluded_topic:datasette"
