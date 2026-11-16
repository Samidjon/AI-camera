// Mirrors the edge agent's schemas (edge/camera-agent/aci_agent/api/schemas.py).

export type SourceType = "rtsp" | "onvif" | "usb" | "file" | "ffmpeg";
export type CameraStatus = "online" | "offline" | "connecting" | "reconnecting" | "error" | "disabled";
export type FailureKind =
  | "unreachable"
  | "auth_failed"
  | "not_found"
  | "invalid_source"
  | "unsupported_format"
  | "stream_lost"
  | "unknown";

export interface Resolution {
  width: number;
  height: number;
}

export interface CameraOptions {
  max_fps?: number | null;
  loop?: boolean | null;
  onvif_profile?: number | null;
}

export interface Camera {
  id: string;
  name: string;
  location: string | null;
  group_id: string | null;
  source_type: SourceType;
  /** Credential-free source description. */
  source: string;
  has_credentials: boolean;
  is_enabled: boolean;
  options: CameraOptions;
  status: CameraStatus;
  /** Measured from received frames. */
  fps: number | null;
  nominal_fps: number | null;
  resolution: Resolution | null;
  last_connected_at: string | null;
  last_frame_at: string | null;
  last_error: string | null;
  failure_kind: FailureKind | null;
  reconnect_attempts: number;
  frames_received: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface CameraGroup {
  id: string;
  name: string;
  description: string | null;
  camera_count: number;
  created_at: string;
  updated_at: string;
}

export interface CameraInput {
  name: string;
  source_type: SourceType;
  source_uri: string;
  location?: string | null;
  group_id?: string | null;
  is_enabled?: boolean;
  options?: CameraOptions;
  username?: string;
  password?: string;
  connect?: boolean;
}

export interface CameraUpdate extends Partial<Omit<CameraInput, "connect">> {
  clear_credentials?: boolean;
}

export interface TestResult {
  ok: boolean;
  message: string;
  failure_kind: FailureKind | null;
  resolution: Resolution | null;
  nominal_fps: number | null;
  latency_ms: number | null;
  stream: string | null;
}

export interface StreamToken {
  token: string;
  expires_in: number;
  stream_url: string;
  snapshot_url: string;
}

export interface AgentStatus {
  status: string;
  version: string;
  uptime_seconds: number;
  auth_configured: boolean;
  database_ok: boolean;
  cameras_total: number;
  cameras_by_status: Record<CameraStatus, number>;
  active_workers: number;
  leaked_workers: number;
}
