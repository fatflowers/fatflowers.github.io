"""Typed domain models for the Git-backed catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional
from uuid import UUID, uuid5


CATALOG_NAMESPACE = UUID("d7fd0429-53d1-4a72-8fd1-196af9cbe596")


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


def stable_id(kind: str, slug: str) -> str:
    """Return a stable identifier without storing generated IDs in YAML."""

    return str(uuid5(CATALOG_NAMESPACE, "%s:%s" % (kind, slug)))


@dataclass(frozen=True)
class Tag:
    slug: str
    name: str
    tag_type: str

    @property
    def id(self) -> str:
        return stable_id("tag", self.slug)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Tag":
        return cls(
            slug=str(value["slug"]),
            name=str(value["name"]),
            tag_type=str(value.get("type", "topic")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"slug": self.slug, "name": self.name, "type": self.tag_type}

    def to_sync_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "tag_type": self.tag_type,
        }


@dataclass(frozen=True)
class Channel:
    slug: str
    name: str
    channel_type: str
    collector_type: str
    interval_minutes: int
    priority: Priority = Priority.NORMAL
    enabled: bool = True
    tier: str = "core"
    url: Optional[str] = None
    handle: Optional[str] = None
    tool_binding: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)
    fallbacks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def id(self) -> str:
        return stable_id("channel", self.slug)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Channel":
        return cls(
            slug=str(value["slug"]),
            name=str(value["name"]),
            channel_type=str(value["type"]),
            collector_type=str(value["collector"]),
            interval_minutes=int(value.get("interval_minutes", 60)),
            priority=Priority(str(value.get("priority", "normal"))),
            enabled=bool(value.get("enabled", True)),
            tier=str(value.get("tier", "core")),
            url=value.get("url"),
            handle=value.get("handle"),
            tool_binding=value.get("tool_binding"),
            tags=list(value.get("tags", [])),
            config=dict(value.get("config", {})),
            fallbacks=[dict(item) for item in value.get("fallbacks", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "slug": self.slug,
            "name": self.name,
            "type": self.channel_type,
            "collector": self.collector_type,
            "interval_minutes": self.interval_minutes,
            "priority": self.priority.value,
            "enabled": self.enabled,
            "tier": self.tier,
            "tags": list(self.tags),
        }
        for key, value in (
            ("url", self.url),
            ("handle", self.handle),
            ("tool_binding", self.tool_binding),
        ):
            if value is not None:
                result[key] = value
        if self.config:
            result["config"] = dict(self.config)
        if self.fallbacks:
            result["fallbacks"] = [dict(item) for item in self.fallbacks]
        return result

    def to_sync_dict(self, target_slug: str) -> Dict[str, Any]:
        config = dict(self.config)
        config["tier"] = self.tier
        if self.fallbacks:
            config["fallbacks"] = [dict(item) for item in self.fallbacks]
        result: Dict[str, Any] = {
            "id": self.id,
            "target_id": stable_id("target", target_slug),
            "slug": self.slug,
            "name": self.name,
            "channel_type": self.channel_type,
            "collector_type": self.collector_type,
            "interval_minutes": self.interval_minutes,
            "priority": self.priority.value,
            "enabled": self.enabled,
            "config": config,
        }
        for key, value in (
            ("url", self.url),
            ("handle", self.handle),
            ("tool_binding", self.tool_binding),
        ):
            if value is not None:
                result[key] = value
        return result


@dataclass(frozen=True)
class Target:
    slug: str
    name: str
    target_type: str
    priority: Priority = Priority.NORMAL
    enabled: bool = True
    description: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    channels: List[Channel] = field(default_factory=list)

    @property
    def id(self) -> str:
        return stable_id("target", self.slug)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Target":
        return cls(
            slug=str(value["slug"]),
            name=str(value["name"]),
            target_type=str(value["type"]),
            priority=Priority(str(value.get("priority", "normal"))),
            enabled=bool(value.get("enabled", True)),
            description=value.get("description"),
            tags=list(value.get("tags", [])),
            channels=[Channel.from_dict(item) for item in value.get("channels", [])],
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "slug": self.slug,
            "name": self.name,
            "type": self.target_type,
            "priority": self.priority.value,
            "enabled": self.enabled,
            "tags": list(self.tags),
            "channels": [channel.to_dict() for channel in self.channels],
        }
        if self.description is not None:
            result["description"] = self.description
        return result

    def to_sync_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "target_type": self.target_type,
            "priority": self.priority.value,
            "enabled": self.enabled,
        }
        if self.description is not None:
            result["description"] = self.description
        return result


@dataclass(frozen=True)
class Catalog:
    version: int
    tags: List[Tag]
    targets: List[Target]
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Catalog":
        return cls(
            version=int(value.get("version", 1)),
            tags=[Tag.from_dict(item) for item in value.get("tags", [])],
            targets=[Target.from_dict(item) for item in value.get("targets", [])],
            metadata=dict(value.get("metadata", {})),
        )

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "version": self.version,
            "tags": [tag.to_dict() for tag in self.tags],
            "targets": [target.to_dict() for target in self.targets],
        }
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result

    def to_sync_dict(self) -> Dict[str, Any]:
        tag_ids = {tag.slug: tag.id for tag in self.tags}
        result: Dict[str, Any] = {
            "version": self.version,
            "mode": "replace",
            "tags": [tag.to_sync_dict() for tag in self.tags],
            "targets": [target.to_sync_dict() for target in self.targets],
            "channels": [
                channel.to_sync_dict(target.slug)
                for target in self.targets
                for channel in target.channels
            ],
            "target_tags": [
                {"target_id": target.id, "tag_id": tag_ids[tag_slug]}
                for target in self.targets
                for tag_slug in target.tags
            ],
            "channel_tags": [
                {"channel_id": channel.id, "tag_id": tag_ids[tag_slug]}
                for target in self.targets
                for channel in target.channels
                for tag_slug in channel.tags
            ],
        }
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result
