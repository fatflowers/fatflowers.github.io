from intelligence.enrichment import enrich_article, discover_links
from intelligence.collectors.http import HTTPCollector, WebDiffCollector
from intelligence.collectors.base import ChannelSpec


def test_official_article_extracts_prose_and_structured_publication_not_modified():
    document = '''<html><head><title>Site</title><script type="application/ld+json">
    {"@graph":[{"@type":"WebSite","datePublished":"1999-01-01"},
      {"@type":"NewsArticle","headline":"New API","datePublished":"2026-09-05T11:00:00-07:00","dateModified":"2026-09-06T01:00:00Z"}]}
    </script></head><body><nav>Products Pricing Login</nav><main><article>
    <header><h1>New API</h1></header><p>Developers can now return <strong>interactive widgets</strong> in tool results.</p>
    <blockquote>“Widgets run in an isolated frame.”</blockquote><p>Existing text integrations remain supported.</p>
    <div class="newsletter">Subscribe now</div></article></main><footer>Copyright Site</footer></body></html>'''
    result = enrich_article("https://example.com/news/new-api", html=document)
    assert result["published_at"] == "2026-09-05T11:00:00-07:00"
    assert result["publication_evidence"]["source"] == "jsonld.datePublished"
    assert result["title"] == "New API"
    assert "interactive widgets" in result["content_text"]
    assert "“Widgets run in an isolated frame.”" in result["content_text"]
    assert all(noise not in result["content_text"] for noise in ["Products Pricing", "Subscribe", "Copyright"])
    assert result["page_kind"] == "article"


def test_date_only_stays_explicit_and_updated_is_not_publication():
    result = enrich_article("https://example.com/news/a", html='<article><h1>A</h1><time datetime="2026-09-05">5 September</time><p>Full body</p></article>')
    assert result["published_at"] == "2026-09-05"
    assert result["publication_precision"] == "day"
    result = enrich_article("https://example.com/news/a", html='<article><h1>A</h1><time class="updated" datetime="2026-09-06">Updated</time></article>', metadata={"dateModified": "2026-09-06", "fetched_at": "2026-09-06"})
    assert result["published_at"] is None


def test_firecrawl_markdown_preserves_body_and_metadata_evidence():
    result = enrich_article("https://example.com/blog/a", markdown="# New API\n\nDevelopers can use widgets.\n\n> Original quote.", metadata={"article:published_time": "2026-09-05T10:00:00Z"})
    assert result["title"] == "New API"
    assert "> Original quote." in result["content_text"]
    assert result["publication_evidence"]["value"] == "2026-09-05T10:00:00Z"
    assert result["body_provenance"]["source"] == "firecrawl_markdown"


def test_index_discovery_dates_remain_local_and_no_index_publication():
    html = '''<main><h1>News</h1><article><time datetime="2026-09-05"></time><a href="/news/new-api">New API launched</a></article>
    <article><a href="/news/another">Another release</a></article>
    <a href="https://elsewhere.test/news">External news story</a><a href="/category/ai">AI category</a>
    <a href="/news/page/2">Next page</a></main><nav><a href="/company/about">About company</a></nav>'''
    links = discover_links("https://example.com/news", html)
    assert [link["url"] for link in links] == ["https://example.com/news/new-api", "https://example.com/news/another"]
    assert links[0]["published_at"] == "2026-09-05"
    assert links[1]["published_at"] is None
    result = enrich_article("https://example.com/news", html=html)
    assert result["page_kind"] == "index"
    assert result["published_at"] is None


def test_linkblog_is_commentary_even_with_an_article_date():
    result = enrich_article("https://simonwillison.net/2026/Sep/5/something/", html='<article><h1>My reading notes</h1><time datetime="2026-09-05"></time><p>This announcement matters.</p></article>')
    assert result["page_kind"] == "commentary"


def test_visible_official_date_qualifies_but_prose_date_does_not():
    result = enrich_article("https://example.com/news/a", html='<main><h1>Launch</h1><div class="publication-date">Published September 5, 2026</div><p>Compatible with the January 1, 2025 protocol.</p></main>')
    assert result["published_at"] == "2026-09-05"
    assert result["publication_evidence"]["source"] == "article.visible_date"
    result = enrich_article("https://example.com/news/a", html='<main><h1>Launch</h1><p>Compatible with the January 1, 2025 protocol.</p></main>')
    assert result["published_at"] is None


def test_last_modified_http_header_does_not_make_new_article():
    collector = HTTPCollector(fetcher=lambda *_: (b"<article><h1>Old undated document</h1><p>Longstanding feature.</p></article>", {"Last-Modified": "Sun, 06 Sep 2026 01:00:00 GMT"}))
    item = collector.collect(ChannelSpec("test", "web", "http", "http", url="https://example.com/news/old")).items[0]
    assert item.published_at is None
    assert item.metadata["publication_evidence"] is None


