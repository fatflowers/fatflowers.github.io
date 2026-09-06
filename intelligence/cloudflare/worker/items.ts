import { ApiError, jsonText, optionalString, parseLimit, requireArray, requireIsoDate, requireNumber, requireObject, requireString } from "./http.ts";
import type { ApiResponse, AuthContext, D1PreparedStatement } from "./types.ts";

const MAX_ITEMS = 100;
const MAX_CONTENT_LENGTH = 100_000;

export async function writeItems({ env, body }: AuthContext): Promise<ApiResponse> {
  const payload = requireObject(body);
  const now = new Date().toISOString();
  const items = requireArray(payload.items, "items", MAX_ITEMS).map((entry, index) => {
    const value = requireObject(entry, `items[${index}]`);
    return {
      id: requireString(value.id, `items[${index}].id`, { max: 128 })!,
      targetId: requireString(value.target_id, `items[${index}].target_id`, { max: 128 })!,
      channelId: requireString(value.channel_id, `items[${index}].channel_id`, { max: 128 })!,
      externalId: optionalString(value.external_id, `items[${index}].external_id`, 512),
      url: requireString(value.url, `items[${index}].url`, { max: 4_096 })!,
      canonicalUrl: optionalString(value.canonical_url, `items[${index}].canonical_url`, 4_096),
      title: optionalString(value.title, `items[${index}].title`, 2_000),
      author: optionalString(value.author, `items[${index}].author`, 512),
      publishedAt: value.published_at == null ? null : requireIsoDate(value.published_at, `items[${index}].published_at`),
      fetchedAt: requireIsoDate(value.fetched_at, `items[${index}].fetched_at`),
      contentText: optionalString(value.content_text, `items[${index}].content_text`, MAX_CONTENT_LENGTH),
      contentHash: requireString(value.content_hash, `items[${index}].content_hash`, { max: 128 })!,
      language: optionalString(value.language, `items[${index}].language`, 32),
      rawMetadataJson: jsonText(value.raw_metadata, `items[${index}].raw_metadata`, 50_000),
      createdAt: value.created_at == null ? now : requireIsoDate(value.created_at, `items[${index}].created_at`),
    };
  });

  const seenIds = new Set<string>();
  const seenHashes = new Set<string>();
  for (const item of items) {
    if (seenIds.has(item.id)) throw new ApiError(400, "duplicate_item_id", "items contains a duplicate id");
    const hashKey = `${item.channelId}\u0000${item.contentHash}`;
    if (seenHashes.has(hashKey)) throw new ApiError(400, "duplicate_item_hash", "items contains a duplicate channel_id/content_hash pair");
    seenIds.add(item.id);
    seenHashes.add(hashKey);
  }

  const channelIds = [...new Set(items.map((item) => item.channelId))];
  const channelTargets = new Map<string, string>();
  if (channelIds.length > 0) {
    const lookups = await env.DB.batch(channelIds.map((channelId) => env.DB.prepare("SELECT id, target_id FROM channels WHERE id = ?").bind(channelId)));
    lookups.forEach((result, index) => {
      const row = result.results?.[0] as { id?: string; target_id?: string } | undefined;
      if (row?.id && row.target_id) channelTargets.set(row.id, row.target_id);
      else throw new ApiError(400, "unknown_channel", `Unknown channel_id: ${channelIds[index]}`);
    });
  }
  for (const item of items) {
    if (channelTargets.get(item.channelId) !== item.targetId) {
      throw new ApiError(400, "target_channel_mismatch", `target_id does not own channel_id for item ${item.id}`);
    }
  }

  const statements: D1PreparedStatement[] = items.map((item) => env.DB.prepare(`INSERT OR IGNORE INTO items
    (id, target_id, channel_id, external_id, url, canonical_url, title, author, published_at,
     fetched_at, content_text, content_hash, language, raw_metadata_json, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(item.id, item.targetId, item.channelId, item.externalId, item.url, item.canonicalUrl,
      item.title, item.author, item.publishedAt, item.fetchedAt, item.contentText, item.contentHash,
      item.language, item.rawMetadataJson, item.createdAt));

  const channelState = payload.channel_state === undefined ? null : requireObject(payload.channel_state, "channel_state");
  if (channelState) {
    const channelId = requireString(channelState.channel_id, "channel_state.channel_id", { max: 128 })!;
    const checkedAt = requireIsoDate(channelState.last_checked_at, "channel_state.last_checked_at");
    const succeeded = channelState.succeeded === true;
    const cursorJson = jsonText(channelState.cursor, "channel_state.cursor", 50_000);
    const errorAt = channelState.last_error_at == null ? checkedAt : requireIsoDate(channelState.last_error_at, "channel_state.last_error_at");
    const errorSummary = optionalString(channelState.error_summary, "channel_state.error_summary", 2_000);
    if (succeeded) {
      statements.push(env.DB.prepare(`UPDATE channels SET cursor_json = COALESCE(?, cursor_json),
        last_checked_at = ?, last_success_at = ?, last_error_at = NULL, last_error = NULL,
        consecutive_failures = 0, updated_at = ? WHERE id = ?`)
        .bind(cursorJson, checkedAt, checkedAt, now, channelId));
    } else {
      statements.push(env.DB.prepare(`UPDATE channels SET last_checked_at = ?, last_error_at = ?,
        last_error = ?, consecutive_failures = consecutive_failures + 1, updated_at = ? WHERE id = ?`)
        .bind(checkedAt, errorAt, errorSummary, now, channelId));
    }
  }

  const results = statements.length > 0 ? await env.DB.batch(statements) : [];
  const inserted = results.slice(0, items.length).reduce((total, result) => total + (result.meta?.changes ?? 0), 0);
  return {
    status: 200,
    body: {
      accepted: items.length,
      inserted,
      duplicates: items.length - inserted,
      channel_state_updated: channelState !== null,
    },
  };
}

export async function getPendingAnalysis({ env, url }: AuthContext): Promise<ApiResponse> {
  const limit = parseLimit(url, 100, 500);
  const targetId = url.searchParams.get("target_id");
  const channelId = url.searchParams.get("channel_id");
  const from = requireIsoDate(url.searchParams.get('since') ?? new Date(Date.now()-72*3600000).toISOString(), 'since');
  const rows = await env.DB.prepare(`WITH eligible AS (SELECT i.*, t.slug AS target_slug, c.slug AS channel_slug,
      GROUP_CONCAT(DISTINCT tg.slug) AS target_tag_slugs,
      GROUP_CONCAT(DISTINCT cg.slug) AS channel_tag_slugs
    FROM items i
    JOIN targets t ON t.id = i.target_id
    JOIN channels c ON c.id = i.channel_id
    LEFT JOIN target_tags tt ON tt.target_id = i.target_id
    LEFT JOIN tags tg ON tg.id = tt.tag_id
    LEFT JOIN channel_tags ct ON ct.channel_id = i.channel_id
    LEFT JOIN tags cg ON cg.id = ct.tag_id
    LEFT JOIN analyses a ON a.item_id = i.id
    WHERE a.item_id IS NULL AND t.enabled=1 AND c.enabled=1
      AND julianday(i.published_at)>=julianday(?) AND julianday(i.published_at)<=julianday('now')
      AND (i.enrichment_status IS NULL OR i.enrichment_status='ready')
      AND COALESCE(json_extract(i.raw_metadata_json, '$.discovery_only'), 0) = 0
      AND (i.enrichment_status='ready'
        OR (c.channel_type IN ('twitter','x') AND length(trim(COALESCE(i.content_text,'')))>0)
        OR (length(trim(COALESCE(i.content_text,'')))>=400 AND json_extract(i.raw_metadata_json,'$.content_complete')=1))
      AND (? IS NULL OR i.target_id = ?) AND (? IS NULL OR i.channel_id = ?)
    GROUP BY i.id
    ), ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY target_id ORDER BY published_at DESC,id) AS target_rank FROM eligible)
    SELECT * FROM ranked ORDER BY target_rank,target_id
    LIMIT ?`).bind(from, targetId, targetId, channelId, channelId, limit).all();
  // Cross-channel retellings do not necessarily share a URL. Give the analyst
  // bounded, actually-published context to distinguish repetition from new facts.
  // Scope by report publication time (not source date) so recent catch-up counts.
  const recent = await env.DB.prepare(`SELECT i.id, substr(i.title,1,500) AS title,
      substr(a.summary,1,2000) AS summary, substr(a.key_change,1,2000) AS key_change,
      i.canonical_url, i.published_at, MAX(r.published_at) AS reported_at
    FROM reports r JOIN report_items ri ON ri.report_id=r.id
    JOIN items i ON i.id=ri.item_id JOIN analyses a ON a.item_id=i.id
    WHERE r.report_status='published' AND r.edition IN ('morning','midday','evening')
      AND julianday(r.published_at)>=julianday('now','-7 days')
      AND julianday(r.published_at)<=julianday('now')
    GROUP BY i.id ORDER BY MAX(r.published_at) DESC, i.id LIMIT 100`).all();
  return { status: 200, body: { items: rows.results ?? [], recent_published_events: recent.results ?? [] } };
}

export async function writeAnalyses({ env, body }: AuthContext): Promise<ApiResponse> {
  const payload = requireObject(body);
  const analyses = requireArray(payload.analyses, "analyses", MAX_ITEMS).map((entry, index) => {
    const value = requireObject(entry, `analyses[${index}]`);
    return {
      itemId: requireString(value.item_id, `analyses[${index}].item_id`, { max: 128 })!,
      contentRevision: value.content_revision ?? 0,
      summary: requireString(value.summary, `analyses[${index}].summary`, { max: 20_000 })!,
      headline: optionalString(value.headline, `analyses[${index}].headline`, 60),
      keyChange: optionalString(value.key_change, `analyses[${index}].key_change`, 20_000),
      whyItMatters: optionalString(value.why_it_matters, `analyses[${index}].why_it_matters`, 20_000),
      companyImpact: optionalString(value.company_impact, `analyses[${index}].company_impact`, 20_000),
      importance: requireNumber(value.importance, `analyses[${index}].importance`, 1, 5),
      confidence: requireNumber(value.confidence, `analyses[${index}].confidence`, 0, 1),
      topicsJson: jsonText(value.topics ?? [], `analyses[${index}].topics`, 20_000),
      watchNextJson: jsonText(value.watch_next ?? [], `analyses[${index}].watch_next`, 20_000),
      evidenceJson: jsonText(value.evidence ?? [], `analyses[${index}].evidence`, 50_000),
      model: requireString(value.model, `analyses[${index}].model`, { max: 256 })!,
      promptVersion: requireString(value.prompt_version, `analyses[${index}].prompt_version`, { max: 128 })!,
      analyzedAt: requireIsoDate(value.analyzed_at, `analyses[${index}].analyzed_at`),
    };
  });
  const seen = new Set<string>();
  for (const analysis of analyses) {
    if (seen.has(analysis.itemId)) throw new ApiError(400, "duplicate_analysis_item", "analyses contains duplicate item_id");
    seen.add(analysis.itemId);
    const item = await env.DB.prepare('SELECT content_revision FROM items WHERE id=?').bind(analysis.itemId).first<{content_revision:number}>();
    if (!item || item.content_revision !== analysis.contentRevision) {
      throw new ApiError(409, 'analysis_revision_conflict', 'Analysis must match current item content_revision');
    }
  }
  const statements = analyses.map((analysis) => env.DB.prepare(`INSERT INTO analyses
    (item_id, headline, summary, key_change, why_it_matters, company_impact, importance, confidence,
     topics_json, watch_next_json, evidence_json, model, prompt_version, analyzed_at)
    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? FROM items WHERE id=? AND content_revision=?
    ON CONFLICT(item_id) DO UPDATE SET headline=excluded.headline, summary=excluded.summary, key_change=excluded.key_change,
      why_it_matters=excluded.why_it_matters, company_impact=excluded.company_impact,
      importance=excluded.importance, confidence=excluded.confidence, topics_json=excluded.topics_json,
      watch_next_json=excluded.watch_next_json, evidence_json=excluded.evidence_json,
      model=excluded.model, prompt_version=excluded.prompt_version, analyzed_at=excluded.analyzed_at`)
    .bind(analysis.itemId, analysis.headline, analysis.summary, analysis.keyChange, analysis.whyItMatters,
      analysis.companyImpact, analysis.importance, analysis.confidence, analysis.topicsJson,
      analysis.watchNextJson, analysis.evidenceJson, analysis.model, analysis.promptVersion,
      analysis.analyzedAt, analysis.itemId, analysis.contentRevision));
  // Recover recent full native posts accidentally included in initial baseline.
  // This happens in the same D1 transaction as the matching analysis write.
  for (const analysis of analyses) statements.push(env.DB.prepare(`UPDATE items SET is_baseline=0
    WHERE id=? AND content_revision=? AND is_baseline=1
      AND julianday(published_at)>=julianday('now','-72 hours') AND julianday(published_at)<=julianday('now')
      AND (enrichment_status IS NULL OR enrichment_status='ready')
      AND COALESCE(json_extract(raw_metadata_json,'$.discovery_only'),0)=0
      AND (enrichment_status='ready'
        OR (EXISTS(SELECT 1 FROM channels c WHERE c.id=items.channel_id AND c.channel_type IN ('twitter','x')) AND length(trim(COALESCE(content_text,'')))>0)
        OR (length(trim(COALESCE(content_text,'')))>=400 AND json_extract(raw_metadata_json,'$.content_complete')=1))
      AND EXISTS(SELECT 1 FROM analyses a WHERE a.item_id=items.id AND a.analyzed_at=?)`)
    .bind(analysis.itemId,analysis.contentRevision,analysis.analyzedAt));
  if (statements.length > 0) {
    const results = await env.DB.batch(statements);
    if (results.slice(0, analyses.length).some(result => result.meta?.changes !== 1)) {
      throw new ApiError(409, 'analysis_revision_conflict', 'Item changed during analysis; reload before retrying');
    }
  }
  return { status: 200, body: { upserted: analyses.length } };
}
