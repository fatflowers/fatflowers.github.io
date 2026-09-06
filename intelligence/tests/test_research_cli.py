from intelligence.cli import research
from intelligence.cli.app import build_parser


class Client:
    _path = staticmethod(lambda path, query: path)

    def __init__(self, items=None):
        self.items = items or [{"id": "one", "url": "https://example.com/blog/article", "title": "Article", "content_revision": 3, "target_id": "t", "channel_id": "c"}]
        self.writes = []
        self.queued = []

    def write_items(self, records, **kwargs):
        self.queued.extend(records)
        self.items.extend({**row, "content_revision": 0} for row in records)
        return {"inserted": len(records)}

    def _request(self, method, path, **kwargs):
        if method == "POST":
            self.writes.append(kwargs["body"])
            return {"content_revision": 4}
        if path.endswith("pending-enrichment"):
            return {"items": self.items}
        if path.endswith("coverage"):
            return {"targets": []}
        return {"item": next(row for row in self.items if row["id"] == path.split("/")[-1])}


def article(**changes):
    result = {"title": "Real announcement", "content_text": "Specific article detail. " * 30,
              "published_at": "2026-09-05", "publication_precision": "day",
              "publication_evidence": {"source": "article.visible_date", "value": "September 5, 2026"},
              "page_kind": "article", "canonical_url": "https://example.com/blog/article"}
    result.update(changes)
    return result


def test_research_commands_parse():
    for command in ("plan", "run", "coverage", "hydrate --item-id one", "ingest --item-id one --input data.json"):
        assert build_parser().parse_args(("research " + command).split()).command == "research"


def test_balanced_selection():
    client = Client([{"id": str(n), "target_id": "a"} for n in range(8)] + [{"id": "b", "target_id": "b"}])
    result = research.research_plan(client, since="2026-09-01", limit=3)
    assert [i["target_id"] for i in result["items"]] == ["a", "b", "a"]


def test_hydrate_writes_body_revision_and_evidence(monkeypatch):
    monkeypatch.setattr(research, "fetch_article", lambda url: article())
    client = Client()
    result = research.research_hydrate(client, item_id="one", since="2026-09-01")
    assert result["status"] == "ready"
    saved = client.writes[0]
    assert saved["expected_revision"] == 3
    assert saved["publication_precision"] == "day"
    assert saved["date_evidence"]["kind"] == "article_text"
    assert saved["published_at"].startswith("2026-09-05")


def test_missing_date_is_failed_and_returns_fixed_fallback(monkeypatch):
    monkeypatch.setattr(research, "fetch_article", lambda url: article(published_at=None, publication_evidence=None))
    client = Client()
    result = research.research_hydrate(client, item_id="one", since="2026-09-01")
    assert result["status"] == "failed"
    assert result["fallback"]["tool_name"] == "post_firecrawl_scrape"
    assert "published_at" not in client.writes[0]


def test_old_articles_rejected(monkeypatch):
    monkeypatch.setattr(research, "fetch_article", lambda url: article(published_at="2024-03-04"))
    result = research.research_hydrate(Client(), item_id="one", since="2026-09-01")
    assert result["status"] == "rejected"


def test_index_returns_links_for_actual_followup(monkeypatch):
    monkeypatch.setattr(research, "fetch_article", lambda url: article(page_kind="index", discovered_links=[{"url": "https://example.com/new"}]))
    result = research.research_hydrate(Client(), item_id="one", since="2026-09-01")
    assert result["status"] == "rejected"
    assert len(result["discovered_links"]) == 1
    assert len(result["queued_children"]) == 1


def test_firecrawl_ingest_real_body_and_metadata():
    client = Client()
    result = research.research_ingest(client, item_id="one", since="2026-09-01", payload={"data": {
        "markdown": "# Concrete announcement\n" + "A concrete feature is available to developers. " * 20,
        "metadata": {"datePublished": "2026-09-05"},
    }})
    assert result["status"] == "ready"
    assert client.writes[0]["tool_name"] == "post_firecrawl_scrape"


def test_http_failure_never_claims_success(monkeypatch):
    def fail(url):
        raise OSError("offline")
    monkeypatch.setattr(research, "fetch_article", fail)
    result = research.research_hydrate(Client(), item_id="one", since="2026-09-01")
    assert result["status"] == "failed"
    assert result["fallback"]["arguments"]["formats"] == ["markdown"]


def test_discovery_scoped_deduplicated_and_without_fake_date():
    client = Client()
    research._queue_children(client, client.items[0], [
        {"url": "https://other.example/new"},
        {"url": "https://example.com/new", "published_at": "2026-09-06"},
        {"url": "https://example.com/new"},
    ])
    assert len(client.queued) == 1
    assert client.queued[0]["published_at"] is None
    assert client.queued[0]["raw_metadata"]["discovery_only"] is True
    assert client.queued[0]["target_id"] == "t"


