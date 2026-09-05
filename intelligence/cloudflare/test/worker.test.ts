import assert from "node:assert/strict";
import test from "node:test";

import worker from "../worker/index.ts";
import { constantTimeEqual, sha256Hex } from "../worker/http.ts";
import type { D1Database, D1PreparedStatement, D1Result, Env } from "../worker/types.ts";

const TOKEN = "test-token-with-at-least-24-characters";

class FakeStatement implements D1PreparedStatement {
  private values: unknown[] = [];
  private readonly database: FakeDatabase;
  private readonly sql: string;

  constructor(database: FakeDatabase, sql: string) {
    this.database = database;
    this.sql = sql;
  }

  bind(...values: unknown[]): D1PreparedStatement {
    this.values = values;
    return this;
  }

  async first<T>(): Promise<T | null> {
    if (this.sql.includes("SELECT 1 AS ok")) return { ok: 1 } as T;
    if (this.sql.includes("FROM idempotency_keys")) {
      const lookupKey = this.values.join("\u0000");
      return (this.database.idempotency.get(lookupKey) ?? null) as T | null;
    }
    return null;
  }

  async all<T>(): Promise<D1Result<T>> {
    return { success: true, results: [] };
  }

  async run<T>(): Promise<D1Result<T>> {
    if (this.sql.includes("INSERT INTO idempotency_keys")) {
      const [key, method, path, requestHash, responseStatus, responseBody, , expiresAt] = this.values;
      this.database.idempotency.set([key, method, path].join("\u0000"), {
        request_hash: requestHash as string,
        response_status: responseStatus as number,
        response_body: responseBody as string,
        expires_at: expiresAt as string,
      });
    }
    return { success: true, results: [], meta: { changes: 1 } };
  }
}

class FakeDatabase implements D1Database {
  idempotency = new Map<string, {
    request_hash: string;
    response_status: number;
    response_body: string;
    expires_at: string;
  }>();

  prepare(query: string): D1PreparedStatement {
    return new FakeStatement(this, query);
  }

  async batch<T>(statements: D1PreparedStatement[]): Promise<D1Result<T>[]> {
    return Promise.all(statements.map((statement) => statement.run<T>()));
  }
}

function env(database = new FakeDatabase()): Env {
  return { DB: database, API_TOKEN: TOKEN };
}

function request(path: string, init: RequestInit = {}): Request {
  return new Request(`https://worker.example${path}`, init);
}

test("health is a minimal public probe", async () => {
  const response = await worker.fetch(request("/v1/health"), env());
  assert.equal(response.status, 200);
  assert.equal((await response.json() as { status: string }).status, "ok");
});

test("health checks D1 with a valid token", async () => {
  const response = await worker.fetch(request("/v1/health/", {
    headers: { authorization: `Bearer ${TOKEN}` },
  }), env());
  assert.equal(response.status, 200);
  assert.equal((await response.json() as { status: string }).status, "ok");
});

test("write endpoints require an idempotency key", async () => {
  const response = await worker.fetch(request("/v1/runs", {
    method: "POST",
    headers: {
      authorization: `Bearer ${TOKEN}`,
      "content-type": "application/json",
    },
    body: "{}",
  }), env());
  assert.equal(response.status, 400);
  assert.equal((await response.json() as { error: { code: string } }).error.code, "invalid_idempotency_key");
});

test("an identical idempotent request replays its stored response", async () => {
  const database = new FakeDatabase();
  const body = JSON.stringify({ id: "audit-1" });
  database.idempotency.set(["stable-key", "POST", "/v1/audit-events"].join("\u0000"), {
    request_hash: await sha256Hex(body),
    response_status: 201,
    response_body: JSON.stringify({ id: "audit-1", created: true }),
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  });
  const response = await worker.fetch(request("/v1/audit-events", {
    method: "POST",
    headers: {
      authorization: `Bearer ${TOKEN}`,
      "content-type": "application/json",
      "idempotency-key": "stable-key",
    },
    body,
  }), env(database));
  assert.equal(response.status, 201);
  assert.equal(response.headers.get("x-idempotent-replay"), "true");
  assert.deepEqual(await response.json(), { id: "audit-1", created: true });
});

test("reusing an idempotency key with another body is rejected", async () => {
  const database = new FakeDatabase();
  database.idempotency.set(["stable-key", "POST", "/v1/audit-events"].join("\u0000"), {
    request_hash: await sha256Hex(JSON.stringify({ id: "first" })),
    response_status: 201,
    response_body: "{}",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  });
  const response = await worker.fetch(request("/v1/audit-events", {
    method: "POST",
    headers: {
      authorization: `Bearer ${TOKEN}`,
      "content-type": "application/json",
      "idempotency-key": "stable-key",
    },
    body: JSON.stringify({ id: "second" }),
  }), env(database));
  assert.equal(response.status, 409);
  assert.equal((await response.json() as { error: { code: string } }).error.code, "idempotency_conflict");
});

test("token comparison handles equal and different lengths", () => {
  assert.equal(constantTimeEqual("same-value", "same-value"), true);
  assert.equal(constantTimeEqual("same-value", "different-value"), false);
  assert.equal(constantTimeEqual("short", "shorter"), false);
});
