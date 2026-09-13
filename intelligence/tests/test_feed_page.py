from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_feed_snapshot_is_public_bounded_and_excludes_internal_fields():
    snapshot = json.loads((ROOT / "static/data/intelligence-feed/index.json").read_text(encoding="utf-8"))
    assert snapshot["window_days"] == 90
    assert snapshot["page_size"] == 30
    assert snapshot["history_preserved"] is True
    assert snapshot["count"] == len(snapshot["items"])
    assert all(item["importance"] >= 2 for item in snapshot["items"])
    assert not any("datasette" in json.dumps(item, ensure_ascii=False).casefold() for item in snapshot["items"])
    assert all("content_text" not in item and "confidence" not in item for item in snapshot["items"])


def test_hugo_builds_chinese_only_feed_route(tmp_path: Path):
    hugo = shutil.which("hugo")
    if not hugo:
        pytest.skip("Hugo is not installed")
    destination = tmp_path / "public"
    subprocess.run(
        [hugo, "--minify", "--destination", str(destination), "--baseURL", "https://example.org/"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    html = (destination / "zh/feed/index.html").read_text(encoding="utf-8")
    assert "INTELLIGENCE STREAM" in html
    assert "feed-from" in html and "feed-target" in html and "feed-importance" in html
    assert "/data/intelligence-feed/index.json" in html
    assert not (destination / "feed/index.html").exists()
