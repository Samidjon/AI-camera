"use client";

import { CloudOff } from "lucide-react";

import { formatTime } from "@/lib/format";
import { useSystemStatus } from "@/lib/system/system-status-provider";

/** Shown on every page while the central server is unreachable: data on screen may be stale. */
export function StaleBanner() {
  const { error, updatedAt } = useSystemStatus();
  if (!error) return null;
  return (
    <div
      role="status"
      data-testid="stale-banner"
      className="flex items-center gap-2 border-b border-status-warning/30 bg-status-warning/10 px-4 py-2 text-xs md:px-6"
    >
      <CloudOff className="size-3.5 shrink-0 text-status-warning" aria-hidden />
      <span>
        Central server unreachable
        {updatedAt ? ` since ${formatTime(updatedAt)}` : ""} — fleet data shown
        is the last known state and may be stale. Edge agents keep recording
        locally; this page refreshes automatically when the server is back.
      </span>
    </div>
  );
}
