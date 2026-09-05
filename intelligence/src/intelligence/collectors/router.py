"""Fixed primary/fallback routing without runtime tool discovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from intelligence.mcp.errors import MCPAuthenticationError, MCPContractError

from .base import ChannelSpec, CollectionPage, Collector


@dataclass(frozen=True, slots=True)
class RouteStep:
    collector_type: str
    overrides: Mapping[str, Any] | None = None


class CollectionRouteError(RuntimeError):
    def __init__(self, channel_slug: str, errors: list[tuple[str, Exception]]):
        self.channel_slug = channel_slug
        self.errors = tuple(errors)
        detail = "; ".join(f"{kind}: {error}" for kind, error in errors)
        super().__init__(f"every collector failed for {channel_slug}: {detail}")


class CollectorRouter:
    def __init__(self, collectors: Mapping[str, Collector]):
        self.collectors = dict(collectors)

    def collect(
        self,
        channel: ChannelSpec,
        cursor: Mapping[str, Any] | None = None,
        *,
        route: list[RouteStep] | None = None,
    ) -> CollectionPage:
        steps = route or [RouteStep(channel.collector_type)]
        errors: list[tuple[str, Exception]] = []
        for step in steps:
            try:
                collector = self.collectors[step.collector_type]
            except KeyError as exc:
                errors.append((step.collector_type, ValueError("collector is not registered")))
                continue
            candidate = channel
            if step.overrides:
                updates = dict(step.overrides)
                config = {**dict(channel.config), **dict(updates.pop("config", {}))}
                candidate = replace(channel, **updates, config=config, collector_type=step.collector_type)
            try:
                result = collector.collect(candidate, cursor)
                return CollectionPage(
                    result.items,
                    result.next_cursor,
                    result.raw_count,
                    {**dict(result.metadata), "collector_type": step.collector_type},
                )
            except (MCPAuthenticationError, MCPContractError):
                # Authentication and schema failures require operator action;
                # silently falling back could hide a broken production binding.
                raise
            except Exception as exc:
                errors.append((step.collector_type, exc))
        raise CollectionRouteError(channel.channel_slug, errors)
