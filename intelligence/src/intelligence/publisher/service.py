from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from intelligence.reporter import Report, ReportStatus, RenderedReport

from .gates import GateContext, GateFailure, GateResult, PublishValidator
from .git import GitPublishResult, GitPublisher
from .verification import verify_publication


@dataclass(frozen=True)
class PublicationResult:
    report: Report
    gates: tuple[GateResult, ...]
    git: Optional[GitPublishResult]


class PublicationService:
    def __init__(self, validator: PublishValidator, git_publisher: GitPublisher, *, verifier=verify_publication):
        self.validator = validator
        self.git_publisher = git_publisher
        self.verifier = verifier

    def validate(
        self,
        report: Report,
        rendered: RenderedReport,
        *,
        changed_paths: Optional[tuple[Path, ...]] = None,
    ) -> PublicationResult:
        validating = report.transition(ReportStatus.VALIDATING)
        context = GateContext(
            repository=self.git_publisher.repository,
            report=validating,
            rendered=rendered,
            changed_paths=changed_paths,
        )
        try:
            results = self.validator.validate(context)
        except GateFailure as exc:
            failed = validating.transition(ReportStatus.FAILED, reason=str(exc))
            return PublicationResult(failed, exc.results, None)
        return PublicationResult(validating.transition(ReportStatus.READY), results, None)

    def publish_ready(
        self,
        report: Report,
        rendered: RenderedReport,
        *,
        published_url: str,
        push: bool = False,
        dry_run: bool = True,
        remote: str = "origin",
        branch: str = "main",
    ) -> PublicationResult:
        if report.status is not ReportStatus.READY:
            raise ValueError("only a ready report may enter the Git publisher")
        git_result = self.git_publisher.publish(
            (rendered.relative_path,),
            message=f"content(intelligence): publish {report.edition.value} report for {report.period}",
            push=push,
            dry_run=dry_run,
            remote=remote,
            branch=branch,
        )
        if dry_run or not push:
            return PublicationResult(report, (), git_result)
        self.verifier(published_url, rendered.markdown)
        published = report.mark_published(
            commit_sha=git_result.commit_sha or "",
            published_url=published_url,
        )
        return PublicationResult(published, (), git_result)
