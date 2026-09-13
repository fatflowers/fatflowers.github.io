CREATE INDEX IF NOT EXISTS idx_analyses_feed_changes
  ON analyses(analyzed_at DESC, item_id);

CREATE INDEX IF NOT EXISTS idx_items_feed_published
  ON items(julianday(published_at) DESC, id DESC)
  WHERE is_baseline = 0;

ANALYZE;
