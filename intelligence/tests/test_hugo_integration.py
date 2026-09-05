from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_intelligence_reports_render_with_home_visibility_policy(tmp_path: Path) -> None:
    hugo = shutil.which("hugo")
    if hugo is None:
        pytest.skip("Hugo is not installed")

    destination = tmp_path / "site"
    subprocess.run(
        [
            hugo,
            "--buildDrafts",
            "--buildFuture",
            "--destination",
            str(destination),
            "--baseURL",
            "/",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    home = (destination / "zh" / "index.html").read_text(encoding="utf-8")
    category = (
        destination / "zh" / "categories" / "intelligence" / "index.html"
    ).read_text(encoding="utf-8")
    daily = (
        destination
        / "zh"
        / "posts"
        / "intelligence"
        / "layout-fixture-daily"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "情报日报布局测试" not in home
    assert "高价值情报布局测试" in home
    assert "情报周报布局测试" in home

    assert "情报日报布局测试" in category
    assert "高价值情报布局测试" in category
    assert "情报周报布局测试" in category

    assert 'aria-label="情报报告信息"' in daily
    assert "早报" in daily
    assert "周期" in daily
    assert "2099-01-01" in daily
    assert "来源" in daily
    assert "3 个" in daily
