"""Loader and renderer for the reviewed fixed-tool registry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .errors import MCPContractError

_TEMPLATE = re.compile(
    r"^\s*{{\s*([a-zA-Z0-9_.-]+)(?:\s*\|\s*default\(([^)]*)\))?\s*}}\s*$"
)
_EMBEDDED_TEMPLATE = re.compile(
    r"{{\s*([a-zA-Z0-9_.-]+)(?:\s*\|\s*default\(([^)]*)\))?\s*}}"
)


def _lookup(context: Mapping[str, Any], path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _default_value(raw: str | None) -> Any:
    if raw is None:
        return None
    value = raw.strip()
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() in {"null", "none"}:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip("'\"")


def _resolved(match: re.Match[str], context: Mapping[str, Any]) -> Any:
    value = _lookup(context, match.group(1))
    if value in (None, "") and match.group(2) is not None:
        return _default_value(match.group(2))
    return value


def _render(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        match = _TEMPLATE.match(value)
        if match:
            return _resolved(match, context)
        # Embedded placeholders are strings by definition.
        return _EMBEDDED_TEMPLATE.sub(lambda match: str(_resolved(match, context) or ""), value)
    if isinstance(value, list):
        return [_render(entry, context) for entry in value]
    if isinstance(value, Mapping):
        return {key: _render(entry, context) for key, entry in value.items()}
    return value


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_empty(entry) for key, entry in value.items() if entry not in (None, "")}
    if isinstance(value, list):
        return [_drop_empty(entry) for entry in value]
    return value


@dataclass(frozen=True, slots=True)
class MCPToolBinding:
    alias: str
    tool_name: str
    status: str
    channel_types: tuple[str, ...]
    input_template: Mapping[str, Any]
    output_adapter: str
    pagination: Mapping[str, Any] = field(default_factory=dict)
    limitations: Mapping[str, Any] = field(default_factory=dict)
    contract_version: int = 1

    @classmethod
    def from_mapping(cls, alias: str, value: Mapping[str, Any]) -> "MCPToolBinding":
        required = ("tool_name", "status", "channel_types", "input_template", "output_adapter")
        missing = [key for key in required if key not in value]
        if missing:
            raise MCPContractError(f"binding {alias!r} lacks: {', '.join(missing)}")
        return cls(
            alias=alias,
            tool_name=str(value["tool_name"]),
            status=str(value["status"]),
            channel_types=tuple(str(entry) for entry in value["channel_types"]),
            input_template=dict(value["input_template"]),
            output_adapter=str(value["output_adapter"]),
            pagination=dict(value.get("pagination", {})),
            limitations=dict(value.get("limitations", {})),
            contract_version=int(
                value.get("contract_version")
                or (value.get("contract", {}).get("version", 1) if isinstance(value.get("contract"), Mapping) else 1)
            ),
        )

    def assert_runnable(self, channel_type: str, *, scheduled: bool = True) -> None:
        if channel_type not in self.channel_types:
            raise MCPContractError(
                f"binding {self.alias!r} does not support channel type {channel_type!r}"
            )
        accepted = {"verified"} if scheduled else {"verified", "schema_verified"}
        if self.status not in accepted:
            raise MCPContractError(
                f"binding {self.alias!r} is {self.status!r}; expected {sorted(accepted)}"
            )

    def render_arguments(self, channel: Mapping[str, Any], cursor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        rendered = _render(self.input_template, {"channel": channel, "cursor": cursor or {}})
        if not isinstance(rendered, dict):
            raise MCPContractError(f"binding {self.alias!r} rendered non-object arguments")
        return _drop_empty(rendered)


@dataclass(frozen=True, slots=True)
class MCPToolRegistry:
    server_name: str
    server_url: str
    bindings: Mapping[str, MCPToolBinding]
    version: int = 1

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "MCPToolRegistry":
        server = data.get("server")
        tools = data.get("tools")
        if not isinstance(server, Mapping) or not isinstance(tools, Mapping):
            raise MCPContractError("registry requires object-valued server and tools")
        bindings = {
            str(alias): MCPToolBinding.from_mapping(str(alias), value)
            for alias, value in tools.items()
            if isinstance(value, Mapping)
        }
        if len(bindings) != len(tools):
            raise MCPContractError("every tool binding must be an object")
        return cls(
            server_name=str(server.get("name", "")),
            server_url=str(server.get("url", "")),
            bindings=bindings,
            version=int(data.get("version", 1)),
        )

    @classmethod
    def load(cls, path: str | Path) -> "MCPToolRegistry":
        source = Path(path).read_text(encoding="utf-8")
        if str(path).endswith(".json"):
            data = json.loads(source)
        else:
            try:
                import yaml  # type: ignore[import-untyped]
            except ImportError as exc:
                raise RuntimeError("PyYAML is required to load mcp-tools.yaml") from exc
            data = yaml.safe_load(source)
        if not isinstance(data, Mapping):
            raise MCPContractError("MCP registry root must be an object")
        return cls.from_mapping(data)

    def get(self, alias: str) -> MCPToolBinding:
        try:
            return self.bindings[alias]
        except KeyError as exc:
            raise MCPContractError(f"unknown MCP tool binding: {alias}") from exc
