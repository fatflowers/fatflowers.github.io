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
