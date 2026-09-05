"""Collectors, adapters and deterministic fallback routing."""

from .base import ChannelSpec, CollectionPage, Collector
from .cursor import CursorCheckpoint
from .github import GitHubCollector, GitHubRateLimitError
from .health import ChannelHealth
from .router import CollectionRouteError, CollectorRouter, RouteStep

__all__ = [
    "ChannelHealth",
    "ChannelSpec",
    "CollectionPage",
    "CollectionRouteError",
    "Collector",
    "CollectorRouter",
    "CursorCheckpoint",
    "GitHubCollector",
    "GitHubRateLimitError",
    "RouteStep",
]
