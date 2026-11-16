// Mirrors the edge agent's schemas (edge/camera-agent/aci_agent/api/schemas.py).

export type SourceType = "rtsp" | "onvif" | "usb" | "file" | "ffmpeg";
export type CameraStatus =
  "online" | "offline" | "connecting" | "reconnecting" | "error" | "disabled";
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
  ai_enabled?: boolean | null;
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
  device?: { device_id: string; device_name: string };
  sync?: { state: string; reason: string | null; queue: { pending: number } };
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
  status:
    | "starting"
    | "waiting_for_video"
    | "warming_up"
    | "active"
    | "paused_memory"
    | "disabled"
    | "error"
    | "not_running";
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
  | "camera_motion_activity"
  | "person_detected"
  | "person_left"
  | "person_crossed_line"
  | "camera_error"
  | "camera_recovered"
  | "ai_error"
  | "ai_recovered";

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

// ---------------------------------------------------------------- AI Engine (MVP 04)

export type AIMode =
  | "disabled"
  | "loading"
  | "active"
  | "degraded"
  | "offline"
  | "error"
  | "recovering";
export type AttributeProviderKind = "off" | "local" | "claude";

export interface AIStatus {
  mode: AIMode;
  reason: string;
  since: string;
  enabled: boolean;
  fallback: "standard_engine" | null;
  warnings: string[];
  model: {
    id: string;
    name: string | null;
    version: string | null;
    status: string;
    provider: string | null;
    backend: string | null;
  };
  metrics: {
    inference_fps: number | null;
    latency_avg_ms: number | null;
    latency_p95_ms: number | null;
    inferences: number;
    detections: number;
    timeouts: number;
    errors: number;
  };
  people_now: number;
  people_seen_session: number;
  recovery: { attempts: number; next_attempt_in_s: number | null };
  attributes_provider: AttributeProviderKind;
  attributes_cloud: Record<string, number | string | null> | null;
  resources: {
    process_rss_mb: number;
    max_rss_mb: number;
    system_available_mb: number;
    gpu: {
      available: boolean;
      detail?: string;
      name?: string;
      utilization_percent?: number;
      memory_used_mb?: number;
      memory_total_mb?: number;
    };
  };
}

export interface AIConfig {
  enabled: boolean;
  model_id: string;
  confidence_threshold: number;
  nms_threshold: number;
  min_box_height: number;
  process_fps: number;
  attributes: AttributeProviderKind;
  track_high_threshold: number;
  track_buffer_frames: number;
  min_track_hits: number;
}

export interface AIModel {
  id: string;
  name: string;
  version: string;
  file: string;
  size_bytes: number;
  sha256: string;
  task: string;
  license: string;
  source: string;
  status: "missing" | "unverified" | "invalid" | "valid" | "loaded";
  detail: string;
  selected: boolean;
}

export interface AIErrors {
  errors: { at: string; kind: string; detail: string }[];
  history: { at: string; mode: AIMode; reason: string }[];
}

// ---------------------------------------------------------------- Network discovery

export interface DiscoveredDevice {
  ip: string;
  open_ports: number[];
  onvif: {
    xaddr: string;
    port: number;
    name: string | null;
    hardware: string | null;
    manufacturer: string | null;
  } | null;
  rtsp: { port: number; server: string | null; status: number | null } | null;
  web: {
    port: number;
    server: string | null;
    title: string | null;
    signature: string | null;
  } | null;
  vendor: string | null;
  suggested_source_type: SourceType;
  suggested_source_uri: string;
  path_hint: string | null;
  already_added: boolean;
  camera_ids: string[];
}

export interface ScanJob {
  id: string;
  status: "running" | "done" | "failed" | "cancelled";
  progress: number;
  networks: string[];
  started_at: number;
  finished_at: number | null;
  devices: DiscoveredDevice[];
  error: string | null;
}

export interface LocalNetwork {
  interface_ip: string;
  network: string;
  hosts: number;
}

// ---------------------------------------------------------------------------- Edge runtime (MVP 05)

export type SyncState =
  "unconfigured" | "registering" | "online" | "offline" | "revoked" | "error";

export interface SyncQueueStats {
  enabled: boolean;
  pending: number;
  oldest_pending: string | null;
  dropped: number;
  max: number;
}

export interface ServerLinkInfo {
  server_url: string;
  remote_device_id: string | null;
  registered: boolean;
  registered_at: string | null;
  revoked: boolean;
  revoked_at: string | null;
  last_heartbeat_at: string | null;
  last_sync_at: string | null;
  config_version: number;
  config_applied_at: string | null;
}

export interface SyncStatus {
  state: SyncState;
  reason: string | null;
  last_error: string | null;
  last_success_at: string | null;
  next_attempt_in_s: number | null;
  heartbeat_interval_s: number;
  heartbeats: number;
  uploaded: number;
  duplicates: number;
  rejected: number;
  queue: SyncQueueStats;
  link: ServerLinkInfo | null;
  tls: { https: boolean; custom_ca: boolean };
  config_applied: {
    version: number;
    status: string;
    applied: string[];
    ignored: string[];
    errors: Record<string, string>;
    at: string;
  } | null;
}

export interface HardwareInfo {
  hostname: string;
  os: string;
  os_release: string;
  platform: string;
  architecture: string;
  cpu_model: string | null;
  cpu_cores_logical: number | null;
  memory_total_mb: number;
  board_model: string | null;
  is_jetson: boolean;
  python: string;
  opencv: string;
  opencv_cuda: boolean;
  agent_version: string;
}

export interface HardwareMetrics {
  cpu: { percent: number; cores: number | null };
  memory: { total_mb: number; used_mb: number; percent: number };
  disk: {
    path?: string;
    total_gb?: number;
    free_gb?: number;
    used_percent?: number;
    available?: boolean;
    detail?: string;
  };
  temperature: {
    available: boolean;
    max_c?: number;
    sensors?: Record<string, number>;
    detail?: string;
  };
  gpu: {
    available: boolean;
    name?: string;
    utilization_percent?: number;
    memory_used_mb?: number;
    memory_total_mb?: number;
    temperature_c?: number;
    detail?: string;
  };
  network: {
    connected: boolean;
    interfaces: {
      interface: string;
      ipv4: string[];
      speed_mbps: number | null;
    }[];
    throughput: { rx_kbps: number; tx_kbps: number } | null;
  };
  uptime_s: number;
}

export interface BackupInfo {
  id: string;
  path: string;
  files: Record<string, number>;
  size_bytes?: number;
}

export interface DeviceInfo {
  device_id: string;
  device_name: string;
  created_at: string | null;
  agent_version: string;
  uptime_seconds: number;
  hardware: HardwareInfo;
  metrics: HardwareMetrics;
  sync: SyncStatus;
  storage: {
    events_total: number;
    db_size_mb: number;
    free_disk_mb: number;
    retention_days: number;
    storage_paused: boolean;
  };
  backups: { count: number; latest: BackupInfo | null };
}

export interface SystemLogEntry {
  id: number;
  at: string;
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  component: string;
  message: string;
}
