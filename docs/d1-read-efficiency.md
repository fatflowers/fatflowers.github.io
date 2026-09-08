# D1 read-limit incident — 2026-09-08

Cloudflare `wrangler d1 insights personal-intelligence --time-period 1d --sort-by reads`
identified the pending-analysis query as the primary reader: 57 executions,
6,134,274 rows read (107,618 average). Coverage used 492,995 rows across 114 calls.
These are rolling 24-hour statistics, not a UTC-day billing total.

The pending-analysis query joined both target tags and channel tags before
grouping and limiting articles, multiplying intermediate rows. It now selects
and materializes the bounded article set first, then resolves each tag list
independently. Eligibility and target balancing are preserved.

Worker deployed: `a2e2c5bd-fe8c-4a1a-adc8-5a430f14bd52`.
27 Worker tests passed. Actual post-change production read counts remain
unverified because the account read quota is exhausted.

## Pending after quota reset

Reset stated by Cloudflare: 2026-09-09 00:00 UTC / 08:00 Asia/Shanghai.
Migration `0005_read_efficiency.sql` is NOT applied: Cloudflare returned 7500.
After reset, from `intelligence/cloudflare`, run:

```sh
wrangler d1 migrations apply personal-intelligence --remote
```

Then inspect `d1_query_cost` logs for `pending_analysis` and D1 insights to
compare rows read against the old average. No plan upgrade was performed.
