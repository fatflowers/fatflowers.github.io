"""Output adapters for reviewed AIsa MCP bindings."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Callable

from intelligence.normalize import NormalizedItem
from intelligence.normalize.text import normalize_text

from .base import ChannelSpec

Adapter = Callable[[Any, ChannelSpec], tuple[list[NormalizedItem], dict[str, Any]]]


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        if "structuredContent" in payload:
            return payload["structuredContent"]
        content = payload.get("content")
        if isinstance(content, list):
            for entry in content:
                if isinstance(entry, Mapping) and entry.get("type") == "text":
                    text = entry.get("text", "")
                    try:
                        return json.loads(str(text))
                    except json.JSONDecodeError:
                        continue
    return payload


def _first(mapping: Mapping[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        value: Any = mapping
        for part in path.split("."):
            if not isinstance(value, Mapping) or part not in value:
                value = None
                break
            value = value[part]
        if value not in (None, ""):
            return value
    return default


def _records(payload: Any, keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
    payload = _unwrap(payload)
    if isinstance(payload, list):
        return [entry for entry in payload if isinstance(entry, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in keys:
        value = _first(payload, key)
        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, Mapping)]
    # Some routers wrap provider JSON in data.result repeatedly.
    for key in ("data", "result", "results", "response"):
        nested = payload.get(key)
        if nested is not None and nested is not payload:
            found = _records(nested, keys)
            if found:
                return found
    return []


def _cursor(payload: Any, *names: str) -> dict[str, Any]:
    root = _unwrap(payload)
    if not isinstance(root, Mapping):
        return {}
    for container in (root, root.get("meta"), root.get("pagination"), root.get("data")):
        if not isinstance(container, Mapping):
            continue
        for name in names:
            value = container.get(name)
            if value not in (None, ""):
                return {"next": value}
    return {}


def twitter_posts_v1(payload: Any, channel: ChannelSpec) -> tuple[list[NormalizedItem], dict[str, Any]]:
    rows = _records(payload, ("tweets", "data.tweets", "items", "statuses", "data"))
    continuation = _cursor(payload, "next_cursor", "nextCursor", "cursor")
    items: list[NormalizedItem] = []
    for row in rows:
        is_reply = bool(_first(row, "in_reply_to_status_id", "in_reply_to_status_id_str", "inReplyToId", "inReplyToStatusId", "legacy.in_reply_to_status_id_str", "isReply", default=False))
        if is_reply and not channel.config.get("include_replies", False):
            continue
        tweet_id = _first(row, "id", "id_str", "tweet_id", "rest_id")
        user = _first(row, "user.screen_name", "author.userName", "author.username", "username", default=channel.handle)
        text = str(_first(row, "note_tweet.note_tweet_results.result.text", "full_text", "legacy.full_text", "text", "content", default=""))
        url = _first(row, "url", "tweet_url", "link")
        if not url and tweet_id:
            url = f"https://x.com/{user or 'i'}/status/{tweet_id}"
        if not url or (not text and not tweet_id):
            continue
        items.append(
            NormalizedItem(
                external_id=str(tweet_id) if tweet_id else None,
                target_slug=channel.target_slug,
                channel_slug=channel.channel_slug,
                url=str(url),
                title=" ".join(normalize_text(text).split())[:160],
                author=str(user) if user else None,
                published_at=_first(row, "created_at", "createdAt", "date", "timestamp"),
                content_text=text,
                language=_first(row, "lang", "language"),
                metadata={
                    "platform": "twitter", "raw": dict(row), "is_reply": is_reply,
                    "source_content_kind": "truncated_social_post" if row.get("truncated") else "complete_social_post",
                    "pagination": {"within_run_only": True, "next": continuation.get("next")},
                },
            )
        )
    # A provider continuation points backwards. Persist a watermark, never the
    # older-page token: every recurring poll must begin at the newest page.
    newest = next((str(_first(row, "id", "id_str", "tweet_id", "rest_id")) for row in rows
                   if _first(row, "id", "id_str", "tweet_id", "rest_id")), None)
    return items, {"last_external_id": newest} if newest else {}


def reddit_posts_v1(payload: Any, channel: ChannelSpec) -> tuple[list[NormalizedItem], dict[str, Any]]:
    rows = _records(payload, ("posts", "children", "data.children", "items", "data"))
    items: list[NormalizedItem] = []
    for original in rows:
        row = original.get("data", original)
        if not isinstance(row, Mapping):
            continue
        post_id = _first(row, "id", "name")
        permalink = _first(row, "permalink", "url")
        if permalink and str(permalink).startswith("/"):
            permalink = f"https://www.reddit.com{permalink}"
        if not permalink and post_id:
            permalink = f"https://www.reddit.com/comments/{post_id}"
        title = str(_first(row, "title", default=""))
        body = str(_first(row, "selftext", "body", "text", default=""))
        if not permalink or not (title or body):
            continue
        published = _first(row, "created_at", "createdAt", "published_at")
        if not published:
            epoch = _first(row, "created_utc", "created")
            if isinstance(epoch, (int, float)):
                from datetime import datetime, timezone

                published = datetime.fromtimestamp(epoch, timezone.utc)
        items.append(
            NormalizedItem(
                external_id=str(post_id) if post_id else None,
                target_slug=channel.target_slug,
                channel_slug=channel.channel_slug,
                url=str(permalink),
                title=title,
                author=str(_first(row, "author.name", "author", default="")) or None,
                published_at=published,
                content_text=body or title,
                language=_first(row, "lang", "language"),
                metadata={"platform": "reddit", "raw": dict(row)},
            )
        )
    return items, _cursor(payload, "after", "cursor", "next_cursor", "nextCursor")


def firecrawl_document_v1(payload: Any, channel: ChannelSpec) -> tuple[list[NormalizedItem], dict[str, Any]]:
    root = _unwrap(payload)
    while isinstance(root, Mapping) and isinstance(root.get("data"), Mapping):
        root = root["data"]
    if not isinstance(root, Mapping):
        return [], {}
    metadata = root.get("metadata", {})
    if not isinstance(metadata, Mapping):
        metadata = {}
    markdown = str(_first(root, "markdown", "content", "text", default=""))
    url = _first(metadata, "sourceURL", "sourceUrl", "url", "canonicalUrl", default=channel.url)
    title = str(_first(metadata, "title", "ogTitle", default=""))
    if not url or not (markdown or title):
        return [], {}
    item = NormalizedItem(
        external_id=None,
        target_slug=channel.target_slug,
        channel_slug=channel.channel_slug,
        url=str(url),
        canonical_url=str(_first(metadata, "canonicalUrl", default=url)),
        title=title or channel.channel_slug,
        author=str(_first(metadata, "author", default="")) or None,
        published_at=_first(metadata, "publishedTime", "published_at", "date"),
        content_text=markdown or title,
        language=_first(metadata, "language", "lang"),
        metadata={"platform": "web", "firecrawl": dict(metadata)},
    )
    return [item], {}


def firecrawl_urls_v1(payload: Any, channel: ChannelSpec) -> tuple[list[NormalizedItem], dict[str, Any]]:
    """Adapt Firecrawl map output as URL-discovery items, never as page content."""

    root = _unwrap(payload)
    if isinstance(root, Mapping):
        candidates = _first(root, "links", "urls", "data.links", "data", default=[])
    else:
        candidates = root
    if not isinstance(candidates, list):
        return [], {}
    items: list[NormalizedItem] = []
    for candidate in candidates:
        if isinstance(candidate, str):
            url, title = candidate, candidate
            raw: Mapping[str, Any] = {"url": candidate}
        elif isinstance(candidate, Mapping):
            raw = candidate
            url = str(_first(candidate, "url", "link", "sourceURL", default=""))
            title = str(_first(candidate, "title", "name", default=url))
        else:
            continue
        if not url:
            continue
        items.append(
            NormalizedItem(
                external_id=None,
                target_slug=channel.target_slug,
                channel_slug=channel.channel_slug,
                url=url,
                title=title,
                author=None,
                published_at=None,
                content_text=title,
                metadata={"platform": "web", "discovery_only": True, "raw": dict(raw)},
            )
        )
    return items, {}


DEFAULT_ADAPTERS: Mapping[str, Adapter] = {
    "twitter_posts_v1": twitter_posts_v1,
    "reddit_posts_v1": reddit_posts_v1,
    "firecrawl_document_v1": firecrawl_document_v1,
    "firecrawl_urls_v1": firecrawl_urls_v1,
}


def get_adapter(name: str, adapters: Mapping[str, Adapter] | None = None) -> Adapter:
    try:
        return (adapters or DEFAULT_ADAPTERS)[name]
    except KeyError as exc:
        raise ValueError(f"unknown MCP output adapter: {name}") from exc
