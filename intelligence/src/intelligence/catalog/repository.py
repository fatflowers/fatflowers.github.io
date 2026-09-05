"""Loading, validation, and atomic persistence for catalog.yaml."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from intelligence.models import Catalog


class CatalogError(RuntimeError):
    """Base error for safe, user-facing catalog operations."""


class CatalogValidationError(CatalogError):
    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> Dict[str, Any]:
    mapping: Dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found duplicate key (%s)" % key,
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def default_catalog_path() -> Path:
    configured = os.getenv("INTELLIGENCE_CATALOG_PATH")
    if configured:
        return Path(configured).expanduser().resolve()

    candidates = [
        Path.cwd() / "intelligence" / "config" / "catalog.yaml",
        Path.cwd() / "config" / "catalog.yaml",
        Path(__file__).resolve().parents[3] / "config" / "catalog.yaml",
    ]
    return next((path for path in candidates if path.exists()), candidates[0])


def default_schema_path() -> Path:
    configured = os.getenv("INTELLIGENCE_CATALOG_SCHEMA_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    candidates = [
        Path(__file__).resolve().parents[3] / "schemas" / "catalog.schema.json",
        Path(sys.prefix) / "share" / "personal-intelligence" / "catalog.schema.json",
    ]
    return next((path for path in candidates if path.exists()), candidates[0])


class CatalogRepository:
    def __init__(
        self,
        path: Optional[Path] = None,
        schema_path: Optional[Path] = None,
    ) -> None:
        self.path = Path(path) if path else default_catalog_path()
        self.schema_path = Path(schema_path) if schema_path else default_schema_path()

    def load_raw(self) -> Dict[str, Any]:
        if not self.path.exists():
            raise CatalogError("catalog not found: %s" % self.path)
        try:
            value = yaml.load(
                self.path.read_text(encoding="utf-8"), Loader=UniqueKeySafeLoader
            )
        except yaml.YAMLError as exc:
            raise CatalogValidationError(["invalid YAML: %s" % exc]) from exc
        if not isinstance(value, dict):
            raise CatalogValidationError(["catalog root must be an object"])
        return value

    def validate(self, value: Mapping[str, Any]) -> List[str]:
        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = []
        for issue in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in issue.absolute_path) or "$"
            errors.append("%s: %s" % (location, issue.message))
        errors.extend(self._semantic_errors(value))
        return errors

    def load(self) -> Catalog:
        value = self.load_raw()
        errors = self.validate(value)
        if errors:
            raise CatalogValidationError(errors)
        return Catalog.from_dict(value)

    def save(self, value: Mapping[str, Any]) -> Catalog:
        candidate = copy.deepcopy(dict(value))
        errors = self.validate(candidate)
        if errors:
            raise CatalogValidationError(errors)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        rendered = yaml.safe_dump(
            candidate,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".%s." % self.path.name,
            suffix=".tmp",
            dir=str(self.path.parent),
            text=True,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return Catalog.from_dict(candidate)

    @staticmethod
    def _semantic_errors(value: Mapping[str, Any]) -> List[str]:
        errors: List[str] = []
        tags = value.get("tags", [])
        targets = value.get("targets", [])
        if not isinstance(tags, list) or not isinstance(targets, list):
            return errors

        tag_slugs = [item.get("slug") for item in tags if isinstance(item, dict)]
        target_slugs = [item.get("slug") for item in targets if isinstance(item, dict)]
        errors.extend(_duplicate_errors(tag_slugs, "tag"))
        errors.extend(_duplicate_errors(target_slugs, "target"))
        known_tags = set(tag_slugs)
        channel_slugs: List[Any] = []

        for target_index, target in enumerate(targets):
            if not isinstance(target, dict):
                continue
            for tag in target.get("tags", []):
                if tag not in known_tags:
                    errors.append(
                        "targets.%d.tags: unknown tag '%s'" % (target_index, tag)
                    )
            for channel_index, channel in enumerate(target.get("channels", [])):
                if not isinstance(channel, dict):
                    continue
                channel_slugs.append(channel.get("slug"))
                for tag in channel.get("tags", []):
                    if tag not in known_tags:
                        errors.append(
                            "targets.%d.channels.%d.tags: unknown tag '%s'"
                            % (target_index, channel_index, tag)
                        )
                if (
                    channel.get("enabled", True)
                    and channel.get("collector") == "mcp"
                    and not channel.get("tool_binding")
                ):
                    errors.append(
                        "targets.%d.channels.%d.tool_binding: enabled MCP channel requires a binding"
                        % (target_index, channel_index)
                    )

        errors.extend(_duplicate_errors(channel_slugs, "channel"))
        return errors


def _duplicate_errors(values: Iterable[Any], kind: str) -> List[str]:
    seen = set()
    duplicates = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return [
        "duplicate %s slug '%s'" % (kind, value)
        for value in sorted(duplicates, key=lambda item: str(item))
    ]
