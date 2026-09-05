"""Safe catalog mutations used by both the CLI and Multica."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from intelligence.catalog.repository import CatalogError, CatalogRepository


class CatalogService:
    def __init__(self, repository: CatalogRepository) -> None:
        self.repository = repository

    def list_targets(self) -> List[Dict[str, Any]]:
        return self.repository.load_raw()["targets"]

    def show_target(self, slug: str) -> Dict[str, Any]:
        return copy.deepcopy(self._find_target(self.repository.load_raw(), slug))

    def add_target(
        self,
        slug: str,
        name: str,
        target_type: str,
        description: Optional[str] = None,
        priority: str = "normal",
        enabled: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        value = self.repository.load_raw()
        if any(item.get("slug") == slug for item in value["targets"]):
            raise CatalogError("target already exists: %s" % slug)
        target: Dict[str, Any] = {
            "slug": slug,
            "name": name,
            "type": target_type,
            "priority": priority,
            "enabled": enabled,
            "tags": [],
            "channels": [],
        }
        if description:
            target["description"] = description
        value["targets"].append(target)
        return self._commit(value, target, dry_run)

    def update_target(
        self, slug: str, changes: Dict[str, Any], dry_run: bool = False
    ) -> Dict[str, Any]:
        value = self.repository.load_raw()
        target = self._find_target(value, slug)
        before = copy.deepcopy(target)
        target.update({key: item for key, item in changes.items() if item is not None})
        return self._commit(value, {"before": before, "after": target}, dry_run)

    def disable_target(self, slug: str, dry_run: bool = False) -> Dict[str, Any]:
        return self.update_target(slug, {"enabled": False}, dry_run=dry_run)

    def list_channels(self, target_slug: Optional[str] = None) -> List[Dict[str, Any]]:
        value = self.repository.load_raw()
        targets = (
            [self._find_target(value, target_slug)]
            if target_slug
            else value["targets"]
        )
        return [
            dict(channel, target=target["slug"])
            for target in targets
            for channel in target.get("channels", [])
        ]

    def add_channel(
        self,
        target_slug: str,
        slug: str,
        name: str,
        channel_type: str,
        collector: str,
        url: Optional[str] = None,
        handle: Optional[str] = None,
        interval_minutes: int = 60,
        priority: str = "normal",
        tool_binding: Optional[str] = None,
        enabled: bool = False,
        config: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        value = self.repository.load_raw()
        if self._find_channel_optional(value, slug):
            raise CatalogError("channel already exists: %s" % slug)
        target = self._find_target(value, target_slug)
        channel: Dict[str, Any] = {
            "slug": slug,
            "name": name,
            "type": channel_type,
            "collector": collector,
            "interval_minutes": interval_minutes,
            "priority": priority,
            "enabled": enabled,
            "tags": [],
        }
        for key, item in (
            ("url", url),
            ("handle", handle),
            ("tool_binding", tool_binding),
        ):
            if item is not None:
                channel[key] = item
        if config:
            channel["config"] = config
        target["channels"].append(channel)
        return self._commit(value, channel, dry_run)

    def update_channel(
        self, slug: str, changes: Dict[str, Any], dry_run: bool = False
    ) -> Dict[str, Any]:
        value = self.repository.load_raw()
        _, channel = self._find_channel(value, slug)
        before = copy.deepcopy(channel)
        channel.update({key: item for key, item in changes.items() if item is not None})
        return self._commit(value, {"before": before, "after": channel}, dry_run)

    def disable_channel(self, slug: str, dry_run: bool = False) -> Dict[str, Any]:
        return self.update_channel(slug, {"enabled": False}, dry_run=dry_run)

    def list_tags(self) -> List[Dict[str, Any]]:
        return self.repository.load_raw()["tags"]

    def add_tag(
        self,
        slug: str,
        name: str,
        tag_type: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        value = self.repository.load_raw()
        if any(item.get("slug") == slug for item in value["tags"]):
            raise CatalogError("tag already exists: %s" % slug)
        tag = {"slug": slug, "name": name, "type": tag_type}
        value["tags"].append(tag)
        return self._commit(value, tag, dry_run)

    def attach_tag(
        self,
        tag_slug: str,
        target_slug: Optional[str] = None,
        channel_slug: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        value = self.repository.load_raw()
        self._require_tag(value, tag_slug)
        entity = self._tag_entity(value, target_slug, channel_slug)
        tags = entity.setdefault("tags", [])
        if tag_slug not in tags:
            tags.append(tag_slug)
            tags.sort()
        return self._commit(value, entity, dry_run)

    def detach_tag(
        self,
        tag_slug: str,
        target_slug: Optional[str] = None,
        channel_slug: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        value = self.repository.load_raw()
        self._require_tag(value, tag_slug)
        entity = self._tag_entity(value, target_slug, channel_slug)
        entity["tags"] = [item for item in entity.get("tags", []) if item != tag_slug]
        return self._commit(value, entity, dry_run)

    def _commit(
        self, value: Dict[str, Any], result: Dict[str, Any], dry_run: bool
    ) -> Dict[str, Any]:
        errors = self.repository.validate(value)
        if errors:
            from intelligence.catalog.repository import CatalogValidationError

            raise CatalogValidationError(errors)
        if not dry_run:
            self.repository.save(value)
        return {"dry_run": dry_run, "change": copy.deepcopy(result)}

    @staticmethod
    def _find_target(value: Dict[str, Any], slug: str) -> Dict[str, Any]:
        for target in value.get("targets", []):
            if target.get("slug") == slug:
                return target
        raise CatalogError("target not found: %s" % slug)

    @classmethod
    def _find_channel(
        cls, value: Dict[str, Any], slug: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        found = cls._find_channel_optional(value, slug)
        if found:
            return found
        raise CatalogError("channel not found: %s" % slug)

    @staticmethod
    def _find_channel_optional(
        value: Dict[str, Any], slug: str
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        for target in value.get("targets", []):
            for channel in target.get("channels", []):
                if channel.get("slug") == slug:
                    return target, channel
        return None

    @staticmethod
    def _require_tag(value: Dict[str, Any], slug: str) -> None:
        if not any(tag.get("slug") == slug for tag in value.get("tags", [])):
            raise CatalogError("tag not found: %s" % slug)

    @classmethod
    def _tag_entity(
        cls,
        value: Dict[str, Any],
        target_slug: Optional[str],
        channel_slug: Optional[str],
    ) -> Dict[str, Any]:
        if bool(target_slug) == bool(channel_slug):
            raise CatalogError("provide exactly one of --target or --channel")
        if target_slug:
            return cls._find_target(value, target_slug)
        return cls._find_channel(value, str(channel_slug))[1]
