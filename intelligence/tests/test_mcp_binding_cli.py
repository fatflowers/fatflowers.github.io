from pathlib import Path

import pytest
import yaml

from intelligence.catalog import CatalogError
from intelligence.cli import app
from intelligence.cli.operations import (
    list_mcp_bindings,
    show_mcp_binding,
    verify_mcp_binding,
)

from test_cli_operations import project


def make_schema_verified(tmp_path: Path):
    root, repository = project(tmp_path)
    path = root / "intelligence" / "config" / "mcp-tools.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    value["tools"]["firecrawl-page-scrape-v1"]["status"] = "schema_verified"
    value["tools"]["firecrawl-page-scrape-v1"]["contract"] = {
        "version": 1,
        "verified_at": None,
    }
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
    return repository, path


def test_binding_list_and_show_are_read_only(tmp_path):
    repository, path = make_schema_verified(tmp_path)
    before = path.read_bytes()

    listed = list_mcp_bindings(repository)
    shown = show_mcp_binding(repository, "firecrawl-page-scrape-v1")

    assert listed["bindings"][0]["status"] == "schema_verified"
    assert shown["binding"]["tool_name"] == "post_firecrawl_scrape"
    assert path.read_bytes() == before


def test_binding_verify_requires_evidence_and_is_atomic(tmp_path):
    repository, path = make_schema_verified(tmp_path)
    result = verify_mcp_binding(
        repository,
        "firecrawl-page-scrape-v1",
        "minimal public scrape fixture passed adapter contract v1",
    )

    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    binding = value["tools"]["firecrawl-page-scrape-v1"]
    assert result["before"]["status"] == "schema_verified"
    assert binding["status"] == "verified"
    assert binding["contract"]["evidence"].startswith("minimal public scrape")
    assert binding["contract"]["verified_at"].endswith("Z")


def test_binding_verify_dry_run_does_not_write(tmp_path):
    repository, path = make_schema_verified(tmp_path)
    before = path.read_bytes()

    result = verify_mcp_binding(
        repository, "firecrawl-page-scrape-v1", "fixture passed", dry_run=True
    )

    assert result["dry_run"] is True
    assert path.read_bytes() == before


def test_binding_verify_refuses_non_schema_transition(tmp_path):
    repository, _ = make_schema_verified(tmp_path)
    verify_mcp_binding(repository, "firecrawl-page-scrape-v1", "fixture passed")

    with pytest.raises(CatalogError, match="only schema_verified"):
        verify_mcp_binding(repository, "firecrawl-page-scrape-v1", "fixture passed again")


@pytest.mark.parametrize(
    "evidence",
    ["", "Authorization: Bearer secret-token-value", "api_key=secretvalue12345"],
)
def test_binding_verify_rejects_empty_or_sensitive_evidence(tmp_path, evidence):
    repository, _ = make_schema_verified(tmp_path)

    with pytest.raises(CatalogError):
        verify_mcp_binding(repository, "firecrawl-page-scrape-v1", evidence)


def test_cli_parser_matches_multica_skill_commands():
    parser = app.build_parser()
    listed = parser.parse_args(["mcp", "binding", "list"])
    shown = parser.parse_args(["mcp", "binding", "show", "twitter-user-timeline-v1"])
    verified = parser.parse_args(
        [
            "mcp",
            "binding",
            "verify",
            "twitter-user-timeline-v1",
            "--evidence",
            "contract fixture passed",
        ]
    )

    assert listed.binding_command == "list"
    assert shown.alias == "twitter-user-timeline-v1"
    assert verified.evidence == "contract fixture passed"
