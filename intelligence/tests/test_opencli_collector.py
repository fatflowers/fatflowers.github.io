import json
import subprocess

import pytest

from intelligence.collectors.base import ChannelSpec
from intelligence.collectors.opencli import OpenCLICollector, OpenCLIError, fetch_article_with_opencli


def completed(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(["opencli"], returncode, stdout=stdout, stderr=stderr)


def test_opencli_web_read_returns_standard_article_and_safe_flags():
    calls = []
    markdown = """# Concrete launch
> 发布时间: 2026-09-10T08:00:00-07:00
> 原文链接: https://example.com/news/launch

## Details
""" + "A concrete product detail. " * 30

    def run(arguments, timeout):
        calls.append((list(arguments), timeout))
        return completed(markdown)

    article = fetch_article_with_opencli(
        "https://example.com/news/launch",
        runner=run,
        binary="/usr/local/bin/opencli",
    )

    assert article["title"] == "Concrete launch"
    assert article["published_at"].startswith("2026-09-10")
    assert article["body_provenance"]["source"] == "opencli_browser_markdown"
    command = calls[0][0]
    assert command[:3] == ["/usr/local/bin/opencli", "web", "read"]
    assert command[command.index("--window") + 1] == "background"
    assert command[command.index("--keep-tab") + 1] == "false"
    assert command[command.index("--download-images") + 1] == "false"


def test_opencli_twitter_normalizes_ids_and_advances_watermark():
    rows = [
        {"id": "12", "author": "OpenAI", "text": "Newest public post", "created_at": "2026-09-11T12:00:00Z"},
        {"id": "11", "author": "OpenAI", "text": "Prior public post", "created_at": "2026-09-11T11:00:00Z"},
    ]
    calls = []

    def run(arguments, timeout):
        calls.append(list(arguments))
        return completed(json.dumps(rows))

    channel = ChannelSpec(
        "openai", "openai-twitter", "twitter", "browser",
        url="https://x.com/OpenAI", handle="OpenAI",
        config={"include_replies": False, "opencli_limit": 50},
    )
    page = OpenCLICollector(runner=run, binary="opencli").collect(
        channel, {"last_external_id": "11"}
    )

    assert [item.external_id for item in page.items] == ["12", "11"]
    assert page.items[0].canonical_url == "https://x.com/OpenAI/status/12"
    assert page.next_cursor["last_external_id"] == "12"
    assert page.next_cursor["collector"] == "opencli-twitter-search"
    assert "from:OpenAI" in calls[0]
    assert calls[0][calls[0].index("--exclude") + 1] == "replies"


def test_opencli_twitter_fails_closed_when_window_misses_watermark():
    rows = [
        {"id": str(value), "author": "OpenAI", "text": f"Post {value}", "created_at": "2026-09-11T12:00:00Z"}
        for value in range(100, 50, -1)
    ]
    channel = ChannelSpec(
        "openai", "openai-twitter", "twitter", "browser",
        url="https://x.com/OpenAI", handle="OpenAI", config={"opencli_limit": 50},
    )
    collector = OpenCLICollector(
        runner=lambda arguments, timeout: completed(json.dumps(rows)), binary="opencli"
    )

    with pytest.raises(OpenCLIError, match="refusing to advance cursor"):
        collector.collect(channel, {"last_external_id": "10", "collector": "opencli-twitter-search"})


def test_opencli_twitter_allows_one_time_watermark_migration_from_aisa():
    rows = [
        {"id": str(value), "author": "OpenAI", "text": f"Post {value}", "created_at": "2026-09-11T12:00:00Z"}
        for value in range(100, 50, -1)
    ]
    channel = ChannelSpec(
        "openai", "openai-twitter", "twitter", "browser",
        url="https://x.com/OpenAI", handle="OpenAI", config={"opencli_limit": 50},
    )
    collector = OpenCLICollector(
        runner=lambda arguments, timeout: completed(json.dumps(rows)), binary="opencli"
    )

    page = collector.collect(channel, {"last_external_id": "old-aisa-retweet"})

    assert page.next_cursor["last_external_id"] == "100"
    assert page.next_cursor["collector"] == "opencli-twitter-search"


def test_opencli_rejects_private_web_targets_before_launching_browser():
    with pytest.raises(OpenCLIError, match="rejects private"):
        fetch_article_with_opencli("https://127.0.0.1/private", binary="opencli")
