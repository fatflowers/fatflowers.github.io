-- Report selection previously re-scanned report membership and canonical URLs
-- once for every candidate item.  These reverse lookup indexes make both
-- duplicate checks point lookups and keep the publication window indexable.
CREATE INDEX IF NOT EXISTS idx_report_items_item_report
  ON report_items(item_id, report_id);

CREATE INDEX IF NOT EXISTS idx_items_canonical
  ON items(canonical_url, id)
  WHERE canonical_url IS NOT NULL AND canonical_url != '';

CREATE INDEX IF NOT EXISTS idx_items_report_window
  ON items(is_baseline, julianday(published_at), target_id, id)
  WHERE is_baseline = 0;

CREATE INDEX IF NOT EXISTS idx_reports_recent_daily
  ON reports(report_status, julianday(published_at), id)
  WHERE edition IN ('morning', 'midday', 'evening');

ANALYZE;
