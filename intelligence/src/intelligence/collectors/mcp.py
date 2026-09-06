"""Collector that invokes only a channel's pre-approved MCP binding."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from intelligence.mcp import MCPClient, MCPToolRegistry

from .adapters import Adapter, get_adapter, _cursor
from .base import ChannelSpec, CollectionPage
from .retry import RetryPolicy


class MCPCollector:
    def __init__(
        self,
        client: MCPClient,
        registry: MCPToolRegistry,
        *,
        adapters: Mapping[str, Adapter] | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.adapters = adapters
        self.retry_policy = retry_policy or RetryPolicy()

    def collect(
        self,
        channel: ChannelSpec,
        cursor: Mapping[str, Any] | None = None,
        *,
        scheduled: bool = True,
    ) -> CollectionPage:
        if not channel.tool_binding:
            raise ValueError(f"channel {channel.channel_slug!r} has no fixed MCP binding")
        binding = self.registry.get(channel.tool_binding)
        binding.assert_runnable(channel.channel_type, scheduled=scheduled)
        timeline = binding.output_adapter == "twitter_posts_v1" and channel.channel_type == "twitter"
        arguments = binding.render_arguments(channel.template_context(), {} if timeline else cursor)
        if timeline:
            arguments["cursor"] = ""

        def invoke() -> Any:
            return self.client.call_tool(binding.tool_name, arguments)

        payload = self.retry_policy.run(
            invoke,
            is_retryable=lambda error: bool(getattr(error, "retryable", False)),
        )
        adapter = get_adapter(binding.output_adapter, self.adapters)
        items, next_cursor = adapter(payload, channel)
        raw_count = len(items)
        pages = 1
        continuation = _cursor(payload, "next_cursor", "nextCursor", "cursor") if timeline else {}
        previous = str((cursor or {}).get("last_external_id") or "")
        seen = {item.external_id or item.canonical_url for item in items}
        reached = bool(previous and any(item.external_id == previous for item in items))
        tokens: set[str] = set()
        limit = max(1, min(10, int(channel.config.get("max_pages_per_run", 3))))
        while timeline and continuation.get("next") and not reached and pages < limit:
            token = str(continuation["next"])
            if token in tokens:
                break
            tokens.add(token)
            arguments["cursor"] = token
            payload = self.retry_policy.run(invoke, is_retryable=lambda error: bool(getattr(error, "retryable", False)))
            page_items, _ = adapter(payload, channel)
            raw_count += len(page_items)
            pages += 1
            reached = bool(previous and any(item.external_id == previous for item in page_items))
            for item in page_items:
                key = item.external_id or item.canonical_url
                if key not in seen:
                    items.append(item)
                    seen.add(key)
            continuation = _cursor(payload, "next_cursor", "nextCursor", "cursor")
        return CollectionPage.of(
            items,
            next_cursor=next_cursor,
            raw_count=raw_count,
            metadata={"binding": binding.alias, "tool_name": binding.tool_name,
                      "pages_fetched": pages,
                      "pagination": {"within_run_only": True, "next": continuation.get("next"),
                                     "bounded": bool(continuation and not reached and pages >= limit)} if timeline else {}},
        )
