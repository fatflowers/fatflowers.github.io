import { getCatalog, getDueChannels, syncCatalog } from "./catalog.ts";
import { ApiError, constantTimeEqual, errorResponse, jsonResponse, requireObject, sha256Hex } from "./http.ts";
import { getPendingAnalysis, writeAnalyses, writeItems } from "./items.ts";
import { revisePublishedReport } from "./revisions.ts";
import { pendingEnrichment, enrichItem, coverage, getItem } from "./enrichment.ts";
import {
  createAuditEvent,
  createReport,
  createRun,
  getReportInput,
  getReport,
  getRun,
  listRuns,
  listAuditEvents,
  updateReportStatus,
  updateRun,
} from "./tracking.ts";
import type { ApiResponse, AuthContext, Env, JsonObject } from "./types.ts";

const MAX_REQUEST_BYTES = 2_000_000;
const IDEMPOTENCY_TTL_SECONDS = 86_400;

type Handler = (context: AuthContext) => Promise<ApiResponse>;

interface Route {
  method: string;
  pattern: RegExp;
  handler: Handler;
  write: boolean;
}

const routes: Route[] = [
  { method: "GET", pattern: /^\/v1\/health$/, handler: health, write: false },
  { method: "GET", pattern: /^\/v1\/catalog$/, handler: getCatalog, write: false },
  { method: "POST", pattern: /^\/v1\/catalog\/sync$/, handler: syncCatalog, write: true },
  { method: "GET", pattern: /^\/v1\/channels\/due$/, handler: getDueChannels, write: false },
  { method: "POST", pattern: /^\/v1\/items\/batch$/, handler: writeItems, write: true },
  { method: "GET", pattern: /^\/v1\/items\/pending-enrichment$/, handler: pendingEnrichment, write: false },
  { method: "POST", pattern: /^\/v1\/items\/(?<id>[^/]+)\/enrichment$/, handler: enrichItem, write: true },
  { method: "GET", pattern: /^\/v1\/coverage$/, handler: coverage, write: false },
  { method: "GET", pattern: /^\/v1\/items\/pending-analysis$/, handler: getPendingAnalysis, write: false },
  { method: "GET", pattern: /^\/v1\/items\/(?<id>[^/]+)$/, handler: getItem, write: false },
  { method: "POST", pattern: /^\/v1\/analyses\/batch$/, handler: writeAnalyses, write: true },
  { method: "GET", pattern: /^\/v1\/reports\/input$/, handler: getReportInput, write: false },
  { method: "GET", pattern: /^\/v1\/reports\/(?<id>[^/]+)$/, handler: getReport, write: false },
  { method: "POST", pattern: /^\/v1\/reports$/, handler: createReport, write: true },
  { method: "PATCH", pattern: /^\/v1\/reports\/(?<id>[^/]+)\/editorial-revision$/, handler: revisePublishedReport, write: true },
  { method: "PATCH", pattern: /^\/v1\/reports\/(?<id>[^/]+)\/status$/, handler: updateReportStatus, write: true },
  { method: "POST", pattern: /^\/v1\/runs$/, handler: createRun, write: true },
  { method: "GET", pattern: /^\/v1\/runs$/, handler: listRuns, write: false },
  { method: "GET", pattern: /^\/v1\/runs\/(?<id>[^/]+)$/, handler: getRun, write: false },
  { method: "PATCH", pattern: /^\/v1\/runs\/(?<id>[^/]+)$/, handler: updateRun, write: true },
  { method: "POST", pattern: /^\/v1\/audit-events$/, handler: createAuditEvent, write: true },
  { method: "GET", pattern: /^\/v1\/audit-events$/, handler: listAuditEvents, write: false },
];

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const requestId = request.headers.get("cf-ray") ?? crypto.randomUUID();
    const startedAt = Date.now();
    try {
      const url = new URL(request.url);
      const path = url.pathname.length > 1 ? url.pathname.replace(/\/$/, "") : url.pathname;
      const route = routes.find((candidate) => candidate.method === request.method && candidate.pattern.test(path));
      if (!route) throw new ApiError(404, "not_found", "Route not found");
      if (path !== "/v1/health") authenticate(request, env);
      const match = route.pattern.exec(path);
      const params = Object.fromEntries(Object.entries(match?.groups ?? {}).map(([key, value]) => [key, decodeURIComponent(value)]));
      if (!route.write) {
        return jsonResponse(await route.handler({ request, env, url, params, body: null }));
      }
      return await executeIdempotentWrite(request, env, url, path, params, route.handler);
    } catch (error) {
      return errorResponse(error, requestId);
    } finally {
      console.log(JSON.stringify({
        level: "info",
        event: "request_complete",
        request_id: requestId,
        method: request.method,
        path: new URL(request.url).pathname,
        duration_ms: Date.now() - startedAt,
      }));
    }
  },
};

