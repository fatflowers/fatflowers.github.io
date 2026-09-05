"""Minimal MCP Streamable HTTP client with injectable transport for tests."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol

from .auth import TokenProvider
from .errors import (
    MCPAuthenticationError,
    MCPContractError,
    MCPError,
    MCPRateLimitError,
    MCPToolNotFoundError,
    MCPTransientError,
)


class MCPClient(Protocol):
    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any: ...


class StreamableHTTPMCPClient:
    """Small synchronous MCP client suitable for deterministic collectors.

    It supports JSON and SSE responses, negotiates an MCP session lazily, and
    deliberately leaves browser-based OAuth to a host-provided TokenProvider.
    """

    def __init__(
        self,
        url: str,
        token_provider: TokenProvider,
        *,
        timeout: float = 30.0,
        protocol_version: str = "2025-03-26",
        opener: Any = None,
    ) -> None:
        self.url = url
        self.token_provider = token_provider
        self.timeout = timeout
        self.protocol_version = protocol_version
        self._opener = opener or urllib.request.urlopen
        self._session_id: str | None = None
        self._request_id = 0
        self._lock = threading.Lock()

    def _next_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _request(self, payload: Mapping[str, Any]) -> Any:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token_provider.get_token()}",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        try:
            response = self._opener(request, timeout=self.timeout)
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id
            body = response.read().decode("utf-8")
            content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            self._raise_http(exc.code, exc.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MCPTransientError(f"MCP transport failed: {exc}") from exc
        return self._decode(body, content_type)

    @staticmethod
    def _raise_http(status: int, body: str) -> None:
        message = body[:500] or f"HTTP {status}"
        if status in {401, 403}:
            raise MCPAuthenticationError(message)
        if status == 404:
            raise MCPToolNotFoundError(message)
        if status == 429:
            raise MCPRateLimitError(message)
        if status >= 500:
            raise MCPTransientError(message)
        raise MCPError(message)

    @staticmethod
    def _decode(body: str, content_type: str) -> Any:
        if "text/event-stream" in content_type:
            events: list[Any] = []
            for line in body.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data and data != "[DONE]":
                        events.append(json.loads(data))
            if not events:
                raise MCPContractError("MCP SSE response contained no data event")
            decoded = events[-1]
        else:
            try:
                decoded = json.loads(body)
            except json.JSONDecodeError as exc:
                raise MCPContractError("MCP returned invalid JSON") from exc
        if isinstance(decoded, Mapping) and decoded.get("error"):
            error = decoded["error"]
            code = error.get("code") if isinstance(error, Mapping) else None
            message = error.get("message", str(error)) if isinstance(error, Mapping) else str(error)
            if code == -32601:
                raise MCPToolNotFoundError(message)
            raise MCPError(message)
        if isinstance(decoded, Mapping) and "result" in decoded:
            return decoded["result"]
        return decoded

    def initialize(self) -> Any:
        result = self._request(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "personal-intelligence", "version": "0.1.0"},
                },
            }
        )
        # A notification has no response body in the protocol. Sending it is best
        # effort because a few routers accept calls immediately after initialize.
        try:
            self._request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except MCPContractError:
            pass
        return result

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        if not self._session_id:
            self.initialize()
        result = self._request(
            {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments)},
            }
        )
        if isinstance(result, Mapping) and result.get("isError"):
            message = _content_text(result) or f"MCP tool {name!r} returned isError"
            lowered = message.casefold()
            if any(token in lowered for token in ("unauthorized", "unauthenticated", "invalid token", "forbidden")):
                raise MCPAuthenticationError(message)
            if any(token in lowered for token in ("rate limit", "too many requests", "quota")):
                raise MCPRateLimitError(message)
            if any(token in lowered for token in ("tool not found", "unknown tool", "does not exist")):
                raise MCPToolNotFoundError(message)
            raise MCPError(message)
        return result

    def list_tools(self) -> Any:
        if not self._session_id:
            self.initialize()
        return self._request(
            {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}}
        )


def _content_text(result: Mapping[str, Any]) -> str:
    content = result.get("content", [])
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(entry.get("text", ""))
        for entry in content
        if isinstance(entry, Mapping) and entry.get("type") == "text"
    )
