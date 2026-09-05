import { ApiError, enumString, jsonText, optionalString, parseLimit, requireArray, requireIsoDate, requireNumber, requireObject, requireString } from "./http.ts";
import type { ApiResponse, AuthContext } from "./types.ts";

const REPORT_EDITIONS = ["morning", "midday", "evening", "weekly", "ad-hoc"] as const;
const REPORT_STATUSES = ["draft", "validating", "ready", "published", "failed"] as const;
const RUN_STATUSES = ["pending", "running", "succeeded", "failed", "skipped"] as const;

export async function getReportInput({ env, url }: AuthContext): Promise<ApiResponse> {
  const from = requireIsoDate(url.searchParams.get("from"), "from");
  const to = requireIsoDate(url.searchParams.get("to"), "to");
  if (Date.parse(from) >= Date.parse(to)) throw new ApiError(400, "invalid_time_window", "from must be before to");
  const rawImportance = url.searchParams.get("min_importance");
  const minImportance = rawImportance === null ? 1 : requireNumber(Number(rawImportance), "min_importance", 1, 5);
  const limit = parseLimit(url, 500, 1_000);
  const targetId = url.searchParams.get("target_id");
  const tag = url.searchParams.get("tag");
  const rows = await env.DB.prepare(`SELECT i.*, a.summary, a.key_change, a.why_it_matters,
      a.company_impact, a.importance, a.confidence, a.topics_json, a.watch_next_json,
      a.evidence_json, a.model, a.prompt_version, a.analyzed_at,
      t.slug AS target_slug, t.name AS target_name, c.slug AS channel_slug, c.name AS channel_name
    FROM items i
    JOIN analyses a ON a.item_id = i.id
    JOIN targets t ON t.id = i.target_id
    JOIN channels c ON c.id = i.channel_id
    WHERE datetime(COALESCE(i.published_at, i.fetched_at)) >= datetime(?)
      AND datetime(COALESCE(i.published_at, i.fetched_at)) < datetime(?)
      AND a.importance >= ?
      AND (? IS NULL OR i.target_id = ?)
      AND (? IS NULL OR EXISTS (
        SELECT 1 FROM target_tags tt JOIN tags tg ON tg.id = tt.tag_id
        WHERE tt.target_id = i.target_id AND tg.slug = ?
      ) OR EXISTS (
        SELECT 1 FROM channel_tags ct JOIN tags cg ON cg.id = ct.tag_id
        WHERE ct.channel_id = i.channel_id AND cg.slug = ?
      ))
    ORDER BY a.importance DESC, COALESCE(i.published_at, i.fetched_at) DESC, i.id
    LIMIT ?`).bind(from, to, minImportance, targetId, targetId, tag, tag, tag, limit).all();
  return { status: 200, body: { window: { from, to }, items: rows.results ?? [] } };
}

export async function createReport({ env, body }: AuthContext): Promise<ApiResponse> {
  const payload = requireObject(body);
  const now = new Date().toISOString();
  const id = requireString(payload.id, "id", { max: 128 })!;
  const reportDate = requireString(payload.report_date, "report_date", { max: 32 })!;
  const edition = enumString(payload.edition, "edition", REPORT_EDITIONS);
  const windowStart = requireIsoDate(payload.window_start, "window_start");
  const windowEnd = requireIsoDate(payload.window_end, "window_end");
  if (Date.parse(windowStart) >= Date.parse(windowEnd)) throw new ApiError(400, "invalid_time_window", "window_start must be before window_end");
  const title = requireString(payload.title, "title", { max: 500 })!;
  const slug = requireString(payload.slug, "slug", { max: 256 })!;
  const contentMarkdown = requireString(payload.content_markdown, "content_markdown", { max: 500_000 })!;
  const createdAt = payload.created_at == null ? now : requireIsoDate(payload.created_at, "created_at");
  const reportItems = requireArray(payload.items ?? [], "items", 1_000).map((entry, index) => {
    const value = requireObject(entry, `items[${index}]`);
    return {
      itemId: requireString(value.item_id, `items[${index}].item_id`, { max: 128 })!,
      rank: requireNumber(value.rank, `items[${index}].rank`, 0, 100_000),
      section: requireString(value.section, `items[${index}].section`, { max: 256 })!,
    };
  });

  const existing = await env.DB.prepare("SELECT report_status FROM reports WHERE id = ?").bind(id).first<{ report_status: string }>();
  if (existing?.report_status === "published") {
    throw new ApiError(409, "published_report_immutable", "A published report cannot be overwritten");
  }
  const statements = [
    env.DB.prepare(`INSERT INTO reports
      (id, report_date, edition, window_start, window_end, title, slug, report_status,
       content_markdown, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
      ON CONFLICT(id) DO UPDATE SET report_date=excluded.report_date, edition=excluded.edition,
        window_start=excluded.window_start, window_end=excluded.window_end, title=excluded.title,
        slug=excluded.slug, report_status='draft', content_markdown=excluded.content_markdown`)
      .bind(id, reportDate, edition, windowStart, windowEnd, title, slug, contentMarkdown, createdAt),
    env.DB.prepare("DELETE FROM report_items WHERE report_id = ?").bind(id),
    ...reportItems.map((item) => env.DB.prepare(`INSERT INTO report_items
      (report_id, item_id, rank, section) VALUES (?, ?, ?, ?)`)
      .bind(id, item.itemId, item.rank, item.section)),
  ];
  await env.DB.batch(statements);
  return { status: existing ? 200 : 201, body: { id, report_status: "draft", item_count: reportItems.length } };
}

