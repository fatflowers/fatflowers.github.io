ALTER TABLE items ADD COLUMN is_baseline INTEGER NOT NULL DEFAULT 0
  CHECK (is_baseline IN (0, 1));

UPDATE items
SET is_baseline = 1
WHERE id NOT IN (SELECT item_id FROM analyses);

CREATE INDEX IF NOT EXISTS idx_items_pending_analysis
  ON items(is_baseline, fetched_at)
  WHERE is_baseline = 0;
