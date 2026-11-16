import type { ApiErrorBody, CurrentUser, SystemStatus, TokenResponse, VersionInfo } from "./types";

export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
const API_PREFIX = "/api/v1";
const DEFAULT_TIMEOUT_MS = 8000;

/** Error raised for any non-2xx response, carrying the server's error envelope. */
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId: string | null = null,
    public readonly details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The central server could not be reached at all (offline, DNS, CORS, timeout). */
export class NetworkError extends Error {
  constructor(message = "Central server is unreachable") {
    super(message);
    this.name = "NetworkError";
  }
}

export interface RequestOptions {
  /** Base URL including the API prefix. Defaults to the central server. */
  baseUrl?: string;
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  token?: string | null;
  signal?: AbortSignal;
  timeoutMs?: number;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), opts.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  opts.signal?.addEventListener("abort", () => controller.abort(), { once: true });

  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;

  let res: Response;
  try {
    res = await fetch(`${opts.baseUrl ?? API_BASE_URL + API_PREFIX}${path}`, {
      method: opts.method ?? "GET",
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: controller.signal,
      cache: "no-store",
    });
  } catch (err) {
    if (opts.signal?.aborted) throw err;
    const who = opts.baseUrl ? "Service" : "Central server";
    throw new NetworkError(controller.signal.aborted ? `${who} did not respond in time` : `${who} is unreachable`);
  } finally {
    clearTimeout(timeout);
  }

  if (!res.ok) {
    let body: Partial<ApiErrorBody> = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error body */
    }
    const e = body.error;
    throw new ApiError(res.status, e?.code ?? "http_error", e?.message ?? res.statusText, e?.request_id ?? null, e?.details);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) => apiRequest<{ status: string; timestamp: string }>("/health", { signal }),
  systemStatus: (signal?: AbortSignal) => apiRequest<SystemStatus>("/system/status", { signal }),
  version: (signal?: AbortSignal) => apiRequest<VersionInfo>("/system/version", { signal }),
  login: (email: string, password: string) =>
    apiRequest<TokenResponse>("/auth/login", { method: "POST", body: { email, password } }),
  me: (token: string, signal?: AbortSignal) => apiRequest<CurrentUser>("/auth/me", { token, signal }),
};
