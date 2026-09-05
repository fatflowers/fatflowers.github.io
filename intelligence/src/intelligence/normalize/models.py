"""Normalized item contract shared by every collector."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

from .text import canonicalize_url, normalize_text


def _iso_utc(value: str | datetime | int | float | None) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        candidate = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            # RFC 2822 is common in RSS and social API responses.
            from email.utils import parsedate_to_datetime

            parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class NormalizedItem:
    external_id: str | None
    target_slug: str
    channel_slug: str
    url: str
    title: str
    author: str | None
    published_at: str | None
    content_text: str
    language: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    canonical_url: str = ""
    fetched_at: str | None = None

    def __post_init__(self) -> None:
        if not self.target_slug.strip() or not self.channel_slug.strip():
            raise ValueError("target_slug and channel_slug are required")
        url = canonicalize_url(self.url)
        if not url:
            raise ValueError("url is required")
        title = normalize_text(self.title)
        content = normalize_text(self.content_text)
        if not title and not content:
            raise ValueError("at least one of title or content_text is required")
        object.__setattr__(self, "url", self.url.strip())
        object.__setattr__(self, "canonical_url", canonicalize_url(self.canonical_url) or url)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "content_text", content)
        object.__setattr__(self, "author", normalize_text(self.author) if self.author else None)
        object.__setattr__(self, "external_id", str(self.external_id) if self.external_id is not None else None)
        object.__setattr__(self, "published_at", _iso_utc(self.published_at))
        object.__setattr__(self, "fetched_at", _iso_utc(self.fetched_at))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def with_fetched_at(self, value: str | datetime) -> "NormalizedItem":
        return replace(self, fetched_at=_iso_utc(value))

    def as_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "target_slug": self.target_slug,
            "channel_slug": self.channel_slug,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "author": self.author,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "content_text": self.content_text,
            "language": self.language,
            "metadata": dict(self.metadata),
        }
