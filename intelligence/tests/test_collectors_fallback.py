import pytest

from intelligence.collectors.base import ChannelSpec, CollectionPage
from intelligence.collectors.health import ChannelHealth
from intelligence.collectors.cursor import CursorCheckpoint
from intelligence.collectors.http import HTTPCollector, WebDiffCollector
from intelligence.collectors.router import CollectorRouter, RouteStep
from intelligence.collectors.rss import RSSCollector
from intelligence.mcp.errors import MCPAuthenticationError


def channel(kind="rss", url="https://example.com/feed"):
    return ChannelSpec("openai", "openai-news", kind, kind, url=url)


def test_channel_spec_bridges_catalog_shape():
    spec = ChannelSpec.from_catalog(
        {"slug": "composio"},
        {"slug": "composio-twitter", "type": "twitter", "collector": "mcp", "handle": "composio", "tool_binding": "twitter-v1", "config": {"resolved_user_id": "1"}},
    )
    assert spec.target_slug == "composio"
    assert spec.template_context()["config"]["resolved_user_id"] == "1"


def test_rss_and_cursor_stop_at_last_seen():
    xml = b'''<rss><channel><item><guid>2</guid><title>New</title><link>https://example.com/2</link><pubDate>Fri, 05 Sep 2026 10:00:00 GMT</pubDate><description><![CDATA[<p>Hello</p>]]></description></item><item><guid>1</guid><title>Old</title><link>https://example.com/1</link></item></channel></rss>'''
    collector = RSSCollector(fetcher=lambda url, timeout: xml)
    page = collector.collect(channel(), {"last_external_id": "1"})
    assert [item.external_id for item in page.items] == ["2"]
    assert page.items[0].content_text == "Hello"
    assert page.next_cursor == {"last_external_id": "2"}


def test_web_diff_emits_only_changed_page():
    response = (b"<html><head><title>Price</title></head><body><main>$10</main></body></html>", {"Content-Type": "text/html; charset=utf-8"})
    collector = WebDiffCollector(HTTPCollector(fetcher=lambda url, timeout: response))
    first = collector.collect(channel("web_diff", "https://example.com/pricing"))
    second = collector.collect(channel("web_diff", "https://example.com/pricing"), first.next_cursor)
    assert len(first.items) == 1
    assert first.metadata["changed"] is True
    assert second.items == ()
    assert second.metadata["changed"] is False


class Fail:
    def collect(self, channel, cursor=None):
        raise RuntimeError("down")


class Succeed:
    def collect(self, channel, cursor=None):
        return CollectionPage.of([], metadata={"source": channel.url})


def test_router_uses_declared_fallback_only():
    routed = CollectorRouter({"mcp": Fail(), "rss": Succeed()}).collect(
        channel("twitter", "https://example.com"),
        route=[RouteStep("mcp"), RouteStep("rss", {"url": "https://example.com/rss"})],
    )
    assert routed.metadata["collector_type"] == "rss"
    assert routed.metadata["source"] == "https://example.com/rss"


class AuthFail:
    def collect(self, channel, cursor=None):
        raise MCPAuthenticationError("login required")


def test_router_does_not_hide_auth_failure_with_fallback():
    with pytest.raises(MCPAuthenticationError):
        CollectorRouter({"mcp": AuthFail(), "rss": Succeed()}).collect(
            channel("twitter"), route=[RouteStep("mcp"), RouteStep("rss")]
        )


def test_health_transitions_are_queryable():
    health = ChannelHealth()
    assert health.state == "unknown"
    health = health.failure(RuntimeError("one"), at="2026-09-05T00:00:00Z")
    assert health.state == "degraded"
    health = health.failure(RuntimeError("two"), at="2026-09-05T00:01:00Z")
    health = health.failure(RuntimeError("three"), at="2026-09-05T00:02:00Z")
    assert health.state == "failing"
    health = health.success(at="2026-09-05T00:03:00Z")
    assert health.state == "healthy"
    assert health.consecutive_failures == 0


def test_cursor_advances_only_after_explicit_commit():
    cursor = CursorCheckpoint({"next": "old"})
    cursor.stage({"next": "new"})
    assert cursor.committed == {"next": "old"}
    assert cursor.rollback() == {"next": "old"}
    cursor.stage({"next": "new"})
    assert cursor.commit() == {"next": "new"}
