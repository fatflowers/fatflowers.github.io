from intelligence.collectors.base import ChannelSpec
from intelligence.collectors.mcp import MCPCollector
from intelligence.collectors.retry import RetryPolicy
from intelligence.mcp.errors import MCPTransientError
from intelligence.mcp.registry import MCPToolRegistry


class Client:
    def __init__(self, response, failures=0):
        self.response = response
        self.failures = failures
        self.calls = []

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if len(self.calls) <= self.failures:
            raise MCPTransientError("try again")
        return self.response


def registry(adapter, channel_type="twitter"):
    return MCPToolRegistry.from_mapping(
        {
            "server": {"name": "aisa", "url": "https://tools.example/mcp"},
            "tools": {
                "fixed-v1": {
                    "status": "verified",
                    "channel_types": [channel_type],
                    "tool_name": "fixed_tool",
                    "input_template": {"userId": "{{ channel.resolved_user_id }}", "cursor": "{{ cursor.next }}"},
                    "output_adapter": adapter,
                }
            },
        }
    )


def test_twitter_adapter_and_cursor_with_retry():
    client = Client(
        {"data": {"tweets": [{"id": "7", "text": "Shipping MCP", "created_at": "2026-09-05T00:00:00Z", "user": {"screen_name": "composio"}}], "cursor": "next"}},
        failures=1,
    )
    channel = ChannelSpec("composio", "composio-twitter", "twitter", "mcp", handle="composio", tool_binding="fixed-v1", config={"resolved_user_id": "100"})
    collector = MCPCollector(client, registry("twitter_posts_v1"), retry_policy=RetryPolicy(max_attempts=2, initial_delay=0))
    page = collector.collect(channel, {"next": "old", "last_external_id": "7"})
    assert len(client.calls) == 2
    assert client.calls[-1] == ("fixed_tool", {"userId": "100", "cursor": ""})
    assert page.items[0].external_id == "7"
    assert page.items[0].url == "https://x.com/composio/status/7"
    assert page.next_cursor == {"last_external_id": "7"}


def test_twitter_paginates_in_run_but_next_poll_starts_newest():
    class Timeline:
        def __init__(self):
            self.cursors = []

        def call_tool(self, name, arguments):
            token = arguments["cursor"]
            self.cursors.append(token)
            return {"tweets": [{"id": "10" if not token else "9", "text": "A complete announcement"}],
                    "next_cursor": "older" if not token else ""}

    client = Timeline()
    spec = ChannelSpec("company", "twitter", "twitter", "mcp", tool_binding="fixed-v1", config={"resolved_user_id": "1"})
    collector = MCPCollector(client, registry("twitter_posts_v1"))
    first = collector.collect(spec, {"next": "stale-backfill"})
    second = collector.collect(spec, first.next_cursor)
    assert client.cursors == ["", "older", ""]
    assert [item.external_id for item in first.items] == ["10", "9"]
    assert second.next_cursor == {"last_external_id": "10"}


def test_twitter_full_text_reply_filter_and_poll_safe_adapter():
    from intelligence.collectors.adapters import twitter_posts_v1
    spec = ChannelSpec("company", "twitter", "twitter", "mcp", config={"include_replies": False})
    items, cursor = twitter_posts_v1({"tweets": [
        {"id": "3", "text": "reply", "isReply": True},
        {"id": "2", "text": "truncated", "full_text": "Complete\nannouncement", "created_at": "2026-09-06T00:00:00Z"},
        {"id": "1", "text": "reply too", "in_reply_to_status_id": "7"},
    ], "next_cursor": "historical"}, spec)
    assert len(items) == 1
    assert items[0].title == "Complete announcement"
    assert items[0].content_text == "Complete\nannouncement"
    assert items[0].metadata["source_content_kind"] == "complete_social_post"
    assert items[0].metadata["pagination"] == {"within_run_only": True, "next": "historical"}
    assert cursor == {"last_external_id": "3"}


def test_reddit_adapter_accepts_listing_shape():
    response = {"data": {"children": [{"data": {"id": "abc", "title": "MCP", "selftext": "News", "permalink": "/r/mcp/comments/abc", "author": "alice", "created_utc": 1788566400}}], "after": "t3_next"}}
    client = Client(response)
    channel = ChannelSpec("mcp", "mcp-reddit", "reddit", "mcp", tool_binding="fixed-v1", config={"resolved_user_id": "unused"})
    page = MCPCollector(client, registry("reddit_posts_v1", "reddit")).collect(channel)
    assert page.items[0].canonical_url == "https://www.reddit.com/r/mcp/comments/abc"
    assert page.next_cursor == {"next": "t3_next"}


def test_firecrawl_adapter_accepts_structured_content():
    client = Client({"structuredContent": {"data": {"markdown": "# Pricing\n$10", "metadata": {"title": "Pricing", "sourceURL": "https://example.com/pricing"}}}})
    channel = ChannelSpec("company", "company-pricing", "web_diff", "mcp", url="https://example.com/pricing", tool_binding="fixed-v1", config={"resolved_user_id": "unused"})
    page = MCPCollector(client, registry("firecrawl_document_v1", "web_diff")).collect(channel)
    assert page.items[0].title == "Pricing"
    assert page.items[0].content_text == "# Pricing\n$10"


def test_firecrawl_map_adapter_marks_discovery_only():
    client = Client({"links": ["https://example.com/a", {"url": "https://example.com/b", "title": "B"}]})
    channel = ChannelSpec("company", "company-docs", "documentation", "mcp", url="https://example.com", tool_binding="fixed-v1", config={"resolved_user_id": "unused"})
    page = MCPCollector(client, registry("firecrawl_urls_v1", "documentation")).collect(channel)
    assert [item.canonical_url for item in page.items] == ["https://example.com/a", "https://example.com/b"]
    assert all(item.metadata["discovery_only"] for item in page.items)