export async function updateReportStatus({ env, body, params }: AuthContext): Promise<ApiResponse> {
  const payload = requireObject(body);
  const id = requireString(params.id, "id", { max: 128 })!;
  const next = enumString(payload.report_status, "report_status", REPORT_STATUSES);
  const current = await env.DB.prepare("SELECT * FROM reports WHERE id = ?").bind(id).first<Record<string, unknown>>();
  if (!current) throw new ApiError(404, "report_not_found", "Report not found");
  const currentStatus = String(current.report_status);
  if (!canTransitionReport(currentStatus, next)) {
    throw new ApiError(409, "invalid_report_transition", `Cannot transition report from ${currentStatus} to ${next}`);
  }
  const publishedUrl = optionalString(payload.published_url, "published_url", 4_096);
  const gitCommit = optionalString(payload.git_commit, "git_commit", 128);
  if (next === "published" && (!publishedUrl || !gitCommit)) {
    throw new ApiError(400, "missing_publication_evidence", "published_url and git_commit are required when publishing");
  }
  const publishedAt = next === "published"
    ? (payload.published_at == null ? new Date().toISOString() : requireIsoDate(payload.published_at, "published_at"))
    : null;
  await env.DB.prepare(`UPDATE reports SET report_status = ?,
    published_url = COALESCE(?, published_url), git_commit = COALESCE(?, git_commit),
    published_at = COALESCE(?, published_at) WHERE id = ?`)
    .bind(next, publishedUrl, gitCommit, publishedAt, id).run();
  return { status: 200, body: { id, previous_status: currentStatus, report_status: next } };
}

function canTransitionReport(current: string, next: string): boolean {
  if (current === next) return true;
  const allowed: Record<string, string[]> = {
    draft: ["validating", "failed"],
    validating: ["ready", "failed"],
    ready: ["published", "failed"],
    failed: ["draft"],
    published: [],
  };
  return allowed[current]?.includes(next) ?? false;
}

