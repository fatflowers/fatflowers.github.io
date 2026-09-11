"""Free local browser collectors backed by the installed OpenCLI runtime."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from intelligence.enrichment import enrich_article
from intelligence.normalize import NormalizedItem

from .adapters import twitter_posts_v1
from .base import ChannelSpec, CollectionPage


class OpenCLIError(RuntimeError):
    """OpenCLI is unavailable, unauthenticated, timed out, or returned bad data."""


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def _default_runner(arguments: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpenCLIError(f"OpenCLI execution failed: {exc}") from exc


def _binary() -> str:
    configured = os.environ.get("INTELLIGENCE_OPENCLI_BIN")
    if configured:
        candidate = Path(configured)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        raise OpenCLIError("INTELLIGENCE_OPENCLI_BIN is not executable")
    discovered = shutil.which("opencli")
    if discovered:
        return discovered
    homebrew = Path("/opt/homebrew/bin/opencli")
    if homebrew.is_file() and os.access(homebrew, os.X_OK):
        return str(homebrew)
    raise OpenCLIError("opencli is not installed or not on PATH")


def _public_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise OpenCLIError("OpenCLI collector accepts public HTTPS URLs only")
    if parsed.hostname in {"localhost", "localhost.localdomain"} or parsed.hostname.endswith(".local"):
        raise OpenCLIError("OpenCLI collector rejects local hosts")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise OpenCLIError("OpenCLI collector rejects private addresses")
    return url


def _run(arguments: Sequence[str], *, runner: Runner, timeout: float) -> str:
    completed = runner(arguments, timeout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()[:500]
        raise OpenCLIError(f"OpenCLI exited {completed.returncode}: {detail}")
    output = completed.stdout.strip()
    if not output:
        raise OpenCLIError("OpenCLI returned an empty result")
    if len(output.encode("utf-8")) > 2_000_000:
        raise OpenCLIError("OpenCLI result exceeds the 2 MB limit")
    return output


def fetch_article_with_opencli(
    url: str,
    *,
    title: str | None = None,
    canonical_url: str | None = None,
    wait_for: str | None = None,
    wait_until: str = "domstable",
    timeout: float = 75,
    runner: Runner | None = None,
    binary: str | None = None,
) -> dict[str, Any]:
    """Render one public page in Chrome and return the standard article shape."""
    url = _public_url(url)
    arguments = [
        binary or _binary(),
        "web",
        "read",
        "--url",
        url,
        "--stdout",
        "true",
        "--download-images",
        "false",
        "--wait-until",
        wait_until if wait_until in {"domstable", "networkidle"} else "domstable",
        "--window",
        "background",
        "--site-session",
        "ephemeral",
        "--keep-tab",
        "false",
        "-f",
        "json",
    ]
    if wait_for:
        arguments.extend(("--wait-for", wait_for))
    markdown = _run(arguments, runner=runner or _default_runner, timeout=timeout)
    published_match = re.search(r"^>\s*(?:发布时间|Publish(?:ed)?(?: time| at)?):\s*(.+?)\s*$", markdown, re.M | re.I)
    source_match = re.search(r"^>\s*(?:原文链接|Original URL|Source URL):\s*(https://\S+)\s*$", markdown, re.M | re.I)
    metadata = {
        "title": title or "",
        "canonicalUrl": canonical_url or (source_match.group(1) if source_match else url),
    }
    if published_match:
        metadata["publishedTime"] = published_match.group(1)
    article = enrich_article(url, markdown=markdown, metadata=metadata)
    article["body_provenance"] = {
        "source": "opencli_browser_markdown",
        "url": url,
        "characters": len(markdown),
    }
    return article


class OpenCLICollector:
    """Collect rendered web pages or an X account's latest public posts."""

    def __init__(
        self,
        *,
        timeout: float = 75,
        runner: Runner | None = None,
        binary: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.runner = runner or _default_runner
        self.binary = binary

    def collect(self, channel: ChannelSpec, cursor: Mapping[str, Any] | None = None) -> CollectionPage:
        if channel.channel_type == "twitter":
            return self._collect_twitter(channel, cursor)
        return self._collect_web(channel)

    def _collect_web(self, channel: ChannelSpec) -> CollectionPage:
        if not channel.url:
            raise OpenCLIError("OpenCLI web collector requires a URL")
        article = fetch_article_with_opencli(
            channel.url,
            wait_for=channel.config.get("wait_for"),
            wait_until=str(channel.config.get("wait_until", "domstable")),
            timeout=self.timeout,
            runner=self.runner,
            binary=self.binary,
        )
        _validate_web_article(channel, article)
        item = NormalizedItem(
            external_id=None,
            target_slug=channel.target_slug,
            channel_slug=channel.channel_slug,
            url=channel.url,
            canonical_url=article["canonical_url"],
            title=article["title"] or channel.channel_slug,
            author=None,
            published_at=article["published_at"],
            content_text=article["content_text"],
            language=None,
            metadata={
                "platform": "web",
                "collector": "opencli-web-read",
                **{
                    key: article[key]
                    for key in (
                        "publication_precision",
                        "publication_evidence",
                        "page_kind",
                        "body_provenance",
                        "discovered_links",
                    )
                },
            },
            fetched_at=datetime.now(timezone.utc),
        )
        return CollectionPage.of([item], metadata={"tool_name": "opencli web read"})

    def _collect_twitter(
        self, channel: ChannelSpec, cursor: Mapping[str, Any] | None
    ) -> CollectionPage:
        if not channel.handle:
            raise OpenCLIError("OpenCLI Twitter collector requires a handle")
        handle = channel.handle.lstrip("@")
        limit = max(20, min(100, int(channel.config.get("opencli_limit", 50))))
        arguments = [
            self.binary or _binary(),
            "twitter",
            "search",
            f"from:{handle}",
            "--product",
            "live",
            "--limit",
            str(limit),
            "--window",
            "background",
            "--site-session",
            "ephemeral",
            "--keep-tab",
            "false",
            "-f",
            "json",
        ]
        if not channel.config.get("include_replies", False):
            arguments.extend(("--exclude", "replies"))
        output = _run(arguments, runner=self.runner, timeout=self.timeout)
        try:
            rows = json.loads(output)
        except json.JSONDecodeError as exc:
            raise OpenCLIError("OpenCLI Twitter result is not JSON") from exc
        if not isinstance(rows, list):
            raise OpenCLIError("OpenCLI Twitter result must be an array")
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                row["url"] = f"https://x.com/{handle}/status/{row['id']}"
                row.setdefault("username", handle)
        items, next_cursor = twitter_posts_v1(rows, channel)
        previous = str((cursor or {}).get("last_external_id") or "")
        same_collector = (cursor or {}).get("collector") == "opencli-twitter-search"
        if same_collector and previous and items and not any(item.external_id == previous for item in items) and len(rows) >= limit:
            raise OpenCLIError(
                f"OpenCLI Twitter window exhausted before prior watermark {previous}; refusing to advance cursor"
            )
        next_cursor["collector"] = "opencli-twitter-search"
        if items:
            next_cursor["last_published_at"] = str(items[0].published_at or "")
        return CollectionPage.of(
            items,
            next_cursor=next_cursor,
            raw_count=len(rows),
            metadata={
                "tool_name": "opencli twitter search",
                "query": f"from:{handle}",
                "bounded": len(rows) >= limit,
            },
        )


def _validate_web_article(channel: ChannelSpec, article: Mapping[str, Any]) -> None:
    minimum = max(1, int(channel.config.get("minimum_content_chars", 200)))
    if len(str(article.get("content_text") or "")) < minimum:
        raise OpenCLIError("OpenCLI page body is too short")
    prefixes = tuple(str(value) for value in channel.config.get("article_path_prefixes", []))
    if prefixes and article.get("page_kind") == "index":
        links = article.get("discovered_links") or []
        if not any(urlsplit(str(link.get("url") or "")).path.startswith(prefixes) for link in links):
            raise OpenCLIError("OpenCLI index has no matching article links")
