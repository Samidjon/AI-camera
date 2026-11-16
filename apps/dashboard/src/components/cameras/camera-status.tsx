import { StatusIndicator, type StatusTone } from "@/components/common/status-indicator";
import type { CameraStatus, FailureKind } from "@/lib/agent/types";

export const STATUS_META: Record<CameraStatus, { label: string; tone: StatusTone; pulse?: boolean }> = {
  online: { label: "Online", tone: "online", pulse: true },
  connecting: { label: "Connecting", tone: "warning" },
  reconnecting: { label: "Reconnecting", tone: "warning" },
  error: { label: "Error", tone: "error" },
  offline: { label: "Offline", tone: "idle" },
  disabled: { label: "Disabled", tone: "idle" },
};

export const FAILURE_HINT: Record<FailureKind, string> = {
  unreachable: "Camera can't be reached. Check power, cable, IP address, port and network.",
  auth_failed: "Camera rejected the credentials. Check the username and password.",
  not_found: "Source not found: wrong stream path, missing file or USB device not present.",
  invalid_source: "The source address is invalid or doesn't speak the expected protocol.",
  unsupported_format: "The stream opens but video can't be decoded (codec or format not supported).",
  stream_lost: "Video stopped arriving. The agent keeps retrying automatically.",
  unknown: "Unexpected error. The agent keeps retrying automatically.",
};

export function CameraStatusBadge({ status, className }: { status: CameraStatus; className?: string }) {
  const meta = STATUS_META[status];
  return <StatusIndicator tone={meta.tone} label={meta.label} pulse={meta.pulse} className={className} />;
}

export const SOURCE_LABEL: Record<string, string> = {
  rtsp: "RTSP",
  onvif: "ONVIF",
  usb: "USB",
  file: "File",
  ffmpeg: "Stream",
};
