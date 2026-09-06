"""Exercise the Worker's real SQL against SQLite, including D1 JSON filtering."""
from pathlib import Path
import re
import sqlite3


def test_report_query_excludes_baseline_discovery_undated_and_old():
    root = Path(__file__).resolve().parents[1]
    # Compact fixture schema keeps unrelated write constraints out of this
    # regression test; the actual SELECT is read directly from Worker source.
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE items(id, target_id, channel_id, published_at, fetched_at, is_baseline, raw_metadata_json, enrichment_status, canonical_url);
        CREATE TABLE analyses(item_id, headline, summary, key_change, why_it_matters, company_impact,
            importance, confidence, topics_json, watch_next_json, evidence_json, model, prompt_version, analyzed_at);
        CREATE TABLE targets(id, slug, name);
        CREATE TABLE channels(id, slug, name);
        CREATE TABLE target_tags(target_id, tag_id);
        CREATE TABLE channel_tags(channel_id, tag_id);
        CREATE TABLE tags(id, slug);
        CREATE TABLE reports(id, report_status, edition);
        CREATE TABLE report_items(report_id, item_id);
        INSERT INTO targets VALUES ('t','target','Target');
        INSERT INTO channels VALUES ('c','channel','Channel');
    """)
    for item_id, published, baseline, metadata in [
        ("current", "2026-09-05T05:00:00Z", 0, None),
        ("baseline", "2026-09-05T05:00:00Z", 1, None),
        ("discovery", "2026-09-05T05:00:00Z", 0, '{"discovery_only":true}'),
        ("undated", None, 0, None),
        ("old", "2025-02-24T00:00:00Z", 0, None),
    ]:
        db.execute("INSERT INTO items VALUES (?, 't', 'c', ?, '2026-09-05T06:00:00Z', ?, ?, NULL, 'https://example.com/article')",
                   (item_id, published, baseline, metadata))
        db.execute("INSERT INTO analyses(item_id, importance) VALUES (?, 4)", (item_id,))
    source = (root / "cloudflare/worker/tracking.ts").read_text()
    query = re.search(r"env.DB.prepare\(`(SELECT i\.\*,.*?)`\)", source, re.S).group(1)
    results = db.execute(query, ("2026-09-05T00:00:00Z", "2026-09-06T00:00:00Z", 1,
                                 0, None, None, None, None, None, 500)).fetchall()
    assert [row[0] for row in results] == ["current"]
    db.execute("INSERT INTO reports VALUES ('r','published','morning')")
    db.execute("INSERT INTO report_items VALUES ('r','current')")
    params = ("2026-09-05T00:00:00Z", "2026-09-06T00:00:00Z", 1,
              0, None, None, None, None, None, 500)
    assert db.execute(query, params).fetchall() == []
    assert [row[0] for row in db.execute(query, (*params[:3], 1, *params[4:]))] == ["current"]

    # A cross-channel copy can have a distinct item ID, but is the same event.
    db.execute("INSERT INTO items SELECT 'retelling', target_id, 'c', published_at, fetched_at, 0, NULL, NULL, canonical_url FROM items WHERE id='current'")
    db.execute("INSERT INTO analyses(item_id, importance) VALUES ('retelling', 4)")
    assert db.execute(query, params).fetchall() == []
    assert {row[0] for row in db.execute(query, (*params[:3], 1, *params[4:]))} == {"current", "retelling"}

    # A genuinely later observed change at a permanent docs URL must remain
    # eligible. The editorial layer validates the complete before/after proof.
    import json
    first = json.dumps({"date_kind": "observed_change", "web_diff": {"after_hash": "a" * 64}})
    db.execute("UPDATE items SET raw_metadata_json=? WHERE id='current'", (first,))
    db.execute("UPDATE items SET raw_metadata_json=? WHERE id='retelling'", (first,))
    assert db.execute(query, params).fetchall() == []
    later = json.dumps({"date_kind": "observed_change", "web_diff": {"after_hash": "b" * 64}})
    db.execute("UPDATE items SET raw_metadata_json=? WHERE id='retelling'", (later,))
    assert [row[0] for row in db.execute(query, params)] == ["retelling"]
