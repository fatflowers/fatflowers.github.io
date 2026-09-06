import json
from pathlib import Path

import pytest
import yaml

from intelligence.catalog import CatalogRepository, CatalogService, CatalogValidationError


SCHEMA = Path(__file__).parents[1] / "schemas" / "catalog.schema.json"


def sample_catalog():
    return {
        "version": 1,
        "metadata": {"public_only": True},
        "tags": [
            {"slug": "official", "name": "官方", "type": "provenance"},
            {"slug": "competitor", "name": "竞品", "type": "relationship"},
        ],
        "targets": [
            {
                "slug": "composio",
                "name": "Composio",
                "type": "company",
                "priority": "high",
                "enabled": True,
                "tags": ["competitor"],
                "channels": [
                    {
                        "slug": "composio-blog",
                        "name": "Official Blog",
                        "type": "blog",
                        "collector": "mcp",
                        "url": "https://composio.dev/blog",
                        "interval_minutes": 60,
                        "enabled": True,
                        "tier": "core",
                        "tags": ["official"],
                        "tool_binding": "firecrawl-page-scrape-v1",
                        "fallbacks": [{"collector": "http"}],
                    }
                ],
            }
        ],
    }


@pytest.fixture
def repository(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(sample_catalog(), allow_unicode=True), encoding="utf-8")
    return CatalogRepository(path, SCHEMA)


def test_valid_catalog_loads_and_has_stable_sync_ids(repository):
    first = repository.load().to_sync_dict()
    second = repository.load().to_sync_dict()

    assert first == second
    assert first["targets"][0]["id"]
    assert first["channels"][0]["target_id"] == first["targets"][0]["id"]
    assert first["channels"][0]["config"]["fallbacks"] == [{"collector": "http"}]
    assert first["target_tags"] == [
        {"target_id": first["targets"][0]["id"], "tag_id": first["tags"][1]["id"]}
    ]


def test_unknown_tag_is_rejected(repository):
    value = repository.load_raw()
    value["targets"][0]["tags"].append("does-not-exist")

    with pytest.raises(CatalogValidationError, match="unknown tag"):
        repository.save(value)


def test_enabled_mcp_channel_without_binding_is_rejected(repository):
    value = repository.load_raw()
    del value["targets"][0]["channels"][0]["tool_binding"]

    with pytest.raises(CatalogValidationError, match="requires a binding"):
        repository.save(value)


def test_duplicate_yaml_key_is_rejected(tmp_path):
    path = tmp_path / "catalog.yaml"
    path.write_text(
        "version: 1\ntags: []\ntags: []\ntargets: []\n", encoding="utf-8"
    )

    with pytest.raises(CatalogValidationError, match="duplicate key"):
        CatalogRepository(path, SCHEMA).load()


def test_mutation_is_atomic_and_dry_run_does_not_write(repository):
    service = CatalogService(repository)
    before = repository.path.read_text(encoding="utf-8")
    result = service.add_tag("new-tag", "新标签", "signal", dry_run=True)

    assert result["dry_run"] is True
    assert repository.path.read_text(encoding="utf-8") == before

    service.add_tag("new-tag", "新标签", "signal")
    assert any(tag["slug"] == "new-tag" for tag in repository.load_raw()["tags"])


def test_global_channel_slug_uniqueness(repository):
    value = repository.load_raw()
    target = {
        "slug": "other",
        "name": "Other",
        "type": "company",
        "enabled": True,
        "channels": [dict(value["targets"][0]["channels"][0])],
    }
    value["targets"].append(target)

    errors = repository.validate(value)
    assert "duplicate channel slug 'composio-blog'" in errors


def test_catalog_schema_is_valid_json():
    assert json.loads(SCHEMA.read_text(encoding="utf-8"))["$schema"].endswith("2020-12/schema")


def test_project_catalog_validates_when_present():
    project_catalog = Path(__file__).parents[1] / "config" / "catalog.yaml"
    if project_catalog.exists():
        catalog = CatalogRepository(project_catalog, SCHEMA).load()
        assert len(catalog.targets) == 9
        assert sum(len(target.channels) for target in catalog.targets) == 38
        for slug in ('grok', 'manus', 'deepseek', 'openrouter'):
            target = next(t for t in catalog.targets if t.slug == slug)
            assert target.enabled and all(c.enabled for c in target.channels)
        channels = {channel.slug: channel for target in catalog.targets for channel in target.channels}
        for slug in ('openai-engineering', 'openai-developer-blog', 'anthropic-engineering', 'claude-blog'):
            assert channels[slug].enabled and channels[slug].channel_type == 'blog'
