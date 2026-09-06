"""Conservative report eligibility; discovery records are not news events.

This catches deterministic defects, not factual truth. Editorial analysis must
still verify the source. No collector currently records a verified before/after
diff, so an unknown publication time cannot be replaced with the fetch time.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit


def exclusion_reason(row: Mapping[str, Any], start: datetime, end: datetime) -> str | None:
    if row.get("is_baseline") in (True, 1, "1"):
        return "baseline"
    raw = row.get("raw_metadata_json", row.get("raw_metadata", {}))
    try:
        metadata = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return "invalid_metadata"
    if isinstance(metadata, Mapping) and metadata.get("discovery_only"):
        return "discovery_only"
    for key in ("canonical_url", "url"):
        if row.get(key) and is_discovery_url(str(row[key])):
            return "discovery_url"
    value = row.get("published_at")
    if not value:
        return "unknown_publication_time"
    try:
        published = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if published.tzinfo is None:
            return "unknown_publication_timezone"
        if not start <= published < end:
            return "outside_report_window"
    except ValueError:
        return "invalid_publication_time"
    body = str(row.get("content_text") or "").strip()
    title = str(row.get("title") or "").strip()
    # Short social posts can be useful; title-only search results cannot.
    if len(body) < 40 or body.casefold() == title.casefold():
        return "insufficient_source_content"
    summary = str(row.get("summary") or "").strip()
    if not summary or summary.casefold().rstrip("。.") == title.casefold().rstrip("。."):
        return "title_only_summary"
    placeholders = ("公开来源显示", "反映相关产品与生态的演进", "结合自身路线评估影响",
                    "持续关注后续动态", "暂无具体信息", "待进一步分析")
    fields = (summary, str(row.get("key_change") or ""), str(row.get("why_it_matters") or ""))
    if any(phrase in field for phrase in placeholders for field in fields):
        return "placeholder_analysis"
    return None


def is_discovery_url(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return True
    path = parsed.path.strip("/").lower()
    parts = path.split("/") if path else []
    if not parts:
        return True
    if parsed.hostname.lower() in {"github.com", "www.github.com"}:
        if len(parts) <= 2 or (len(parts) == 3 and parts[2] in {"releases", "tags", "issues", "pulls"}):
            return True
        if len(parts) >= 5 and parts[2:4] == ["releases", "tag"]:
            return False
    if path in {"blog", "blogs", "news", "posts", "articles", "docs", "documentation", "changelog", "releases"}:
        return True
    if re.search(r"(?:^|/)(?:page/\d+|tags?(?:/.*)?|categories(?:/.*)?|index\.(?:html?|php))$", path):
        return True
    return any(key in parse_qs(parsed.query) for key in ("page", "paged", "offset"))
