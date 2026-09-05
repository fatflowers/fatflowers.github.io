from intelligence.normalize import DedupeIndex, NormalizedItem, canonicalize_url, content_hash
from intelligence.normalize.diff import compare_text


def make_item(**changes):
    values = {
        "external_id": None,
        "target_slug": "composio",
        "channel_slug": "composio-blog",
        "url": "HTTPS://Example.COM/post/?utm_source=x&b=2&a=1#top",
        "title": " A   title ",
        "author": " Simon  Sun ",
        "published_at": "2026-09-05T10:00:00+08:00",
        "content_text": "First  line\r\n\r\n\r\nSecond line",
        "metadata": {},
    }
    values.update(changes)
    return NormalizedItem(**values)


def test_normalized_item_is_stable():
    item = make_item()
    assert item.canonical_url == "https://example.com/post?a=1&b=2"
    assert item.title == "A title"
    assert item.author == "Simon Sun"
    assert item.published_at == "2026-09-05T02:00:00Z"
    assert item.content_text == "First line\n\nSecond line"


def test_url_normalization_preserves_non_tracking_query():
    assert canonicalize_url("https://EXAMPLE.com:443/a/?z=2&utm_medium=social&z=1#x") == "https://example.com/a?z=1&z=2"


def test_dedupe_prioritizes_external_id_then_url():
    index = DedupeIndex()
    first = make_item(external_id="42")
    changed = make_item(external_id="42", url="https://example.com/else", content_text="changed")
    assert index.add(first)
    assert not index.add(changed)
    assert len(content_hash(first)) == 64


def test_text_diff_ignores_incidental_whitespace():
    same = compare_text("one   two\n", "one two")
    changed = compare_text("one", "two")
    assert not same.changed
    assert changed.changed
    assert "-one" in changed.unified_diff
    assert "+two" in changed.unified_diff