def test_web_diff_baseline_then_real_change_has_before_after_evidence():
    documents = iter([b"<main>Price $10</main>", b"<main>Price $20</main>"])
    collector = WebDiffCollector(HTTPCollector(fetcher=lambda *_: (next(documents), {})))
    channel = ChannelSpec("test", "pricing", "web_diff", "http", url="https://example.com/pricing")
    first = collector.collect(channel)
    assert first.items == ()
    assert first.metadata["baseline"] is True
    second = collector.collect(channel, first.next_cursor)
    diff = second.items[0].metadata["web_diff"]
    assert diff["before_text"] == "Price $10"
    assert diff["after_text"] == "Price $20"
    assert diff["before_hash"] != diff["after_hash"]
    assert diff["previous_observed_at"]


def test_simon_real_entry_markup_ordinal_date_excludes_recent_articles():
    html = '''<div id="primary"><div class="entry entryPage"><div data-permalink-context="/2026/Sep/4/rogue-agent-wikis/">
    <h2>OpenAI’s rogue agents were caught communicating via public wikis</h2><p class="mobile-date">4th September 2026</p>
    <p>UseMod allows GET requests to update data.</p></div>
    <div class="entryFooter">Posted <a href="/2026/Sep/4/">4th September 2026</a> at 5:38 pm</div></div>
    <div class="recent-articles"><h2>More recent articles</h2><p>Unrelated story</p></div></div>'''
    result = enrich_article("https://simonwillison.net/2026/Sep/4/rogue-agent-wikis/", html=html)
    assert result["published_at"] == "2026-09-04"
    assert result["publication_evidence"]["value"] == "4th September 2026"
    assert "Unrelated story" not in result["content_text"]
    assert result["title"].startswith("OpenAI’s rogue agents")


def test_simon_linkblog_primary_link_and_til_created_date_are_separate_evidence():
    html = '''<div class="entry entryPage"><p class="mobile-date-eyebrow">5th September 2026</p>
    <div class="beat"><span class="beat-title"><a href="https://til.simonwillison.net/llms/blender-coding-agents-macos">Using Blender with coding agents on macOS</a></span>
    <div class="beat-note blogmark-body"><p>Short commentary.</p></div></div></div>'''
    result = enrich_article("https://simonwillison.net/2026/Sep/5/blender-coding-agents-macos/", html=html)
    assert result["primary_link"] == "https://til.simonwillison.net/llms/blender-coding-agents-macos"
    assert result["page_kind"] == "commentary"
    result = enrich_article(result["primary_link"], html='''<section class="body"><h1>Using Blender with coding agents on macOS</h1><p>Run Blender with --background --python scene.py.</p>
    <p class="created">Created 2026-09-05T08:42:28-07:00, updated 2026-09-05T08:47:13-07:00 · <a href="/history">History</a></p></section>''')
    assert result["published_at"] == "2026-09-05T08:42:28-07:00"
    assert result["publication_evidence"]["source"] == "article.created_timestamp"


def test_firecrawl_markdown_index_discovers_same_host_article_links():
    result = enrich_article("https://example.com/blog", markdown='''# Blog
    [New API released](https://example.com/blog/new-api)
    [AI category](https://example.com/category/ai)
    [External story](https://elsewhere.test/story)
    ![Decorative image](https://example.com/cover.png)''')
    assert result["page_kind"] == "index"
    assert [link["url"] for link in result["discovered_links"]] == ["https://example.com/blog/new-api"]
def test_developer_blog_date_outside_markdown_article():
    from intelligence.enrichment import enrich_article
    html='''<main><div><div><span class="text-default font-medium">Sep 4, 2026</span><span>Codex</span></div>
      <div><h1>Practical architecture</h1></div></div><article><p>Concrete construction method and results.</p></article></main>'''
    result=enrich_article('https://developers.openai.com/blog/practical-architecture',html=html)
    assert result['published_at']=='2026-09-04'
    assert result['publication_evidence']['source']=='article.developer_blog_header'
def test_blog_discovery_filters_navigation_before_spending_link_budget():
    nav=''.join(f'<a href="/api/docs/page-{n}">Documentation page {n}</a>' for n in range(100))
    html=nav+'<a href="/blog/new-practice">New engineering practice</a><a href="/blog/topic/code">Code topic</a>'
    links=discover_links('https://developers.openai.com/blog/',html)
    assert [x['url'] for x in links]==['https://developers.openai.com/blog/new-practice']
def test_anthropic_engineering_header_date_is_not_site_updated_at():
    html='''<header><h1>Containment methods</h1><p class="HeroEngineering-module__abc__date">Published May 25, 2026</p></header>
    <article><p>Concrete containment methods for agents.</p></article><footer>Updated September 4, 2026</footer>'''
    result=enrich_article('https://www.anthropic.com/engineering/how-we-contain-claude',html=html)
    assert result['published_at']=='2026-05-25'
    assert result['publication_evidence']['source']=='article.engineering_header'
