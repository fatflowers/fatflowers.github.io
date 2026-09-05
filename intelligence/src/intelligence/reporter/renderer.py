from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .models import Report, ReportEdition, ReportSignal


@dataclass(frozen=True)
class RenderedReport:
    relative_path: Path
    markdown: str


_EDITION_LABEL = {
    ReportEdition.MORNING: "早报",
    ReportEdition.MIDDAY: "午间快讯",
    ReportEdition.EVENING: "晚报",
    ReportEdition.WEEKLY: "战略周报",
    ReportEdition.AD_HOC: "专题报告",
}


def _yaml_string(value: str) -> str:
    # JSON strings are valid YAML double-quoted scalars and make escaping stable.
    return json.dumps(value, ensure_ascii=False)


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9-]+", "-", value.lower()).strip("-")
    if not value:
        raise ValueError("period must contain at least one slug-safe character")
    return value


def _markdown_text(value: str) -> str:
    """Keep untrusted source/model text from becoming raw HTML or a shortcode."""

    return (
        value.replace("{{", "&#123;&#123;")
        .replace("}}", "&#125;&#125;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _markdown_link_label(value: str) -> str:
    return _markdown_text(value).replace("[", "\\[").replace("]", "\\]")


def _signal_markdown(signal: ReportSignal) -> list[str]:
    analysis = signal.analysis
    lines = [
        f"### {_markdown_text(signal.target)}：{_markdown_text(signal.title)}",
        "",
        f"**重要度：** {'★' * analysis.importance}{'☆' * (5 - analysis.importance)}  ",
        f"**置信度：** {round(analysis.confidence * 100)}%",
        "",
        "**发生了什么**",
        "",
        _markdown_text(analysis.summary),
        "",
        "**变化是什么**",
        "",
        _markdown_text(analysis.key_change),
        "",
        "**为什么重要**",
        "",
        _markdown_text(analysis.why_it_matters),
        "",
        "**对 Aisa 的影响**",
        "",
        _markdown_text(analysis.company_impact),
    ]
    if analysis.watch_next:
        lines.extend(["", "**继续观察**", ""])
        lines.extend(f"- {_markdown_text(item)}" for item in analysis.watch_next)
    lines.append("")
    return lines


def render_hugo_report(report: Report) -> RenderedReport:
    """Render a byte-stable Chinese Hugo report from validated structures."""

    if not report.signals:
        raise ValueError("cannot render a report without signals")

    weekly = report.edition is ReportEdition.WEEKLY
    tags = sorted(
        {
            value
            for signal in report.signals
            for value in (signal.target, *signal.analysis.topics)
        },
        key=lambda value: (value.casefold(), value),
    )
    source_by_url = {
        source.url: source
        for signal in report.signals
        for source in signal.sources
    }
    sources = tuple(source_by_url[url] for url in sorted(source_by_url))

    front_matter = [
        "---",
        f"title: {_yaml_string(report.title)}",
        f"date: {_yaml_string(report.generated_at.isoformat())}",
        'categories: ["Intelligence"]',
        "tags: [" + ", ".join(_yaml_string(tag) for tag in tags) + "]",
        f"description: {_yaml_string(report.description)}",
        f"reportType: {_yaml_string(report.edition.value)}",
        f"period: {_yaml_string(report.period)}",
        "generated: true",
        f"sourcesCount: {len(sources)}",
        f"hiddenInHomeList: {'false' if weekly or any(s.analysis.importance == 5 for s in report.signals) else 'true'}",
        f"reportId: {_yaml_string(report.report_id)}",
        "---",
        "",
    ]

    body = [f"## {_EDITION_LABEL[report.edition]}关键信号", ""]
    for signal in report.signals:
        body.extend(_signal_markdown(signal))

    body.extend(["## 趋势变化", ""])
    if report.trends:
        body.extend(f"- {_markdown_text(trend)}" for trend in report.trends)
    else:
        body.append("本期尚未识别出需要单独记录的跨事件趋势。")

    low_priority = tuple(signal for signal in report.signals if signal.analysis.importance <= 2)
    if low_priority:
        body.extend(["", "## 低优先级动态", ""])
        body.extend(
            f"- **{_markdown_text(signal.target)}**：{_markdown_text(signal.analysis.summary)}"
            for signal in low_priority
        )

    body.extend(["", "## 来源", ""])
    body.extend(f"- [{_markdown_link_label(source.title)}]({source.url})" for source in sources)
    body.append("")

    filename = f"{_slug(report.period)}-{report.edition.value}.zh.md"
    return RenderedReport(
        relative_path=Path("content/posts/intelligence") / filename,
        markdown="\n".join(front_matter + body),
    )
