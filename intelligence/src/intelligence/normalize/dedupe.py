"""Stable hashes and in-memory duplicate detection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .models import NormalizedItem
from .text import normalize_text


def content_hash(item: NormalizedItem) -> str:
    material = "\n".join(
        (
            normalize_text(item.title).casefold(),
            normalize_text(item.author).casefold(),
            normalize_text(item.content_text),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def dedupe_key(item: NormalizedItem) -> tuple[str, str]:
    """Apply the design's external-id, canonical URL, then content-hash priority."""

    if item.external_id:
        return ("external_id", f"{item.channel_slug}:{item.external_id}")
    if item.canonical_url:
        return ("canonical_url", item.canonical_url)
    return ("content_hash", f"{item.channel_slug}:{content_hash(item)}")


@dataclass(slots=True)
class DedupeIndex:
    _keys: set[tuple[str, str]] = field(default_factory=set)

    def add(self, item: NormalizedItem) -> bool:
        """Return True only when the item was not previously present."""

        key = dedupe_key(item)
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    def unique(self, items: list[NormalizedItem]) -> list[NormalizedItem]:
        return [item for item in items if self.add(item)]
