"""Free, unauthenticated collector for the official MCP Registry API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from intelligence.normalize import NormalizedItem

from .base import ChannelSpec, CollectionPage


class MCPRegistryCollector:
    def __init__(self, *, timeout: float = 30.0, fetcher: Callable[[str, float], bytes] | None = None):
        self.timeout = timeout
        self.fetcher = fetcher or self._fetch

    @staticmethod
    def _fetch(url: str, timeout: float) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "personal-intelligence/0.1"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()

    def collect(self, channel: ChannelSpec, cursor: Mapping[str, Any] | None = None) -> CollectionPage:
        if not channel.url:
            raise ValueError("MCP Registry channel requires an API URL")
        started = datetime.now(timezone.utc)
        since = str((cursor or {}).get("registry_updated_since") or (
            started - timedelta(hours=int(channel.config.get("initial_lookback_hours", 24)))
        ).isoformat())
        limit = min(max(int(channel.config.get("page_limit", 100)), 1), 100)
        max_pages = min(max(int(channel.config.get("max_pages", 10)), 1), 100)
        items, continuation = [], (cursor or {}).get("registry_cursor")
        for _ in range(max_pages):
            query = {"limit": limit, "updated_since": since}
            if continuation:
                query["cursor"] = continuation
            url = channel.url + ("&" if "?" in channel.url else "?") + urllib.parse.urlencode(query)
            for attempt in range(3):
                try:
                    payload = json.loads(self.fetcher(url, self.timeout))
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code < 500 or attempt == 2:
                        raise
                    time.sleep(0.25 * (attempt + 1))
            rows = payload.get("servers", [])
            if not isinstance(rows, list):
                raise ValueError("MCP Registry response servers must be an array")
            for row in rows:
                server = row.get("server", {}) if isinstance(row, Mapping) else {}
                official = row.get("_meta", {}).get("io.modelcontextprotocol.registry/official", {}) if isinstance(row, Mapping) else {}
                name, version = str(server.get("name", "")), str(server.get("version", ""))
                if not name or not version:
                    continue
                updated = official.get("updatedAt") or official.get("publishedAt")
                items.append(NormalizedItem(
                    external_id=f"{name}:{version}", target_slug=channel.target_slug,
                    channel_slug=channel.channel_slug,
                    url="https://registry.modelcontextprotocol.io/?" + urllib.parse.urlencode({"q": name}),
                    title=str(server.get("title") or name), author=None, published_at=updated,
                    content_text=json.dumps(server, ensure_ascii=False, sort_keys=True), language=None,
                    metadata={"platform": "mcp_registry", "content_complete": True,
                              "registry_status": official.get("status"), "registry_updated_at": updated},
                    fetched_at=started,
                ))
            metadata = payload.get("metadata", {})
            continuation = metadata.get("nextCursor") if isinstance(metadata, Mapping) else None
            if not continuation:
                break
        next_cursor = (
            {"registry_updated_since": since, "registry_cursor": continuation}
            if continuation else {"registry_updated_since": started.isoformat()}
        )
        return CollectionPage.of(
            items,
            next_cursor=next_cursor,
            raw_count=len(items),
            metadata={"pages_complete": not bool(continuation), "collector_type": "mcp_registry_api"},
        )
