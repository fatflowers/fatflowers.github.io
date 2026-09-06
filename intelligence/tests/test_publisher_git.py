from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from intelligence.publisher import (
    FrontMatterGate,
    GitPublisher,
    PublicationService,
    PublishValidator,
    SecretsGate,
)
from intelligence.reporter import ReportStatus, render_hugo_report
from test_reporter_renderer import make_report


def test_git_publisher_is_dry_run_by_default(tmp_path: Path) -> None:
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args[0], 0, "", "")

    result = GitPublisher(tmp_path, runner=runner).publish(
        (Path("content/posts/intelligence/2026-09-05-morning.zh.md"),),
        message="publish report",
    )
    assert result.dry_run
    assert not result.pushed
    assert called is False
    assert result.commands[-1][0:2] == ("git", "commit")


def test_git_publisher_requires_explicit_push_and_non_dry_run(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def runner(command, **kwargs):
        commands.append(tuple(command))
        output = "abc123\n" if tuple(command) == ("git", "rev-parse", "HEAD") else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    result = GitPublisher(tmp_path, runner=runner).publish(
        (Path("content/posts/intelligence/report.zh.md"),),
        message="publish report",
        dry_run=False,
        push=True,
    )
    assert result.pushed
    assert result.commit_sha == "abc123"
    assert ("git", "push", "origin", "HEAD:main") in commands


def test_git_publisher_rejects_paths_outside_allowlist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allowlist"):
        GitPublisher(tmp_path).publish((Path("hugo.toml"),), message="bad")


def test_publisher_blocks_unrelated_snapshot_commits_before_staging(tmp_path: Path) -> None:
    commands = []

    def runner(command, **kwargs):
        commands.append(tuple(command))
        output = ".claude/settings.json\0" if command[1] == "log" else ""
        return subprocess.CompletedProcess(command, 0, output, "")

    with pytest.raises(RuntimeError, match="outgoing commit outside publish allowlist"):
        GitPublisher(tmp_path, runner=runner).publish(
            (Path("content/posts/intelligence/report.zh.md"),),
            message="publish", dry_run=False, push=True,
        )
    assert not any(command[1] in {"add", "commit", "push"} for command in commands)


def test_publication_service_moves_draft_to_ready_then_dry_runs(tmp_path: Path) -> None:
    report = make_report()
    rendered = render_hugo_report(report)
    service = PublicationService(
        PublishValidator((SecretsGate(), FrontMatterGate())),
        GitPublisher(tmp_path),
    )
    validated = service.validate(report, rendered, changed_paths=(rendered.relative_path,))
    assert validated.report.status is ReportStatus.READY

    preview = service.publish_ready(
        validated.report,
        rendered,
        published_url="https://fatflowers.github.io/zh/posts/intelligence/report/",
    )
    assert preview.report.status is ReportStatus.READY
    assert preview.git is not None and preview.git.dry_run


def test_publication_service_records_failed_validation(tmp_path: Path) -> None:
    report = make_report()
    rendered = render_hugo_report(report)
    rendered = type(rendered)(rendered.relative_path, rendered.markdown + "\napi_key=abcdefghijklmnopqrstuv")
    service = PublicationService(PublishValidator((SecretsGate(),)), GitPublisher(tmp_path))
    result = service.validate(report, rendered)
    assert result.report.status is ReportStatus.FAILED
    assert "possible generic secret" in (result.report.status_reason or "")
