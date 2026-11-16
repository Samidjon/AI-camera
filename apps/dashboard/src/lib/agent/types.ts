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

// ---------------------------------------------------------------- Standard Vision Engine (MVP 03)

export type MotionAlgorithm = "frame_diff" | "background" | "mog2" | "knn";

export interface VisionConfig {
  enabled: boolean;
  algorithm: MotionAlgorithm;
  sensitivity: number;
  min_object_area: number;
  merge_distance: number;
  blur_kernel: number;
  morphology_iterations: number;
  process_width: number;
  process_fps: number;
  restrict_to_zones: boolean;
  motion_end_delay_s: number;
  lighting_change_ratio: number;
  track_max_distance: number;
  track_max_missed: number;
  activity_interval_s: number;
}

export interface VisionTrack {
  track_id: string;
  box: [number, number, number, number];
  direction: string | null;
  duration_s: number;
}

export interface VisionState {
  status: "starting" | "waiting_for_video" | "warming_up" | "active" | "paused_memory" | "disabled" | "error" | "not_running";
  motion_active?: boolean;
  motion_since?: string | null;
  objects?: number;
  tracks?: VisionTrack[];
  zone_occupancy_estimate?: Record<string, number>;
  session_line_counts?: Record<string, { forward: number; backward: number }>;
  motion_ratio?: number;
  frames_analysed?: number;
  frames_skipped?: number;
  lighting_changes?: number;
  stream_interruptions?: number;
  processing_ms?: number | null;
  analysis_fps?: number | null;
  last_error?: string | null;
}

export interface Zone {
  id: string;
  camera_id: string;
  name: string;
  role: "monitor" | "exclude";
  polygon: [number, number][];
  is_enabled: boolean;
  occupancy_estimate?: number | null;
}

export interface CrossingLine {
  id: string;
  camera_id: string;
  name: string;
  a: [number, number];
  b: [number, number];
  is_enabled: boolean;
  counts_today?: { forward: number; backward: number };
}

export type AgentEventType =
  | "motion_started"
  | "motion_ended"
  | "object_entered_zone"
  | "object_exited_zone"
  | "line_crossed"
  | "camera_motion_activity";

export interface AgentEvent {
  id: string;
  camera_id: string;
  event_type: AgentEventType;
  source: "standard" | "ai" | "system";
  occurred_at: string;
  zone_id: string | null;
  line_id: string | null;
  track_id: string | null;
  metadata: Record<string, unknown>;
}

export interface EventsPage {
  items: AgentEvent[];
  next_cursor: string | null;
}
