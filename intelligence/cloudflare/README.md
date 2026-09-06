# Personal Intelligence Cloudflare API

Cloudflare Worker + D1 persistence layer for the personal intelligence pipeline. The API is internal. Every endpoint except the minimal health probe requires a bearer token; browser-side Hugo code must never call it.

## Resources

- Worker: `personal-intelligence-api`
- D1: `personal-intelligence`
- D1 binding: `DB`
- Worker secret: `API_TOKEN` (minimum 24 characters)

Before the first deployment, replace the all-zero `database_id` in `wrangler.jsonc` with the ID returned by:

```bash
wrangler d1 create personal-intelligence --location=apac
```

Then set a random API token locally without committing it:

```bash
wrangler secret put API_TOKEN
```

Apply and verify migrations:

```bash
npm run db:migrate:local
npm run db:migrate:remote
```

Build checks and dependency-free unit tests:

```bash
npm run check
npm test
```

## Request contract

All write requests require these headers:

```text
Authorization: Bearer <API_TOKEN>
Content-Type: application/json
Idempotency-Key: <stable-key-for-the-logical-operation>
```

An idempotency key is scoped to method and path and retained for 24 hours. Reusing it with an identical raw JSON body replays the stored response; using it with a different body returns `409 idempotency_conflict`. Database unique constraints remain the final defense against duplicate writes.

Timestamps are ISO-8601 strings. JSON-valued fields are accepted as JSON and encoded by the Worker; callers do not send pre-encoded JSON strings.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/health` | Public minimal Worker and D1 health probe |
| GET | `/v1/catalog` | Complete target/channel/tag catalog |
| POST | `/v1/catalog/sync` | Upsert a compiled catalog |
| GET | `/v1/channels/due` | Enabled channels whose interval elapsed |
| POST | `/v1/items/batch` | Insert up to 100 normalized items and optionally advance channel state |
| GET | `/v1/items/pending-analysis` | Items that do not yet have an analysis |
| POST | `/v1/analyses/batch` | Upsert up to 100 schema-validated analyses |
| GET | `/v1/reports/input` | Analyzed items for a reporting window |
| POST | `/v1/reports` | Create or regenerate a non-published report draft |
| PATCH | `/v1/reports/:id/status` | Enforce report state transitions |
| POST | `/v1/runs` | Create a tracked pipeline run |
| GET/PATCH | `/v1/runs/:id` | Inspect or transition a run |
| GET/POST | `/v1/audit-events` | Query or append audit records |

### Catalog sync

`POST /v1/catalog/sync` accepts either the normalized/compiled catalog directly or wrapped as `{ "catalog": { ... } }`. IDs must already be stable; resolving YAML slugs to IDs is the responsibility of `intelctl`. It accepts the domain aliases `type`/`collector` or the canonical API fields `target_type`, `channel_type`, `tag_type` and `collector_type`.

```json
{
  "mode": "replace",
  "targets": [{
    "id": "target-composio",
    "slug": "composio",
    "name": "Composio",
    "target_type": "company",
    "priority": "high",
    "enabled": true
  }],
  "channels": [{
    "id": "channel-composio-blog",
    "target_id": "target-composio",
    "slug": "composio-blog",
    "name": "Official Blog",
    "channel_type": "blog",
    "collector_type": "mcp",
    "url": "https://composio.dev/blog",
    "interval_minutes": 60,
    "tool_binding": "firecrawl-page-scrape-v1",
    "enabled": true,
    "config": {}
  }],
  "tags": [{
    "id": "tag-competitor",
    "slug": "competitor",
    "name": "竞品",
    "tag_type": "relationship"
  }],
  "target_tags": [{"target_id": "target-composio", "tag_id": "tag-competitor"}],
  "channel_tags": []
}
```

`replace` is authoritative: existing targets/channels missing from the request are disabled, and tag relationships are rebuilt. Historical records are retained. `merge` only upserts supplied records and adds supplied relationships.

### Report and run states

Report transitions are enforced as:

```text
draft -> validating -> ready -> published
   |          |          |
   +----------+----------+-> failed -> draft
