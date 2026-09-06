"""Build the actual public template against the real catalog and hostile fixtures."""
from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class Page(HTMLParser):
    def __init__(self, html: str) -> None:
        super().__init__()
        self.links: list[str] = []
        self.channels: list[dict[str, str | None]] = []
        self.targets = 0
        self.details: list[dict[str, str | None]] = []
        self.script_text: list[str] = []
        self.unsafe_images = 0
        self.in_script = False
        self.feed(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        if tag == "details":
            self.details.append(data)
        if tag == "script":
            self.in_script = True
        if tag == "img" and "onerror" in data:
            self.unsafe_images += 1
        if tag == "a" and data.get("href"):
            self.links.append(data["href"] or "")
        if data.get("class") == "source-channel":
            self.channels.append(data)
        if data.get("class") == "source-target":
            self.targets += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.in_script = False

    def handle_data(self, data: str) -> None:
        if self.in_script:
            self.script_text.append(data)


def build(tmp_path: Path, catalog: dict) -> Path:
    hugo = shutil.which("hugo")
    if not hugo:
        pytest.skip("Hugo is not installed")
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy(ROOT / "hugo.toml", source / "hugo.toml")
    for directory in ("layouts", "assets"):
        shutil.copytree(ROOT / directory, source / directory)
    (source / "themes").symlink_to(ROOT / "themes", target_is_directory=True)
    (source / "content").mkdir()
    for filename in ("sources.md", "sources.zh.md"):
        shutil.copy(ROOT / "content" / filename, source / "content" / filename)
    (source / "intelligence/config").mkdir(parents=True)
    (source / "intelligence/config/catalog.yaml").write_text(
        yaml.safe_dump(catalog, allow_unicode=True), encoding="utf-8"
    )
    destination = tmp_path / "public"
    subprocess.run(
        [hugo, "--minify", "--destination", str(destination), "--baseURL", "https://example.org/"],
        cwd=source, check=True, capture_output=True, text=True,
    )
    return destination


def test_sources_page_reads_current_catalog_and_navigation(tmp_path: Path) -> None:
    catalog = yaml.safe_load((ROOT / "intelligence/config/catalog.yaml").read_text())
    destination = build(tmp_path, catalog)
    channels = [channel for target in catalog["targets"] for channel in target["channels"]]
    active = sum(bool(t["enabled"] and c["enabled"]) for t in catalog["targets"] for c in t["channels"])
    for language in ("", "zh"):
        html = (destination / language / "sources/index.html").read_text()
        page = Page(html)
        assert page.targets == len(catalog["targets"])
        assert len(page.channels) == len(channels)
        channel_details = [item for item in page.details if item.get("class") == "source-details"]
        assert len(channel_details) == len(channels)
        assert all("open" not in item for item in channel_details)
        assert len([item for item in page.details if item.get("class") == "source-target-info"]) == len(catalog["targets"])
        assert 'source-row-interval' in html and 'source-row-collector' in html
        assert sum(c["data-enabled"] == "true" for c in page.channels) == active
        assert all(channel["url"] in page.links for channel in channels)
        assert "https://openai.com/news/rss.xml" in page.links
        assert "Engineering" in html
        assert "resolved_user_id" not in html
        assert "4398626122" not in html
        assert "tool_binding" not in html
        assert "worker-token" not in html
        assert "source-search" in html and "source-status" in html
        assert ("配置快照" if language else "configured snapshot") in html
        home = Page((destination / language / "index.html").read_text())
        assert f"https://example.org/{language + '/' if language else ''}sources/" in home.links


def test_sources_page_whitelists_fields_escapes_html_and_disables_children(tmp_path: Path) -> None:
    channels = [
        {"name": "<script>alert('x')</script>", "url": url, "slug": f"channel-{index}",
         "type": "rss", "collector": "rss", "enabled": True, "interval_minutes": 60,
         "tags": ["official"], "secret": "SECRET-CHANNEL", "handle": "SECRET-HANDLE",
         "tool_binding": "SECRET-TOOL", "config": {
             "feed_url": "https://example.org/feed?token=SECRET-FEED#SECRET-FRAGMENT",
             "api_key": "SECRET-API", "resolved_user_id": "SECRET-ID",
             "credentials": {"password": "SECRET-PASSWORD"},
         }}
        for index, url in enumerate([
            "https://example.org/public?token=SECRET-QUERY#SECRET-ANCHOR",
            "https://SECRET-USER:SECRET-PASS@example.org/private",
            "javascript:SECRET-JAVASCRIPT", "http://localhost:3000/SECRET-LOCAL",
            "http://127.0.0.1/SECRET-IP", "http://169.254.169.254/SECRET-METADATA",
            "http://worker.internal/SECRET-INTERNAL", "http://100.64.0.1/SECRET-CGNAT",
            "http://[::1]/SECRET-IPV6", "http://box.local/SECRET-LOCAL-DNS",
        ])
    ]
    catalog = {"version": 1, "metadata": {"secret": "SECRET-METADATA-CONFIG"},
               "tags": [{"slug": "official", "name": "<b>Official</b>"}],
               "targets": [{"slug": "test", "name": "<img src=x onerror=alert(1)>",
                            "description": "safe description", "enabled": False,
                            "tags": ["official"], "secret": "SECRET-TARGET", "channels": channels}]}
    destination = build(tmp_path, catalog)
    html = (destination / "zh/sources/index.html").read_text()
    page = Page(html)
    assert len(page.channels) == len(channels)
    assert all(c["data-enabled"] == "false" for c in page.channels)
    assert "SECRET-" not in html
    assert "https://example.org/public" in page.links
    assert "https://example.org/feed" in page.links
    # Hugo's HTML minifier may unescape angle brackets inside quoted attributes;
    # inspect the parsed DOM rather than mistaking inert attribute text for tags.
    assert not any("alert(" in text for text in page.script_text)
    assert page.unsafe_images == 0
    assert "&lt;script" in html and "&lt;b" in html
    assert "目标已停用" in html


def test_source_filters_match_target_channel_tags_and_effective_status() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    subprocess.run([node, "-e", r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const input = {value: '', addEventListener(name, fn) {this[name] = fn;}};
const status = {value: 'all', addEventListener(name, fn) {this[name] = fn;}};
const result = {dataset: {zh: 'true'}};
const form = {hidden: true, addEventListener(name, fn) {this[name] = fn;}};
const empty = {hidden: true};
const channel = (search, active) => ({dataset: {search, enabled: String(active)}});
const openai = [channel('Engineering blog ai-engineering', true), channel('Reddit community social', false)];
const simon = [channel('Everything Atom rss official', true)];
const targets = [
  {dataset: {search: 'OpenAI 科技大厂'}, querySelectorAll() {return openai;}},
  {dataset: {search: 'Simon Willison 意见领袖'}, querySelectorAll() {return simon;}}
];
const document = {
  querySelector(selector) {return selector === '.sources-filters' ? form : empty;},
  querySelectorAll() {return targets;},
  getElementById(id) {return {'source-search': input, 'source-status': status, 'source-results': result}[id];}
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document});
assert.equal(form.hidden, false);
assert.equal(result.textContent, '显示 2 个目标 · 3 个频道');
input.value = 'OPENAI engineering'; input.input();
assert.equal(openai[0].hidden, false); assert.equal(openai[1].hidden, true);
assert.equal(targets[1].hidden, true);
input.value = 'social'; input.input();
assert.equal(openai[1].hidden, false);
status.value = 'enabled'; status.change();
assert.equal(empty.hidden, false);
input.value = ''; status.value = 'disabled'; status.change();
assert.equal(result.textContent, '显示 1 个目标 · 1 个频道');
input.value = '意见领袖'; status.value = 'all'; input.input();
assert.equal(simon[0].hidden, false); assert.equal(targets[0].hidden, true);
""", str(ROOT / "assets/js/sources.js")], check=True, capture_output=True, text=True)
