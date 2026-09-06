"""Dependency-free RSS 2.0 and Atom fallback collector."""

from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

from intelligence.normalize import NormalizedItem

from .base import ChannelSpec, CollectionPage

_TAG = re.compile(r"<[^>]+>")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, *names: str) -> str:
    wanted = set(names)
    for child in element:
        if _local(child.tag) in wanted:
            return "".join(child.itertext()).strip()
    return ""


def _link(element: ET.Element) -> str:
    for child in element:
        if _local(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        relation = child.attrib.get("rel", "alternate")
        if href and relation == "alternate":
            return href
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def _plain_html(value: str) -> str:
    return html.unescape(_TAG.sub(" ", value))


class RSSCollector:
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
            raise ValueError("RSS channel requires a URL")
        feed_url = str(channel.config.get('feed_url') or channel.url)
        if urlsplit(feed_url).hostname != urlsplit(channel.url).hostname or urlsplit(feed_url).scheme not in {'https', 'http'}:
            raise ValueError('RSS feed override must be a same-host public HTTP(S) URL')
        root = ET.fromstring(self.fetcher(feed_url, self.timeout))
        required_categories = {str(value).casefold() for value in channel.config.get('include_categories', [])}
        entries = [node for node in root.iter() if _local(node.tag) in {"item", "entry"}]
        seen = str((cursor or {}).get("last_external_id", ""))
        items: list[NormalizedItem] = []
        newest_id = ""
        for entry in entries:
            external_id = _child_text(entry, "guid", "id") or _link(entry)
            if not newest_id:
                newest_id = external_id
            if seen and external_id == seen:
                break
            categories = [_child_text(entry, 'category')] if not required_categories else [
                (node.attrib.get('term') or ''.join(node.itertext())).strip()
                for node in entry if _local(node.tag) == 'category']
            if required_categories and not required_categories.intersection(value.casefold() for value in categories):
                continue
            title = _child_text(entry, "title")
            content = _child_text(entry, "content", "encoded", "description", "summary")
            url = _link(entry) or external_id
            if not url or not (title or content):
                continue
            items.append(
                NormalizedItem(
                    external_id=external_id or None,
                    target_slug=channel.target_slug,
                    channel_slug=channel.channel_slug,
                    url=url,
                    title=title,
                    author=_child_text(entry, "author", "creator") or None,
                    published_at=_child_text(entry, "published", "updated", "pubDate") or None,
                    content_text=_plain_html(content or title),
                    language=None,
                    metadata={"platform": "rss", "categories": categories, "feed_url": feed_url},
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        next_cursor = {"last_external_id": newest_id} if newest_id else dict(cursor or {})
        return CollectionPage.of(items, next_cursor=next_cursor, raw_count=len(entries))
