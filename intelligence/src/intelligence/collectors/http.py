"""HTTP page and deterministic web-diff fallback collectors."""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from intelligence.enrichment import enrich_article
from intelligence.normalize import NormalizedItem, content_hash

from .base import ChannelSpec, CollectionPage


class HTTPCollector:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        fetcher: Callable[[str, float], tuple[bytes, Mapping[str, str]]] | None = None,
    ) -> None:
        self.timeout = timeout
        self.fetcher = fetcher or self._fetch

    @staticmethod
    def _fetch(url: str, timeout: float) -> tuple[bytes, Mapping[str, str]]:
        request = urllib.request.Request(url, headers={"User-Agent": "personal-intelligence/0.1"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(), dict(response.headers.items())

    def collect(self, channel: ChannelSpec, cursor: Mapping[str, Any] | None = None) -> CollectionPage:
        if not channel.url:
            raise ValueError("HTTP channel requires a URL")
        payload, headers = self.fetcher(channel.url, self.timeout)
        charset_match = re.search(r"charset=([^; ]+)", headers.get("Content-Type", ""), re.I)
        charset = charset_match.group(1) if charset_match else "utf-8"
        document = payload.decode(charset, "replace")
        article = enrich_article(channel.url, html=document)
        item = NormalizedItem(
            external_id=headers.get("ETag") or headers.get("Last-Modified"),
            target_slug=channel.target_slug,
            channel_slug=channel.channel_slug,
            url=channel.url,
            canonical_url=article["canonical_url"],
            title=article["title"] or channel.channel_slug,
            author=None,
            published_at=article["published_at"],
            content_text=article["content_text"],
            language=None,
            metadata={"platform": "web", "headers": {k: v for k, v in headers.items() if k.lower() in {"etag", "last-modified", "content-type"}}, **{key: article[key] for key in ("publication_precision", "publication_evidence", "page_kind", "body_provenance", "discovered_links")}},
            fetched_at=datetime.now(timezone.utc),
        )
        return CollectionPage.of([item], metadata={"content_hash": content_hash(item)})


class WebDiffCollector:
    """Emit a page item only when its normalized content hash changed."""

    def __init__(self, page_collector: HTTPCollector):
        self.page_collector = page_collector

    def collect(self, channel: ChannelSpec, cursor: Mapping[str, Any] | None = None) -> CollectionPage:
        page = self.page_collector.collect(channel, cursor)
        if not page.items:
            return page
        digest = content_hash(page.items[0])
        observed_at = datetime.now(timezone.utc).isoformat()
        previous_hash = (cursor or {}).get("content_hash")
        # Keep a bounded excerpt for an auditable difference. Long-page changes
        # outside it are explicitly unverified until complete evidence is read.
        after = page.items[0].content_text[:12000]
        before = (cursor or {}).get("content_excerpt")
        next_cursor = {**dict(cursor or {}), "content_hash": digest, "content_excerpt": after, "observed_at": observed_at}
        if (cursor or {}).get("content_hash") == digest:
            return CollectionPage.of([], next_cursor=next_cursor, raw_count=1, metadata={"changed": False})
        if not previous_hash:
            return CollectionPage.of([], next_cursor=next_cursor, raw_count=1, metadata={"changed": False, "baseline": True})
        diff = {"before_hash": previous_hash, "after_hash": digest, "before_text": before, "after_text": after, "observed_at": observed_at, "previous_observed_at": (cursor or {}).get("observed_at"), "excerpt_changed": before is not None and before != after, "excerpt_limit": 12000}
        item = replace(page.items[0], metadata={**page.items[0].metadata, "changed": True, "baseline": False, "web_diff": diff})
        return CollectionPage.of([item], next_cursor=next_cursor, raw_count=1, metadata={"changed": True, "web_diff": diff})
