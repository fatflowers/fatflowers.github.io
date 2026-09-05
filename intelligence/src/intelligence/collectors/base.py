"""Collector contracts independent from storage and scheduling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from intelligence.normalize import NormalizedItem


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    target_slug: str
    channel_slug: str
    channel_type: str
    collector_type: str
    url: str | None = None
    handle: str | None = None
    tool_binding: str | None = None
    config: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_catalog(cls, target: Mapping[str, Any], channel: Mapping[str, Any]) -> "ChannelSpec":
        """Bridge the YAML catalog dictionaries to the collector contract."""

        return cls(
            target_slug=str(target["slug"]),
            channel_slug=str(channel["slug"]),
            channel_type=str(channel["type"]),
            collector_type=str(channel["collector"]),
            url=str(channel["url"]) if channel.get("url") else None,
            handle=str(channel["handle"]) if channel.get("handle") else None,
            tool_binding=str(channel["tool_binding"]) if channel.get("tool_binding") else None,
            config=dict(channel.get("config", {})),
        )

    def template_context(self) -> dict[str, Any]:
        return {
            "target_slug": self.target_slug,
            "channel_slug": self.channel_slug,
            "channel_type": self.channel_type,
            "collector_type": self.collector_type,
            "url": self.url,
            "handle": self.handle,
            "config": dict(self.config),
            **dict(self.config),
        }


@dataclass(frozen=True, slots=True)
class CollectionPage:
    items: tuple[NormalizedItem, ...]
    next_cursor: Mapping[str, Any] = field(default_factory=dict)
    raw_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def of(
        cls,
        items: list[NormalizedItem],
        *,
        next_cursor: Mapping[str, Any] | None = None,
        raw_count: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "CollectionPage":
        return cls(tuple(items), dict(next_cursor or {}), len(items) if raw_count is None else raw_count, dict(metadata or {}))


class Collector(Protocol):
    def collect(self, channel: ChannelSpec, cursor: Mapping[str, Any] | None = None) -> CollectionPage: ...
