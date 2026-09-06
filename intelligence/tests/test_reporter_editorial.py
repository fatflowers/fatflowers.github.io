from datetime import datetime, timezone

import pytest

from intelligence.reporter.editorial import exclusion_reason, is_discovery_url


START = datetime(2026, 9, 5, tzinfo=timezone.utc)
END = datetime(2026, 9, 6, tzinfo=timezone.utc)


def event(**overrides):
    return {
        "url": "https://example.com/news/new-api",
        "title": "New integration API",
        "published_at": "2026-09-05T05:00:00Z",
        "fetched_at": "2026-09-05T06:00:00Z",
        "content_text": "Integrations now support scoped tokens and per-tool audit logs, available to existing accounts.",
        "summary": "集成 API 新增按工具限定权限的令牌和审计日志，现有账户可用。",
        **overrides,
    }


@pytest.mark.parametrize("overrides,reason", [
    ({"is_baseline": 1}, "baseline"),
    ({"raw_metadata_json": '{"discovery_only":true}'}, "discovery_only"),
    ({"published_at": None}, "unknown_publication_time"),
    ({"published_at": "2025-02-24T00:00:00Z"}, "outside_report_window"),
    ({"published_at": "2026-09-06T00:00:00Z"}, "outside_report_window"),
    ({"published_at": "2026-09-05T06:00:00"}, "unknown_publication_timezone"),
    ({"content_text": "New integration API"}, "insufficient_source_content"),
    ({"summary": "New integration API"}, "title_only_summary"),
    ({"summary": "公开来源显示：New integration API"}, "placeholder_analysis"),
    ({"why_it_matters": "反映相关产品与生态的演进"}, "placeholder_analysis"),
    ({"url": "https://example.com/blog/page/2/"}, "discovery_url"),
])
def test_excludes_unpublishable_events(overrides, reason):
    assert exclusion_reason(event(**overrides), START, END) == reason


def test_accepts_dated_source_with_substantive_body():
    assert exclusion_reason(event(), START, END) is None


def test_fetch_or_first_diff_flag_does_not_make_undated_page_news():
    assert exclusion_reason(event(published_at=None, raw_metadata={"changed": True}), START, END) == "unknown_publication_time"


@pytest.mark.parametrize("url", [
    "https://github.com/simonw", "https://github.com/composiohq/composio",
    "https://example.com/blog/", "https://example.com/docs/", "https://example.com/",
    "https://example.com/blog/tag/mcp", "https://example.com/blog?page=2",
])
def test_discovery_urls_are_not_events(url):
    assert is_discovery_url(url)


@pytest.mark.parametrize("url", [
    "https://github.com/composiohq/composio/releases/tag/v1.2.3",
    "https://example.com/news/new-api", "https://x.com/composio/status/123456789",
])
def test_specific_event_urls_are_allowed(url):
    assert not is_discovery_url(url)
