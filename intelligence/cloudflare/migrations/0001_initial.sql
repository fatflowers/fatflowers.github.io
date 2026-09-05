PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS targets (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (target_type IN ('company', 'product', 'person', 'project', 'topic')),
  description TEXT,
  priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'critical')),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
  id TEXT PRIMARY KEY,
  target_id TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  channel_type TEXT NOT NULL,
  collector_type TEXT NOT NULL,
  url TEXT,
  handle TEXT,
  interval_minutes INTEGER NOT NULL DEFAULT 60 CHECK (interval_minutes BETWEEN 5 AND 43200),
  priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'critical')),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  tool_binding TEXT,
  config_json TEXT,
  cursor_json TEXT,
  last_checked_at TEXT,
  last_success_at TEXT,
  last_error_at TEXT,
  last_error TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_channels_target ON channels(target_id);
CREATE INDEX IF NOT EXISTS idx_channels_due ON channels(enabled, last_checked_at);

CREATE TABLE IF NOT EXISTS tags (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  tag_type TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS target_tags (
  target_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  PRIMARY KEY (target_id, tag_id),
  FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE,
  FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS channel_tags (
  channel_id TEXT NOT NULL,
  tag_id TEXT NOT NULL,
  PRIMARY KEY (channel_id, tag_id),
  FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE CASCADE,
  FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  target_id TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  external_id TEXT,
  url TEXT NOT NULL,
  canonical_url TEXT,
  title TEXT,
  author TEXT,
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  content_text TEXT,
  content_hash TEXT NOT NULL,
  language TEXT,
  raw_metadata_json TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(channel_id, content_hash),
  FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE RESTRICT,
  FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_items_external_id
  ON items(channel_id, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_target_channel ON items(target_id, channel_id, fetched_at);

CREATE TABLE IF NOT EXISTS analyses (
  item_id TEXT PRIMARY KEY,
  summary TEXT NOT NULL,
  key_change TEXT,
  why_it_matters TEXT,
  company_impact TEXT,
  importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  topics_json TEXT,
  watch_next_json TEXT,
  evidence_json TEXT,
  model TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  analyzed_at TEXT NOT NULL,
  FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_analyses_importance ON analyses(importance, analyzed_at);

CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  report_date TEXT NOT NULL,
  edition TEXT NOT NULL CHECK (edition IN ('morning', 'midday', 'evening', 'weekly', 'ad-hoc')),
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  title TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  report_status TEXT NOT NULL CHECK (report_status IN ('draft', 'validating', 'ready', 'published', 'failed')),
  content_markdown TEXT NOT NULL,
  published_url TEXT,
  git_commit TEXT,
  created_at TEXT NOT NULL,
  published_at TEXT,
  UNIQUE(report_date, edition, window_start, window_end)
);

CREATE TABLE IF NOT EXISTS report_items (
  report_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  rank INTEGER NOT NULL CHECK (rank >= 0),
  section TEXT NOT NULL,
  PRIMARY KEY (report_id, item_id),
  FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE,
  FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_report_items_rank ON report_items(report_id, rank);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  multica_run_id TEXT,
  target_id TEXT,
  channel_id TEXT,
  run_status TEXT NOT NULL CHECK (run_status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
  started_at TEXT,
  finished_at TEXT,
  attempt INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
  item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
  error_code TEXT,
  error_summary TEXT,
  metadata_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE SET NULL,
  FOREIGN KEY (channel_id) REFERENCES channels(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(run_status, created_at);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_multica ON pipeline_runs(multica_run_id);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  multica_issue_id TEXT,
  git_commit TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id, created_at);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  idempotency_key TEXT NOT NULL,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  response_status INTEGER NOT NULL,
  response_body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  PRIMARY KEY (idempotency_key, method, path)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_expiry ON idempotency_keys(expires_at);