```

Publishing requires both `published_url` and `git_commit`. Published report rows are immutable through `POST /v1/reports`.

Run transitions are enforced as:

```text
pending -> running -> succeeded | failed | skipped
pending -> failed | skipped
```

Terminal run states are immutable.
# Candidate enrichment API

`GET /v1/items/:id` returns `{ "item": {...} }` including `content_revision`.
`GET /v1/reports/input` excludes items used by published morning/midday/evening reports
by default. Weekly reports and explicit editorial corrections must pass
`include_reported=true` when they intentionally need previously reported material.

Apply migration `0003_article_enrichment.sql` before deploying this Worker version.

`GET /v1/items/pending-enrichment?since=<ISO>&limit=100&target_id=<optional>`
returns discovery-only records, including initial baseline discoveries, from enabled
targets/channels. Default `since` is seven days before now; limit is capped at 500.
It also recovers recent (72-hour publication window) or undated snippets under 400
characters and placeholder analyses beginning `公开来源显示`, even when they were
previously treated as complete. Known old non-discovery articles are not rehydrated.
Ordering interleaves targets, newest candidate first per target, so a large sitemap
cannot monopolize the queue. Failed attempts are retried up to three times; rejected
or ready candidates leave the queue. The returned `content_revision` is the optimistic
concurrency token. `since` filters discovery retrieval, not event publication: callers
must reject old articles after reading their actual publication evidence.

Pending analysis defaults to a 72-hour publication window (optional `since`), never
uses fetch time as publication time, and interleaves targets newest-first. Recent
baseline items can be recovered only when ready or complete native platform/feed
content. Native Twitter/X text is accepted; feed bodies require at least 400 characters
and explicit `content_complete: true` metadata. Failed/rejected or unknown-date items
cannot enter analysis. `ready` still requires enrichment's evidence validation.

`POST /v1/items/:id/enrichment` requires Bearer and Idempotency-Key headers, with:

```json
{
  "expected_revision": 0,
  "status": "ready",
  "reason": "Fetched full original article and extracted publication metadata",
  "title": "Article title",
  "content_text": "Full article body (at least 200 nonblank characters)",
  "final_url": "https://example.com/article",
  "published_at": "2026-09-06T00:00:00Z",
  "fetched_at": "2026-09-06T01:00:00Z",
  "tool_name": "configured-scrape-tool",
  "date_evidence": {
    "kind": "article_metadata",
    "value": "2026-09-06T00:00:00Z",
    "source_url": "https://example.com/article"
  }
}
```

Allowed evidence kinds: `article_metadata`, `article_text`, `feed`, `platform`.
Optional `publication_precision` is `day` or `second` (default); preserve `day` for
source dates without a time instead of presenting invented precision to readers.
These identify source evidence, not a guarantee of factual correctness: the caller
must extract evidence from the original response and the editor must inspect it.
The Worker rejects missing evidence, unsupported kinds and future dates relative to
retrieval, and does not accept a caller-supplied `verified` flag as proof.
`failed`/`rejected` require only expected revision, status and a nonblank reason.

Each accepted attempt atomically archives original fields and the previous analysis
in `item_enrichments`, increments `content_revision`, and invalidates old analysis.
Ready replaces body/title/canonical URL/date on the same item, computes SHA-256 on the
server, clears discovery/baseline flags, and retains provenance in raw metadata.
Original discovery URLs and archived fields remain recoverable. No INSERT OR IGNORE
operation is used for hydration. Revision conflicts return 409.

`POST /v1/analyses/batch` accepts `content_revision` on each analysis; it must match
the item. Omission means revision zero for compatibility with un-enriched records.
Clients must carry the revision read alongside the body; stale analysis returns 409.

`GET /v1/coverage?since=<ISO>` returns per-enabled-target discovered, enriched,
rejected, failed, pending_enrichment and analyzed counts. Default window is seven days.
Pending includes exhausted failures so incomplete research remains visible to operators.
