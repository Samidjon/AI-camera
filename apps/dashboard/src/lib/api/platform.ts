import { API_BASE_URL, ApiError, apiRequest, NetworkError } from "./client";

/** Central server — platform-wide views (MVP 07). All aggregation happens on the server. */

export interface Overview {
  since: string;
  generated_at: string;
  cameras: {
    total: number;
    online: number;
    offline: number;
    by_status: Record<string, number>;
  } | null;
  devices: {
    total: number;
    active: number;
    by_status: Record<string, number>;
    ai_active: number;
    without_ai: number;
    ai_not_running: number;
  } | null;
  events_today: {
    motion: number;
    zone: number;
    line: number;
    person_events: number;
    by_type: Record<string, number>;
  } | null;
  observations_today: number | null;
  recent_events:
    | {
        id: string;
        event_type: string;
        source: string;
        occurred_at: string;
        camera_name: string | null;
        camera_ref: string | null;
        device_name: string | null;
      }[]
    | null;
}

export interface RegistryCamera {
  id: string;
  external_id: string | null;
  name: string;
  location: string | null;
  source_type: string;
  status: string;
  is_enabled: boolean;
  ai_enabled: boolean | null;
  fps: number | null;
  last_error: string | null;
  device_id: string;
  device_name: string;
  device_status: string;
}

export interface ServerEvent {
  id: string;
  device_id: string | null;
  camera_id: string | null;
  camera_ref: string | null;
  camera_name: string | null;
  device_name: string | null;
  event_type: string;
  source: "standard" | "ai" | "system";
  occurred_at: string;
  received_at: string;
  zone_id: string | null;
  line_id: string | null;
  track_id: string | null;
  metadata: Record<string, unknown>;
}

export interface EventQuery {
  category?: string[];
  device_id?: string;
  camera_ref?: string;
  since?: string;
  until?: string;
  q?: string;
}

export interface AttributeValue {
  value: string;
  confidence: number;
  source?: string;
}

export interface Observation {
  id: string;
  track_id: string;
  device_id: string | null;
  device_name: string | null;
  camera_id: string;
  camera_name: string | null;
  first_seen: string;
  last_seen: string;
  duration_s: number;
  direction: string | null;
  confidence: number | null;
  attributes: Record<string, AttributeValue | string | null> | null;
  attributes_hidden: boolean;
  detector: string | null;
  events: string[];
  lines_crossed: string[];
  ended: boolean;
  event_count: number;
}

