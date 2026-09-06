import json
from urllib.parse import parse_qs, urlsplit

from intelligence.collectors import ChannelSpec, MCPRegistryCollector


def test_registry_collector_uses_free_incremental_api_and_cursor_pagination():
    calls = []
    pages = [
        {"servers": [{"server": {"name": "io.example/one", "title": "One", "version": "1.0.0"},
                      "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active", "updatedAt": "2026-09-06T01:00:00Z"}}}],
         "metadata": {"nextCursor": "next"}},
        {"servers": [{"server": {"name": "io.example/two", "version": "2.0.0"},
                      "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active", "publishedAt": "2026-09-06T02:00:00Z"}}}],
         "metadata": {}},
    ]
    def fetch(url, timeout):
        calls.append(url)
        return json.dumps(pages[len(calls) - 1]).encode()
    channel = ChannelSpec("mcp", "registry", "documentation", "mcp_registry_api",
                          url="https://registry.modelcontextprotocol.io/v0.1/servers",
                          config={"page_limit": 100})
    page = MCPRegistryCollector(fetcher=fetch).collect(
        channel, {"registry_updated_since": "2026-09-06T00:00:00Z"}
    )
    assert [item.external_id for item in page.items] == ["io.example/one:1.0.0", "io.example/two:2.0.0"]
    assert parse_qs(urlsplit(calls[0]).query)["updated_since"] == ["2026-09-06T00:00:00Z"]
    assert parse_qs(urlsplit(calls[1]).query)["cursor"] == ["next"]
    assert page.metadata["pages_complete"] is True
    assert "registry_updated_since" in page.next_cursor


def test_registry_collector_persists_cursor_when_page_budget_is_exhausted():
    payload = {"servers": [], "metadata": {"nextCursor": "resume-here"}}
    collector = MCPRegistryCollector(fetcher=lambda *_: json.dumps(payload).encode())
    channel = ChannelSpec("mcp", "registry", "documentation", "mcp_registry_api",
                          url="https://registry.modelcontextprotocol.io/v0.1/servers",
                          config={"max_pages": 1})
    page = collector.collect(channel, {"registry_updated_since": "2026-09-06T00:00:00Z"})
    assert page.next_cursor == {
        "registry_updated_since": "2026-09-06T00:00:00Z",
        "registry_cursor": "resume-here",
    }
    assert page.metadata["pages_complete"] is False