def test_run_follows_index_children_and_is_bounded(monkeypatch):
    client = Client()
    client.items[0]["url"] = "https://example.com/blog"
    def fetch(url):
        if url.endswith("/blog"):
            return article(page_kind="index", discovered_links=[{"url": "https://example.com/new"}])
        return article()
    monkeypatch.setattr(research, "fetch_article", fetch)
    result = research.research_run(client, since="2026-09-01", limit=2)
    assert result["attempted"] == 2
    assert [row["status"] for row in result["results"]] == ["rejected", "ready"]
    assert not result["fallback_plans"]


def test_discover_reads_configured_blog_and_queues_twenty(tmp_path, monkeypatch):
    from test_cli_operations import project
    _, repository = project(tmp_path)
    monkeypatch.setattr(research, "fetch_article", lambda url: article(page_kind="index", discovered_links=[
        {"url": "https://composio.dev/blog/article-%d" % n, "title": "Article %d" % n} for n in range(35)]))
    client = Client()
    result = research.research_discover(repository, client)
    assert result["targets_checked"] == 1
    assert result["results"][0]["queued"] == 20
    assert len(client.queued) == 21  # seed plus bounded children
    assert not result["fallback_plans"]


def test_discover_inaccessible_blog_returns_exact_fixed_plan(tmp_path, monkeypatch):
    from test_cli_operations import project
    _, repository = project(tmp_path)
    def fail(url):
        raise OSError("blocked")
    monkeypatch.setattr(research, "fetch_article", fail)
    result = research.research_discover(repository, Client(), target="composio")
    assert result["fallback_plans"][0]["tool_name"] == "post_firecrawl_scrape"
    assert result["fallback_plans"][0]["arguments"]["url"] == "https://composio.dev/blog"


def test_discover_uses_rss_when_no_blog(tmp_path, monkeypatch):
    from test_cli_operations import project
    from intelligence.collectors import CollectionPage
    from intelligence.collectors.rss import RSSCollector
    from intelligence.normalize import NormalizedItem
    _, repository = project(tmp_path)
    raw = repository.load_raw()
    raw["targets"][0]["channels"][0].update(type="rss", collector="rss")
    repository.save(raw)
    feed_item = NormalizedItem(external_id="feed-one", target_slug="composio", channel_slug="composio-blog",
        url="https://composio.dev/blog/new", title="New release", author=None,
        published_at="2026-09-05", content_text="Original feed body")
    monkeypatch.setattr(RSSCollector, "collect", lambda self, channel: CollectionPage.of([feed_item]))
    client = Client()
    result = research.research_discover(repository, client)
    assert result["results"][0]["queued"] == 1
    assert client.queued[0]["published_at"].startswith("2026-09-05")


def test_exact_rss_date_evidence_can_complete_article(monkeypatch):
    client = Client()
    client.items[0].update(published_at="2026-09-05", raw_metadata_json='{"platform":"rss"}')
    monkeypatch.setattr(research, "fetch_article", lambda url: article(published_at=None, publication_evidence=None))
    result = research.research_hydrate(client, item_id="one", since="2026-09-01")
    assert result["status"] == "ready"
    assert client.writes[0]["date_evidence"]["kind"] == "feed"


def test_linkblog_child_has_no_inherited_date(monkeypatch):
    client = Client()
    client.items[0]["url"] = "https://simonwillison.net/2026/Sep/5/note/"
    monkeypatch.setattr(research, "fetch_article", lambda url: article(primary_link="https://til.simonwillison.net/python/example"))
    result = research.research_hydrate(client, item_id="one", since="2026-09-01")
    assert len(result["queued_children"]) == 1
    assert client.queued[0]["published_at"] is None


def test_batch_envelope_selects_matching_successful_scrape():
    import json
    client = Client()
    def row(url, successful=True, tool="post_firecrawl_scrape"):
        return {"call_id": url, "tool": tool, "successful": successful, "upstream_status": 200,
                "data": {"success": True, "data": {"markdown": "Article body", "metadata": {"sourceURL": url}}}}
    batch = {"results": [row("https://different.example/article"), row(client.items[0]["url"])]}
    envelope = {"content": [{"type": "text", "text": json.dumps(batch)}], "structuredContent": batch}
    assert research._firecrawl_document(envelope, client.items[0])["metadata"]["sourceURL"] == client.items[0]["url"]


def test_batch_failed_or_ambiguous_calls_rejected():
    import pytest
    from intelligence.catalog import CatalogError
    item = Client().items[0]
    document = {"markdown": "body", "metadata": {}}
    with pytest.raises(CatalogError):
        research._firecrawl_document({"results": [{"tool": "post_firecrawl_scrape", "successful": False, "data": document}]}, item)
    with pytest.raises(CatalogError):
        research._firecrawl_document({"results": [{"data": document}, {"data": document}]}, item)
