ALTER TABLE items ADD COLUMN content_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN enrichment_status TEXT CHECK(enrichment_status IN ('ready','rejected','failed'));
ALTER TABLE items ADD COLUMN enrichment_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE items ADD COLUMN enrichment_reason TEXT;
ALTER TABLE items ADD COLUMN enriched_at TEXT;
CREATE TABLE item_enrichments (
  id TEXT PRIMARY KEY,
  item_id TEXT NOT NULL REFERENCES items(id),
  revision INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ready','rejected','failed')),
  reason TEXT NOT NULL,
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  previous_analysis_json TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(item_id, revision)
);
CREATE INDEX idx_items_enrichment ON items(enrichment_status, fetched_at);
