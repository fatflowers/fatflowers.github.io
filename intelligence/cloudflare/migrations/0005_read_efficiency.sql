-- Expression indexes preserve mixed ISO timezone semantics used by existing SQL.
CREATE INDEX IF NOT EXISTS idx_items_published_jd ON items(julianday(published_at));
CREATE INDEX IF NOT EXISTS idx_items_fetched_jd ON items(julianday(fetched_at));
CREATE INDEX IF NOT EXISTS idx_items_target_fetched_jd ON items(target_id, julianday(fetched_at));
CREATE INDEX IF NOT EXISTS idx_runs_created ON pipeline_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_published_jd ON reports(report_status, julianday(published_at));
