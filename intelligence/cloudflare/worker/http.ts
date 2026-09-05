import type { ApiResponse, JsonObject } from "./types.ts";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: unknown,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function jsonResponse(response: ApiResponse): Response {
  return new Response(JSON.stringify(response.body), {
    status: response.status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

export function errorResponse(error: unknown, requestId: string): Response {
  if (error instanceof ApiError) {
    return jsonResponse({
      status: error.status,
      body: {
        error: {
          code: error.code,
          message: error.message,
          ...(error.details === undefined ? {} : { details: error.details }),
        },
        request_id: requestId,
      },
    });
  }

  console.error(JSON.stringify({ level: "error", event: "unhandled_error", request_id: requestId }));
  return jsonResponse({
    status: 500,
    body: {
      error: { code: "internal_error", message: "The request could not be completed" },
      request_id: requestId,
    },
  });
}

export function requireObject(value: unknown, name = "body"): JsonObject {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ApiError(400, "invalid_request", `${name} must be an object`);
  }
  return value as JsonObject;
}

export function requireArray(value: unknown, name: string, max: number): unknown[] {
  if (!Array.isArray(value)) throw new ApiError(400, "invalid_request", `${name} must be an array`);
  if (value.length > max) throw new ApiError(413, "batch_too_large", `${name} cannot exceed ${max} entries`);
  return value;
}

export function requireString(
  value: unknown,
  name: string,
  options: { min?: number; max?: number; nullable?: boolean } = {},
): string | null {
  if (value === null && options.nullable) return null;
  if (typeof value !== "string") throw new ApiError(400, "invalid_request", `${name} must be a string`);
  const min = options.min ?? 1;
  const max = options.max ?? 10_000;
  if (value.length < min || value.length > max) {
    throw new ApiError(400, "invalid_request", `${name} length must be between ${min} and ${max}`);
  }
  return value;
}

export function optionalString(value: unknown, name: string, max = 10_000): string | null {
  if (value === undefined || value === null) return null;
  return requireString(value, name, { min: 0, max, nullable: true });
}

export function requireNumber(value: unknown, name: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    throw new ApiError(400, "invalid_request", `${name} must be a number between ${min} and ${max}`);
  }
  return value;
}

export function optionalBoolean(value: unknown, defaultValue: boolean): boolean {
  if (value === undefined) return defaultValue;
  if (typeof value !== "boolean") throw new ApiError(400, "invalid_request", "enabled must be a boolean");
  return value;
}

export function enumString(value: unknown, name: string, choices: readonly string[]): string {
  const text = requireString(value, name, { max: 64 });
  if (!choices.includes(text!)) {
    throw new ApiError(400, "invalid_request", `${name} must be one of: ${choices.join(", ")}`);
  }
  return text!;
}

export function jsonText(value: unknown, name: string, max = 50_000): string | null {
  if (value === undefined || value === null) return null;
  let encoded: string;
  try {
    encoded = JSON.stringify(value);
  } catch {
    throw new ApiError(400, "invalid_request", `${name} must be JSON serializable`);
  }
  if (encoded.length > max) throw new ApiError(413, "field_too_large", `${name} is too large`);
  return encoded;
}

export function parseLimit(url: URL, fallback = 100, max = 500): number {
  const raw = url.searchParams.get("limit");
  if (raw === null) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > max) {
    throw new ApiError(400, "invalid_request", `limit must be an integer between 1 and ${max}`);
  }
  return value;
}

export function isIsoDate(value: string): boolean {
  return !Number.isNaN(Date.parse(value));
}

export function requireIsoDate(value: unknown, name: string): string {
  const text = requireString(value, name, { max: 64 })!;
  if (!isIsoDate(text)) throw new ApiError(400, "invalid_request", `${name} must be an ISO-8601 date/time`);
  return text;
}

export async function sha256Hex(value: string): Promise<string> {
  const data = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((part) => part.toString(16).padStart(2, "0")).join("");
}

export function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = new TextEncoder().encode(left);
  const rightBytes = new TextEncoder().encode(right);
  const length = Math.max(leftBytes.length, rightBytes.length);
  let mismatch = leftBytes.length ^ rightBytes.length;
  for (let index = 0; index < length; index += 1) {
    mismatch |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }
  return mismatch === 0;
}
