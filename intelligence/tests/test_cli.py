import json
from pathlib import Path

import yaml

from intelligence.cli import app

from test_catalog import SCHEMA, sample_catalog


def make_catalog(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.yaml"
    path.write_text(yaml.safe_dump(sample_catalog(), allow_unicode=True), encoding="utf-8")
    return path


def invoke(capsys, argv):
    exit_code = app.main(argv)
    output = json.loads(capsys.readouterr().out)
    return exit_code, output


def test_catalog_validate_outputs_run_id(tmp_path, capsys):
    catalog = make_catalog(tmp_path)
    exit_code, output = invoke(
        capsys,
        ["--catalog", str(catalog), "--schema", str(SCHEMA), "catalog", "validate"],
    )

    assert exit_code == 0
    assert output["ok"] is True
    assert output["run_id"]
    assert output["data"] == {
        "valid": True,
        "path": str(catalog),
        "targets": 1,
        "channels": 1,
        "tags": 2,
    }


def test_target_add_defaults_to_disabled(tmp_path, capsys):
    catalog = make_catalog(tmp_path)
    exit_code, output = invoke(
        capsys,
        [
            "--catalog",
            str(catalog),
            "--schema",
            str(SCHEMA),
            "target",
            "add",
            "anthropic",
            "--name",
            "Anthropic",
            "--type",
            "company",
        ],
    )

    assert exit_code == 0
    assert output["data"]["change"]["enabled"] is False
    value = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    assert value["targets"][-1]["slug"] == "anthropic"


def test_tag_attach_to_channel(tmp_path, capsys):
    catalog = make_catalog(tmp_path)
    exit_code, output = invoke(
        capsys,
        [
            "--catalog",
            str(catalog),
            "--schema",
            str(SCHEMA),
            "tag",
            "attach",
            "competitor",
            "--channel",
            "composio-blog",
        ],
    )

    assert exit_code == 0
    assert "competitor" in output["data"]["change"]["tags"]


def test_invalid_catalog_has_machine_readable_failure(tmp_path, capsys):
    catalog = make_catalog(tmp_path)
    value = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    value["targets"][0]["channels"][0]["interval_minutes"] = 0
    catalog.write_text(yaml.safe_dump(value), encoding="utf-8")

    exit_code, output = invoke(
        capsys,
        ["--catalog", str(catalog), "--schema", str(SCHEMA), "catalog", "validate"],
    )

    assert exit_code == 2
    assert output["ok"] is False
    assert output["run_id"]
    assert any("interval_minutes" in error for error in output["errors"])


def test_catalog_sync_uses_deterministic_idempotency_key(tmp_path, capsys, monkeypatch):
    catalog = make_catalog(tmp_path)
    calls = []

    class FakeClient:
        def __init__(self, *args):
            pass

        def sync_catalog(self, payload, idempotency_key):
            calls.append((payload, idempotency_key))
            return {"synced": True}

        def create_audit_event(self, payload, *, idempotency_key):
            return {"created": True}

    monkeypatch.setattr(app, "WorkerAPIClient", FakeClient)
    exit_code, output = invoke(
        capsys,
        ["--catalog", str(catalog), "--schema", str(SCHEMA), "catalog", "sync"],
    )

    assert exit_code == 0
    assert output["data"] == {"synced": True}
    assert calls[0][1].startswith("catalog:")
    assert calls[0][0]["targets"][0]["id"]


def test_status_is_useful_without_remote_configuration(tmp_path, capsys, monkeypatch):
    catalog = make_catalog(tmp_path)
    monkeypatch.delenv("INTELLIGENCE_API_URL", raising=False)
    exit_code, output = invoke(
        capsys,
        ["--catalog", str(catalog), "--schema", str(SCHEMA), "status"],
    )

    assert exit_code == 0
    assert output["data"]["local"]["enabled_channels"] == 1
    assert output["data"]["remote"] == {"configured": False}


def test_run_show_delegates_to_worker_client(tmp_path, capsys, monkeypatch):
    catalog = make_catalog(tmp_path)

    class FakeClient:
        configured = True

        def __init__(self, *args):
            pass

        def get_run(self, run_id):
            return {"id": run_id, "run_status": "succeeded"}

    monkeypatch.setattr(app, "WorkerAPIClient", FakeClient)
    exit_code, output = invoke(
        capsys,
        [
            "--catalog",
            str(catalog),
            "--schema",
            str(SCHEMA),
            "run",
            "show",
            "run-123",
        ],
    )

    assert exit_code == 0
    assert output["data"]["id"] == "run-123"


def test_operational_parser_supports_reviewed_and_legacy_runbook_forms():
    parser = app.build_parser()

    planned = parser.parse_args(["collect", "plan", "--due"])
    local = parser.parse_args(["collect", "local", "--channel", "openai-news"])
    legacy_collect = parser.parse_args(["collect", "--due"])
    pending = parser.parse_args(["analyze", "pending", "--limit", "12"])
    legacy_analyze = parser.parse_args(["analyze", "--pending"])
    scheduler = parser.parse_args(["scheduler", "apply", "--dry-run"])

    assert planned.collect_command == "plan" and planned.due is True
    assert local.collect_command == "local" and local.channel == "openai-news"
    assert legacy_collect.collect_command is None and legacy_collect.legacy_due is True
    assert pending.analyze_command == "pending" and pending.limit == 12
    assert legacy_analyze.analyze_command is None and legacy_analyze.legacy_pending is True
    assert scheduler.dry_run is True


def test_report_push_requires_two_explicit_switches():
    args = app.build_parser().parse_args(
        [
            "report",
            "publish",
            "--edition",
            "morning",
            "--execute",
            "--push",
            "--published-url",
            "https://fatflowers.github.io/zh/posts/intelligence/example/",
        ]
    )

    assert args.execute is True
    assert args.push is True
