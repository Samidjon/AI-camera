// Mirrors the API server's Pydantic schemas (apps/api-server/app/schemas).
// Source of truth: GET /api/v1/openapi.json (exported to shared/contracts/openapi.json).

export type ComponentState = "ok" | "error" | "not_configured";
export type OverallState = "ok" | "degraded" | "error";

export interface ComponentHealth {
  state: ComponentState;
  latency_ms: number | null;
  detail: string | null;
}

export interface SystemStatus {
  status: OverallState;
  environment: string;
  uptime_seconds: number;
  started_at: string;
  timestamp: string;
  components: Record<string, ComponentHealth>;
}

export interface VersionInfo {
  name: string;
  version: string;
  api_version: string;
  python_version: string;
  environment: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  role: { name: "administrator" | "operator" | "viewer" | string; permissions: string[] };
  last_login_at: string | null;
  created_at: string;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details: unknown; request_id: string | null };
}
