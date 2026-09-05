"""Browser fallback boundary; concrete automation is supplied by the runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .base import ChannelSpec, CollectionPage


class BrowserPageFetcher(Protocol):
    def fetch_public_page(self, url: str, *, wait_for: str | None = None) -> Mapping[str, Any]: ...


class BrowserCollector:
    def __init__(self, fetcher: BrowserPageFetcher):
        self.fetcher = fetcher

    def collect(self, channel: ChannelSpec, cursor: Mapping[str, Any] | None = None) -> CollectionPage:
        if not channel.url:
            raise ValueError("browser fallback requires a public URL")
        result = self.fetcher.fetch_public_page(channel.url, wait_for=channel.config.get("wait_for"))
        # Keep browser integration narrow: reuse the same document contract as
        # Firecrawl instead of exposing browser state or private session data.
        from .adapters import firecrawl_document_v1

        items, next_cursor = firecrawl_document_v1(
            {
                "markdown": result.get("markdown") or result.get("text"),
                "metadata": {
                    "sourceURL": channel.url,
                    "canonicalUrl": result.get("canonical_url"),
                    "title": result.get("title"),
                    "language": result.get("language"),
                },
            },
            channel,
        )
        return CollectionPage.of(items, next_cursor=next_cursor, metadata={"fallback": "browser"})
