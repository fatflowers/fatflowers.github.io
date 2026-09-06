from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from intelligence.publisher import PublicationService, PublishValidator
from intelligence.publisher.verification import publication_marker, verify_publication
from intelligence.reporter import ReportStatus, render_hugo_report
from test_reporter_renderer import make_report


class Response(BytesIO):
    status = 200

    def geturl(self):
        return "https://example.com/report/"


def test_verifies_minified_marker_only_after_new_artifact_is_live():
    markdown = publication_marker("# Actual report")
    digest = markdown.split('artifact="')[1].split('"')[0]
    bodies = iter(["<h1>Old report</h1>", '<span hidden data-intelligence-artifact=%s></span>' % digest])
    elapsed = [0]
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        elapsed[0] += seconds
    verify_publication("https://example.com/report/", markdown,
                       opener=lambda *a, **kw: Response(next(bodies).encode()),
                       clock=lambda: elapsed[0], sleep=sleep)
    assert sleeps == [5]


def test_http_200_with_old_artifact_is_not_success():
    with pytest.raises(RuntimeError, match="expected artifact"):
        verify_publication("https://example.com/report/", publication_marker("new"),
                           timeout_seconds=0, opener=lambda *a, **kw: Response(b"old"))


def test_requires_fingerprint_and_https_before_network():
    with pytest.raises(ValueError, match="fingerprint"):
        verify_publication("https://example.com", "no marker")
    with pytest.raises(ValueError, match="HTTPS"):
        verify_publication("http://example.com", publication_marker("report"))


def test_deployment_failure_does_not_return_published():
    report = replace(make_report(), status=ReportStatus.READY)
    publisher = SimpleNamespace(repository=Path("."), publish=lambda *a, **kw:
                                SimpleNamespace(commit_sha="abc", pushed=True))
    def fail(*args):
        raise RuntimeError("deployment not live")
    service = PublicationService(PublishValidator(()), publisher, verifier=fail)
    with pytest.raises(RuntimeError, match="deployment not live"):
        service.publish_ready(report, render_hugo_report(report),
                              published_url="https://example.com", push=True, dry_run=False)
    assert report.status is ReportStatus.READY


def test_commit_without_push_is_not_publication():
    report = replace(make_report(), status=ReportStatus.READY)
    publisher = SimpleNamespace(repository=Path("."), publish=lambda *a, **kw:
                                SimpleNamespace(commit_sha="abc", pushed=False))
    service = PublicationService(PublishValidator(()), publisher,
                                 verifier=lambda *args: pytest.fail("must not verify unpushed content"))
    result = service.publish_ready(report, render_hugo_report(report),
                                   published_url="https://example.com", dry_run=False)
    assert result.report.status is ReportStatus.READY
