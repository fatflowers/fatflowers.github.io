import { ApiError, requireArray, requireObject, requireString } from "./http.ts";
import type { ApiResponse, AuthContext } from "./types.ts";

/** Explicit editorial corrections after publishing the replacement Git commit. */
export async function revisePublishedReport({ env, body, params }: AuthContext): Promise<ApiResponse> {
  const payload = requireObject(body);
  const id = requireString(params.id, "id", { max: 128 })!;
  const title = requireString(payload.title, "title", { max: 500 })!;
  const content = requireString(payload.content_markdown, "content_markdown", { max: 500_000 })!;
  const reason = requireString(payload.reason, "reason", { max: 2_000 })!;
  const commit = requireCommit(payload.git_commit, "git_commit");
  const expected = requireCommit(payload.expected_git_commit, "expected_git_commit");
  const itemIds = payload.item_ids === undefined ? null : requireArray(payload.item_ids, "item_ids", 100)
    .map((value, index) => requireString(value, `item_ids[${index}]`, {max:128})!);
  if (itemIds && new Set(itemIds).size !== itemIds.length) throw new ApiError(400, "invalid_request", "Duplicate item IDs");
  if (!title.trim() || !content.trim() || !reason.trim() || commit === expected) {
    throw new ApiError(400, "invalid_editorial_revision", "Nonblank content, title, reason and a new Git commit are required");
  }
  const current = await env.DB.prepare("SELECT report_status, git_commit FROM reports WHERE id = ?")
    .bind(id).first<{ report_status: string; git_commit: string | null }>();
  if (!current) throw new ApiError(404, "report_not_found", "Report not found");
  if (current.report_status !== "published") {
    throw new ApiError(409, "report_not_published", "Editorial revisions require a published report");
  }
  if (current.git_commit !== expected) throw revisionConflict();

  const auditId = crypto.randomUUID();
  // Both statements use the same compare-and-swap predicate inside D1's atomic batch.
  // Archive directly from SQL so the snapshot cannot become stale between read and write.
  const results = await env.DB.batch([
    env.DB.prepare(`INSERT INTO audit_events
      (id, actor, action, entity_type, entity_id, before_json, after_json, git_commit, created_at)
      SELECT ?, 'authenticated-api', 'editorial_revision', 'report', id,
        json_object('title', title, 'content_markdown', content_markdown, 'git_commit', git_commit,
          'item_ids', json((SELECT COALESCE(json_group_array(item_id),'[]') FROM report_items WHERE report_id=reports.id))),
        json_object('title', ?, 'content_markdown', ?, 'git_commit', ?, 'reason', ?), ?, ?
      FROM reports WHERE id = ? AND report_status = 'published' AND git_commit = ?`)
      .bind(auditId, title, content, commit, reason, commit, new Date().toISOString(), id, expected),
    env.DB.prepare(`UPDATE reports SET title = ?, content_markdown = ?, git_commit = ?
      WHERE id = ? AND report_status = 'published' AND git_commit = ?
        AND EXISTS (SELECT 1 FROM audit_events WHERE id = ?)`)
      .bind(title, content, commit, id, expected, auditId),
    ...(itemIds === null ? [] : [
      env.DB.prepare('DELETE FROM report_items WHERE report_id=? AND EXISTS(SELECT 1 FROM audit_events WHERE id=?)').bind(id,auditId),
      ...itemIds.map((itemId, rank) => env.DB.prepare(`INSERT INTO report_items(report_id,item_id,rank,section)
        SELECT ?,?,?, 'reader-revision' WHERE EXISTS(SELECT 1 FROM audit_events WHERE id=?)`).bind(id,itemId,rank+1,auditId)),
    ]),
  ]);
  if (results[0]?.meta?.changes !== 1 || results[1]?.meta?.changes !== 1) throw revisionConflict();
  return { status: 200, body: { id, report_status: "published", git_commit: commit, audit_event_id: auditId } };
}

function requireCommit(value: unknown, name: string): string {
  const commit = requireString(value, name, { min: 7, max: 40 })!;
  if (!/^[0-9a-f]{7,40}$/.test(commit)) {
    throw new ApiError(400, "invalid_request", `${name} must be a 7-40 character lowercase Git commit hash`);
  }
  return commit;
}

function revisionConflict(): ApiError {
  return new ApiError(409, "editorial_revision_conflict", "Published report changed; reload its Git commit before revising");
}
