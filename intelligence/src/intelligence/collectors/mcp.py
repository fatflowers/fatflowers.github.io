"""Collector that invokes only a channel's pre-approved MCP binding."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from intelligence.mcp import MCPClient, MCPToolRegistry

from .adapters import Adapter, get_adapter
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
        arguments = binding.render_arguments(channel.template_context(), cursor)

        def invoke() -> Any:
            return self.client.call_tool(binding.tool_name, arguments)

        payload = self.retry_policy.run(
            invoke,
            is_retryable=lambda error: bool(getattr(error, "retryable", False)),
        )
        adapter = get_adapter(binding.output_adapter, self.adapters)
        items, next_cursor = adapter(payload, channel)
        return CollectionPage.of(
            items,
            next_cursor=next_cursor,
            raw_count=len(items),
            metadata={"binding": binding.alias, "tool_name": binding.tool_name},
        )