function authenticate(request: Request, env: Env): void {
  if (!env.API_TOKEN || env.API_TOKEN.length < 24) {
    throw new ApiError(503, "service_not_configured", "API authentication is not configured");
  }
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ")) throw new ApiError(401, "unauthorized", "Bearer authentication is required");
  const candidate = authorization.slice("Bearer ".length);
  if (!constantTimeEqual(candidate, env.API_TOKEN)) throw new ApiError(401, "unauthorized", "Invalid bearer token");
}

async function readJsonBody(request: Request): Promise<{ text: string; body: JsonObject }> {
  const declaredLength = Number(request.headers.get("content-length") ?? 0);
  if (declaredLength > MAX_REQUEST_BYTES) throw new ApiError(413, "request_too_large", "Request body is too large");
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") throw new ApiError(415, "unsupported_media_type", "Content-Type must be application/json");
  const text = await request.text();
  if (text.length === 0) throw new ApiError(400, "invalid_json", "Request body is required");
  if (new TextEncoder().encode(text).byteLength > MAX_REQUEST_BYTES) {
    throw new ApiError(413, "request_too_large", "Request body is too large");
  }
  let decoded: unknown;
  try {
    decoded = JSON.parse(text);
  } catch {
    throw new ApiError(400, "invalid_json", "Request body must contain valid JSON");
  }
  return { text, body: requireObject(decoded) };
}

async function executeIdempotentWrite(
  request: Request,
  env: Env,
  url: URL,
  path: string,
  params: Record<string, string>,
  handler: Handler,
): Promise<Response> {
  const key = request.headers.get("idempotency-key");
  if (!key || key.length > 128 || !/^[A-Za-z0-9._:-]+$/.test(key)) {
    throw new ApiError(400, "invalid_idempotency_key", "A 1-128 character Idempotency-Key is required");
  }
  const { text, body } = await readJsonBody(request);
  const requestHash = await sha256Hex(text);
  const now = new Date().toISOString();
  const existing = await env.DB.prepare(`SELECT request_hash, response_status, response_body, expires_at
    FROM idempotency_keys WHERE idempotency_key = ? AND method = ? AND path = ?`)
    .bind(key, request.method, path).first<{
      request_hash: string;
      response_status: number;
      response_body: string;
      expires_at: string;
    }>();
  if (existing && existing.expires_at > now) {
    if (existing.request_hash !== requestHash) {
      throw new ApiError(409, "idempotency_conflict", "Idempotency-Key was already used with a different request body");
    }
    return new Response(existing.response_body, {
      status: existing.response_status,
      headers: {
        "content-type": "application/json; charset=utf-8",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff",
        "x-idempotent-replay": "true",
      },
    });
  }
  if (existing) {
    await env.DB.prepare("DELETE FROM idempotency_keys WHERE idempotency_key = ? AND method = ? AND path = ?")
      .bind(key, request.method, path).run();
  }

  const result = await handler({ request, env, url, params, body });
  const responseBody = JSON.stringify(result.body);
  const expiresAt = new Date(Date.now() + IDEMPOTENCY_TTL_SECONDS * 1_000).toISOString();
  await env.DB.prepare(`INSERT INTO idempotency_keys
    (idempotency_key, method, path, request_hash, response_status, response_body, created_at, expires_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(key, request.method, path, requestHash, result.status, responseBody, now, expiresAt).run();
  return new Response(responseBody, {
    status: result.status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

async function health({ env }: AuthContext): Promise<ApiResponse> {
  const result = await env.DB.prepare("SELECT 1 AS ok").first<{ ok: number }>();
  if (result?.ok !== 1) throw new ApiError(503, "database_unavailable", "Database health check failed");
  return {
    status: 200,
    body: {
      status: "ok",
      service: "personal-intelligence-api",
      database: "ok",
      timestamp: new Date().toISOString(),
    },
  };
}
