from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from intelligence.publisher import (
    FrontMatterGate,
    GateContext,
    GateFailure,
    GitDiffScopeGate,
    HugoBuildGate,
    PublicSourcesGate,
    PublishValidator,
    SecretsGate,
)
from intelligence.reporter import RenderedReport, ReportSource, render_hugo_report
from test_reporter_renderer import make_report


def context(tmp_path: Path) -> GateContext:
    report = make_report()
    rendered = render_hugo_report(report)
    return GateContext(tmp_path, report, rendered, (rendered.relative_path,))


def success_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args[0], 0, "", "")


def failure_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args[0], 1, "", "broken")


def test_all_five_default_gates_pass_with_injected_build(tmp_path: Path) -> None:
    results = PublishValidator.default(runner=success_runner).validate(context(tmp_path))
    assert [result.name for result in results] == [
        "public_sources",
        "sensitive_content",
        "front_matter",
        "hugo_build",
        "git_diff_scope",
    ]
    assert all(result.passed for result in results)


def test_public_source_gate_rejects_private_source(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    signal = ctx.report.signals[0]
    private_signal = replace(signal, sources=(ReportSource(signal.sources[0].url, "x", False),))
    ctx = replace(ctx, report=replace(ctx.report, signals=(private_signal,)))
    assert PublicSourcesGate().check(ctx).passed is False


@pytest.mark.parametrize(
    "secret",
    [
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "api_key=abcdefghijklmnopqrstuv",
        "https://127.0.0.1:8787/private",
        "sk-" + "A1b2" * 8,
        "api_key=sk-proj-" + "A1b2" * 12,
        "https://example.com/?key=sk-" + "A1b2" * 8,
    ],
)
def test_secrets_gate_rejects_sensitive_content(tmp_path: Path, secret: str) -> None:
    ctx = context(tmp_path)
    ctx = replace(ctx, rendered=replace(ctx.rendered, markdown=ctx.rendered.markdown + secret))
    assert SecretsGate().check(ctx).passed is False


def test_front_matter_gate_rejects_missing_required_field(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    markdown = ctx.rendered.markdown.replace('reportType: "morning"\n', "")
    ctx = replace(ctx, rendered=replace(ctx.rendered, markdown=markdown))
    assert FrontMatterGate().check(ctx).passed is False


def test_secrets_gate_allows_sk_inside_an_ordinary_url_word(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    markdown = ctx.rendered.markdown + "\n[Report](https://example.com/risk-progress-and-capability-updates)\n"
    ctx = replace(ctx, rendered=replace(ctx.rendered, markdown=markdown))
    assert SecretsGate().check(ctx).passed


def test_hugo_gate_reports_build_failure(tmp_path: Path) -> None:
    result = HugoBuildGate(runner=failure_runner).check(context(tmp_path))
    assert not result.passed
    assert result.message == "broken"


def test_diff_scope_gate_rejects_unrelated_change(tmp_path: Path) -> None:
    ctx = replace(context(tmp_path), changed_paths=(Path("README.md"),))
    assert GitDiffScopeGate(runner=success_runner).check(ctx).passed is False


def test_validator_returns_all_gate_results_on_failure(tmp_path: Path) -> None:
    validator = PublishValidator((SecretsGate(), FrontMatterGate()))
    ctx = context(tmp_path)
    ctx = replace(ctx, rendered=RenderedReport(ctx.rendered.relative_path, "api_key=abcdefghijklmnopqrstuv"))
    with pytest.raises(GateFailure) as caught:
        validator.validate(ctx)
    assert len(caught.value.results) == 2
