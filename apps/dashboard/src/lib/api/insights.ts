import { API_BASE_URL, ApiError, apiRequest, NetworkError } from "./client";

/** MVP 08 — analytics, reports and alerts (all computed on the central server from stored events). */

export interface Bucket {
  bucket: string;
  value: number;
}
export interface HeatCell {
  weekday: number;
  hour: number;
  value: number;
}

export interface StandardAnalytics {
  motion_events: number;
  activity_duration_s: number;
  motion_frequency_per_hour: number;
  hourly: Bucket[];
  daily: Bucket[];
  heatmap: HeatCell[];
  zones: {
    device_id: string | null;
    camera_ref: string | null;
    camera_name: string | null;
    zone_id: string;
    name: string;
    entries: number;
    exits: number;
    occupancy_estimate: number;
    activity_duration_s: number;
    avg_dwell_s: number | null;
  }[];
  lines: {
    camera_ref: string | null;
    camera_name: string | null;
    line_id: string;
    name: string;
    forward: number;
    backward: number;
  }[];
  cameras: {
    device_id: string | null;
    camera_ref: string | null;
    camera_name: string | null;
    motion_events: number;
    activity_duration_s: number;
  }[];
}

export type AiAnalytics =
  | { available: false; reason: string }
  | {
      available: true;
      people_observations: number;
      inbound: number;
      outbound: number;
      avg_observation_duration_s: number | null;
      hourly: Bucket[];
      daily: Bucket[];
      time_distribution: { hour: number; value: number }[];
      heatmap: HeatCell[];
      peak_hours: { hour: number; observations: number }[];
      lines: {
        camera_ref: string | null;
        line_id: string;
        name: string;
        inbound: number;
        outbound: number;
      }[];
      cameras: {
        camera_ref: string | null;
        camera_name: string | null;
        observations: number;
        inbound: number;
        outbound: number;
      }[];
    };

export interface AnalyticsSummary {
  range: { since: string; until: string; timezone: string; hours: number };
  standard: StandardAnalytics;
  ai: AiAnalytics;
  health: {
    devices?: unknown[];
    [k: string]: unknown;
  };
  limitations: Record<string, string>;
  generated_at: string;
}

export interface AnalyticsQuery {
  since?: string;
  until?: string;
  tz?: string;
  device_id?: string;
  camera_ref?: string;
}

export type ReportKind =
  "daily" | "weekly" | "monthly" | "camera" | "device" | "zone" | "ai";

export interface AlertItem {
  id: string;
  rule_id: string | null;
  kind: string;
  severity: "info" | "warning" | "critical";
  title: string;
  message: string;
  device_id: string | null;
  camera_ref: string | null;
  context: Record<string, unknown>;
  status: "open" | "acknowledged" | "resolved";
  created_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
}

export interface AlertRule {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  severity: "info" | "warning" | "critical";
  threshold: number | null;
  device_id: string | null;
  camera_ref: string | null;
  zone_id: string | null;
  line_id: string | null;
  cooldown_s: number;
  channels: ("in_app" | "email")[];
  email_recipients: string[];
  created_at: string;
  updated_at: string;
}

export type AlertRuleInput = Omit<
  AlertRule,
  "id" | "created_at" | "updated_at" | "severity"
> & {
  severity: AlertRule["severity"] | null;
};

export interface AlertCatalog {
  kinds: {
    kind: string;
    group: string;
    label: string;
    severity: string;
    threshold?: number;
    unit?: string;
    metric: boolean;
  }[];
  email: { configured: boolean; from: string | null };
}

export interface Delivery {
  channel: string;
  recipient: string | null;
  status: "pending" | "sent" | "failed" | "skipped";
  attempts: number;
  error: string | null;
  sent_at: string | null;
}

function params(
  q: AnalyticsQuery,
  extra: Record<string, string> = {},
): URLSearchParams {
  const p = new URLSearchParams(extra);
  for (const [k, v] of Object.entries(q)) if (v) p.set(k, v);
  return p;
}

async function download(
  path: string,
  token: string,
  fallback: string,
): Promise<{ blob: Blob; filename: string }> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}/api/v1${path}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
  } catch {
    throw new NetworkError();
  }
  if (!res.ok) {
    let message = res.statusText;
    try {
      message = (await res.json()).error?.message ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, "download_failed", message);
  }
  const name =
    /filename="([^"]+)"/.exec(
      res.headers.get("content-disposition") ?? "",
    )?.[1] ?? fallback;
  return { blob: await res.blob(), filename: name };
}

export function saveBlob({ blob, filename }: { blob: Blob; filename: string }) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const insights = {
  summary: (token: string, q: AnalyticsQuery, signal?: AbortSignal) =>
    apiRequest<AnalyticsSummary>(`/analytics/summary?${params(q)}`, {
      token,
      signal,
    }),
  report: (
    token: string,
    kind: ReportKind,
    format: "csv" | "pdf",
    q: AnalyticsQuery,
  ) =>
    download(
      `/analytics/reports/${kind}?${params(q, { format })}`,
      token,
      `${kind}-report.${format}`,
    ),

  alerts: (token: string, status?: string, signal?: AbortSignal) =>
    apiRequest<AlertItem[]>(
      `/alerts?limit=200${status ? `&status=${status}` : ""}`,
      { token, signal },
    ),
  alertCounts: (token: string, signal?: AbortSignal) =>
    apiRequest<{ open: number; acknowledged: number; resolved: number }>(
      "/alerts/counts",
      { token, signal },
    ),
  acknowledge: (token: string, id: string) =>
    apiRequest<AlertItem>(`/alerts/${id}/acknowledge`, {
      token,
      method: "POST",
    }),
  resolve: (token: string, id: string) =>
    apiRequest<AlertItem>(`/alerts/${id}/resolve`, { token, method: "POST" }),
  acknowledgeAll: (token: string) =>
    apiRequest<{ acknowledged: number }>("/alerts/acknowledge-all", {
      token,
      method: "POST",
    }),
  deliveries: (token: string, id: string) =>
    apiRequest<Delivery[]>(`/alerts/${id}/deliveries`, { token }),

  catalog: (token: string) =>
    apiRequest<AlertCatalog>("/alert-rules/catalog", { token }),
  rules: (token: string) => apiRequest<AlertRule[]>("/alert-rules", { token }),
  createRule: (token: string, body: AlertRuleInput) =>
    apiRequest<AlertRule>("/alert-rules", { token, method: "POST", body }),
  updateRule: (token: string, id: string, body: AlertRuleInput) =>
    apiRequest<AlertRule>(`/alert-rules/${id}`, { token, method: "PUT", body }),
  deleteRule: (token: string, id: string) =>
    apiRequest<void>(`/alert-rules/${id}`, { token, method: "DELETE" }),
  addRecommended: (token: string) =>
    apiRequest<AlertRule[]>("/alert-rules/recommended", {
      token,
      method: "POST",
    }),
  testEmail: (token: string, to: string) =>
    apiRequest<{ sent: boolean }>("/alert-rules/test-email", {
      token,
      method: "POST",
      body: { to },
    }),
};

export const SEVERITY_LABEL = {
  info: "Info",
  warning: "Warning",
  critical: "Critical",
} as const;
