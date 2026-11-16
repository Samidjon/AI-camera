import { apiRequest } from "./client";

/** Central server — edge device fleet (MVP 06). */

export type FleetStatus =
  | "online"
  | "offline"
  | "degraded"
  | "synchronizing"
  | "error"
  | "revoked"
  | "pending";

export interface FleetDevice {
  id: string;
  name: string;
  description: string | null;
  group_id: string | null;
  group_name: string | null;
  status: FleetStatus;
  status_reason: string | null;
  status_changed_at: string | null;
  agent_version: string | null;
  last_heartbeat_at: string | null;
  last_seen_ip: string | null;
  heartbeat_interval_s: number;
  ai_status: { enabled?: boolean; mode?: string; reason?: string | null };
  camera_status: { total?: number; by_status?: Record<string, number> };
  sync_status: {
    pending?: number | null;
    dropped?: number | null;
    oldest_pending?: string | null;
  };
  desired_config_version: number;
  applied_config_version: number;
  registered_at: string | null;
  revoked_at: string | null;
}

export interface FleetCamera {
  id: string;
  external_id: string | null;
  name: string;
  location: string | null;
  source_type: string;
  status: string;
  is_enabled: boolean;
  ai_enabled: boolean | null;
  fps: number | null;
  resolution_width: number | null;
  resolution_height: number | null;
  last_error: string | null;
  last_frame_at: string | null;
}

export interface FleetDeviceDetail extends FleetDevice {
  hardware: Record<string, unknown>;
  metrics: {
    hardware?: {
      cpu?: { percent?: number };
      memory?: { percent?: number; used_mb?: number; total_mb?: number };
      disk?: { used_percent?: number; free_gb?: number };
      temperature?: { available?: boolean; max_c?: number };
      gpu?: {
        available?: boolean;
        name?: string;
        utilization_percent?: number;
      };
    };
    health?: { database_ok?: boolean; storage_paused?: boolean };
    uptime_s?: number;
    reported_name?: string;
  };
  cameras: FleetCamera[];
  rotate_credentials: boolean;
}

export interface DeviceGroup {
  id: string;
  name: string;
  description: string | null;
  device_count: number;
}

export interface EnrollmentToken {
  id: string;
  name: string | null;
  group_id: string | null;
  created_at: string;
  expires_at: string;
  max_uses: number;
  uses: number;
  revoked_at: string | null;
  token?: string;
}

export interface DeviceConfigDoc {
  device_name?: string;
  heartbeat_interval_s?: number;
  ai_enabled?: boolean;
  events?: {
    retention_days?: number;
    max_db_mb?: number;
    min_free_disk_mb?: number;
  };
  cameras?: Record<
    string,
    {
      is_enabled?: boolean;
      max_fps?: number;
      ai_enabled?: boolean;
      motion?: {
        enabled?: boolean;
        sensitivity?: number;
        algorithm?: string;
        min_object_area?: number;
      };
      zones?: {
        name: string;
        role: "monitor" | "exclude";
        polygon: [number, number][];
        is_enabled?: boolean;
      }[];
    }
  >;
}

export interface ConfigState {
  version: number;
  config: DeviceConfigDoc;
  checksum: string;
  status: string | null;
  applied_version: number;
  acknowledged_at: string | null;
  ack: {
    applied?: string[];
    ignored?: string[];
    errors?: Record<string, string>;
  };
}

export interface ConfigVersion {
  version: number;
  config: DeviceConfigDoc;
  checksum: string;
  comment: string | null;
  rollback_of: number | null;
  created_at: string;
  status: string;
  acknowledged_at: string | null;
  ack: ConfigState["ack"];
}

export interface SyncLog {
  id: number;
  kind: string;
  status: string;
  received: number;
  accepted: number;
  duplicate: number;
  rejected: number;
  duration_ms: number | null;
  created_at: string;
}

export const fleet = {
  devices: (token: string, signal?: AbortSignal) =>
    apiRequest<FleetDevice[]>("/devices", { token, signal }),
  device: (token: string, id: string, signal?: AbortSignal) =>
    apiRequest<FleetDeviceDetail>(`/devices/${id}`, { token, signal }),
  update: (
    token: string,
    id: string,
    body: {
      name?: string;
      group_id?: string | null;
      description?: string | null;
    },
  ) =>
    apiRequest<FleetDeviceDetail>(`/devices/${id}`, {
      token,
      method: "PATCH",
      body,
    }),
  revoke: (token: string, id: string) =>
    apiRequest<FleetDeviceDetail>(`/devices/${id}/revoke`, {
      token,
      method: "POST",
    }),
  remove: (token: string, id: string) =>
    apiRequest<void>(`/devices/${id}`, { token, method: "DELETE" }),
  rotate: (token: string, id: string) =>
    apiRequest<FleetDeviceDetail>(`/devices/${id}/rotate-credentials`, {
      token,
      method: "POST",
    }),
  syncLogs: (token: string, id: string) =>
    apiRequest<SyncLog[]>(`/devices/${id}/sync-logs?limit=20`, { token }),
  groups: (token: string) =>
    apiRequest<DeviceGroup[]>("/device-groups", { token }),
  createGroup: (token: string, name: string) =>
    apiRequest<DeviceGroup>("/device-groups", {
      token,
      method: "POST",
      body: { name },
    }),
  createEnrollment: (
    token: string,
    body: {
      name?: string;
      group_id?: string | null;
      max_uses?: number;
      expires_in_hours?: number;
    },
  ) =>
    apiRequest<EnrollmentToken>("/devices/enrollment-tokens", {
      token,
      method: "POST",
      body,
    }),
  config: (token: string, id: string) =>
    apiRequest<ConfigState>(`/devices/${id}/configuration`, { token }),
  patchConfig: (
    token: string,
    id: string,
    body: {
      base_version: number;
      config: unknown;
      replace?: boolean;
      comment?: string;
    },
  ) =>
    apiRequest<ConfigState>(`/devices/${id}/configuration`, {
      token,
      method: "PATCH",
      body,
    }),
  rollbackConfig: (
    token: string,
    id: string,
    to_version: number,
    base_version: number,
  ) =>
    apiRequest<ConfigState>(`/devices/${id}/configuration/rollback`, {
      token,
      method: "POST",
      body: { to_version, base_version },
    }),
  configHistory: (token: string, id: string) =>
    apiRequest<ConfigVersion[]>(`/devices/${id}/configuration/history`, {
      token,
    }),
};
