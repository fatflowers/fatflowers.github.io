import { parseLimit, requireIsoDate, requireNumber } from "./http.ts";
import type { ApiResponse, AuthContext } from "./types.ts";

const FEED_FIELDS = `i.id, i.title, i.canonical_url, i.url, i.published_at,
  a.analyzed_at, a.headline, a.summary, a.key_change, a.why_it_matters,
  a.company_impact, a.importance, a.confidence, a.topics_json,
  a.watch_next_json, a.evidence_json,
  t.slug AS target_slug, t.name AS target_name,
  c.slug AS channel_slug, c.name AS channel_name`;

export async function exportFeed({ env, url }: AuthContext): Promise<ApiResponse> {
  const minimum = requireNumber(Number(url.searchParams.get("min_importance") ?? 2), "min_importance", 1, 5);
  const changedAfter = url.searchParams.get("changed_after");
  if (changedAfter !== null) {
    const after = requireIsoDate(changedAfter, "changed_after");
    const changes = await env.DB.prepare(`SELECT item_id, analyzed_at
      FROM analyses
      WHERE analyzed_at > ?
      ORDER BY analyzed_at DESC, item_id DESC LIMIT 1`)
      .bind(after).all<Record<string, unknown>>();
    const latest = changes.results?.[0] ?? null;
    console.log(JSON.stringify({ event: "d1_query_cost", query: "feed_changes",
      rows_read: changes.meta?.rows_read, returned: latest ? 1 : 0 }));
    return { status: 200, body: { changed: latest !== null, latest: latest ?? null } };
  }

  const from = requireIsoDate(url.searchParams.get("from") ?? "1970-01-01T00:00:00Z", "from");
  const to = requireIsoDate(url.searchParams.get("to") ?? new Date().toISOString(), "to");
  const cursorPublished = url.searchParams.get("cursor_published");
  const cursorId = url.searchParams.get("cursor_id");
  const cursorTime = cursorPublished === null ? null : requireIsoDate(cursorPublished, "cursor_published");
  const limit = parseLimit(url, 500, 500);
  const rows = await env.DB.prepare(`SELECT ${FEED_FIELDS}
    FROM items i
    JOIN analyses a ON a.item_id=i.id
    JOIN targets t ON t.id=i.target_id
    JOIN channels c ON c.id=i.channel_id
    WHERE i.is_baseline=0
      AND (i.enrichment_status IS NULL OR i.enrichment_status='ready')
      AND COALESCE(json_extract(i.raw_metadata_json,'$.discovery_only'),0)=0
      AND a.importance >= ?
      AND julianday(i.published_at) >= julianday(?)
      AND julianday(i.published_at) < julianday(?)
      AND (? IS NULL OR julianday(i.published_at) < julianday(?)
        OR (julianday(i.published_at)=julianday(?) AND i.id < ?))
    ORDER BY julianday(i.published_at) DESC, i.id DESC
    LIMIT ?`)
    .bind(minimum, from, to, cursorTime, cursorTime, cursorTime, cursorId, limit).all<Record<string, unknown>>();
  const items = rows.results ?? [];
  const last = items.at(-1);
  const latest = await env.DB.prepare(`SELECT item_id, analyzed_at FROM analyses
    ORDER BY analyzed_at DESC, item_id DESC LIMIT 1`)
    .first<Record<string, unknown>>();
  console.log(JSON.stringify({ event: "d1_query_cost", query: "feed_export",
    rows_read: rows.meta?.rows_read, returned: items.length }));
  return {
    status: 200,
    body: {
      items,
      latest_analyzed_at: latest?.analyzed_at ?? null,
      next_cursor: items.length === limit && last
        ? { published_at: last.published_at, item_id: last.id }
        : null,
    },
  };
}
