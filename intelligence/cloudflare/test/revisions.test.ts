import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import worker from "../worker/index.ts";
import type { D1Database, D1PreparedStatement, D1Result } from "../worker/types.ts";

class SqliteD1 implements D1Database {
  db = new DatabaseSync(":memory:");
  beforeBatch?: () => void;
  constructor() {
    this.db.exec(readFileSync(new URL("../migrations/0001_initial.sql", import.meta.url), "utf8"));
    this.db.exec(`INSERT INTO reports
      (id, report_date, edition, window_start, window_end, title, slug, report_status,
       content_markdown, published_url, git_commit, created_at, published_at)
      VALUES ('report-1', '2026-09-06', 'morning', '2026-09-05', '2026-09-06',
        'Original', 'morning', 'published', 'Original content', 'https://example.com/morning',
        'aaaaaaa', '2026-09-06', '2026-09-06')`);
  }
  prepare(query: string): D1PreparedStatement {
    let values: unknown[] = [];
    const stmt: D1PreparedStatement = {
      bind: (...args) => { values = args; return stmt; },
      first: async <T>() => (this.db.prepare(query).get(...values as never[]) ?? null) as T | null,
      all: async <T>() => ({ success: true, results: this.db.prepare(query).all(...values as never[]) as T[] }),
      run: async <T>() => ({ success: true, results: [] as T[], meta: { changes: Number(this.db.prepare(query).run(...values as never[]).changes) } }),
    };
    return stmt;
  }
  async batch<T>(statements: D1PreparedStatement[]): Promise<D1Result<T>[]> {
    this.beforeBatch?.();
    this.db.exec("BEGIN");
    try {
      const results: D1Result<T>[] = [];
      for (const statement of statements) results.push(await statement.run<T>());
      this.db.exec("COMMIT");
      return results;
    } catch (error) { this.db.exec("ROLLBACK"); throw error; }
  }
}

const TOKEN = "test-token-with-at-least-24-characters";
const payload = { title: "Corrected", content_markdown: "Useful [original](https://example.com/source)",
  reason: "Reader-focused correction", git_commit: "bbbbbbb", expected_git_commit: "aaaaaaa" };
function revise(db: SqliteD1, changes = {}, authenticated = true) {
  return worker.fetch(new Request("https://example.com/v1/reports/report-1/editorial-revision", {
    method: "PATCH", headers: { "content-type": "application/json", "idempotency-key": "revision-1",
      ...(authenticated ? { authorization: `Bearer ${TOKEN}` } : {}) },
    body: JSON.stringify({ ...payload, ...changes }),
  }), { DB: db, API_TOKEN: TOKEN });
}

test("published correction archives old content and retains publication identity; retry replays", async () => {
  const db = new SqliteD1();
  assert.equal((await revise(db)).status, 200);
  const row = db.db.prepare("SELECT * FROM reports").get()!;
  assert.equal(row.content_markdown, payload.content_markdown);
  assert.equal(row.git_commit, "bbbbbbb");
  assert.equal(row.report_status, "published");
  assert.equal(row.published_url, "https://example.com/morning");
  assert.equal(row.published_at, "2026-09-06");
  const audit = db.db.prepare("SELECT * FROM audit_events").get()!;
  assert.deepEqual(JSON.parse(String(audit.before_json)), { title: "Original", content_markdown: "Original content", git_commit: "aaaaaaa", item_ids: [] });
  assert.equal(JSON.parse(String(audit.after_json)).reason, payload.reason);
  const replay = await revise(db);
  assert.equal(replay.headers.get("x-idempotent-replay"), "true");
  assert.equal(db.db.prepare("SELECT count(*) AS n FROM audit_events").get()!.n, 1);
});

test("editorial correction replaces story membership and archives previous selection atomically", async () => {
  const db = new SqliteD1();
  db.db.exec(`INSERT INTO targets(id,slug,name,target_type,created_at,updated_at)
    VALUES ('t','t','Target','company','now','now');
    INSERT INTO channels(id,target_id,slug,name,channel_type,collector_type,created_at,updated_at)
    VALUES ('c','t','c','Channel','blog','http','now','now');
    INSERT INTO items(id,target_id,channel_id,url,fetched_at,content_hash,created_at)
    VALUES ('old','t','c','https://example.com/old','now','old','now'),
           ('new','t','c','https://example.com/new','now','new','now');
    INSERT INTO report_items VALUES ('report-1','old',1,'brief');`);
  assert.equal((await revise(db, { item_ids: ['new'] })).status, 200);
  assert.deepEqual(db.db.prepare('SELECT item_id FROM report_items').all().map(row => row.item_id), ['new']);
  const audit = db.db.prepare('SELECT before_json FROM audit_events').get()!;
  assert.deepEqual(JSON.parse(String(audit.before_json)).item_ids, ['old']);
});

test("stale revision and a change between lookup and atomic batch cannot overwrite or add audit", async () => {
  for (const race of [false, true]) {
    const db = new SqliteD1();
    const mutate = () => db.db.exec("UPDATE reports SET git_commit = 'ccccccc'");
    if (race) db.beforeBatch = mutate; else mutate();
    assert.equal((await revise(db)).status, 409);
    assert.equal(db.db.prepare("SELECT git_commit FROM reports").get()!.git_commit, "ccccccc");
    assert.equal(db.db.prepare("SELECT count(*) AS n FROM audit_events").get()!.n, 0);
  }
});

test("failed update rolls its preceding archive back", async () => {
  const db = new SqliteD1();
  db.db.exec("CREATE TRIGGER fail_update BEFORE UPDATE ON reports BEGIN SELECT RAISE(ABORT, 'test failure'); END");
  assert.equal((await revise(db)).status, 500);
  assert.equal(db.db.prepare("SELECT content_markdown FROM reports").get()!.content_markdown, "Original content");
  assert.equal(db.db.prepare("SELECT count(*) AS n FROM audit_events").get()!.n, 0);
});

test("revision requires authentication, published state, valid commit, nonblank reason and bounded content", async () => {
  assert.equal((await revise(new SqliteD1(), {}, false)).status, 401);
  const draft = new SqliteD1();
  draft.db.exec("UPDATE reports SET report_status = 'draft'");
  assert.equal((await revise(draft)).status, 409);
  for (const change of [{ git_commit: "main" }, { expected_git_commit: "no-hash" },
    { reason: "   " }, { content_markdown: "x".repeat(500_001) }, { git_commit: "aaaaaaa" }]) {
    assert.equal((await revise(new SqliteD1(), change)).status, 400);
  }
});
