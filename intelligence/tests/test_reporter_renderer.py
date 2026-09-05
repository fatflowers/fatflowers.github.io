from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from intelligence.analyzer import validate_analysis
from intelligence.reporter import (
    Report,
    ReportEdition,
    ReportLifecycleError,
    ReportSignal,
    ReportSource,
    ReportStatus,
    render_hugo_report,
)


def make_report(*, edition: ReportEdition = ReportEdition.MORNING) -> Report:
    source = ReportSource("https://example.com/news", "官方公告")
    analysis = validate_analysis(
        {
            "summary": "发布了新的 Agent 能力。",
            "key_change": "新增稳定接口。",
            "why_it_matters": "降低集成成本。",
            "company_impact": "Aisa 可评估兼容能力。",
            "importance": 4,
            "confidence": 0.87,
            "topics": ["MCP", "Agent"],
            "watch_next": ["定价"],
            "evidence": [{"url": source.url, "claim": "官方宣布新接口"}],
        }
    )
    now = datetime(2026, 9, 5, 8, 30, tzinfo=timezone.utc)
    return Report(
        report_id="report-1",
        edition=edition,
        period="2026-09-05" if edition is not ReportEdition.WEEKLY else "2026-W36",
        generated_at=now,
        window_start=datetime(2026, 9, 4, 19, tzinfo=timezone.utc),
        window_end=now,
        title="AI 情报早报｜2026-09-05",
        description="今日值得关注的公开信号",
        signals=(ReportSignal("item-1", "Composio", "新接口", now, analysis, (source,)),),
        trends=("Agent 工具接口趋于标准化。",),
    )


def test_render_is_deterministic_and_has_required_front_matter() -> None:
    report = make_report()
    first = render_hugo_report(report)
    second = render_hugo_report(report)
    assert first == second
    assert first.relative_path.as_posix() == "content/posts/intelligence/2026-09-05-morning.zh.md"
    assert 'reportType: "morning"' in first.markdown
    assert "sourcesCount: 1" in first.markdown
    assert "hiddenInHomeList: true" in first.markdown
    assert "## 早报关键信号" in first.markdown
    assert "[官方公告](https://example.com/news)" in first.markdown


def test_weekly_report_appears_on_home_page() -> None:
    rendered = render_hugo_report(make_report(edition=ReportEdition.WEEKLY))
    assert "hiddenInHomeList: false" in rendered.markdown
    assert rendered.relative_path.name == "2026-w36-weekly.zh.md"


def test_lifecycle_enforces_publish_sequence() -> None:
    report = make_report()
    with pytest.raises(ReportLifecycleError):
        report.mark_published(commit_sha="abc", published_url="https://example.com")
    validating = report.transition(ReportStatus.VALIDATING)
    ready = validating.transition(ReportStatus.READY)
    published = ready.mark_published(commit_sha="abc", published_url="https://example.com")
    assert published.status is ReportStatus.PUBLISHED
    assert published.commit_sha == "abc"


def test_empty_report_cannot_render() -> None:
    report = make_report()
    with pytest.raises(ValueError, match="without signals"):
        render_hugo_report(
            Report(
                report_id=report.report_id,
                edition=report.edition,
                period=report.period,
                generated_at=report.generated_at,
                window_start=report.window_start,
                window_end=report.window_end,
                title=report.title,
                description=report.description,
                signals=(),
            )
        )


def test_renderer_escapes_raw_html_and_hugo_shortcodes() -> None:
    report = make_report()
    analysis = replace(report.signals[0].analysis, summary="<script>x</script> {{< bad >}}")
    signal = replace(report.signals[0], analysis=analysis)
    markdown = render_hugo_report(replace(report, signals=(signal,))).markdown
    assert "<script>" not in markdown
    assert "{{<" not in markdown
    assert "&lt;script&gt;" in markdown
