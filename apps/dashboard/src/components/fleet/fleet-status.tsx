import {
  StatusIndicator,
  type StatusTone,
} from "@/components/common/status-indicator";
import type { FleetStatus } from "@/lib/api/fleet";

const STATUS: Record<FleetStatus, { label: string; tone: StatusTone }> = {
  online: { label: "Online", tone: "online" },
  synchronizing: { label: "Synchronizing", tone: "warning" },
  degraded: { label: "Degraded", tone: "warning" },
  error: { label: "Error", tone: "error" },
  offline: { label: "Offline", tone: "idle" },
  revoked: { label: "Revoked", tone: "error" },
  pending: { label: "Pending", tone: "idle" },
};

export function FleetStatusIndicator({ status }: { status: FleetStatus }) {
  const meta = STATUS[status] ?? { label: status, tone: "idle" as StatusTone };
  return (
    <StatusIndicator
      tone={meta.tone}
      label={meta.label}
      pulse={status === "online" || status === "synchronizing"}
    />
  );
}

export function timeAgo(iso: string | null | undefined, now: number): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
