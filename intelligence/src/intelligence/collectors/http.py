"""HTTP page and deterministic web-diff fallback collectors."""

from __future__ import annotations

import html
import re
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable

from intelligence.normalize import NormalizedItem, content_hash

from .base import ChannelSpec, CollectionPage


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.text: list[str] = []
        self.canonical = ""
        self._ignored = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style", "svg", "noscript", "template"}:
            self._ignored += 1
        if tag == "title":
            self._in_title = True
        if tag == "link" and attributes.get("rel") == "canonical" and attributes.get("href"):
            self.canonical = str(attributes["href"])
        if tag in {"p", "div", "article", "section", "li", "h1", "h2", "h3", "br"}:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "noscript", "template"} and self._ignored:
            self._ignored -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        if self._in_title:
            self.title.append(data)
        self.text.append(data)


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
        parser = _DocumentParser()
        parser.feed(document)
        text = html.unescape(" ".join(parser.text))
        item = NormalizedItem(
            external_id=headers.get("ETag") or headers.get("Last-Modified"),
            target_slug=channel.target_slug,
            channel_slug=channel.channel_slug,
            url=channel.url,
            canonical_url=parser.canonical or channel.url,
            title=" ".join(parser.title) or channel.channel_slug,
            author=None,
            published_at=headers.get("Last-Modified"),
            content_text=text,
            language=None,
            metadata={"platform": "web", "headers": {k: v for k, v in headers.items() if k.lower() in {"etag", "last-modified", "content-type"}}},
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
        next_cursor = {**dict(cursor or {}), "content_hash": digest}
        if (cursor or {}).get("content_hash") == digest:
            return CollectionPage.of([], next_cursor=next_cursor, raw_count=1, metadata={"changed": False})
        return CollectionPage.of(list(page.items), next_cursor=next_cursor, raw_count=1, metadata={"changed": True})
