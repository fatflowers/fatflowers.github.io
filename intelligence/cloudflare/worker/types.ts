export interface D1Result<T = Record<string, unknown>> {
  results?: T[];
  success: boolean;
  meta?: { changes?: number; last_row_id?: number };
}

export interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
  run<T = Record<string, unknown>>(): Promise<D1Result<T>>;
}

export interface D1Database {
  prepare(query: string): D1PreparedStatement;
  batch<T = Record<string, unknown>>(statements: D1PreparedStatement[]): Promise<D1Result<T>[]>;
}

export interface Env {
  DB: D1Database;
  API_TOKEN: string;
}

export type JsonObject = Record<string, unknown>;

export interface AuthContext {
  request: Request;
  env: Env;
  url: URL;
  params: Record<string, string>;
  body: JsonObject | null;
}

export interface ApiResponse {
  status: number;
  body: JsonObject;
}