export interface UserItem {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface RoleInfo {
  name: string;
  description: string | null;
  permissions: string[];
  is_system: boolean;
  user_count: number;
  editable: boolean;
}

export interface SystemSettings {
  events_retention_days: number;
  observations_retention_days: number;
  store_person_attributes: boolean;
  show_person_attributes: boolean;
}

function eventParams(q: EventQuery): URLSearchParams {
  const p = new URLSearchParams();
  q.category?.forEach((c) => p.append("category", c));
  if (q.device_id) p.set("device_id", q.device_id);
  if (q.camera_ref) p.set("camera_ref", q.camera_ref);
  if (q.since) p.set("since", q.since);
  if (q.until) p.set("until", q.until);
  if (q.q?.trim()) p.set("q", q.q.trim());
  return p;
}

export const platform = {
  overview: (token: string, since: string, signal?: AbortSignal) =>
    apiRequest<Overview>(
      `/dashboard/overview?since=${encodeURIComponent(since)}`,
      { token, signal },
    ),
  cameras: (token: string, signal?: AbortSignal) =>
    apiRequest<RegistryCamera[]>("/cameras", { token, signal }),
  events: (
    token: string,
    q: EventQuery,
    cursor?: string | null,
    limit = 50,
    signal?: AbortSignal,
  ) => {
    const p = eventParams(q);
    p.set("limit", String(limit));
    if (cursor) p.set("cursor", cursor);
    return apiRequest<{ items: ServerEvent[]; next_cursor: string | null }>(
      `/events?${p}`,
      { token, signal },
    );
  },
  /** Downloads the CSV with the same filters (auth header → fetch + blob, never a token in a URL). */
  exportCsv: async (
    token: string,
    q: EventQuery,
  ): Promise<{ blob: Blob; filename: string }> => {
    let res: Response;
    try {
      res = await fetch(
        `${API_BASE_URL}/api/v1/events/export.csv?${eventParams(q)}`,
        {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
        },
      );
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
      throw new ApiError(res.status, "export_failed", message);
    }
    const name =
      /filename="([^"]+)"/.exec(
        res.headers.get("content-disposition") ?? "",
      )?.[1] ?? "events.csv";
    return { blob: await res.blob(), filename: name };
  },
  observations: (
    token: string,
    q: {
      since?: string;
      camera_ref?: string;
      device_id?: string;
      offset?: number;
      limit?: number;
    },
    signal?: AbortSignal,
  ) => {
    const p = new URLSearchParams();
    if (q.since) p.set("since", q.since);
    if (q.camera_ref) p.set("camera_ref", q.camera_ref);
    if (q.device_id) p.set("device_id", q.device_id);
    p.set("limit", String(q.limit ?? 24));
    p.set("offset", String(q.offset ?? 0));
    return apiRequest<{
      items: Observation[];
      total: number;
      limit: number;
      offset: number;
    }>(`/observations?${p}`, { token, signal });
  },
  users: (token: string) => apiRequest<UserItem[]>("/users", { token }),
  createUser: (
    token: string,
    body: { email: string; full_name?: string; password: string; role: string },
  ) => apiRequest<UserItem>("/users", { token, method: "POST", body }),
  updateUser: (
    token: string,
    id: string,
    body: Partial<{
      full_name: string | null;
      role: string;
      is_active: boolean;
      password: string;
    }>,
  ) => apiRequest<UserItem>(`/users/${id}`, { token, method: "PATCH", body }),
  deleteUser: (token: string, id: string) =>
    apiRequest<void>(`/users/${id}`, { token, method: "DELETE" }),
  roles: (token: string) => apiRequest<RoleInfo[]>("/roles", { token }),
  permissionCatalog: (token: string) =>
    apiRequest<Record<string, string>>("/roles/permissions", { token }),
  updateRole: (token: string, name: string, permissions: string[]) =>
    apiRequest<RoleInfo>(`/roles/${name}`, {
      token,
      method: "PUT",
      body: { permissions },
    }),
  settings: (token: string) =>
    apiRequest<SystemSettings>("/system/settings", { token }),
  updateSettings: (token: string, body: Partial<SystemSettings>) =>
    apiRequest<SystemSettings>("/system/settings", {
      token,
      method: "PATCH",
      body,
    }),
  configuration: (token: string) =>
    apiRequest<Record<string, unknown>>("/system/configuration", { token }),
};

/** Shared labels: event categories as the server defines them. Motion and person events are never mixed. */
export const EVENT_CATEGORIES: { key: string; label: string }[] = [
  { key: "motion", label: "Motion" },
  { key: "person", label: "Person detection (AI)" },
  { key: "zone", label: "Zones" },
  { key: "line", label: "Line crossing" },
  { key: "camera_error", label: "Camera errors" },
  { key: "ai_error", label: "AI errors" },
  { key: "device_error", label: "Device errors" },
];

export const EVENT_LABELS: Record<string, string> = {
  motion_started: "Motion started",
  motion_ended: "Motion ended",
  camera_motion_activity: "Motion activity",
  object_entered_zone: "Entered zone",
  object_exited_zone: "Exited zone",
  line_crossed: "Line crossed",
  person_detected: "Person detected (AI)",
  person_left: "Person left (AI)",
  person_crossed_line: "Person crossed line (AI)",
  camera_error: "Camera error",
  camera_recovered: "Camera recovered",
  ai_error: "AI error",
  ai_recovered: "AI recovered",
  device_offline: "Device offline",
  device_error: "Device error",
  device_online: "Device back online",
};