export async function createRun({ env, body }: AuthContext): Promise<ApiResponse> {
  const payload = requireObject(body);
  const id = requireString(payload.id, "id", { max: 128 })!;
  const now = new Date().toISOString();
  const status = payload.run_status === undefined ? "pending" : enumString(payload.run_status, "run_status", RUN_STATUSES);
  const result = await env.DB.prepare(`INSERT OR IGNORE INTO pipeline_runs
    (id, run_type, trigger_type, multica_run_id, target_id, channel_id, run_status,
     started_at, finished_at, attempt, item_count, error_code, error_summary, metadata_json, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(id,
      requireString(payload.run_type, "run_type", { max: 128 }),
      requireString(payload.trigger_type, "trigger_type", { max: 128 }),
      optionalString(payload.multica_run_id, "multica_run_id", 256),
      optionalString(payload.target_id, "target_id", 128),
      optionalString(payload.channel_id, "channel_id", 128),
      status,
      payload.started_at == null ? null : requireIsoDate(payload.started_at, "started_at"),
      payload.finished_at == null ? null : requireIsoDate(payload.finished_at, "finished_at"),
      payload.attempt == null ? 1 : requireNumber(payload.attempt, "attempt", 1, 1_000),
      payload.item_count == null ? 0 : requireNumber(payload.item_count, "item_count", 0, 10_000_000),
      optionalString(payload.error_code, "error_code", 128),
      optionalString(payload.error_summary, "error_summary", 2_000),
      jsonText(payload.metadata, "metadata", 50_000),
      payload.created_at == null ? now : requireIsoDate(payload.created_at, "created_at"),
    ).run();
  const inserted = (result.meta?.changes ?? 0) > 0;
  return { status: inserted ? 201 : 200, body: { id, created: inserted, run_status: status } };
}

export async function getRun({ env, params }: AuthContext): Promise<ApiResponse> {
  const id = requireString(params.id, "id", { max: 128 })!;
  const run = await env.DB.prepare("SELECT * FROM pipeline_runs WHERE id = ?").bind(id).first<Record<string, unknown>>();
  if (!run) throw new ApiError(404, "run_not_found", "Run not found");
  return { status: 200, body: { run } };
}

export async function listRuns({ env, url }: AuthContext): Promise<ApiResponse> {
  const limit = parseLimit(url, 20, 500);
  const rawStatus = url.searchParams.get("status");
  const status = rawStatus === null ? null : enumString(rawStatus, "status", RUN_STATUSES);
  const rows = await env.DB.prepare(`SELECT * FROM pipeline_runs
    WHERE (? IS NULL OR run_status = ?)
    ORDER BY created_at DESC LIMIT ?`).bind(status, status, limit).all();
  return { status: 200, body: { runs: rows.results ?? [] } };
}

export async function updateRun({ env, body, params }: AuthContext): Promise<ApiResponse> {
  const payload = requireObject(body);
  const id = requireString(params.id, "id", { max: 128 })!;
  const current = await env.DB.prepare("SELECT * FROM pipeline_runs WHERE id = ?").bind(id).first<Record<string, unknown>>();
  if (!current) throw new ApiError(404, "run_not_found", "Run not found");
  const next = enumString(payload.run_status, "run_status", RUN_STATUSES);
  const currentStatus = String(current.run_status);
  if (!canTransitionRun(currentStatus, next)) {
    throw new ApiError(409, "invalid_run_transition", `Cannot transition run from ${currentStatus} to ${next}`);
  }
  const startedAt = payload.started_at == null ? (next === "running" ? new Date().toISOString() : null) : requireIsoDate(payload.started_at, "started_at");
  const finishedAt = payload.finished_at == null
    ? (["succeeded", "failed", "skipped"].includes(next) ? new Date().toISOString() : null)
    : requireIsoDate(payload.finished_at, "finished_at");
  await env.DB.prepare(`UPDATE pipeline_runs SET run_status = ?, started_at = COALESCE(?, started_at),
    finished_at = COALESCE(?, finished_at), item_count = COALESCE(?, item_count),
    error_code = ?, error_summary = ?, metadata_json = COALESCE(?, metadata_json) WHERE id = ?`)
    .bind(next, startedAt, finishedAt,
      payload.item_count == null ? null : requireNumber(payload.item_count, "item_count", 0, 10_000_000),
      optionalString(payload.error_code, "error_code", 128),
      optionalString(payload.error_summary, "error_summary", 2_000),
      jsonText(payload.metadata, "metadata", 50_000), id).run();
  return { status: 200, body: { id, previous_status: currentStatus, run_status: next } };
}

function canTransitionRun(current: string, next: string): boolean {
  if (current === next) return true;
  const allowed: Record<string, string[]> = {
    pending: ["running", "skipped", "failed"],
    running: ["succeeded", "failed", "skipped"],
    succeeded: [], failed: [], skipped: [],
  };
  return allowed[current]?.includes(next) ?? false;
}

export async function createAuditEvent({ env, body }: AuthContext): Promise<ApiResponse> {
  const payload = requireObject(body);
  const id = requireString(payload.id, "id", { max: 128 })!;
  const result = await env.DB.prepare(`INSERT OR IGNORE INTO audit_events
    (id, actor, action, entity_type, entity_id, before_json, after_json,
     multica_issue_id, git_commit, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(id,
      requireString(payload.actor, "actor", { max: 256 }),
      requireString(payload.action, "action", { max: 256 }),
      requireString(payload.entity_type, "entity_type", { max: 128 }),
      requireString(payload.entity_id, "entity_id", { max: 128 }),
      jsonText(payload.before, "before", 100_000),
      jsonText(payload.after, "after", 100_000),
      optionalString(payload.multica_issue_id, "multica_issue_id", 256),
      optionalString(payload.git_commit, "git_commit", 128),
      payload.created_at == null ? new Date().toISOString() : requireIsoDate(payload.created_at, "created_at"),
    ).run();
  const inserted = (result.meta?.changes ?? 0) > 0;
  return { status: inserted ? 201 : 200, body: { id, created: inserted } };
}

export async function listAuditEvents({ env, url }: AuthContext): Promise<ApiResponse> {
  const limit = parseLimit(url, 100, 500);
  const entityType = url.searchParams.get("entity_type");
  const entityId = url.searchParams.get("entity_id");
  const rows = await env.DB.prepare(`SELECT * FROM audit_events
    WHERE (? IS NULL OR entity_type = ?) AND (? IS NULL OR entity_id = ?)
    ORDER BY created_at DESC LIMIT ?`).bind(entityType, entityType, entityId, entityId, limit).all();
  return { status: 200, body: { audit_events: rows.results ?? [] } };
}
