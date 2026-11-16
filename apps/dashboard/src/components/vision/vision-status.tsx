"use client";

import { Activity } from "lucide-react";
import { useCallback } from "react";

import { StatusIndicator, type StatusTone } from "@/components/common/status-indicator";
import { usePolling } from "@/hooks/use-polling";
import { agent } from "@/lib/agent/client";
import type { VisionState } from "@/lib/agent/types";
import { usePermissions } from "@/lib/auth/permissions";

const STATUS: Record<VisionState["status"], { label: string; tone: StatusTone }> = {
  active: { label: "Analysing", tone: "online" },
  warming_up: { label: "Learning background", tone: "warning" },
  starting: { label: "Starting", tone: "idle" },
  waiting_for_video: { label: "Waiting for video", tone: "idle" },
  paused_memory: { label: "Paused (memory limit)", tone: "warning" },
  disabled: { label: "Disabled", tone: "idle" },
  error: { label: "Error", tone: "error" },
  not_running: { label: "Not running", tone: "idle" },
};

/** Live Standard Vision Engine state for one camera (polled every 2 s while visible). */
export function VisionStatus({ cameraId }: { cameraId: string }) {
  const { token } = usePermissions();
  const fetcher = useCallback(
    async (signal: AbortSignal) => (token ? (await agent.vision(token, cameraId, signal)).state : null),
    [token, cameraId],
  );
  const { data: s, error } = usePolling(fetcher, 2000);

  if (error) return <p className="text-xs text-muted-foreground">Vision state unavailable: {error.message}</p>;
  if (!s) return null;
  const meta = STATUS[s.status] ?? STATUS.not_running;

  return (
    <div className="rounded-md border p-3">
      <div className="flex items-center gap-2">
        <Activity className="size-4 text-muted-foreground" aria-hidden />
        <span className="text-sm font-medium">Motion detection</span>
        <StatusIndicator className="ml-auto" tone={meta.tone} label={meta.label} />
      </div>
      {s.status !== "not_running" && (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-4">
          <Stat label="Motion now" value={s.motion_active ? "Yes" : "No"} highlight={s.motion_active} />
          <Stat label="Motion objects" value={s.objects ?? 0} />
          <Stat label="Analysis FPS" value={s.analysis_fps ?? "—"} />
          <Stat label="Processing" value={s.processing_ms != null ? `${s.processing_ms} ms` : "—"} />
          <Stat label="Frames analysed" value={(s.frames_analysed ?? 0).toLocaleString()} />
          <Stat label="Frames skipped" value={(s.frames_skipped ?? 0).toLocaleString()} />
          <Stat label="Lighting changes" value={s.lighting_changes ?? 0} />
          <Stat label="Stream interruptions" value={s.stream_interruptions ?? 0} />
        </dl>
      )}
      {s.last_error && <p className="mt-2 text-xs text-destructive">{s.last_error}</p>}
    </div>
  );
}

function Stat({ label, value, highlight }: { label: string; value: React.ReactNode; highlight?: boolean }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={highlight ? "font-mono text-status-warning" : "font-mono"}>{value}</dd>
    </div>
  );
}
